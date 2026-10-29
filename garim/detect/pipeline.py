"""Layer orchestration for one lossless source slice."""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from time import perf_counter
from typing import TypeVar

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


_T = TypeVar("_T")


class L1BusyError(RuntimeError):
    """A previous timed-out inference still owns the bounded L1 worker."""


# 백엔드마다 돌고 있는 추론 수(예산을 넘겨 버려진 것 포함). id(백엔드) 로 센다 — 도는 워커가
# 백엔드를 붙잡고 있으므로 수가 0 이 될 때까지 id 가 바뀌지 않는다. 상한은 프록시의 동시 요청을
# 받을 만큼 두되, 끝나지 않는 백엔드가 스레드를 끝없이 쌓지 못하게 한다.
_INFLIGHT: dict[int, int] = {}
_INFLIGHT_LOCK = threading.Lock()
MAX_INFLIGHT_PER_BACKEND = 8


def _release_inflight(key: int | None) -> None:
    if key is None:
        return
    with _INFLIGHT_LOCK:
        remaining = _INFLIGHT.get(key, 1) - 1
        if remaining > 0:
            _INFLIGHT[key] = remaining
        else:
            _INFLIGHT.pop(key, None)


def _call_with_deadline(
    func: Callable[[], _T],
    budget_seconds: float,
    *,
    gate: threading.Lock,
    on_timeout: Callable[[int | None], None] | None = None,
    owner: object | None = None,
) -> _T:
    """`budget_seconds` 안에 결과를 못 받으면 포기한다 — §3.6.

    ★ 회귀 — `ThreadPoolExecutor` 를 쓰면 안 된다.

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(...)
            l1_findings = future.result(timeout=budget)   # TimeoutError 발생
        # ← 여기서 __exit__ 가 shutdown(wait=True) 로 폭주 워커를 join 한다

    `future.result()` 가 제때 터져도 `with` 블록을 빠져나가며 스레드가 끝날 때까지
    기다렸다. 실행으로 확인: 예산 100ms 에 3초 자는 백엔드를 물리면 **실제 3,003ms**
    를 쓰고도 `degraded_reasons=['l1_timeout']` 로 "타임아웃했다"고 보고했다.
    예산의 30배를 블로킹한 것이라 §7.1.3 의 p50 300ms / p95 700ms 를 어떤 구현으로도
    지킬 수 없었다.

    파이썬에는 임의의 스레드를 안전하게 죽이는 방법이 없다. 대신 파이프라인마다
    gate 하나만 허용한다. 타임아웃 난 워커가 끝나기 전에는 다음 추론을 시작하지
    않아 공격자가 타임아웃 호출마다 새 백그라운드 추론을 쌓을 수 없다. ONNX
    백엔드는 `cancel_current()` 로 ORT RunOptions 종료도 함께 요청한다.

    ★ 전수 검토(2026-10-28) — 프록시는 요청마다 Inspector(= 파이프라인 · gate)를 새로 만들고
      백엔드만 공유한다. 그래서 gate 의 상한이 요청 하나 안에서만 걸렸고, 느린 플러그인
      백엔드에 동시 요청 12개를 보내면 버려진 추론 12개가 살아 있었다. 처음 고친 판은 시간
      초과 **뒤에** 세어서 한꺼번에 몰린 요청은 다 통과했다(재검토). 이제 `owner`(백엔드)마다
      도는 추론을 **시작 전에** 세고 상한이면 시작하지 않는다(`l1_busy`).
    """
    key = None if owner is None else id(owner)
    if key is not None:
        with _INFLIGHT_LOCK:
            if _INFLIGHT.get(key, 0) >= MAX_INFLIGHT_PER_BACKEND:
                raise L1BusyError("too many L1 inferences in flight on this backend")
            _INFLIGHT[key] = _INFLIGHT.get(key, 0) + 1
    if not gate.acquire(blocking=False):
        _release_inflight(key)
        raise L1BusyError("previous L1 inference is still running")
    box: dict[str, object] = {}

    def runner() -> None:
        try:
            box["value"] = func()
        except BaseException as exc:  # 워커의 예외를 호출자 스레드로 옮긴다
            box["error"] = exc
        finally:
            gate.release()
            _release_inflight(key)

    worker = threading.Thread(target=runner, daemon=True, name="garim-l1")
    try:
        worker.start()
    except BaseException:
        gate.release()
        _release_inflight(key)
        raise
    worker.join(budget_seconds)
    if worker.is_alive():
        if on_timeout is not None:
            try:
                # 워커 id 를 넘긴다 — 백엔드를 공유하는 다른 요청의 추론을 끊지 않는다.
                on_timeout(worker.ident)
            except Exception:
                pass  # cancellation is best-effort; timeout semantics must remain stable
        raise TimeoutError("L1 inference exceeded its budget")
    error = box.get("error")
    if error is not None:
        raise error  # type: ignore[misc]
    return box["value"]  # type: ignore[return-value]


# ★ 키 뒤 공백은 줄 안에서만, YAML 키는 탐욕 매치 뒤 코드에서 공백을 뗀다. 게으른 키와 `\s*`
#   가 겹쳐 `'{"a":\n' + ' ' * 1500 + '1}'` 에 8초(세제곱)가 걸렸다(5차 검토).
_STRUCTURED_KEY = re.compile(
    r"""(?m)(?:["'](?P<json>[^"'\r\n]+)["'][ \t]*:|^[ \t]*(?P<yaml>[^\s#:][^#\r\n:]*):)"""
)


def _structured_key_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for match in _STRUCTURED_KEY.finditer(text):
        group = "json" if match.group("json") is not None else "yaml"
        start, end = match.span(group)
        while end > start and text[end - 1] in " \t":
            end -= 1
        spans.append((start, end))
    return spans


def _is_pii_finding(finding: Finding) -> bool:
    """Only PII identifiers are noise in a structured/code key.

    Secret and attack findings must survive even when their bytes happen to occupy a
    mapping key.  Treating ``key_exclude`` as a blanket span filter allowed API keys used
    as dictionary keys to bypass the DLP boundary.
    """

    return finding.code.startswith(("L-KR-", "L-CM-", "L-CT-"))


def _code_identifier_spans(text: str, allowlist: list[str]) -> list[tuple[int, int]]:
    """Locate allow-listed assignment keys without suppressing matching values."""

    names = sorted({name for name in allowlist if name}, key=len, reverse=True)
    if not names:
        return []
    pattern = re.compile(
        rf"(?m)(?<![\w])(?P<name>{'|'.join(re.escape(name) for name in names)})"
        r"(?![\w])(?=\s*(?::|=(?!=)))"
    )
    return [match.span("name") for match in pattern.finditer(text)]


# 손실 스트림에서 이어 붙은 주민 · 외국인번호가 원문에서 공백 · 하이픈으로만 나뉘어 있었는가.
_SPLIT_IDENTIFIER = re.compile(r"\d{6}[\s\-]{1,5}[1-8]\d{6}")


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
    if stream == "detensified":
        codes.append("A-KO-07")
    if bundle.delta["nfkc"]:
        codes.append("A-MISC-01")
    return codes


def attack_code_for(label: str, *, origin: Origin, normalized_text: str) -> str:
    """Map a model's 4-way attack label to a project attack code (L1 and L2 share it)."""
    if label == "INJECTION":
        # ★ origin 으로 끄지 않는다(§2.5b). 강등은 정책층이 trust_declared 를 보고 한다.
        return "A-INJ-02" if origin in ("retrieved", "tool") else "A-INJ-01"
    if label == "JAILBREAK":
        lexical = detect_attacks(normalized_text, origin=origin)
        return "A-JB-01" if any(item.code == "A-JB-01" for item in lexical) else "A-JB-02"
    if label == "SYSTEM_LEAK":
        return "A-LEAK-01"
    raise ValueError(f"not an attack label: {label!r}")


def _trim_span_whitespace(span: tuple[int, int], text: str) -> tuple[int, int]:
    start, end = span
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


class DetectionPipeline:
    def __init__(self, policy: PolicyEngine, l1_backend: L1Backend | None = None) -> None:
        self.policy = policy
        self.l1_backend = l1_backend
        self._l1_gate = threading.Lock()

    def _cancel_l1(self, thread_id: int | None = None) -> None:
        cancel = getattr(self.l1_backend, "cancel_current", None)
        if callable(cancel):
            cancel(thread_id)

    def _scan_view(
        self,
        view: NormalizedView,
        *,
        source_text: str,
        origin: Origin,
        stream: str,
    ) -> list[Finding]:
        local = (
            detect_kr_pii(view.text, rrn_anchor=self.policy.config.rules.rrn_anchor)
            + detect_common_pii(view.text)
            + detect_secrets(view.text)
            + detect_markers(
                view.text, score_threshold=self.policy.config.rules.marker_score_threshold
            )
            + detect_attacks(view.text, origin=origin)
        )
        for pattern, compiled in self.policy.custom_patterns:
            for match in compiled.finditer(view.text):
                if match.start() < match.end():  # 길이 0 탐지는 마스킹을 멈춘다(스키마도 막는다)
                    local.append(Finding(pattern.code, match.span(), 0.9, "L0", pattern.name))

        mapped: list[Finding] = []
        source_len = len(source_text)
        for finding in local:
            source_span = to_source_span(finding.span, view.interval_map, source_len)
            if stream in {"nospace", "detensified"}:
                # 손실 스트림이다 — 체크섬 없이 형태만 맞은 식별자는 채택하지 않고,
                # 나머지는 신뢰도를 깎는다(§2.3).
                if finding.rule in ("secret_assignment", "korean_password_phrase"):
                    # ★ 재검토 — 공백을 지우면 `Password: at least 8 characters` 가 값 하나가 되어
                    #   block 됐다. 대입 꼴은 원문 · 정규화 스트림에서만 받는다.
                    continue
                if finding.code == "L-KR-08":
                    # ★ 공백을 지우면 날짜 · 시각 · 금액이 붙어 계좌 모양이 된다
                    #   (`입금 확인: 2025 01 15 1200` → 12자리). 계좌는 원문 · 정규화
                    #   스트림에서만 받는다(전수 검토).
                    continue
                split = _SPLIT_IDENTIFIER.fullmatch(source_text[source_span[0] : source_span[1]])
                if finding.code in {"L-KR-01", "L-KR-02"} and finding.confidence < 1.0:
                    if not split:
                        continue
                    # ★ 앵커가 있는 주민 · 외국인번호를 줄바꿈 · 띄어쓰기로 나누면(`주민번호
                    #   900101\n1234569`) 이 스트림만 보는데, 여기서 버려 통째로 나갔다(전수 검토).
                    #   2020년 10월 이후 번호는 체크섬이 없어 앵커가 유일한 길이다.
                else:
                    finding = replace(finding, confidence=finding.confidence * 0.8)
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
            span = _trim_span_whitespace(span, bundle.raw.text)
            if span[0] == span[1]:
                continue
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
            code = attack_code_for(attack_label, origin=origin, normalized_text=bundle.norm.text)
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
        use_l0: bool = True,
        use_l1: bool = True,
    ) -> PipelineResult:
        started = perf_counter()
        normalization_started = perf_counter()
        bundle = normalize(text)
        normalization_ms = (perf_counter() - normalization_started) * 1000

        l0_started = perf_counter()
        all_l0: list[Finding] = []
        if use_l0:
            raw_findings = self._scan_view(
                bundle.raw, source_text=text, origin=origin, stream="raw"
            )
            all_l0.extend(raw_findings)
            all_l0.extend(
                self._scan_view(bundle.norm, source_text=text, origin=origin, stream="norm")
            )
            all_l0.extend(
                self._scan_view(bundle.nospace, source_text=text, origin=origin, stream="nospace")
            )
            if bundle.detensified.text != bundle.norm.text:
                all_l0.extend(
                    self._scan_view(
                        bundle.detensified,
                        source_text=text,
                        origin=origin,
                        stream="detensified",
                    )
                )
            for decoded in bundle.decoded:
                all_l0.extend(
                    self._scan_view(
                        decoded,
                        source_text=text,
                        origin=origin,
                        stream=decoded.name,
                    )
                )
        l0_findings = deduplicate_streams(all_l0, self.policy.mode_for, text)
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
        # `use_l1=False` 는 호출자가 L1 예산을 다 쓴 경우다(§3.6, api.py 의
        # max_l1_chunks). L0 는 그대로 돌고, degraded 보고는 호출자가 한다.
        if self.l1_backend is not None and use_l1:
            l1_started = perf_counter()
            budget = self.policy.config.limits.l1_timeout_ms / 1000
            try:
                l1_findings = _call_with_deadline(
                    lambda: self._l1_findings(bundle, origin=origin, content_type=content_type),
                    budget,
                    gate=self._l1_gate,
                    on_timeout=self._cancel_l1,
                    owner=self.l1_backend,
                )
            except TimeoutError:
                # ★ §3.6 — 예산 초과면 L0 결과를 채택하고 degraded 로 알린다.
                degraded = True
                degraded_reasons.append("l1_timeout")
            except L1BusyError:
                degraded = True
                degraded_reasons.append("l1_busy")
            except KeyboardInterrupt:
                raise
            except BaseException as exc:
                # ★ 전수 검토 — 백엔드가 `sys.exit()` 를 부르면 검사 전체가 끝나 CLI 가 0 으로
                #   나갔다(주민번호가 든 입력에서도). 백엔드 경계에서는 SystemExit · 사용자
                #   정의 BaseException 도 실패다(재검토).
                degraded = True
                degraded_reasons.append(f"l1_error:{type(exc).__name__}")
            l1_ms = (perf_counter() - l1_started) * 1000

        accepted = [
            finding
            for finding in l0_findings + l1_findings
            if self.policy.accepts(finding, content_type, origin=origin)
        ]
        type_policy = self.policy.config.content_type_policy.get(content_type)
        if type_policy is not None and type_policy.key_exclude:
            # ★ 키 제외는 **L1** 의 문맥 개체에만 건다. 키 이름(`user_name` · `phone`)을
            #   개체로 읽는 것은 모델뿐이다. 초판은 L0 형식 탐지까지 지워서
            #   `{"ids": {"900101-…": "x"}}` 처럼 체크섬이 맞는 주민번호가 키 자리에 있으면
            #   그대로 통과했다 — 그건 이름이 아니라 값이다.
            key_spans = _structured_key_spans(text)
            accepted = [
                finding
                for finding in accepted
                if not (
                    finding.layer == "L1"
                    and _is_pii_finding(finding)
                    and any(
                        finding.span[0] < key_end and key_start < finding.span[1]
                        for key_start, key_end in key_spans
                    )
                )
            ]
        if type_policy is not None and type_policy.identifier_allowlist:
            identifier_spans = _code_identifier_spans(text, type_policy.identifier_allowlist)
            accepted = [
                finding
                for finding in accepted
                if not (
                    _is_pii_finding(finding)
                    and any(
                        finding.span[0] < identifier_end and identifier_start < finding.span[1]
                        for identifier_start, identifier_end in identifier_spans
                    )
                )
            ]
        # 겹침 해소는 **활성 정책**의 심각도를 따른다. 하드코딩 기본값을 쓰면 사용자가
        # block 이라고 적은 코드가 조용히 사라진다(fail-open).
        mode_for = self.policy.mode_for
        leak_findings = merge_overlaps(
            [finding for finding in accepted if not finding.is_attack], mode_for
        )
        attack_findings = merge_overlaps(
            [finding for finding in accepted if finding.is_attack], mode_for
        )
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
