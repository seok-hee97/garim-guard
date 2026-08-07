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
    (re.compile(r"(?:ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})"), "github_token"),
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
_PEM = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----")
_JWT = re.compile(r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
_DB_URL = re.compile(
    r"(?i)(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^\s:/]+:[^\s@]+@[^\s]+"
)
_ENV = re.compile(
    r"(?im)^\s*(?:export\s+)?([A-Z][A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|API_KEY|PRIVATE_KEY)[A-Z0-9_]*)"
    r"\s*=\s*([^\s#]{8,})"
)
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
        findings.append(Finding("L-SEC-04", match.span(2), 0.9, "L0", "secret_assignment"))

    for match in _GENERIC_TOKEN.finditer(text):
        value = match.group(0)
        if _PLACEHOLDER.search(value) or not value.isascii() or _entropy(value) < 3.5:
            continue
        context = text[max(0, match.start() - 40) : match.start()]
        if _SECRET_ANCHOR.search(context):
            findings.append(Finding("L-SEC-05", match.span(), 0.7, "L0", "anchored_entropy"))
    return findings
