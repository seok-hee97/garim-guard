#!/usr/bin/env python3
"""§5.4 교사 라벨링 입력 — 생성기가 붙인 4-way 라벨을 벗겨 낸다.

`run_teacher.py` 는 라벨이 있는 행을 거부한다.

    if any(record.task != "attack" or record.attack_label is not None for record in records):
        raise ValueError("teacher input must contain only unlabeled attack records")

실수가 아니다. §5.4 의 흐름은 생성기의 1,800행이 **라벨 없는 "기본 공격 텍스트"** 이고
4-way 라벨은 교사 3종이 붙이는 것이다. 생성기가 라벨을 직접 넣는 것은 L1 학습을 먼저
돌려 보기 위한 편의였고, 그 라벨은 아무 외부 근거가 없다.

## 원본 라벨을 버리지 않고 곁에 둔다

벗긴 라벨을 따로 적어 둔다. 그러면 교사가 붙인 라벨과 대조해 **세 독립 모델이 우리
생성기의 4-way 라벨에 동의하는지**를 잴 수 있다 — 자작 코퍼스의 타당성을 외부 모델로
확인하는 기회이고, 지금은 정답을 갖고 있으므로 추가 비용이 0이다. 일치도가 낮으면
공격 코퍼스 전체를 의심해야 한다(§7.6-3).

⚠️ 이 사이드카는 **교사에게 주지 않는다.** 정답을 보여 주면 일치도가 아니라 정답
복사를 재게 된다.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from garim.training.data import load_jsonl, write_jsonl


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", action="append", required=True)
    parser.add_argument("--output", required=True, help="라벨 없는 교사 입력 JSONL")
    parser.add_argument("--gold", required=True, help="벗겨 낸 라벨 사이드카 JSON")
    args = parser.parse_args()

    records = [record for path in args.input for record in load_jsonl(path)]
    attack_rows = [record for record in records if record.task == "attack"]
    if len(attack_rows) != len(records):
        raise ValueError("teacher input must contain only attack-task records")

    gold: dict[str, str] = {}
    unlabeled = []
    for record in attack_rows:
        if record.attack_label is None:
            raise ValueError(f"{record.id}: already unlabeled — nothing to strip")
        gold[record.id] = record.attack_label
        # 라벨만 벗긴다. 보조 타깃(로마자·존대)은 뮤테이터가 아는 값이라 그대로 둔다 —
        # 교사가 판정하는 것은 4-way 의도이고 그 둘은 §2.2.3 이 축 밖으로 뺀 것이다.
        unlabeled.append(record.model_copy(update={"attack_label": None}))

    count = write_jsonl(Path(args.output), unlabeled)
    Path(args.gold).parent.mkdir(parents=True, exist_ok=True)
    Path(args.gold).write_text(
        json.dumps(gold, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {count} unlabeled attack rows -> {args.output}")
    print(f"kept {len(gold)} generator labels -> {args.gold} (교사에게 주지 않는다)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
