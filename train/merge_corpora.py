#!/usr/bin/env python3
"""Merge compatible JSONL shards while removing exact train/eval text leakage."""

from __future__ import annotations

import argparse

from garim.training.data import load_jsonl, validate_corpus, write_jsonl
from garim.training.split import fingerprint_text


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", action="append", required=True)
    parser.add_argument("--blocked-eval", action="append", default=[])
    parser.add_argument(
        "--blocked-other-task",
        action="append",
        default=[],
        help="다른 태스크 코퍼스(공격 풀이면 PII train/validation). 같은 문장을 빼낸다",
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--task", choices=("pii", "attack"), required=True)
    parser.add_argument("--split", choices=("train", "validation", "test"), required=True)
    parser.add_argument("--require-labels", action="store_true")
    args = parser.parse_args()

    blocked = {
        fingerprint_text(record.text)
        for path in args.blocked_eval
        for record in load_jsonl(path)
    }
    # ★ 회귀 — `네, 맞습니다.` 같은 일반 발화가 KDPII(스팬 없음, train·validation 양쪽)와
    #   PolyGuardMix 에 같이 있었다. PII 행끼리는 `validate_corpus` 가 허용하지만 공격 행은
    #   라벨을 지므로 분할을 넘는 순간 거부된다. 옛 봉인에는 PolyGuard 가 없어 드러나지
    #   않다가, RunPod 리허설 8단계(학습 봉인)가 6행에서 멈췄다. 교사에게 넘기기 **전에**
    #   공격 풀에서 뺀다 — 교사 라벨을 받은 뒤 빼면 그만큼 GPU 를 버린다.
    cross_task = {
        fingerprint_text(record.text)
        for path in args.blocked_other_task
        for record in load_jsonl(path)
    }
    seen: set[str] = set()
    merged = []
    dropped_eval = 0
    dropped_cross_task = 0
    dropped_duplicate = 0
    for path in args.input:
        for record in load_jsonl(path):
            if record.task != args.task or record.split != args.split:
                raise ValueError(f"{record.id}: incompatible task/split in {path}")
            if args.require_labels and record.task == "attack" and record.attack_label is None:
                raise ValueError(f"{record.id}: unresolved attack label")
            fingerprint = fingerprint_text(record.text)
            if fingerprint in blocked:
                dropped_eval += 1
                continue
            if fingerprint in cross_task:
                dropped_cross_task += 1
                continue
            if fingerprint in seen:
                dropped_duplicate += 1
                continue
            seen.add(fingerprint)
            merged.append(record)
    validate_corpus(merged)
    write_jsonl(args.output, merged)
    print(
        f"merged={len(merged)} dropped_eval={dropped_eval} "
        f"dropped_cross_task={dropped_cross_task} "
        f"dropped_duplicate={dropped_duplicate} -> {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
