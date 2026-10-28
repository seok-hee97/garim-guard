"""Deterministic three-stream and L0/L1 finding merge."""

from __future__ import annotations

import bisect
from collections.abc import Callable, Iterator
from dataclasses import replace
from itertools import accumulate

from garim.models import Finding

# 코드 → 판정 모드. `PolicyEngine.mode_for` 가 이 모양이다.
ModeResolver = Callable[[str], str]

_STREAM_ORDER = {"raw": 0, "norm": 1, "nospace": 2, "base64": 3, "hex": 3, "l1": 4}
# 글자를 지워 원문과 경계가 어긋나는 스트림 — 공백 제거 · 된소리 되돌림.
_LOSSY_STREAMS = frozenset({"nospace", "detensified"})


def overlaps(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return left[0] < right[1] and right[0] < left[1]


class _SpanIndex:
    """시작 위치로 정렬한 탐지 — 겹칠 수 있는 후보만 훑는다.

    ★ 5차 검토 — 탐지끼리 모든 쌍을 비교해서 연락처 1,200줄(탐지 2,400개)에 12.6초가 걸렸다.
    """

    def __init__(self, findings: list[Finding]) -> None:
        self._items = sorted(findings, key=lambda item: item.span[0])
        self._starts = [item.span[0] for item in self._items]
        self._max_end = list(accumulate((item.span[1] for item in self._items), max))

    def overlapping(self, span: tuple[int, int]) -> Iterator[Finding]:
        for index in range(bisect.bisect_left(self._starts, span[1]) - 1, -1, -1):
            if self._max_end[index] <= span[0]:
                return  # 이보다 앞의 탐지는 전부 span 시작 전에 끝난다
            item = self._items[index]
            if item.span[1] > span[0]:
                yield item


def _severity(finding: Finding, mode_for: ModeResolver) -> int:
    """**활성 정책** 기준 판정 심각도. 코드가 다른 스팬이 겹칠 때 길이보다 먼저 본다.

    ★ 여기가 `DEFAULT_CODE_MODES` 를 봤다 — 하드코딩된 기본값이지 사용자 정책이
      아니다. 그래서 사용자가 뒤집으면 **fail-open** 이 났다.

          codes: {L-KR-01: allow, L-CM-03: block}
          "주민등록번호 900101-1234567 hong@example.test"
          → 기본값 기준으로 L-KR-01(block) 이 이겨 이메일 탐지가 사라지고 verdict=allow

      사용자가 block 이라고 적은 것이 조용히 사라지는 것은 정책 엔진이 있는 이유를
      부정한다. 겹침 해소는 **그 정책이 무엇을 더 무겁게 보는가** 를 따라야 한다.
    """
    from garim.policy.engine import SEVERITY

    return SEVERITY.get(mode_for(finding.code), 1)


def _preferred(left: Finding, right: Finding, mode_for: ModeResolver) -> Finding:
    # ★ 코드가 다르면 판정 심각도가 길이를 이긴다.
    #   초판은 길이만 봤다. 그래서 `주민등록번호 <RRN> hong@a.com` 에서
    #   norm_nospace 가 `<RRN>hong@a.com` 을 이메일 1건으로 잡아 25자 스팬을 만들면
    #   14자짜리 L-KR-01(block)이 폐기되고 verdict 가 mask 로 내려갔다.
    #   공격자가 알아야 할 것이 "뒤에 이메일을 붙인다" 하나뿐이었다.
    #   Luhn 우연일치(1/10)로 주민번호가 카드로 마스킹되던 것도 같은 구멍이다.
    if left.code != right.code:
        left_severity = _severity(left, mode_for)
        right_severity = _severity(right, mode_for)
        if left_severity != right_severity:
            return left if left_severity > right_severity else right

    left_length = left.span[1] - left.span[0]
    right_length = right.span[1] - right.span[0]
    if left_length != right_length:
        return left if left_length > right_length else right
    if left.layer != right.layer:
        return left if left.layer == "L0" else right
    left_order = _STREAM_ORDER.get(left.stream, 99)
    right_order = _STREAM_ORDER.get(right.stream, 99)
    if left_order != right_order:
        return left if left_order < right_order else right
    return left if left.span[0] <= right.span[0] else right


def _broken_address(text: str, lossy: Finding, exact: Finding) -> bool:
    """공백 제거본이 줄 · 공백으로 끊긴 **한 주소**를 이은 것인가 — 그러면 남긴다.

    끊긴 주소로 보는 것은 셋뿐이다: 줄 끝 하이픈 · 밑줄(`hong-\ngildong@…`), 점으로 시작하는
    나머지(`kim.minsu\n.lee@…` — 원문 매치가 `.` 로 시작한다), 끊긴 도메인(`kim@x.co\n.kr`).
    나머지는 옆 단어를 삼킨 것이다 — `please mail lee@…`, `Thanks. kim@… is mine`.
    """
    left = text[lossy.span[0] : exact.span[0]]
    right = text[exact.span[1] : lossy.span[1]]
    inner = text[exact.span[0] : exact.span[1]]
    if left and left[-1].isspace():
        word = left.rstrip()
        if inner[:1] in {".", "-", "_"}:
            return True
        if word[-1:] in {"-", "_"} and word[-2:-1].isalnum():
            return True
    if right and right[0].isspace():
        word = right.lstrip()
        if word[:1] in {".", "-"} and word[1:2].isalnum():
            return True
    return False


def _spurious_email(text: str, lossy: Finding, exact: Finding) -> bool:
    """공백 제거 스트림의 이메일 `lossy` 를 정확한 스트림의 `exact` 때문에 버릴 것인가."""
    if not overlaps(lossy.span, exact.span):
        return False
    contains = lossy.span[0] <= exact.span[0] and exact.span[1] <= lossy.span[1]
    inside = exact.span[0] <= lossy.span[0] and lossy.span[1] <= exact.span[1]
    if not (contains or inside):
        # ★ 4차 검토 — 공백을 지우면 다음 줄 주소가 앞 주소의 도메인부터 다시 매치된다
        #   (`- kim@a.com\n- lee@b.com` → `a.com-lee@b.com`). 이 반쪽 겹침이 이기면 `kim@` 이
        #   원문으로 남았다. 정확한 매치와 반만 겹치는 공백 제거본은 주소가 아니다.
        return True
    return contains and not _broken_address(text, lossy, exact)


def deduplicate_streams(
    findings: list[Finding], mode_for: ModeResolver, text: str | None = None
) -> list[Finding]:
    """Merge equal-code overlaps, preferring raw while retaining the maximum confidence.

    `text` 는 스팬이 가리키는 원문이다 — 공백 제거 스트림의 이메일을 버릴지 볼 때만 쓴다.
    """
    raw_by_code: dict[str, list[Finding]] = {}
    for finding in findings:
        if finding.stream == "raw":
            raw_by_code.setdefault(finding.code, []).append(finding)
    raw_index = {code: _SpanIndex(items) for code, items in raw_by_code.items()}
    prepared: list[Finding] = []
    for finding in findings:
        index = raw_index.get(finding.code)
        normalized_only = finding.stream != "raw" and not (
            index is not None and any(True for _ in index.overlapping(finding.span))
        )
        prepared.append(replace(finding, normalized_only=normalized_only))

    # ★ 공백을 지운 스트림의 이메일이 옆 단어까지 먹었을 뿐이면 버린다. 공백이 사라지면
    #   이메일 패턴이 이웃 영어 단어를 삼켜서 `please mail lee@example.test now` 가 통째로
    #   `<EMAIL_1>` 하나가 됐다. 이메일만이다 — 줄바꿈으로 쪼개진 키처럼 공백 제거 스트림만
    #   전체를 보는 경우는 긴 쪽이 이겨야 꼬리가 원문으로 남지 않는다. 줄바꿈으로 끊긴 주소도
    #   같은 이유로 남긴다(`_broken_address`). 제로폭으로 쪼갠 이메일은 norm 이 전체를 잡는다.
    if text is not None:
        exact = _SpanIndex(
            [
                finding
                for finding in prepared
                if finding.code == "L-CM-03" and finding.stream not in _LOSSY_STREAMS
            ]
        )
        prepared = [
            finding
            for finding in prepared
            if not (
                finding.code == "L-CM-03"
                and finding.stream in _LOSSY_STREAMS
                and any(
                    _spurious_email(text, finding, other)
                    for other in exact.overlapping(finding.span)
                )
            )
        ]

    # 시작 순으로 합치면 같은 코드의 합친 결과는 서로 겹치지 않고 끝도 정렬된다 — 새 탐지는
    # 그 코드의 마지막 결과와만 겹칠 수 있다.
    merged: list[Finding] = []
    last_by_code: dict[str, int] = {}
    for finding in sorted(prepared, key=lambda item: (item.span[0], -item.span[1], item.code)):
        duplicate_index = last_by_code.get(finding.code)
        if duplicate_index is None or not overlaps(merged[duplicate_index].span, finding.span):
            last_by_code[finding.code] = len(merged)
            merged.append(finding)
            continue
        current = merged[duplicate_index]
        selected = _preferred(current, finding, mode_for)
        merged[duplicate_index] = replace(
            selected,
            # 같은 코드도 합집합이다 — 반만 겹친 두 매치 중 긴 쪽만 남기면 짧은 쪽 꼬리가 나간다.
            span=(min(current.span[0], finding.span[0]), max(current.span[1], finding.span[1])),
            confidence=max(current.confidence, finding.confidence),
            evasion=sorted(set(current.evasion + finding.evasion)),
            normalized_only=current.normalized_only and finding.normalized_only,
            decoded_from=current.decoded_from or finding.decoded_from,
        )
    return merged


def merge_overlaps(findings: list[Finding], mode_for: ModeResolver) -> list[Finding]:
    """Resolve cross-layer/entity overlap by policy severity, then longest span, then L0.

    `mode_for` 는 필수다. 기본값을 두면 넘기는 것을 잊은 호출부가 조용히 하드코딩
    기본값으로 돌아가고, 그게 정확히 이 함수가 냈던 fail-open 이다.
    """
    # 시작 순으로 합집합을 쌓으면 결과는 서로 겹치지 않는다 — 마지막 결과와만 비교한다.
    output: list[Finding] = []
    for finding in sorted(findings, key=lambda item: (item.span[0], -item.span[1], item.code)):
        if not output or not overlaps(output[-1].span, finding.span):
            output.append(finding)
            continue
        index = len(output) - 1
        current = output[index]
        selected = _preferred(current, finding, mode_for)
        # ★ 코드가 다르면 confidence 를 옮기지 않는다.
        #   초판은 무조건 max() 라 폐기된 L-KR-01 의 1.0 이 살아남은 이메일 코드에
        #   옮겨 붙었다 — 감사 로그의 사유와 확신도가 둘 다 거짓이 됐다.
        confidence = (
            max(current.confidence, finding.confidence)
            if current.code == finding.code
            else selected.confidence
        )
        # ★ 스팬은 합집합으로 넓힌다. 초판은 채택된 쪽 스팬만 남겨서, L1 PERSON (0, 14) 와
        #   전화번호 (4, 17) 이 겹치면 `<PERSON_1>678 끝` — 폐기된 쪽의 꼬리 숫자가 원문으로
        #   나갔다. 마스킹 경계는 탐지된 글자를 하나도 남기지 않아야 한다.
        # ★ 전수 검토(2026-10-28) — warn(L-SEC-05)이 겹친 이메일(mask)을 이기면 채택된 코드는
        #   warn 이라 아무것도 가리지 않았고, 프록시는 warn 에서 원문을 그대로 보냈다
        #   (`password reset mail goes to <긴 이메일>`). 흡수한 쪽이 가려야 하는 탐지였으면 남긴다.
        mask_required = (
            current.mask_required
            or finding.mask_required
            or "mask" in {mode_for(current.code), mode_for(finding.code)}
        )
        output[index] = replace(
            selected,
            span=(
                min(current.span[0], finding.span[0]),
                max(current.span[1], finding.span[1]),
            ),
            confidence=confidence,
            evasion=sorted(set(current.evasion + finding.evasion)),
            mask_required=mask_required,
        )
    return sorted(output, key=lambda item: item.span)
