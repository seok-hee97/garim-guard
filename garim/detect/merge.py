"""Deterministic three-stream and L0/L1 finding merge."""

from __future__ import annotations

from dataclasses import replace

from garim.models import Finding

_STREAM_ORDER = {"raw": 0, "norm": 1, "nospace": 2, "base64": 3, "hex": 3, "l1": 4}


def overlaps(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return left[0] < right[1] and right[0] < left[1]


def _severity(finding: Finding) -> int:
    """코드의 기본 판정 심각도. 코드가 다른 스팬이 겹칠 때 길이보다 먼저 본다."""
    from garim.policy.engine import DEFAULT_CODE_MODES, SEVERITY

    return SEVERITY.get(DEFAULT_CODE_MODES.get(finding.code, "mask"), 1)


def _preferred(left: Finding, right: Finding) -> Finding:
    # ★ 코드가 다르면 판정 심각도가 길이를 이긴다.
    #   초판은 길이만 봤다. 그래서 `주민등록번호 <RRN> hong@a.com` 에서
    #   norm_nospace 가 `<RRN>hong@a.com` 을 이메일 1건으로 잡아 25자 스팬을 만들면
    #   14자짜리 L-KR-01(block)이 폐기되고 verdict 가 mask 로 내려갔다.
    #   공격자가 알아야 할 것이 "뒤에 이메일을 붙인다" 하나뿐이었다.
    #   Luhn 우연일치(1/10)로 주민번호가 카드로 마스킹되던 것도 같은 구멍이다.
    if left.code != right.code:
        left_severity = _severity(left)
        right_severity = _severity(right)
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


def deduplicate_streams(findings: list[Finding]) -> list[Finding]:
    """Merge equal-code overlaps, preferring raw while retaining the maximum confidence."""
    raw = [finding for finding in findings if finding.stream == "raw"]
    prepared: list[Finding] = []
    for finding in findings:
        normalized_only = finding.stream != "raw" and not any(
            other.code == finding.code and overlaps(other.span, finding.span) for other in raw
        )
        prepared.append(replace(finding, normalized_only=normalized_only))

    merged: list[Finding] = []
    for finding in sorted(prepared, key=lambda item: (item.span[0], -item.span[1], item.code)):
        duplicate_index = next(
            (
                index
                for index, current in enumerate(merged)
                if current.code == finding.code and overlaps(current.span, finding.span)
            ),
            None,
        )
        if duplicate_index is None:
            merged.append(finding)
            continue
        current = merged[duplicate_index]
        selected = _preferred(current, finding)
        merged[duplicate_index] = replace(
            selected,
            confidence=max(current.confidence, finding.confidence),
            evasion=sorted(set(current.evasion + finding.evasion)),
            normalized_only=current.normalized_only and finding.normalized_only,
            decoded_from=current.decoded_from or finding.decoded_from,
        )
    return merged


def merge_overlaps(findings: list[Finding]) -> list[Finding]:
    """Resolve cross-layer/entity overlap by longest span, then L0."""
    output: list[Finding] = []
    for finding in sorted(findings, key=lambda item: (item.span[0], -item.span[1], item.code)):
        index = next(
            (idx for idx, current in enumerate(output) if overlaps(current.span, finding.span)),
            None,
        )
        if index is None:
            output.append(finding)
            continue
        current = output[index]
        selected = _preferred(current, finding)
        # ★ 코드가 다르면 confidence 를 옮기지 않는다.
        #   초판은 무조건 max() 라 폐기된 L-KR-01 의 1.0 이 살아남은 이메일 코드에
        #   옮겨 붙었다 — 감사 로그의 사유와 확신도가 둘 다 거짓이 됐다.
        confidence = (
            max(current.confidence, finding.confidence)
            if current.code == finding.code
            else selected.confidence
        )
        output[index] = replace(
            selected,
            confidence=confidence,
            evasion=sorted(set(current.evasion + finding.evasion)),
        )
    return sorted(output, key=lambda item: item.span)
