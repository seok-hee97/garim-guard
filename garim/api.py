"""Library entry point for the local inspection pipeline."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace
from time import perf_counter

from garim.detect.l1 import L1Backend
from garim.detect.merge import ModeResolver, merge_overlaps
from garim.detect.pipeline import DetectionPipeline, PipelineResult
from garim.mask.vault import Vault, mask_text
from garim.models import CheckResult, ContentType, Finding, Origin, Segment
from garim.policy.engine import MASK_LABELS, SEVERITY, PolicyEngine
from garim.policy.schema import PolicyConfig, load_policy
from garim.segment.chunk import chunk_text
from garim.segment.content_type import detect_content_type
from garim.segment.split import split_text


@dataclass(frozen=True, slots=True)
class _L1Job:
    text: str
    origin: Origin
    content_type: ContentType
    segment_idx: int
    source_offset: int
    group: int


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
        # 검사하지 않은 꼬리를 외부로 보낼 수 없으므로 절단은 fail-closed다.
        forced_block = truncated_input
        forced_warn = mode == "thorough"
        internal_segments = 0
        # ★ §3.6 "청크 수 상한 초과 → warn + degraded" — 초판에 이 행이 없었다.
        #   `max_segments` 가드는 `split_text` 결과만 세고 `chunk_text` 결과를 세지
        #   않아, 10만자 입력에 L1 `infer` 가 447회 불리고도 `degraded=False` 였다.
        #   상한은 **L1 에만** 건다 — 스캔을 멈추면 chunk.py 가 닫은 fail-open 이
        #   되살아난다. L0 는 전량 돌고, L1 이 꺼진 사실만 degraded 로 알린다.
        l1_chunk_cap = self.policy.config.limits.max_l1_chunks
        l1_jobs: list[_L1Job] = []

        def accumulate(result: PipelineResult, *, include_delta: bool) -> None:
            nonlocal degraded
            findings.extend(result.findings)
            degraded = degraded or result.degraded
            degraded_reasons.extend(result.degraded_reasons)
            if include_delta:
                for key, value in result.delta.items():
                    delta[key] += value
            for key, value in result.latency_ms.items():
                latency[key] += value

        for segment_idx, segment in enumerate(inputs):
            try:
                segment.text.encode("utf-8", errors="strict")
            except UnicodeEncodeError:
                degraded = True
                forced_warn = True
                degraded_reasons.append("input_encoding_error")
                continue
            slices = split_text(segment.text)
            internal_segments += len(slices)
            slice_types: list[str] = []
            for sliced in slices:
                content_type = (
                    segment.content_type
                    if segment.content_type != "unknown"
                    else sliced.content_type
                )
                if content_type == "unknown":
                    content_type = detect_content_type(sliced.text)
                slice_types.append(content_type)
                content_types.append(content_type)

            # L0는 원문 세그먼트 전체를 한 번만 본다. 세그먼트/청크 경계가 긴
            # 시크릿이나 식별자를 자르는 문제를 없앤다. 혼합 타입은 보수적으로 prose다.
            l0_content_type = (
                segment.content_type
                if segment.content_type != "unknown"
                else (slice_types[0] if len(set(slice_types)) == 1 else "prose")
            )
            try:
                accumulate(
                    self.pipeline.scan(
                        segment.text,
                        origin=segment.origin,
                        content_type=l0_content_type,
                        segment_idx=segment_idx,
                        use_l0=True,
                        use_l1=False,
                    ),
                    include_delta=True,
                )
            except Exception:
                degraded = True
                forced_block = True
                degraded_reasons.append("l0_error")
                continue

            if self.pipeline.l1_backend is None:
                continue
            tokenizer = getattr(self.pipeline.l1_backend, "tokenizer", None)
            group_base = internal_segments - len(slices)
            for slice_index, (sliced, content_type) in enumerate(
                zip(slices, slice_types, strict=True)
            ):
                pieces = chunk_text(
                    sliced.text,
                    chunk_size=self.policy.config.limits.chunk_size,
                    chunk_overlap=self.policy.config.limits.chunk_overlap,
                    tokenizer=tokenizer,
                )
                for piece in pieces:
                    l1_jobs.append(
                        _L1Job(
                            piece.text,
                            segment.origin,
                            content_type,
                            segment_idx,
                            sliced.start + piece.start,
                            group=group_base + slice_index,
                        )
                    )

        selected_jobs = _select_l1_jobs(l1_jobs, l1_chunk_cap)
        if len(selected_jobs) < len(l1_jobs):
            degraded = True
            forced_warn = True
            degraded_reasons.append("l1_chunk_cap")
        for job in selected_jobs:
            try:
                accumulate(
                    self.pipeline.scan(
                        job.text,
                        origin=job.origin,
                        content_type=job.content_type,
                        segment_idx=job.segment_idx,
                        source_offset=job.source_offset,
                        use_l0=False,
                        use_l1=True,
                    ),
                    include_delta=False,
                )
            except Exception:
                degraded = True
                forced_warn = True
                degraded_reasons.append("l1_pipeline_error")

        if internal_segments > self.policy.config.limits.max_segments:
            # ★ 사유를 안 적으면 감사 로그가 "무언가 나빠졌다" 까지만 말한다. 다른
            #   강등 경로는 전부 사유를 남기는데(`input_truncated`·`l0_error`·
            #   `l1_pipeline_error`) 이 자리만 비어 있어 `degraded_reasons=[]` 인 채
            #   verdict 만 warn 으로 내려갔다 — 왜 내려갔는지 밖에서 알 방법이 없었다.
            degraded = True
            forced_warn = True
            degraded_reasons.append("segment_cap_exceeded")

        # ★ origin 정책 — trust_declared 일 때만 공격 축 강등 (§2.5b)
        origins = {idx: segment.origin for idx, segment in enumerate(inputs)}
        findings = _merge_across_chunks(findings, self.policy.mode_for)
        findings = self.policy.apply_origin_policy(findings, origins)

        leaks = sorted((finding for finding in findings if not finding.is_attack), key=_finding_key)
        attacks = sorted((finding for finding in findings if finding.is_attack), key=_finding_key)
        # ★ 상한은 공격 축에만 건다(§2.5b). 초판은 전체 판정에 걸어 system 세그먼트의
        #   공격 문구 한 줄이 user 세그먼트의 유출 block 을 warn 으로 끌어내렸다.
        verdict = self.policy.aggregate_with_origin(findings, origins)
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


def _select_l1_jobs(jobs: list[_L1Job], limit: int) -> list[_L1Job]:
    """Select bounded jobs while giving every source slice deterministic coverage."""
    if len(jobs) <= limit:
        return jobs
    grouped: dict[int, list[int]] = defaultdict(list)
    for index, job in enumerate(jobs):
        grouped[job.group].append(index)

    quotas = {group: 0 for group in grouped}
    remaining = limit
    while remaining:
        progressed = False
        for group in sorted(grouped):
            if quotas[group] >= len(grouped[group]):
                continue
            quotas[group] += 1
            remaining -= 1
            progressed = True
            if remaining == 0:
                break
        if not progressed:
            break

    chosen: set[int] = set()
    for group, indexes in grouped.items():
        quota = quotas[group]
        if quota == 1:
            positions = [len(indexes) // 2]
        else:
            positions = [round(step * (len(indexes) - 1) / (quota - 1)) for step in range(quota)]
        chosen.update(indexes[position] for position in positions)
    return [job for index, job in enumerate(jobs) if index in chosen]


def _merge_across_chunks(findings: list[Finding], mode_for: ModeResolver) -> list[Finding]:
    """Deduplicate overlaps after all findings share original segment coordinates.

    청크 경계를 넘어 합칠 때도 **활성 정책**의 심각도를 따른다. 파이프라인 안쪽만
    고치고 이 자리를 놓치면, 세그먼트가 쪼개진 입력에서만 fail-open 이 남는다.
    """
    grouped: dict[tuple[int, bool], list[Finding]] = defaultdict(list)
    for finding in findings:
        grouped[(finding.segment_idx, finding.is_attack)].append(finding)
    merged: list[Finding] = []
    for group in grouped.values():
        merged.extend(merge_overlaps(group, mode_for))
    return sorted(merged, key=_finding_key)


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
