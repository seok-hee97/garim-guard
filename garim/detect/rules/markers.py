"""Korean confidentiality marking recognizer with position-aware scoring.

★ 이전 판은 §2.5 를 두 군데에서 재현하지 못했다.

1. **사전이 25개 중 3개뿐이었다** — `대외비`·`사내한`·`CONFIDENTIAL`. §2.5 가 근거와 함께
   열거한 `기밀`·`영업비밀`·`극비`·`취급주의`·`비밀`·`秘`·`極秘`·`RESTRICTED`·
   `PROPRIETARY`·`TLP:RED` 가 전부 빠져 있었다. `보안등급` 은 `[1-4]급` 숫자형만 받아
   `보안등급: 기밀` 을 놓쳤다.
2. **위치 가중치가 상대 오프셋 한 개로 근사돼 있었다** — §2.5 가 실측한 것은
   `첫3줄 +2 / 단독·괄호 +3 / 등급표기 +3 / 조사결합 −3, 임계 3` 의 4-특징 방식이다.
   근사판은 `대외비 처리 절차가 궁금합니다` 를 문두라는 이유만으로 0.85 로 올렸다.

설계 프로브의 자기 케이스 10건에서 이전 판은 **재현율 60% / 오탐 40%** 였다(§2.5 가
주장한 값은 100% / 0%). [marker_probe.py](../../../docs/marker_probe.py) 가 통과하고
있었던 것은 그 스크립트가 알고리즘을 자기 안에 다시 구현해 검사했기 때문이다 —
배포 코드를 한 번도 부르지 않았다.

⚠️ 임계 3 은 표본 10건에서 나온 값이다. §2.5 대로 실제 코퍼스에서 재조정해야 하며,
그래서 정책(`rules.marker_score_threshold`)으로 뺐다.
"""

from __future__ import annotations

import re

from garim.models import Finding

# §2.5 사전 — 근거 있는 것 / 실무 관행 / 영문.
# `보안` 같은 단독 단어는 §2.5 가 "오탐이 극심하므로 사전에서 제외" 라고 못박았다.
_MARKER_TERMS = (
    # 법령·표준 근거
    "대외비",
    "영업비밀",
    "기밀",
    "비밀",
    # 실무 관행
    "극비",
    "사내한",
    "사외비",
    "부외비",
    "내부한정",
    "내부용",
    "취급주의",
    "예고문",
    "관계자 외 열람 금지",
    "관계자외 열람금지",
    "무단 배포 금지",
    "무단배포금지",
    "외부 유출 금지",
    "외부유출금지",
    "極秘",
    "秘",
    # 영문
    "CONFIDENTIAL",
    "INTERNAL USE ONLY",
    "INTERNAL ONLY",
    "RESTRICTED",
    "PROPRIETARY",
    "DO NOT DISTRIBUTE",
    "TLP:RED",
    "TLP:AMBER",
)

# 긴 표현이 짧은 표현에 먹히지 않도록 길이 내림차순으로 교대한다
# (`영업비밀` 이 `비밀` 로, `INTERNAL USE ONLY` 가 `INTERNAL ONLY` 로 잘리는 것을 막는다).
_MARKER = re.compile(
    "|".join(
        re.escape(term).replace(r"\ ", r"\s+")
        for term in sorted(_MARKER_TERMS, key=len, reverse=True)
    ),
    re.IGNORECASE,
)

# 보안업무규정 제4조의 3등급 + 임의 등급 표기. `보안등급: 기밀` 처럼 사전 단어가
# 값으로 오는 형태와 `보안등급: 2급` 처럼 숫자로 오는 형태를 모두 받는다.
_GRADE_LABEL = re.compile(
    r"(보안\s*등급|비밀\s*등급|분류|CLASSIFICATION)\s*[:：]?\s*$", re.IGNORECASE
)
_NUMERIC_GRADE = re.compile(r"(?:[1-4]|[ⅠⅡⅢIV]{1,3})\s*급\s*비?밀?")
_WRAPPED = "「」『』[]（）()【】<>《》"
# 조사·어미가 붙으면 문장 속 언급이다 — "대외비인데", "기밀이라서".
_PARTICLE = re.compile(r"^(인데|이라|라서|입니다|이었|였|니까|지만|이고|여서|라고|이며|인)")

FIRST_LINES = 3
SCORE_FIRST_LINES = 2
SCORE_STANDALONE = 3
SCORE_GRADE_LABEL = 3
SCORE_PARTICLE = -3
DEFAULT_SCORE_THRESHOLD = 3


def _standalone(line: str, term: str) -> bool:
    """The marker occupies the whole line, optionally wrapped in brackets."""
    stripped = line.strip().strip(_WRAPPED).strip()
    return stripped.casefold() == term.casefold()


def score_marker(line: str, line_index: int, match: re.Match[str]) -> int:
    """§2.5 위치 가중치 — 네 특징의 합."""
    term = match.group(0)
    score = 0
    if line_index < FIRST_LINES:
        score += SCORE_FIRST_LINES
    if _standalone(line, term):
        score += SCORE_STANDALONE
    if _GRADE_LABEL.search(line[: match.start()]):
        score += SCORE_GRADE_LABEL
    if _PARTICLE.match(line[match.end() :]):
        score += SCORE_PARTICLE
    return score


def detect_markers(text: str, *, score_threshold: int = DEFAULT_SCORE_THRESHOLD) -> list[Finding]:
    """Report confidentiality markings whose position-weighted score clears the bar."""
    findings: list[Finding] = []
    offset = 0
    for line_index, line in enumerate(text.split("\n")):
        patterns = ((_MARKER, "confidential_marker"), (_NUMERIC_GRADE, "classified_grade"))
        for pattern, rule in patterns:
            for match in pattern.finditer(line):
                score = score_marker(line, line_index, match)
                if score < score_threshold:
                    continue
                # 점수를 두 단계 신뢰도로 접는다 — 하한은 L0 임계(0.5)를 넘겨야 채택된다.
                confidence = 0.85 if score >= SCORE_FIRST_LINES + SCORE_STANDALONE else 0.65
                span = (offset + match.start(), offset + match.end())
                findings.append(Finding("L-DOC-01", span, confidence, "L0", rule))
        offset += len(line) + 1
    return findings
