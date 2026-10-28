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
    # ★ 전수 검토(2026-10-28) — 흔히 붙여 넣는 형식인데 없었다.
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{20,}=*"), "bearer_token"),
    (re.compile(r"https://hooks\.slack\.com/services/T[A-Z0-9]+/B[A-Z0-9]+/[A-Za-z0-9]{20,}"),
     "slack_webhook"),
    (re.compile(r"https://(?:ptb\.|canary\.)?discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_-]{40,}"),
     "discord_webhook"),
    (re.compile(r"GOCSPX-[A-Za-z0-9_-]{24,}"), "google_oauth_client_secret"),
    (re.compile(r"(?<![A-Za-z0-9])ya29\.[A-Za-z0-9_-]{20,}"), "google_oauth_access_token"),
    (re.compile(r"(?<![A-Za-z0-9])npm_[A-Za-z0-9]{36}(?![A-Za-z0-9])"), "npm_token"),
    (re.compile(r"pypi-AgEIcHlwaS5vcmc[A-Za-z0-9_-]{40,}"), "pypi_token"),
    (re.compile(r"(?<![A-Za-z0-9])dop_v1_[a-f0-9]{64}(?![a-f0-9])"), "digitalocean_token"),
    (re.compile(r"(?<![A-Za-z0-9])SG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])"),
     "sendgrid_key"),
    (re.compile(r"AccountKey=[A-Za-z0-9+/]{60,}={0,2}"), "azure_storage_key"),
)
_KOREAN_HEADER = re.compile(
    r"(?i)(?:Authorization\s*:\s*KakaoAK|KAKAO_(?:REST_)?API_KEY|X-Naver-Client-(?:Id|Secret)|"
    r"NCP_(?:ACCESS_KEY_ID|SECRET_KEY)|TOSS_(?:SECRET|CLIENT)_KEY|DATA_GO_KR_KEY|"
    # 네이버 클라우드는 키 형태가 아니라 **헤더명**이 앵커다(§2.4).
    r"x-ncp-(?:iam-access-key|apigw-api-key|apigw-signature-v2))"
    r"\s*[:=]\s*['\"]?([A-Za-z0-9_+/=-]{10,})"
)
# 카카오 JavaScript 키 — `Kakao.init('<32hex>')`.
_KAKAO_INIT = re.compile(r"Kakao\.init\(\s*['\"]([0-9a-f]{32})['\"]")
# 공공데이터포털은 shape 보다 파라미터 키워드가 정확하다(§2.4).
_PUBLIC_DATA_KEY = re.compile(r"(?i)serviceKey\s*=\s*['\"]?([A-Za-z0-9%_+/=-]{20,})")
# 공동인증서(NPKI) — 파일명·경로 자체가 자격증명 유출 신호다(§2.4).
_NPKI = re.compile(r"(?i)(?:signCert\.der|signPri\.key|NPKI[\\/][^\s'\"]+)")
# RSA · EC · DSA · OPENSSH · ENCRYPTED · PGP(`… PRIVATE KEY BLOCK`) 모두 같은 머리다.
_PEM = re.compile(r"-----BEGIN (?:[A-Z0-9]+ ){0,3}PRIVATE KEY(?: BLOCK)?-----")
_JWT = re.compile(r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
# ★ 길이에 상한을 둔다 — `"mysql://a:" * 5000` 처럼 `@` 가 없으면 시작점마다 끝까지 되짚어
#   제곱 시간이 들었다(5차 검토). 실제 자격증명은 이 길이 안에 든다.
# 드라이버 접미(`postgresql+psycopg2`) · TLS 변형(`rediss`) · AMQP · MSSQL 도 받는다(전수 검토).
_DB_URL = re.compile(
    r"(?i)(?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|rediss?|amqps?|mssql|sqlserver|oracle)"
    r"(?:\+[a-z0-9]+)?://[^\s:/]{1,256}:[^\s@]{1,2048}@[^\s]{1,2048}"
)
# `이름 = 값` · `이름: 값` · `"이름": "값"` · `?이름=값`(.env · 코드 · YAML · JSON · 쿼리 문자열).
# ★ 처음 판은 `.env` 모양만 봤고 이름 **안**의 키워드면 받았으며(`TOKEN_EXPIRE_SECONDS`), 값이
#   식인지 보지 않아 `api_key = os.getenv("…")` · `API_KEY=your_api_key_here` 가 block 됐다. 반대로
#   Spring · YAML · JSON 비밀번호와 `DB_PASS` 는 놓쳤다(전수 검토 2026-10-28).
#   이름의 **마지막 낱말**이 비밀 낱말이고 값이 리터럴일 때만 받는다. 길이 상한으로 선형을 지킨다.
#   이름과 값을 따로 찾는다 — 한 정규식으로 잡으면 `https://…?token=…` 의 `https:` 가 값으로 뒤를
#   통째로 삼켜 `token=` 을 다시 보지 못했다.
_ASSIGNMENT = re.compile(
    r"(?m)(?:^|(?<=[\s{,;(?&]))(?:export[ \t]+)?[\"']?(?P<name>[A-Za-z][A-Za-z0-9_.-]{0,80})[\"']?"
    r"(?P<before>[ \t]*)(?P<op>[=:])[ \t]*"
)
_ASSIGNED_VALUE = re.compile(r"\"[^\"\n]{1,256}\"|'[^'\n]{1,256}'|[^\s#,;}\]&]{1,256}")
_PATH_LIKE = re.compile(r"[\w.~-]*(?:/[\w.~-]+)+/?")
_KOREAN_PASSWORD = re.compile(
    r"(?:비밀번호|패스워드|비번)\s*(?:[:=]|(?:은|는)\s)\s*(?P<value>[^\s가-힣ㄱ-ㅎㅏ-ㅣ]{6,64})"
)
_SECRET_LAST_WORDS = frozenset(
    {"password", "passwd", "pwd", "pass", "passphrase", "secret", "token", "apikey",
     "credential", "credentials"}
)
_KEY_QUALIFIERS = frozenset(
    {"api", "access", "secret", "private", "encryption", "signing", "master", "client", "auth"}
)
_PLACEHOLDER_VALUE = re.compile(
    r"(?i)(?:example|sample|dummy|placeholder|your[_-]|changeme|change_me|redacted|"
    r"test[_-]?key|x{4,}|\*{3,}|\$\{|\{\{|%\(|"
    r"^(?:none|null|nil|true|false|undefined|required|optional|string|str|env|"
    r"password|secret|token|api_?key)$)"
)
_IDENTIFIER = re.compile(r"[A-Za-z_]\w*")
_DOTTED_REFERENCE = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+")
_ENV_REFERENCE = re.compile(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+")
_GENERIC_TOKEN = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_+/=-]{20,}(?![A-Za-z0-9_-])")
_SECRET_ANCHOR = re.compile(
    r"(?i)(?:api[_ -]?key|secret|token|password|비밀번호|인증키|접근키|시크릿)"
)
_WORDS_JOINED = re.compile(r"[a-z]+(?:[-_][a-z]+)+(?:=\d+)?")
_PLACEHOLDER = re.compile(
    r"(?i)(?:example|sample|dummy|placeholder|your[_-]|changeme|test[_-]?key|x{4,})"
)


def _entropy(value: str) -> float:
    counts = Counter(value)
    length = len(value)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def _secret_name(name: str) -> bool:
    words = [
        word
        for word in re.split(r"[._-]+", re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name).lower())
        if word
    ]
    if not words:
        return False
    if words[-1] in _SECRET_LAST_WORDS:
        return True
    return words[-1] == "key" and len(words) >= 2 and words[-2] in _KEY_QUALIFIERS


def _literal_secret(raw: str, *, env_style: bool) -> bool:
    """값이 식 · 참조 · 자리표시가 아닌 리터럴인가.

    `env_style` 은 따옴표 없는 영숫자 값도 리터럴로 볼 자리다 — `.env`(`NAME=값`), YAML ·
    헤더(`이름: 값`), 쿼리 문자열(`?password=값`). 코드의 `password = user_password` 는 아니다.
    """
    quoted = len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'"
    value = raw[1:-1] if quoted else raw
    if not 6 <= len(value) <= 256 or "://" in value:
        return False
    if any(character in value for character in "()[]") or value[0] in "$%{<@&*":
        return False
    if _PLACEHOLDER_VALUE.search(value):
        return False
    if _PATH_LIKE.fullmatch(value) and not any(c.isdigit() for c in value):
        return False  # `projects/x/secrets/db-password`, `/etc/app/secrets/prod`
    if not quoted:
        if _DOTTED_REFERENCE.fullmatch(value) or _ENV_REFERENCE.fullmatch(value):
            return False  # `settings.JWT_SECRET`, `GITHUB_TOKEN`
        if _IDENTIFIER.fullmatch(value) and (not env_style or not any(c.isdigit() for c in value)):
            return False
    has_signal = any(c.isdigit() for c in value) or any(not c.isalnum() for c in value)
    return has_signal or len(value) >= 16


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
    for match in _KAKAO_INIT.finditer(text):
        findings.append(Finding("L-SEC-02", match.span(1), 0.9, "L0", "kakao_javascript_key"))
    for match in _DB_URL.finditer(text):
        findings.append(Finding("L-SEC-04", match.span(), 0.95, "L0", "database_url"))
    for match in _ASSIGNMENT.finditer(text):
        value_match = _ASSIGNED_VALUE.match(text, match.end())
        if value_match is None:
            continue
        name, raw = match.group("name"), value_match.group(0)
        env_style = (
            match.group("op") == ":"
            or text[match.start() - 1 : match.start()] in ("?", "&")
            or (not match.group("before") and name.isupper())
        )
        if _secret_name(name) and _literal_secret(raw, env_style=env_style):
            findings.append(
                Finding("L-SEC-04", value_match.span(), 0.9, "L0", "secret_assignment")
            )
    for match in _KOREAN_PASSWORD.finditer(text):
        if _literal_secret(match.group("value"), env_style=True):
            findings.append(
                Finding("L-SEC-04", match.span("value"), 0.9, "L0", "korean_password_phrase")
            )

    for match in _GENERIC_TOKEN.finditer(text):
        value = match.group(0)
        if _PLACEHOLDER.search(value) or not value.isascii() or _entropy(value) < 3.5:
            continue
        # ★ 비밀 낱말 뒤의 코드 이름 · 경로(`RefreshTokenRepository`,
        #   `projects/x/secrets/db-password`)를 시크릿으로 봤다(전수 검토). 무작위 키는 거의
        #   언제나 숫자를 품는다. 낱말을 하이픈 · 밑줄로 이은 설정 이름
        #   (`access-token-validity-in-seconds=3600`)도 뺀다.
        if not any(c.isdigit() for c in value) or _WORDS_JOINED.fullmatch(value):
            continue
        context = text[max(0, match.start() - 40) : match.start()]
        if _SECRET_ANCHOR.search(context):
            findings.append(Finding("L-SEC-05", match.span(), 0.7, "L0", "anchored_entropy"))
    return findings
