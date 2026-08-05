"""Library entry point for the local inspection pipeline."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import replace
from time import perf_counter

from garim.detect.l1 import L1Backend
from garim.detect.pipeline import DetectionPipeline
from garim.mask.vault import Vault, mask_text
from garim.models import CheckResult, Finding, Segment
from garim.policy.engine import MASK_LABELS, SEVERITY, PolicyEngine
from garim.policy.schema import PolicyConfig, load_policy
from garim.segment.chunk import chunk_text
from garim.segment.content_type import detect_content_type
from garim.segment.split import split_text


class Inspector:
    """Inspect prompts locally and return policy decisions without logging source values."""

    def __init__(
        self,
        policy: PolicyConfig | str | None = None,
        *,
        l1_backend: L1Backend | None = None,
        vault: Vault | None = None,
    ) -> None:
        if isinstance(policy, PolicyConfig):
            config = policy
        else:
            config = load_policy(policy)
        self.policy = PolicyEngine(config)
        self.pipeline = DetectionPipeline(self.policy, l1_backend)
        self.vault = vault or Vault(scope="session")

    def check(
        self,
        text_or_segments: str | Sequence[Segment],
        *,
        mode: str = "interactive",
    ) -> CheckResult:
        if mode not in {"interactive", "thorough"}:
            raise ValueError("mode must be 'interactive' or 'thorough'")
        single_input = isinstance(text_or_segments, str)
        if single_input:
            inputs = [Segment(text_or_segments)]
        else:
            inputs = list(text_or_segments)
            if not all(isinstance(segment, Segment) for segment in inputs):
                raise TypeError("segment input must contain only Segment instances")

        if not inputs:
            return CheckResult(
                "allow", [], [], None, [], None, _empty_delta(), [], False, _empty_latency()
            )
        degraded_reasons: list[str] = []
        # ★ §3.6 은 "검사 실패는 통과시키되 degraded=true" 다.
        #   초판은 여기서 ValueError 를 던져 앱을 멈췄다 — 문서가 스스로
        #   "검사기가 앱을 멈추면 사용자가 꺼버리고, 그러면 아무것도 못 막는다"고 쓴 방향이다.
        limit = self.policy.config.limits.max_input_chars
        total_chars = sum(len(segment.text) for segment in inputs)
        truncated_input = total_chars > limit
        if truncated_input:
            budget = limit
            capped: list[Segment] = []
            for segment in inputs:
                if budget <= 0:
                    break
                text = segment.text[:budget]
                budget -= len(text)
                capped.append(replace(segment, text=text))
            inputs = capped
            degraded_reasons.append("input_truncated")

        started = perf_counter()
        findings: list[Finding] = []
        content_types: list[str] = []
        delta = _empty_delta()
        latency = _empty_latency()
        degraded = truncated_input or mode == "thorough"  # L2 미배선 + 입력 절단
        if mode == "thorough":
            degraded_reasons.append("l2_not_wired")
        forced_block = False
        forced_warn = truncated_input or mode == "thorough"
        internal_segments = 0

        for segment_idx, segment in enumerate(inputs):
            try:
                segment.text.encode("utf-8", errors="strict")
            except UnicodeEncodeError:
                degraded = True
                forced_warn = True
                continue
            slices = split_text(segment.text)
            internal_segments += len(slices)
            for sliced in slices:
                # ★ 청킹 — 없으면 L1 이 512토큰에서 조용히 자른다(§3.6·§10.1)
                pieces = chunk_text(
                    sliced.text,
                    chunk_size=self.policy.config.limits.chunk_size,
                    chunk_overlap=self.policy.config.limits.chunk_overlap,
                )
                for piece in pieces:
                    content_type = (
                        segment.content_type
                        if segment.content_type != "unknown"
                        else sliced.content_type
                    )
                    if content_type == "unknown":
                        content_type = detect_content_type(piece.text)
                    if piece.start == 0:
                        content_types.append(content_type)
                    try:
                        result = self.pipeline.scan(
                            piece.text,
                            origin=segment.origin,
                            content_type=content_type,
                            segment_idx=segment_idx,
                            source_offset=sliced.start + piece.start,
                        )
                    except Exception:
                        # L0 예외는 결정적 코어의 실패이므로 fail-closed 다.
                        degraded = True
                        forced_block = True
                        continue
                    findings.extend(result.findings)
                    degraded = degraded or result.degraded
                    degraded_reasons.extend(result.degraded_reasons)
                    for key, value in result.delta.items():
                        delta[key] += value
                    for key, value in result.latency_ms.items():
                        latency[key] += value

        if internal_segments > self.policy.config.limits.max_segments:
            degraded = True
            forced_warn = True

        # ★ origin 정책 — trust_declared 일 때만 공격 축 강등 (§2.5b)
        origins = {idx: segment.origin for idx, segment in enumerate(inputs)}
        findings = self.policy.apply_origin_policy(findings, origins)
        origin_cap = self.policy.origin_verdict_cap(findings, origins)

        leaks = sorted((finding for finding in findings if not finding.is_attack), key=_finding_key)
        attacks = sorted((finding for finding in findings if finding.is_attack), key=_finding_key)
        verdict = self.policy.aggregate(findings)
        if origin_cap is not None and SEVERITY[verdict] > SEVERITY[origin_cap]:
            verdict = origin_cap
        if forced_warn and SEVERITY[verdict] < SEVERITY["warn"]:
            verdict = "warn"
        if forced_block:
            verdict = "block"

        masked_text: str | None = None
        masked_segments: list[str] | None = None
        result_vault: Vault | None = None
        maskable = [finding for finding in leaks if self.policy.mode_for(finding.code) == "mask"]
        if verdict != "block" and maskable:
            by_segment: dict[int, list[Finding]] = defaultdict(list)
            for finding in maskable:
                by_segment[finding.segment_idx].append(finding)
            rendered = [
                mask_text(
                    segment.text,
                    by_segment.get(index, []),
                    vault=self.vault,
                    labels=MASK_LABELS,
                )
                for index, segment in enumerate(inputs)
            ]
            result_vault = self.vault
            if single_input:
                masked_text = rendered[0]
            else:
                masked_segments = rendered

        latency["total"] = (perf_counter() - started) * 1000
        return CheckResult(
            verdict=verdict,
            leaks=leaks,
            attacks=attacks,
            masked_text=masked_text,
            masked_segments=masked_segments,
            vault=result_vault,
            normalization_delta=delta,
            content_types=content_types,
            degraded=degraded,
            latency_ms={key: round(value, 3) for key, value in latency.items()},
            degraded_reasons=sorted(set(degraded_reasons)),
        )


def _finding_key(finding: Finding) -> tuple[int, int, int, str]:
    return (finding.segment_idx, finding.span[0], finding.span[1], finding.code)


def _empty_delta() -> dict[str, int]:
    return {
        "nfkc": 0,
        "zero_width": 0,
        "tag_chars": 0,
        "homoglyph": 0,
        "jamo_composed": 0,
        "hanja": 0,
        "space": 0,
        "b64_decoded": 0,
    }


def _empty_latency() -> dict[str, float]:
    return {"normalize": 0.0, "l0": 0.0, "l1": 0.0, "total": 0.0}
