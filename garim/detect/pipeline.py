"""Layer orchestration for one lossless source slice."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from dataclasses import dataclass, field, replace
from time import perf_counter

from garim.detect.l1 import PII_LABEL_TO_CODE, L1Backend
from garim.detect.merge import deduplicate_streams, merge_overlaps
from garim.detect.rules import (
    detect_attacks,
    detect_common_pii,
    detect_kr_pii,
    detect_markers,
    detect_secrets,
)
from garim.detect.rules.checksum import rrn_date_sex_valid
from garim.models import ContentType, Finding, Origin
from garim.normalize.chain import NormalizedBundle, NormalizedView, normalize
from garim.normalize.offsets import to_source_span
from garim.policy.engine import PolicyEngine


@dataclass(slots=True)
class PipelineResult:
    findings: list[Finding]
    delta: dict[str, int]
    degraded: bool
    latency_ms: dict[str, float]
    # ★ §3.6 은 모델 로드 실패 / 추론 예외 / 지연 초과를 별개 행으로 나열하고
    #   "조용한 성능 저하가 구 프로젝트에서 문제였다"고 적었다. bool 하나로는
    #   호출자가 무엇이 degraded 인지 알 수 없다.
    degraded_reasons: list[str] = field(default_factory=list)


_STRUCTURED_KEY = re.compile(
    r"""(?m)(?:["'](?P<json>[^"'\r\n]+)["']\s*:|^\s*(?P<yaml>[^#\r\n:]+?)\s*:)"""
)


def _structured_key_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for match in _STRUCTURED_KEY.finditer(text):
        group = "json" if match.group("json") is not None else "yaml"
        spans.append(match.span(group))
    return spans


def _evasion_codes(bundle: NormalizedBundle, stream: str) -> list[str]:
    codes: list[str] = []
    if stream in {"base64", "hex"}:
        codes.append("A-ENC-01")
    if bundle.delta["zero_width"] or bundle.delta["tag_chars"]:
        codes.append("A-ENC-02")
    if bundle.delta["homoglyph"]:
        codes.append("A-ENC-03")
    if bundle.delta["jamo_composed"]:
        codes.append("A-KO-01")
    if bundle.delta["hanja"]:
        codes.append("A-KO-04")
    if stream == "nospace" and bundle.delta["space"]:
        codes.append("A-KO-05")
    if bundle.delta["nfkc"]:
        codes.append("A-MISC-01")
    return codes


class DetectionPipeline:
    def __init__(self, policy: PolicyEngine, l1_backend: L1Backend | None = None) -> None:
        self.policy = policy
        self.l1_backend = l1_backend

    def _scan_view(
        self,
        view: NormalizedView,
        *,
        source_len: int,
        origin: Origin,
        stream: str,
    ) -> list[Finding]:
        local = (
            detect_kr_pii(view.text, rrn_anchor=self.policy.config.rules.rrn_anchor)
            + detect_common_pii(view.text)
            + detect_secrets(view.text)
            + detect_markers(view.text)
            + detect_attacks(view.text, origin=origin)
        )
        for pattern, compiled in self.policy.custom_patterns:
            for match in compiled.finditer(view.text):
                local.append(Finding(pattern.code, match.span(), 0.9, "L0", pattern.name))

        mapped: list[Finding] = []
        for finding in local:
            if stream == "nospace":
                if finding.code in {"L-KR-01", "L-KR-02"} and finding.confidence < 1.0:
                    continue
                finding = replace(finding, confidence=finding.confidence * 0.8)
            source_span = to_source_span(finding.span, view.interval_map, source_len)
            mapped.append(
                replace(
                    finding,
                    span=source_span,
                    decoded_from=view.decoded_from,
                    stream=stream,
                )
            )
        return mapped

    def _l1_findings(
        self,
        bundle: NormalizedBundle,
        *,
        origin: Origin,
        content_type: ContentType,
    ) -> list[Finding]:
        assert self.l1_backend is not None
        output = self.l1_backend.infer(
            bundle.norm.text,
            normalization_delta=bundle.delta,
            origin=origin,
            content_type=content_type,
        )
        output.validate(len(bundle.norm.text))
        findings: list[Finding] = []
        for entity in output.entities:
            code = PII_LABEL_TO_CODE.get(entity.label)
            if code is None:
                continue
            span = to_source_span(entity.span, bundle.norm.interval_map, len(bundle.raw.text))
            multiplier = 0.9
            rule = "ner"
            if code in {"L-KR-01", "L-KR-02"}:
                multiplier = 0.7
                entity_text = bundle.norm.text[entity.span[0] : entity.span[1]]
                rule = "rrn_ctx" if rrn_date_sex_valid(entity_text) else "ner_over_format_fail"
            findings.append(Finding(code, span, entity.score * multiplier, "L1", rule, stream="l1"))

        attack_label, attack_score = max(
            output.attack_probabilities.items(), key=lambda item: item[1]
        )
        if attack_label != "NONE":
            if attack_label == "INJECTION":
                # ★ origin 으로 끄지 않는다(§2.5b). 강등은 정책층이 trust_declared 를 보고 한다.
                code = "A-INJ-02" if origin in ("retrieved", "tool") else "A-INJ-01"
            elif attack_label == "JAILBREAK":
                lexical = detect_attacks(bundle.norm.text, origin=origin)
                code = "A-JB-01" if any(item.code == "A-JB-01" for item in lexical) else "A-JB-02"
            else:
                code = "A-LEAK-01"
            if attack_label != "NONE":
                evasion: list[str] = []
                thresholds = self.policy.config.thresholds
                if output.romanized_evasion >= thresholds.romanized_evasion:
                    evasion.append("A-KO-03")
                if output.honorific_social_engineering >= thresholds.honorific_social_engineering:
                    evasion.append("A-KO-06")
                span = to_source_span(
                    (0, len(bundle.norm.text)), bundle.norm.interval_map, len(bundle.raw.text)
                )
                findings.append(
                    Finding(
                        code,
                        span,
                        attack_score,
                        "L1",
                        "classifier",
                        evasion=evasion,
                        stream="l1",
                    )
                )
        return findings

    def scan(
        self,
        text: str,
        *,
        origin: Origin,
        content_type: ContentType,
        segment_idx: int,
        source_offset: int = 0,
    ) -> PipelineResult:
        started = perf_counter()
        normalization_started = perf_counter()
        bundle = normalize(text)
        normalization_ms = (perf_counter() - normalization_started) * 1000

        l0_started = perf_counter()
        raw_findings = self._scan_view(
            bundle.raw, source_len=len(text), origin=origin, stream="raw"
        )
        all_l0 = list(raw_findings)
        all_l0.extend(
            self._scan_view(bundle.norm, source_len=len(text), origin=origin, stream="norm")
        )
        all_l0.extend(
            self._scan_view(bundle.nospace, source_len=len(text), origin=origin, stream="nospace")
        )
        for decoded in bundle.decoded:
            all_l0.extend(
                self._scan_view(
                    decoded,
                    source_len=len(text),
                    origin=origin,
                    stream=decoded.name,
                )
            )
        l0_findings = deduplicate_streams(all_l0)
        l0_findings = [
            replace(
                finding,
                evasion=sorted(set(finding.evasion + _evasion_codes(bundle, finding.stream)))
                if finding.normalized_only
                else finding.evasion,
            )
            for finding in l0_findings
        ]
        l0_ms = (perf_counter() - l0_started) * 1000

        l1_findings: list[Finding] = []
        l1_ms = 0.0
        degraded = False
        degraded_reasons: list[str] = []
        if self.l1_backend is not None:
            l1_started = perf_counter()
            budget = self.policy.config.limits.l1_timeout_ms / 1000
            try:
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(
                        self._l1_findings, bundle, origin=origin, content_type=content_type
                    )
                    l1_findings = future.result(timeout=budget)
            except FuturesTimeout:
                # ★ §3.6 — 예산 초과면 L0 결과를 채택하고 degraded 로 알린다.
                degraded = True
                degraded_reasons.append("l1_timeout")
            except Exception as exc:
                degraded = True
                degraded_reasons.append(f"l1_error:{type(exc).__name__}")
            l1_ms = (perf_counter() - l1_started) * 1000

        accepted = [
            finding
            for finding in l0_findings + l1_findings
            if self.policy.accepts(finding, content_type)
        ]
        type_policy = self.policy.config.content_type_policy.get(content_type)
        if type_policy is not None and type_policy.key_exclude:
            key_spans = _structured_key_spans(text)
            accepted = [
                finding
                for finding in accepted
                if not any(
                    finding.span[0] < key_end and key_start < finding.span[1]
                    for key_start, key_end in key_spans
                )
            ]
        leak_findings = merge_overlaps([finding for finding in accepted if not finding.is_attack])
        attack_findings = merge_overlaps([finding for finding in accepted if finding.is_attack])
        merged = sorted(leak_findings + attack_findings, key=lambda item: item.span)
        merged = [
            replace(
                finding,
                span=(finding.span[0] + source_offset, finding.span[1] + source_offset),
                segment_idx=segment_idx,
                decoded_from=(
                    None
                    if finding.decoded_from is None
                    else (
                        finding.decoded_from[0] + source_offset,
                        finding.decoded_from[1] + source_offset,
                    )
                ),
            )
            for finding in merged
        ]
        return PipelineResult(
            findings=merged,
            delta=bundle.delta,
            degraded=degraded,
            latency_ms={
                "normalize": normalization_ms,
                "l0": l0_ms,
                "l1": l1_ms,
                "total": (perf_counter() - started) * 1000,
            },
            degraded_reasons=degraded_reasons,
        )
