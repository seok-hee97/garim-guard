"""ko_agentguard 레일 학습 아티팩트를 HF 에서 train/artifacts/ 로 내려받는다(R5-#1).

    python -m scripts.fetch_rail          # 다운로드 + 체크섬 검증

clean clone 에는 아티팩트가 없어(gitignore) 평가(`eval/run.py`)가 fail-loud 한다.
이 스크립트로 받은 뒤 재실행하면 실제 ko_agentguard 레일로 재현된다.
공개 저장소: https://huggingface.co/cycloevan/safeagent-kr
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from src.config import ROOT

REPO = "cycloevan/safeagent-kr"
FILES = {
    # 파일명 → 기대 SHA256(재현성 고정). 값이 바뀌면 아티팩트 변경 → 재현 불일치 경고.
    "ko-agentguard-lr.joblib": "10cca996f7f193ffcd633dc8cb98e07c4a330db0cb4424400952c4838dadb2c8",
}
DEST = ROOT / "train" / "artifacts"


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        print("huggingface_hub 필요: pip install huggingface_hub", file=sys.stderr)
        return 1
    DEST.mkdir(parents=True, exist_ok=True)
    ok = True
    for fname, expect in FILES.items():
        dst = DEST / fname
        if dst.exists() and _sha(dst) == expect:
            print(f"✓ {fname} 이미 존재(체크섬 일치)"); continue
        print(f"↓ {REPO}/{fname} → {dst.relative_to(ROOT)}")
        got = hf_hub_download(repo_id=REPO, filename=fname, local_dir=str(DEST))
        actual = _sha(Path(got))
        if actual != expect:
            print(f"⚠️ 체크섬 불일치 {fname}: 기대 {expect[:12]} 실제 {actual[:12]}", file=sys.stderr)
            ok = False
        else:
            print(f"✓ {fname} 체크섬 검증 통과")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
