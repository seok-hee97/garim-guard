"""고정 모델 하나를 로드하기 위한 좁은 호환 계층.

★ `kakaocorp/kanana-safeguard-prompt-2.1b` 가 `transformers>=5` 에서 로드되지 않는다.

    hidden_size 1792 · num_attention_heads 24 · head_dim 128

`head_dim` 을 명시해 `hidden_size` 와 헤드 수를 분리한 구성이고, Llama 3.2·Gemma 가
쓰는 정상 패턴이다. 그런데 `LlamaConfig.validate_architecture` 가 `head_dim` 을 보지
않고 `hidden_size % num_attention_heads` 만 검사한다:

    def validate_architecture(self):
        if self.hidden_size % self.num_attention_heads != 0:
            raise ValueError(...)

즉 **설정이 유효하고 검증자가 틀렸다.** `AutoConfig.from_pretrained` 단계에서 막히므로
우회 없이는 로드 자체가 불가능하다.

영향 범위가 평가에 그치지 않는다 — 같은 모델이 `teacher_kanana` 로 L1 증류 교사에
고정돼 있고(§6.2.2) L2-fast 채택 모델이기도 하다(§6.4.3). transformers 를 4.x 로
내리면 A.X 백본 학습 경로가 그 버전에 묶이므로, 검증자만 한시적으로 걷어낸다.

⚠️ 범위를 좁힌다. `head_dim` 이 실제로 선언돼 있고 그 값이 헤드 수와 정합할 때만
발동하며, 그 밖에는 원래 검증을 그대로 통과시킨다. 컨텍스트를 벗어나면 복원한다.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

_VALIDATOR_NAME = "validate_architecture"


def config_needs_decoupled_head_shim(config: dict[str, Any]) -> bool:
    """`head_dim` 을 선언했는데 검증자가 거부할 구성인가."""
    hidden = config.get("hidden_size")
    heads = config.get("num_attention_heads")
    head_dim = config.get("head_dim")
    if not all(isinstance(value, int) for value in (hidden, heads, head_dim)):
        return False
    if heads <= 0 or head_dim <= 0:
        return False
    return hidden % heads != 0


def read_local_config(model_dir: str | Path) -> dict[str, Any]:
    path = Path(model_dir) / "config.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read local model config: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"model config is not an object: {path}")
    return payload


@contextmanager
def allow_decoupled_head_dim(config_classes: tuple[type, ...] | None = None) -> Iterator[bool]:
    """`validate_architecture` 를 한시적으로 걷어낸다. 컨텍스트를 벗어나면 복원한다.

    Yields whether the shim actually engaged, so callers can record it.
    """
    if config_classes is None:
        from transformers.models.llama.configuration_llama import LlamaConfig

        config_classes = (LlamaConfig,)

    saved: list[tuple[type, tuple[Any, ...]]] = []
    for config_class in config_classes:
        validators = getattr(config_class, "__class_validators__", None)
        if not validators:
            continue
        kept = tuple(
            item for item in validators if getattr(item, "__name__", "") != _VALIDATOR_NAME
        )
        if len(kept) != len(validators):
            saved.append((config_class, tuple(validators)))
            config_class.__class_validators__ = kept
    try:
        yield bool(saved)
    finally:
        for config_class, original in saved:
            config_class.__class_validators__ = original


def _load_with_shim(loader: Any, model_dir: str | Path, kwargs: dict[str, Any]) -> Any:
    kwargs.setdefault("local_files_only", True)
    if not config_needs_decoupled_head_shim(read_local_config(model_dir)):
        return loader(str(model_dir), **kwargs)
    with allow_decoupled_head_dim():
        return loader(str(model_dir), **kwargs)


def load_local_causal_lm(model_dir: str | Path, **kwargs: Any) -> Any:
    """Load a pinned local causal LM, engaging the shim only when the config needs it."""
    from transformers import AutoModelForCausalLM

    return _load_with_shim(AutoModelForCausalLM.from_pretrained, model_dir, kwargs)


def load_local_tokenizer(model_dir: str | Path, **kwargs: Any) -> Any:
    """★ 토크나이저도 같은 게이트를 타야 한다.

    `AutoTokenizer.from_pretrained` 가 토크나이저 클래스를 정하려고 `config.json` 을
    먼저 읽는다 — 모델보다 앞서 같은 검증자에 막힌다. 모델 쪽만 감싸 두면 shim 이
    한 번도 발동하지 못하고 그대로 실패한다(실제로 그랬다).
    """
    from transformers import AutoTokenizer

    return _load_with_shim(AutoTokenizer.from_pretrained, model_dir, kwargs)
