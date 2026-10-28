"""패키지 밖의 모델을 L1 · L2 자리에 붙인다 — `모듈:이름` 또는 `파일.py:이름`.

가림은 모델을 고르거나 내려받지 않는다. 사용자가 학습 · 선택한 모델은 `L1Backend` · `L2Backend`
계약을 지키는 객체를 돌려주는 함수(또는 클래스) 하나로 연결한다 — docs/INTEGRATION.md.

★ 가림이 외부로 아무것도 보내지 않는다는 성질은 가림 코드의 것이다. 붙인 백엔드가 무엇을 하는지는
  그 코드가 정한다.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Literal, overload

from garim.detect.l1 import L1Backend
from garim.detect.l2 import L2Backend

_CONTRACTS = {"L1": (L1Backend, "infer"), "L2": (L2Backend, "judge")}


@overload
def load_backend(spec: str, *, layer: Literal["L1"]) -> L1Backend: ...
@overload
def load_backend(spec: str, *, layer: Literal["L2"]) -> L2Backend: ...
def load_backend(spec: str, *, layer: Literal["L1", "L2"]) -> L1Backend | L2Backend:
    """`spec` 의 함수를 인자 없이 불러 받은 백엔드를 계약과 대조해 돌려준다.

    `spec` 은 `패키지.모듈:함수` (설치됐거나 PYTHONPATH 위에 있는 모듈) 또는
    `경로/파일.py:함수` 다. 클래스도 인자 없이 부를 수 있으면 된다. 설정(모델 경로 등)은
    그 함수가 환경 변수 · 자기 설정 파일에서 읽는다.
    """
    contract, method = _CONTRACTS[layer]
    target, separator, attribute = spec.rpartition(":")
    if not separator or not target or not attribute:
        raise ValueError(f"{layer} backend must be 'module:name' or 'file.py:name': {spec!r}")
    factory = getattr(_import(target, layer), attribute, None)
    if not callable(factory):
        raise ValueError(f"{layer} backend {spec!r}: no callable {attribute!r} in {target}")
    try:
        backend = factory()
    except Exception as exc:  # 사용자 코드의 경계 — 무엇이 나든 시작하지 않는다
        raise RuntimeError(
            f"{layer} backend {spec!r} failed to start: {type(exc).__name__}: {exc}"
        ) from exc
    if not isinstance(backend, contract):
        raise TypeError(
            f"{layer} backend {spec!r} must return an object with 'name' and '{method}()'"
        )
    return backend


def _import(target: str, layer: str) -> ModuleType:
    if not target.endswith(".py"):
        try:
            return importlib.import_module(target)
        except ImportError as exc:
            raise ValueError(f"cannot import {layer} backend module {target!r}: {exc}") from exc
        except Exception as exc:
            raise RuntimeError(
                f"{layer} backend module {target!r} failed to import: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

    # 파일 경로는 sys.path 를 건드리지 않고 그 파일만 올린다 — 현재 디렉터리를 경로에 넣으면
    # 거기 놓인 `json.py` 같은 파일이 이후의 모든 import 를 가로챈다.
    path = Path(target).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"{layer} backend file not found: {target}")
    name = f"_garim_{layer.lower()}_backend_{path.stem}"
    module_spec = importlib.util.spec_from_file_location(name, path)
    if module_spec is None or module_spec.loader is None:
        raise ValueError(f"cannot load {layer} backend file: {target}")
    module = importlib.util.module_from_spec(module_spec)
    sys.modules[name] = module  # dataclass 등은 모듈이 sys.modules 에 있어야 한다
    try:
        module_spec.loader.exec_module(module)
    except Exception as exc:
        del sys.modules[name]
        raise RuntimeError(
            f"{layer} backend file {target!r} failed to import: {type(exc).__name__}: {exc}"
        ) from exc
    return module
