"""ko_agentguard 레일 학습 아티팩트를 HF 에서 train/artifacts/ 로 내려받는다(R5-#1·R6-#1).

    python -m scripts.fetch_rail          # 다운로드 + 체크섬 검증

clean clone 에는 아티팩트가 없어(gitignore·용량) 평가(`eval/run.py`)가 fail-loud 한다.
이 스크립트로 받은 뒤 재실행하면 실제 ko_agentguard 레일로 재현된다.
공개 저장소: https://huggingface.co/cycloevan/safeagent-kr

★원격 경로(models/…)와 최종 로컬 경로(train/artifacts/…)를 분리한다★ — HF 는 models/ 아래에
두지만 KoAgentGuardRail(policy.yaml head)은 train/artifacts/ 를 기대한다.
"""

from __future__ import annotations

import hashlib
import shutil
import sys
from pathlib import Path

from src.config import ROOT

REPO = "cycloevan/safeagent-kr"
# 로컬파일명 → (HF 원격경로, 기대 SHA256). 값이 바뀌면 아티팩트 변경 → 재현 불일치 경고.
ARTIFACTS = {
    "ko-agentguard-lr.joblib": (
        "models/ko-agentguard-lr.joblib",
        "10cca996f7f193ffcd633dc8cb98e07c4a330db0cb4424400952c4838dadb2c8"),
}
DEST = ROOT / "train" / "artifacts"


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def main() -> int:
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        print("huggingface_hub 필요: pip install huggingface_hub", file=sys.stderr)
        return 1
    DEST.mkdir(parents=True, exist_ok=True)
    ok = True
    for local_name, (remote, expect) in ARTIFACTS.items():
        dst = DEST / local_name
        if dst.exists() and _sha(dst) == expect:
            print(f"✓ {local_name} 이미 존재(체크섬 일치)"); continue
        print(f"↓ {REPO}/{remote} → {_rel(dst)}")
        got = Path(hf_hub_download(repo_id=REPO, filename=remote))   # 캐시로 받음
        actual = _sha(got)
        if actual != expect:
            print(f"⚠️ 체크섬 불일치 {local_name}: 기대 {expect[:12]} 실제 {actual[:12]} "
                  f"(size={got.stat().st_size}) — HF 아티팩트 손상 가능", file=sys.stderr)
            ok = False
            continue
        shutil.copyfile(got, dst)                                    # ★로컬 최종 경로로 배치★
        print(f"✓ {local_name} 체크섬 검증 통과 → {_rel(dst)}")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
