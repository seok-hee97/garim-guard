"""L1 multi-head boundary.

The runtime owns exactly one encoder session. A backend returns four logical heads from one
forward pass: PII spans, a 4-way attack distribution, romanization probability, and Korean
honorific/social-engineering probability. This module intentionally does not download models.
"""

from __future__ import annotations

import json
import math
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from garim.models import ContentType, Origin


@dataclass(frozen=True, slots=True)
class L1Entity:
    label: str
    span: tuple[int, int]
    score: float


@dataclass(frozen=True, slots=True)
class L1Output:
    entities: list[L1Entity] = field(default_factory=list)
    attack_probabilities: dict[str, float] = field(
        default_factory=lambda: {
            "NONE": 1.0,
            "INJECTION": 0.0,
            "JAILBREAK": 0.0,
            "SYSTEM_LEAK": 0.0,
        }
    )
    romanized_evasion: float = 0.0
    honorific_social_engineering: float = 0.0

    def validate(self, text_length: int) -> None:
        required = {"NONE", "INJECTION", "JAILBREAK", "SYSTEM_LEAK"}
        if set(self.attack_probabilities) != required:
            raise ValueError("L1 attack head must expose exactly four classes")
        if any(not 0.0 <= score <= 1.0 for score in self.attack_probabilities.values()):
            raise ValueError("L1 attack probabilities must be in [0, 1]")
        if any(not 0 <= entity.span[0] < entity.span[1] <= text_length for entity in self.entities):
            raise ValueError("L1 entity span is outside normalized input")
        if not 0.0 <= self.romanized_evasion <= 1.0:
            raise ValueError("romanized_evasion must be in [0, 1]")
        if not 0.0 <= self.honorific_social_engineering <= 1.0:
            raise ValueError("honorific_social_engineering must be in [0, 1]")


@runtime_checkable
class L1Backend(Protocol):
    name: str

    def infer(
        self,
        text: str,
        *,
        normalization_delta: dict[str, int],
        origin: Origin,
        content_type: ContentType,
    ) -> L1Output:
        """Run one shared-encoder forward pass and return all four logical heads."""


PII_LABEL_TO_CODE = {
    "KR_RRN": "L-KR-01",
    "KR_FRN": "L-KR-02",
    "KR_PASSPORT": "L-KR-03",
    "KR_DL": "L-KR-04",
    "KR_BIZNO": "L-KR-05",
    "KR_CORPNO": "L-KR-06",
    "KR_NHI": "L-KR-07",
    "KR_ACCOUNT": "L-KR-08",
    "CARD": "L-CM-01",
    "PHONE": "L-CM-02",
    "EMAIL": "L-CM-03",
    "PERSON": "L-CT-01",
    "ADDRESS": "L-CT-02",
    "ORG_ID": "L-CT-03",
}

# §6.2.2 L1 **feature 계약**. §3.2.5 의 델타 **스키마**(8차원)와 일부러 다르다.
#
# ★ `space` 를 뺐다. 이유가 셋인데 마지막이 결정적이다.
#
# 1. 비단조다. 실측하면 공백 **제거** 우회 0 / 평범한 한국어 6~8 / 공백 **삽입** 우회 20
#    이라 평범한 글이 두 공격 방향 **사이**에 있다. 어떤 임계도 가르지 못한다.
# 2. 이 칸만 **다른 스트림**을 기술한다. `norm` 은 공백을 보존하고(`chain.py`
#    `_conservative_spaces`), `space` 는 `nospace` 뷰를 만들며 지운 개수다. L1 은
#    `nospace` 를 받지 않는다(`pipeline.py` 는 `bundle.norm.text` 를 넘긴다).
# 3. **나머지 일곱은 정규화가 지운 정보를 나른다.** 제로위드·태그문자·동형이의·자모·
#    한자·NFKC·base64 는 `norm.text` 안에 흔적이 없으니 이 숫자가 유일한 채널이다.
#    `space` 는 반대다 — 공백은 텍스트에 그대로 남아 토크나이저가 이미 본다. 입력의
#    저차원 사영을 하나 더 붙이는 셈이고, 더하는 것은 정보가 아니라 중간 구간에서
#    "무해" 와 상관되는 지름길뿐이다.
#
# `space` 는 §3.2.5 스키마에 그대로 남는다 — A-KO-05 배출과 `nospace` 재검사를 켜는
# L0 신호다. 빠진 것은 **학습 feature 자격**이지 델타 자체가 아니다.
#
# 가중치가 아직 없어서 이 변경에 호환 부담이 0 인 시점이다.
NORMALIZATION_DELTA_ORDER = (
    "nfkc",
    "zero_width",
    "tag_chars",
    "homoglyph",
    "jamo_composed",
    "hanja",
    "b64_decoded",
)
# 스키마에는 있으나 feature 로는 쓰지 않는 칸. 테스트가 이 관계를 고정한다.
DELTA_KEYS_EXCLUDED_FROM_FEATURES = ("space",)


class _SerializedTokenizer:
    """Fast tokenizer wrapper that lets one backend serve concurrent requests.

    HF fast tokenizers mutate their Rust-side truncation/padding state on each call; two
    threads calling with different settings can fail with ``RuntimeError: Already
    borrowed``. The runtime calls it with two settings — ``chunk_text`` without
    truncation and ``infer`` with it — so a shared backend would hit exactly that.
    Tokenization is short next to the ONNX run, so a lock costs little.
    """

    def __init__(self, tokenizer: object) -> None:
        self._tokenizer = tokenizer
        self._lock = threading.Lock()

    def __call__(self, *args: object, **kwargs: object) -> object:
        with self._lock:
            return self._tokenizer(*args, **kwargs)  # type: ignore[operator]

    def __getattr__(self, name: str) -> object:
        return getattr(self._tokenizer, name)


# cgroup v2 · v1 CPU 할당량 파일. 컨테이너 밖이면 없다.
CGROUP_CPU_MAX = Path("/sys/fs/cgroup/cpu.max")
CGROUP_V1_QUOTA = Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")
CGROUP_V1_PERIOD = Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us")


def cpu_quota_threads(
    cpu_max: Path = CGROUP_CPU_MAX,
    v1_quota: Path = CGROUP_V1_QUOTA,
    v1_period: Path = CGROUP_V1_PERIOD,
) -> int | None:
    """cgroup CPU 할당량을 코어 수로(내림, 최소 1). 할당량이 없거나 못 읽으면 None.

    ★ onnxruntime 은 intra-op 스레드를 **보이는** 물리 코어 수로 잡는다. RunPod 컨테이너는 64코어가
      보이고 할당량은 8.5코어였다 — 32스레드가 할당량에 걸려 throttling 을 반복하며 행당 347ms 가
      걸렸고, 8스레드면 36ms 였다(출력은 같다). 검사기의 L1 예산(1,000ms)이 그 사이에 있다.
    """
    try:
        if cpu_max.is_file():
            quota, period = cpu_max.read_text().split()[:2]
            if quota == "max":
                return None
            return max(1, math.floor(int(quota) / int(period)))
        if v1_quota.is_file() and v1_period.is_file():
            quota_us = int(v1_quota.read_text())
            if quota_us <= 0:
                return None
            return max(1, math.floor(quota_us / int(v1_period.read_text())))
    except (OSError, ValueError):
        return None
    return None


def _intra_op_threads() -> int | None:
    """`GARIM_L1_THREADS` 가 있으면 그 값, 없으면 cgroup 할당량, 둘 다 없으면 onnxruntime 기본."""
    configured = os.environ.get("GARIM_L1_THREADS")
    if configured:
        threads = int(configured)
        if threads < 1:
            raise ValueError("GARIM_L1_THREADS must be a positive integer")
        return threads
    return cpu_quota_threads()


class OnnxL1Backend:
    """Local ONNX Runtime adapter for the shared-encoder/four-head model contract.

    ``model_dir`` must contain ``garim_l1.int8.onnx``, tokenizer files, and
    ``labels.json`` with a ``pii_labels`` array. All model loading is local-only.
    """

    name = "onnx-int8"

    def __init__(
        self,
        model_dir: str | Path,
        *,
        max_length: int = 512,
        model_filename: str = "garim_l1.int8.onnx",
    ) -> None:
        try:
            import onnxruntime as ort
            from transformers import AutoTokenizer
        except ImportError as exc:
            raise RuntimeError("install garim-guard[l1] to use OnnxL1Backend") from exc

        directory = Path(model_dir)
        if Path(model_filename).name != model_filename or not model_filename.endswith(".onnx"):
            raise ValueError("model_filename must be a local .onnx basename")
        model_path = directory / model_filename
        label_path = directory / "labels.json"
        if not model_path.is_file() or not label_path.is_file():
            raise ValueError(f"model_dir must contain {model_filename} and labels.json")
        try:
            metadata = json.loads(label_path.read_text(encoding="utf-8"))
            self.pii_labels = list(metadata["pii_labels"])
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ValueError(f"invalid L1 labels file: {label_path}") from exc
        if len(self.pii_labels) != 29 or self.pii_labels[0] != "O":
            raise ValueError("pii_labels must contain O plus 14 BIO entity pairs")

        tokenizer = AutoTokenizer.from_pretrained(directory, local_files_only=True, use_fast=True)
        if not tokenizer.is_fast:
            raise ValueError("L1 requires a fast tokenizer for source offset mapping")
        self.tokenizer = _SerializedTokenizer(tokenizer)
        options = ort.SessionOptions()
        threads = _intra_op_threads()
        if threads is not None:
            options.intra_op_num_threads = threads
        self.session = ort.InferenceSession(
            str(model_path), sess_options=options, providers=["CPUExecutionProvider"]
        )
        self._ort = ort
        self._run_options_lock = threading.Lock()
        # 워커 스레드 id → 그 스레드의 RunOptions. 프록시는 요청마다 Inspector 를 만들고
        # 이 백엔드 하나를 공유한다. 슬롯이 하나뿐이면 요청 A 의 타임아웃이 요청 B 의
        # 추론을 끊는다.
        self._active_run_options: dict[int, object] = {}
        self.input_names = {item.name for item in self.session.get_inputs()}
        self.output_names = [item.name for item in self.session.get_outputs()]
        required_outputs = {
            "pii_logits",
            "attack_logits",
            "romanized_logits",
            "honorific_logits",
        }
        if set(self.output_names) != required_outputs:
            raise ValueError(
                "ONNX outputs must be pii_logits, attack_logits, romanized_logits, honorific_logits"
            )
        self.max_length = max_length

    def cancel_current(self, thread_id: int | None = None) -> None:
        """Ask ONNX Runtime to terminate the inference running on ``thread_id``.

        Without a thread id every active inference is terminated.
        """
        with self._run_options_lock:
            if thread_id is None:
                targets = list(self._active_run_options.values())
            else:
                target = self._active_run_options.get(thread_id)
                targets = [] if target is None else [target]
        for run_options in targets:
            run_options.terminate = True  # type: ignore[attr-defined]

    @staticmethod
    def _softmax(values: object) -> object:
        import numpy as np

        array = np.asarray(values, dtype=np.float32)
        shifted = array - array.max(axis=-1, keepdims=True)
        exponent = np.exp(shifted)
        return exponent / exponent.sum(axis=-1, keepdims=True)

    @staticmethod
    def _sigmoid(value: object) -> float:
        import numpy as np

        scalar = float(np.asarray(value, dtype=np.float32).reshape(-1)[0])
        return float(1.0 / (1.0 + np.exp(-scalar)))

    def _decode_entities(self, logits: object, offsets: list[tuple[int, int]]) -> list[L1Entity]:
        import numpy as np

        probabilities = self._softmax(logits)[0]
        label_ids = np.asarray(probabilities).argmax(axis=-1)
        entities: list[L1Entity] = []
        active_label: str | None = None
        active_start = 0
        active_end = 0
        active_scores: list[float] = []

        def flush() -> None:
            nonlocal active_label, active_scores
            if active_label is not None and active_end > active_start:
                entities.append(
                    L1Entity(
                        active_label,
                        (active_start, active_end),
                        sum(active_scores) / len(active_scores),
                    )
                )
            active_label = None
            active_scores = []

        for token_index, label_id in enumerate(label_ids):
            start, end = offsets[token_index]
            label = self.pii_labels[int(label_id)]
            if start == end or label == "O":
                flush()
                continue
            prefix, _, entity_label = label.partition("-")
            if prefix == "B" or entity_label != active_label:
                flush()
                active_label = entity_label
                active_start = start
            active_end = end
            active_scores.append(float(probabilities[token_index, int(label_id)]))
        flush()
        return entities

    def infer(
        self,
        text: str,
        *,
        normalization_delta: dict[str, int],
        origin: Origin,
        content_type: ContentType,
    ) -> L1Output:
        import numpy as np

        del origin, content_type  # origin/type decisions are applied outside the model
        encoded = self.tokenizer(
            text,
            truncation=True,
            max_length=self.max_length,
            return_offsets_mapping=True,
            return_tensors="np",
        )
        offsets = [tuple(map(int, pair)) for pair in encoded.pop("offset_mapping")[0]]
        feed = {name: value for name, value in encoded.items() if name in self.input_names}
        # ★ §6.2.2 는 "규칙별 카운트를 log1p 스케일"을 요구한다. 초판은 원시 카운트를
        #   넘겼고, 델타 입력이 그래프에 없으면 조용히 건너뛰었다 — §6.2.2 의 핵심
        #   feature 가 오류 없이 빠졌다. 이제 필수로 요구하고 log1p 를 적용한다.
        if "normalization_delta" not in self.input_names:
            raise RuntimeError(
                "ONNX graph is missing the required 'normalization_delta' input "
                "(DESIGN §6.2.2 · §4.6). Re-export with the delta head wired in-graph."
            )
        feed["normalization_delta"] = np.log1p(
            np.asarray(
                [[float(normalization_delta[key]) for key in NORMALIZATION_DELTA_ORDER]],
                dtype=np.float32,
            )
        )
        run_options = self._ort.RunOptions()
        worker = threading.get_ident()
        with self._run_options_lock:
            self._active_run_options[worker] = run_options
        try:
            raw_outputs = self.session.run(self.output_names, feed, run_options)
        finally:
            with self._run_options_lock:
                if self._active_run_options.get(worker) is run_options:
                    del self._active_run_options[worker]
        outputs = dict(zip(self.output_names, raw_outputs, strict=True))

        attack = self._softmax(outputs["attack_logits"])[0]
        attack_labels = ("NONE", "INJECTION", "JAILBREAK", "SYSTEM_LEAK")
        result = L1Output(
            entities=self._decode_entities(outputs["pii_logits"], offsets),
            attack_probabilities={
                label: float(attack[index]) for index, label in enumerate(attack_labels)
            },
            romanized_evasion=self._sigmoid(outputs["romanized_logits"]),
            honorific_social_engineering=self._sigmoid(outputs["honorific_logits"]),
        )
        result.validate(len(text))
        return result
