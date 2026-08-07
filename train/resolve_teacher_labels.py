#!/usr/bin/env python3
"""Resolve three pinned teacher artifacts into accepted and human-review queues."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from garim.training.data import load_jsonl, validate_corpus, write_jsonl
from garim.training.teacher_io import load_teacher_artifacts, resolve_teacher_corpus
from garim.training.teachers import load_teacher_policy


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="unlabeled Korean attack JSONL")
    parser.add_argument("--kanana", required=True)
    parser.add_argument("--gemma4", required=True)
    parser.add_argument("--qwen3guard-stream", required=True)
    parser.add_argument("--policy", default="train/teachers.yaml")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--validation-percent", type=int, default=10)
    parser.add_argument(
        "--drop-review",
        action="store_true",
        help=(
            "사람 검수를 하지 않는 운영에서 교사 불일치 행을 학습에서 제외한다. "
            "§6.2.2 가 금지한 것은 NONE 흡수이지 폐기가 아니다 — 폐기 건수는 감사에 남는다."
        ),
    )
    args = parser.parse_args()

    records = load_jsonl(args.input)
    policy = load_teacher_policy(args.policy)
    artifacts = {
        "kanana": load_teacher_artifacts(args.kanana),
        "gemma4": load_teacher_artifacts(args.gemma4),
        "qwen3guard_stream": load_teacher_artifacts(args.qwen3guard_stream),
    }
    accepted, review, audit = resolve_teacher_corpus(
        records,
        artifacts,
        policy,
        validation_percent=args.validation_percent,
    )
    validate_corpus(accepted)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    for split in ("train", "validation"):
        write_jsonl(
            output / f"attack-teacher-accepted-{split}.jsonl",
            [record for record in accepted if record.split == split],
        )
    if args.drop_review:
        # ★ 조용히 버리지 않는다. 큐 파일은 그대로 쓰고 폐기 사실을 감사에 남긴다.
        (output / "attack-teacher-dropped.jsonl").write_text("", encoding="utf-8")
        write_jsonl(output / "attack-teacher-dropped.jsonl", review)
        review_path = output / "attack-teacher-dropped.jsonl"
    else:
        write_jsonl(output / "attack-teacher-review.jsonl", review)
        review_path = output / "attack-teacher-review.jsonl"
    (output / "teacher-resolution-audit.json").write_text(
        json.dumps(
            {
                "human_review": not args.drop_review,
                "accepted": len(accepted),
                "unresolved": len(review),
                "unresolved_path": str(review_path),
                "rows": audit,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    disposition = "dropped" if args.drop_review else "review"
    print(f"accepted={len(accepted)} {disposition}={len(review)} -> {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
