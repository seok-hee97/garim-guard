"""배포 버전 — `pyproject.toml` 한 곳에서만 올린다.

★ 0.1.1 을 올리면서 `__version__` 문자열을 따로 두어 PyPI 0.1.1 이 `0.1.0` 을 보고했다.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("garim-guard")
except PackageNotFoundError:  # 설치하지 않은 소스 트리를 바로 import 한 경우
    __version__ = "0+unknown"
