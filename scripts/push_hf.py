"""공개 가능한 산출물만 Hugging Face 로 올린다 — ★라이선스 경계를 코드가 강제한다★.

    python -m scripts.push_hf --dry-run          # 무엇이 올라가고 무엇이 빠지는지만 출력
    HF_TOKEN=hf_... python -m scripts.push_hf    # 실제 업로드

왜 스크립트인가 — 이 저장소에는 ★재배포하면 안 되는 가중치★ 가 섞여 있다. 사람이 손으로
올리면 실수로 섞인다. 무엇을 올릴지를 표(ALLOW)로 고정하고, 그 밖의 파일은 존재해도
쳐다보지 않는다. 새 산출물이 생기면 이 표에 명시적으로 추가해야만 올라간다.

라이선스 경계(DATA_CARD.md 기준):
  ✅ KoTIP-Bench          자체 구축 · CC-BY-4.0
  ✅ nemotron_rail.*      Nemotron 파생 · CC-BY-4.0(출처표기)
  ✅ ko-agentguard-lr     자체 학습(TF-IDF+LR)
  ✅ e5-lr-head           e5=MIT + 자체 head
  ❌ r6-pii-ner (259M)    klue/roberta-small 파생 — 베이스 라이선스 미명시, 상위 KLUE 는
  ❌ r7-kmhas   (423M)    CC-BY-SA-4.0 → ★공개 재배포 지양★. 수치 보고만 하고 가중치는 로컬 보관.
                          (models/gpu_trained/MANIFEST.md 에 같은 판단이 적혀 있다)

업로드 후에는 scripts/fetch_rail.py 의 SHA256 과 대조해 재현성이 유지되는지 확인한다.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = "cycloevan/safeagent-kr"

# (로컬 경로, HF 원격 경로) — ★여기 없는 파일은 절대 올라가지 않는다★
ALLOW: list[tuple[str, str]] = [
    # ── 데이터셋: 평가셋 + 레일 학습셋 ──
    ("data/kotip/v0.1/attacks.dev.jsonl",        "data/kotip/v0.1/attacks.dev.jsonl"),
    ("data/kotip/v0.1/attacks.test.jsonl",       "data/kotip/v0.1/attacks.test.jsonl"),
    ("data/kotip/v0.1/attacks.smoke.jsonl",      "data/kotip/v0.1/attacks.smoke.jsonl"),
    ("data/kotip/v0.1/utility.dev.jsonl",        "data/kotip/v0.1/utility.dev.jsonl"),
    ("data/kotip/v0.1/utility.test.jsonl",       "data/kotip/v0.1/utility.test.jsonl"),
    ("data/kotip/v0.1/utility.smoke.jsonl",      "data/kotip/v0.1/utility.smoke.jsonl"),
    ("data/kotip/v0.1/nemotron_rail.train.jsonl", "data/kotip/v0.1/nemotron_rail.train.jsonl"),
    ("data/kotip/v0.1/nemotron_rail.dev.jsonl",   "data/kotip/v0.1/nemotron_rail.dev.jsonl"),
    ("data/kotip/v0.1/nemotron_rail.test.jsonl",  "data/kotip/v0.1/nemotron_rail.test.jsonl"),
    # ── 모델: 자체 학습 경량 아티팩트만 ──
    ("train/artifacts/ko-agentguard-lr.joblib",  "models/ko-agentguard-lr.joblib"),
    ("models/gpu_trained/models_final/e5-lr-head.joblib", "models/e5-lr-head.joblib"),
    # ── 카드 ──
    ("DATA_CARD.md", "DATA_CARD.md"),
]

# 존재하더라도 ★절대 올리지 않는다★ — 실수 방지용 명시적 거부 목록(경로 접두).
DENY_PREFIXES = [
    "models/gpu_trained/models_final/r6-pii-ner",   # klue/roberta-small 파생
    "models/gpu_trained/models_final/r7-kmhas",     # klue/roberta-base 파생
    ".env",
    "results/runs",                                  # 실행 로그 — 저장소로 충분
]


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="업로드 없이 계획만 출력")
    ap.add_argument("--repo", default=REPO)
    args = ap.parse_args()

    planned: list[tuple[Path, str]] = []
    missing: list[str] = []
    for local, remote in ALLOW:
        if any(local.startswith(d) for d in DENY_PREFIXES):
            print(f"✗ ALLOW 와 DENY 가 충돌한다: {local}", file=sys.stderr)
            return 2
        p = ROOT / local
        (planned.append((p, remote)) if p.is_file() else missing.append(local))

    print(f"┌─ 업로드 대상 → https://huggingface.co/{args.repo}")
    total = 0
    for p, remote in planned:
        kb = p.stat().st_size / 1024
        total += kb
        print(f"│  {kb:>8.1f} KB  {p.relative_to(ROOT)}  →  {remote}")
    print(f"│  {'─' * 58}\n│  {total:>8.1f} KB  합계 {len(planned)}개")

    if missing:
        print("│\n│  ⚠️ 없어서 건너뛴 항목(먼저 생성 필요):")
        for m in missing:
            print(f"│     {m}")

    print("│\n│  ★재배포 금지로 제외한 것★ — 라이선스 판단은 DATA_CARD.md 참조")
    for d in DENY_PREFIXES:
        p = ROOT / d
        if p.exists():
            size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.is_dir() \
                else p.stat().st_size
            print(f"│     {size / 1048576:>8.1f} MB  {d}")
    print("└─")

    if args.dry_run:
        print("\n(dry-run — 아무것도 올리지 않았다)")
        return 0

    token = os.environ.get("HF_TOKEN")
    if not token:
        print("\n✗ HF_TOKEN 이 없다. `export HF_TOKEN=hf_...` 후 다시 실행한다.", file=sys.stderr)
        return 1
    try:
        from huggingface_hub import HfApi
    except ImportError:
        print("\n✗ huggingface_hub 미설치: pip install huggingface_hub", file=sys.stderr)
        return 1

    api = HfApi(token=token)
    api.create_repo(args.repo, repo_type="model", exist_ok=True)
    for p, remote in planned:
        api.upload_file(path_or_fileobj=str(p), path_in_repo=remote,
                        repo_id=args.repo, repo_type="model")
        print(f"  ↑ {remote}  sha256={sha256(p)[:12]}")

    print("\n다음 단계: scripts/fetch_rail.py 의 SHA256 이 위 값과 맞는지 확인한다"
          " (`python -m scripts.fetch_rail` 이 검증한다).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
