"""Prompt-oriented secret detection: precise prefixes first, heuristics gated by context."""

from __future__ import annotations

import math
import re
from collections import Counter

from garim.models import Finding

_PREFIX_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"sk-(?:proj|svcacct|admin)-[A-Za-z0-9_-]{20,}"), "openai_key"),
    (re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"), "anthropic_key"),
    (re.compile(r"(?:AKIA|ASIA)[A-Z0-9]{16}"), "aws_access_key"),
    # ghp_ · gho_ · ghu_ · ghs_ · ghr_ — 개인 · OAuth · 앱 토큰이 같은 모양이다.
    (re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})"), "github_token"),
    (re.compile(r"(?:sk|rk)_live_[A-Za-z0-9]{20,}"), "stripe_live_key"),
    # 구형 OpenAI 키(`sk-` + 48자). 다른 `sk-` 접두는 위 줄들이 먼저 받는다.
    (re.compile(r"(?<![A-Za-z0-9_-])sk-[A-Za-z0-9]{40,}(?![A-Za-z0-9_-])"), "openai_legacy_key"),
    (re.compile(r"(?:xox[baprs]-[A-Za-z0-9-]{20,}|xapp-[A-Za-z0-9-]{20,})"), "slack_token"),
    (re.compile(r"AIza[A-Za-z0-9_-]{30,}"), "google_api_key"),
    (re.compile(r"(?:glpat-|hf_)[A-Za-z0-9_-]{20,}"), "developer_token"),
    # ★ §2.4 「국내 서비스 키 앵커」 표가 지목한 것들인데 구현에 없었다. 이 표는
    #   §7.5·§9-3 이 기여로 내세우는 부분이라 비어 있으면 안 된다.
    #   토스페이먼츠 — API 연동(`test_sk_`/`live_sk_`)과 결제위젯(`test_gsk_`/`live_gsk_`).
    (re.compile(r"(?:test|live)_g?sk_[A-Za-z0-9]{20,}"), "toss_secret_key"),
    # 카카오 — §2.4 가 "키 자체보다 이 앵커가 신뢰도 최상" 이라고 적은 헤더 문자열.
    #   `Authorization:` 이 앞에 붙지 않은 맨 `KakaoAK ` 형태도 받아야 한다.
    (re.compile(r"KakaoAK\s+[A-Za-z0-9]{20,}"), "kakao_admin_key"),
)
_KOREAN_HEADER = re.compile(
    r"(?i)(?:Authorization\s*:\s*KakaoAK|KAKAO_(?:REST_)?API_KEY|"
    r"NCP_(?:ACCESS_KEY_ID|SECRET_KEY)|TOSS_(?:SECRET|CLIENT)_KEY|DATA_GO_KR_KEY|"
    # 네이버 클라우드는 키 형태가 아니라 **헤더명**이 앵커다(§2.4).
    r"x-ncp-(?:iam-access-key|apigw-api-key|apigw-signature-v2))"
    r"\s*[:=]\s*['\"]?([A-Za-z0-9_+/=-]{12,})"
)
# 공공데이터포털은 shape 보다 파라미터 키워드가 정확하다(§2.4).
_PUBLIC_DATA_KEY = re.compile(r"(?i)serviceKey\s*=\s*['\"]?([A-Za-z0-9%_+/=-]{20,})")
# 공동인증서(NPKI) — 파일명·경로 자체가 자격증명 유출 신호다(§2.4).
_NPKI = re.compile(r"(?i)(?:signCert\.der|signPri\.key|NPKI[\\/][^\s'\"]+)")
# RSA · EC · DSA · OPENSSH · ENCRYPTED · PGP(`… PRIVATE KEY BLOCK`) 모두 같은 머리다.
_PEM = re.compile(r"-----BEGIN (?:[A-Z0-9]+ ){0,3}PRIVATE KEY(?: BLOCK)?-----")
_JWT = re.compile(r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
# ★ 길이에 상한을 둔다 — `"mysql://a:" * 5000` 처럼 `@` 가 없으면 시작점마다 끝까지 되짚어
#   제곱 시간이 들었다(5차 검토). 실제 자격증명은 이 길이 안에 든다.
_DB_URL = re.compile(
    r"(?i)(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://"
    r"[^\s:/]{1,256}:[^\s@]{1,512}@[^\s]{1,1024}"
)
# ★ 이름 안의 키워드는 코드에서 본다. `[A-Z0-9_]*(?:SECRET|…)[A-Z0-9_]*` 는 겹친 반복이라
#   `"A" + "SECRET" * 8000` 에 22초, 여러 줄 모드 `^\s*` 는 빈 줄을 건너 제곱이었다(5차 검토).
_ENV = re.compile(
    r"(?im)^[ \t]*(?:export[ \t]+)?([A-Z][A-Z0-9_]{0,127})[ \t]*=[ \t]*([^\s#]{8,})"
)
_ENV_NAME = re.compile(r"(?i)SECRET|TOKEN|PASSWORD|PASSWD|API_KEY|PRIVATE_KEY")
_GENERIC_TOKEN = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_+/=-]{20,}(?![A-Za-z0-9_-])")
_SECRET_ANCHOR = re.compile(
    r"(?i)(?:api[_ -]?key|secret|token|password|비밀번호|인증키|접근키|시크릿)"
)
_PLACEHOLDER = re.compile(
    r"(?i)(?:example|sample|dummy|placeholder|your[_-]|changeme|test[_-]?key|x{4,})"
)


def _entropy(value: str) -> float:
    counts = Counter(value)
    length = len(value)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def detect_secrets(text: str) -> list[Finding]:
    findings: list[Finding] = []
    for pattern, rule in _PREFIX_PATTERNS:
        for match in pattern.finditer(text):
            # ★ 고정밀 prefix 도 플레이스홀더 검사를 거친다. 이 검사가 (A) 단계에
            #   걸려 있지 않아 `AKIAIOSFODNN7EXAMPLE`(AWS 공식 문서 예제)과
            #   `sk-proj-xxxxxxxx` 가 신뢰도 1.0 으로 나갔다 — 축 (d) 오탐 0.170 의
            #   실체다. 같은 슬라이스에서 CredSweeper 는 0.000 이었다.
            #
            #   버리지 않고 **강등**한다. §2.4 의 (C) 는 "애매한 것은 차단이 아니라
            #   사용자 확인" 이고, `sk-proj-xxxx` 는 진짜 키를 부분 가린 것일 수도
            #   있다. 기본 임계(0.5) 아래로 내려 판정에서 빠지되 감사에는 남는다.
            if _PLACEHOLDER.search(match.group(0)):
                findings.append(
                    Finding("L-SEC-01", match.span(), 0.35, "L0", f"{rule}_placeholder")
                )
                continue
            findings.append(Finding("L-SEC-01", match.span(), 1.0, "L0", rule))
    for match in _KOREAN_HEADER.finditer(text):
        findings.append(Finding("L-SEC-02", match.span(1), 0.95, "L0", "korean_service_header"))
    for match in _PUBLIC_DATA_KEY.finditer(text):
        findings.append(Finding("L-SEC-02", match.span(1), 0.9, "L0", "public_data_service_key"))
    for match in _NPKI.finditer(text):
        findings.append(Finding("L-SEC-03", match.span(), 0.9, "L0", "npki_certificate"))
    for match in _PEM.finditer(text):
        findings.append(Finding("L-SEC-03", match.span(), 1.0, "L0", "private_key_header"))
    for match in _JWT.finditer(text):
        findings.append(Finding("L-SEC-03", match.span(), 0.95, "L0", "jwt_format"))
    for match in _DB_URL.finditer(text):
        findings.append(Finding("L-SEC-04", match.span(), 0.95, "L0", "database_url"))
    for match in _ENV.finditer(text):
        if _ENV_NAME.search(match.group(1)):
            findings.append(Finding("L-SEC-04", match.span(2), 0.9, "L0", "secret_assignment"))

    for match in _GENERIC_TOKEN.finditer(text):
        value = match.group(0)
        if _PLACEHOLDER.search(value) or not value.isascii() or _entropy(value) < 3.5:
            continue
        context = text[max(0, match.start() - 40) : match.start()]
        if _SECRET_ANCHOR.search(context):
            findings.append(Finding("L-SEC-05", match.span(), 0.7, "L0", "anchored_entropy"))
    return findings
