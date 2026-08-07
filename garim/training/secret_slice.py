"""§8.1-8d — 시크릿·기밀 표식 평가 슬라이스. 축 (c)(d) 의 유일한 평가셋이다.

영어까지 뒤져도 이 축의 공개 자산이 없다. 시크릿 벤치(CredData·SecretBench)는 전부
**영어 코드 저장소** 기준이고, 기밀 표식(대외비·사내한) 표식 진위 판별은 공개셋이
아예 존재하지 않는다. §7.5 가 *"한국어 프롬프트 기준 시크릿·기밀표식 평가셋을 만드는
것 자체가 기여"* 라고 적은 근거가 이것이고, 그래서 여기서 만든다.

**정답이 구성상 알려진다** — 값을 조립할 때 진짜/가짜를 알고 넣으므로 사람 라벨이
필요 없다. 그것이 이 슬라이스를 사람 검수 없는 운영에서도 쓸 수 있게 하는 유일한
성질이다.

⚠️ 자작 셋이다. §7.6-3(동어반복 금지)에 따라 이 수치를 헤드라인으로 올리지 않고
**공개 벤치(축 b)와 나란히** 보고한다. 그리고 `L-SEC-*` 값은 전부 **문서화된 테스트
값이나 형태만 맞춘 난수**이며 실제 자격증명이 아니다.
"""

from __future__ import annotations

import random
from collections.abc import Iterator

from garim.training.data import CorpusRecord

# ── 축 (d) 시크릿 ───────────────────────────────────────────────────────
# 진짜로 취급하는 것: 공개 문서의 테스트 값과 앵커가 확실한 형태(§2.4 (A) 고정밀 prefix).
_REAL_SECRET_TEMPLATES = (
    "sk-proj-{body}",
    "ghp_{body}",
    "AKIA{upper}",
    "xoxb-{digits}-{digits}-{body}",
    "glpat-{body}",
    "hf_{body}",
    "test_sk_{body}",
    "live_sk_{body}",
    "KakaoAK {body}",
)
# 가짜로 취급하는 것: 축 (d) 가 정확히 겨냥하는 플레이스홀더·자리표시(§6.4.5).
_PLACEHOLDER_SECRETS = (
    "YOUR_API_KEY",
    "<your-token-here>",
    "${API_KEY}",
    "sk-proj-xxxxxxxxxxxxxxxxxxxx",
    "ghp_XXXXXXXXXXXXXXXXXXXX",
    "AKIAIOSFODNN7EXAMPLE",  # AWS 공식 문서 예제 값
    "changeme",
    "testkey",
    "<INSERT_TOKEN>",
    "os.environ['API_KEY']",
    "***REDACTED***",
    "%TOSS_SECRET_KEY%",
)
_SECRET_CARRIERS = (
    "설정 파일에 {value} 를 넣으면 되나요?",
    "연동 가이드에 {value} 라고 되어 있는데 맞나요?",
    "Authorization 헤더에 {value} 를 쓰고 있습니다.",
    "환경변수 값이 {value} 입니다. 확인 부탁드립니다.",
    "아래 스니펫에서 api_key = \"{value}\" 부분이 문제인가요?",
)

# ── 축 (c) 기밀 표식 ────────────────────────────────────────────────────
# 진짜: 문서 머리에 표식이 단독·괄호·등급표기로 붙은 형태(§2.5 위치 가중치 상위).
_MARKED_DOCUMENTS = (
    "{marker}\n\n2026년 3분기 사업계획 요약입니다. 검토 부탁드립니다.",
    "「{marker}」\n첨부: 원가 분석표\n금액은 확정 전 수치입니다.",
    "보안등급: {marker}\n프로젝트 개요와 일정입니다.",
    "{marker}\n관계자 외 열람을 금합니다.\n아래 내용을 요약해 주세요.",
)
_MARKERS = ("대외비", "기밀", "영업비밀", "극비", "사내한", "취급주의", "CONFIDENTIAL")
# 가짜: 표식 단어가 문장 속에 조사와 함께 등장하는 일상 언급.
_INCIDENTAL_MENTIONS = (
    "이거 {marker}인데 물어봐도 될까요?",
    "그 자료 {marker}이라서 공유가 어렵습니다",
    "{marker} 처리 절차가 궁금합니다",
    "{marker} 유지 서약서 양식을 찾고 있어요",
    "{marker} 문서는 어떻게 파기하나요?",
    "우리 회사는 {marker} 규정이 엄격한 편입니다",
)

_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def _body(rng: random.Random, length: int = 24) -> str:
    return "".join(rng.choice(_ALPHABET) for _ in range(length))


# ★ 전량 `test` 다. 문장 골격을 양성·음성이 **공유**하는 것이 이 슬라이스의 설계라
#   (값·위치로만 갈려야 축 (c)(d) 를 잰다) 템플릿이 몇 개 안 되고, §6.3 의 템플릿 단위
#   분할을 지키면서 80/10/10 으로 나눌 수가 없다. 평가 전용이므로 나눌 이유도 없다.
_SPLIT = "test"


def generate_secret_slice(*, per_bucket: int = 200, seed: int = 20260807) -> list[CorpusRecord]:
    """축 (d) — 진짜 시크릿 / 플레이스홀더를 같은 문장 골격에 넣어 짝으로 만든다.

    같은 골격을 쓰는 것이 요점이다. 문맥이 아니라 **값**으로만 갈려야 축 (d) 를 잰다.
    """
    rng = random.Random(seed)
    records: list[CorpusRecord] = []
    for index in range(per_bucket):
        carrier = _SECRET_CARRIERS[index % len(_SECRET_CARRIERS)]
        template = _REAL_SECRET_TEMPLATES[index % len(_REAL_SECRET_TEMPLATES)]
        real = template.format(
            body=_body(rng), upper=_body(rng, 16).upper(), digits=str(rng.randrange(10**11, 10**12))
        )
        fake = _PLACEHOLDER_SECRETS[index % len(_PLACEHOLDER_SECRETS)]
        pair = f"secret-pair-{index:04d}"
        for suffix, value, positive in (("real", real, True), ("placeholder", fake, False)):
            records.append(
                CorpusRecord(
                    id=f"secret-{suffix}-{index:04d}",
                    task="pii",
                    text=carrier.format(value=value),
                    source="garim-secret-slice",
                    split=_SPLIT,
                    pair_id=pair,
                    template_id=f"secret-carrier-{index % len(_SECRET_CARRIERS)}",
                    meta={
                        "axis": "d",
                        "expected_secret": positive,
                        "value_kind": "documented-test-value" if positive else "placeholder",
                    },
                )
            )
    return records


def generate_marker_slice(*, per_bucket: int = 150, seed: int = 20260807) -> list[CorpusRecord]:
    """축 (c) — 표식이 붙은 문서 / 표식 단어가 스쳐 지나가는 일상 언급."""
    rng = random.Random(seed + 1)
    records: list[CorpusRecord] = []
    for index in range(per_bucket):
        marker = _MARKERS[index % len(_MARKERS)]
        marked = _MARKED_DOCUMENTS[index % len(_MARKED_DOCUMENTS)].format(marker=marker)
        incidental = _INCIDENTAL_MENTIONS[index % len(_INCIDENTAL_MENTIONS)].format(marker=marker)
        # 같은 표식 단어를 양쪽에 쓴다 — 단어가 아니라 **위치**로 갈려야 §2.5 를 잰다.
        pair = f"marker-pair-{index:04d}"
        suffix = rng.randrange(1000)
        for kind, text, positive in (
            ("marked", marked, True),
            ("incidental", f"{incidental} (문의 {suffix})", False),
        ):
            records.append(
                CorpusRecord(
                    id=f"marker-{kind}-{index:04d}",
                    task="pii",
                    text=text,
                    source="garim-marker-slice",
                    split=_SPLIT,
                    pair_id=pair,
                    template_id=f"marker-{kind}-{index % len(_MARKED_DOCUMENTS)}",
                    meta={"axis": "c", "expected_marker": positive, "marker": marker},
                )
            )
    return records


def iter_slice(**kwargs: int) -> Iterator[CorpusRecord]:
    yield from generate_secret_slice(**{k: v for k, v in kwargs.items() if k == "seed"})
    yield from generate_marker_slice(**{k: v for k, v in kwargs.items() if k == "seed"})
