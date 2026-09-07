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
    parser.add_argument(
        "--teacher",
        action="append",
        required=True,
        help="teacher-name=predictions.jsonl; repeat once for every policy teacher",
    )
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
    artifact_paths: dict[str, str] = {}
    for entry in args.teacher:
        name, separator, path = entry.partition("=")
        if not separator or not name or not path:
            raise ValueError(f"--teacher expects name=path, got {entry!r}")
        if name in artifact_paths:
            raise ValueError(f"duplicate teacher artifact: {name}")
        artifact_paths[name] = path
    expected = set(policy.teachers)
    if set(artifact_paths) != expected:
        raise ValueError(
            "teacher artifacts must match policy exactly; "
            f"missing={sorted(expected - set(artifact_paths))}, "
            f"extra={sorted(set(artifact_paths) - expected)}"
        )
    artifacts = {
        name: load_teacher_artifacts(path) for name, path in artifact_paths.items()
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
