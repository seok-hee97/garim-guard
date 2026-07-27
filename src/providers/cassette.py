"""카세트 record/replay — API·키·쿼터를 크리티컬 패스에서 제거한다.

🔴 무료 티어 실측 한도 = 5 RPM (gemini-3.5-flash). 애블레이션 전량 스윕은
   카세트 없이 불가능하다. 이것은 폴백이 아니라 유일하게 현실적인 실행 경로다.

무결성 규칙 (project_plan §7.4):
 1. 손저작 카세트로 만든 표는 날조된 수치다. allow_handwritten=false 가 기본이며,
    손저작 항목은 synthetic_cassette=true 로 표시되어 결과 표에서 자동 제외된다.
 2. 재생 arm 은 replayed=true 로 표시되고 분산 주장에서 제외된다
    (동일 입력의 표준편차는 허위다).
 3. 카세트 키 = sha256(model, cfg, contents). on_miss=error 가 기본.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def cassette_key(model: str, cfg_fingerprint: str, contents: list[dict[str, Any]]) -> str:
    """모델·설정·대화이력의 결정적 해시. 같은 입력 → 같은 키."""
    payload = json.dumps(
        {"model": model, "cfg": cfg_fingerprint, "contents": contents},
        ensure_ascii=False, sort_keys=True, default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class CassetteStore:
    """키 → 기록된 GenResult(dict) 저장소. 디렉터리 안 JSON 파일 하나로 관리."""

    def __init__(self, cassette_dir: str | Path, mode: str = "replay",
                 on_miss: str = "error", allow_handwritten: bool = False):
        self.dir = Path(cassette_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.store_path = self.dir / "cassettes.json"
        self.mode = mode                    # record | replay | passthrough
        self.on_miss = on_miss              # error | record
        self.allow_handwritten = allow_handwritten
        self._data: dict[str, dict] = {}
        if self.store_path.exists():
            self._data = json.loads(self.store_path.read_text(encoding="utf-8"))

    def get(self, key: str) -> dict | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        if entry.get("synthetic_cassette") and not self.allow_handwritten:
            raise ValueError(
                f"카세트 {key[:12]}… 는 손저작(synthetic)이며 allow_handwritten=false 다. "
                "보고용 수치 생성 금지."
            )
        return entry

    def put(self, key: str, result: dict, *, synthetic: bool = False) -> None:
        entry = dict(result)
        if synthetic:
            entry["synthetic_cassette"] = True
        self._data[key] = entry
        self._flush()

    def _flush(self) -> None:
        self.store_path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=1), encoding="utf-8"
        )

    def miss(self, key: str) -> None:
        """replay 중 미스 처리."""
        if self.on_miss == "error":
            raise KeyError(
                f"카세트 미스: {key[:12]}… (mode=replay, on_miss=error). "
                "먼저 mode=record 로 실제 호출을 녹화하라."
            )
