"""Nemotron IPI 에피소드 → (text, label) 인젝션 탐지 예제 변환기.

Nemotron 의 각 행은 (text,label) 쌍이 아니라 **에이전트 에피소드**다. 인젝션은
`environment` 의 중첩 필드(injection_vector 가 이름을 지정)에 박혀 있다.

핵심 설계 — 음성 클래스를 어디서 얻는가:
  - Nemotron 은 1,272행 전부 양성이고 깨끗한 도구출력이 0건이다.
  - 다른 코퍼스의 benign 텍스트를 음성으로 쓰면 **도메인 분류기**가 되어
    아름답고 무의미한 AUROC 가 나온다 (project_plan §6.2).
  - 따라서 **동일 필드에서 injection_text 만 제거한 정상 텍스트**를 음성으로 쓴다.
    양성과 음성이 같은 필드 분포에서 나오므로 분류기가 '주제'가 아니라
    '인젝션 유무'를 학습하게 된다.

분할: domain(9종) 기준 grouped 60/20/20. 랜덤 분할 금지 — 환경이 템플릿이라
같은 도메인 행이 train/test 에 흩어지면 근사중복 누수가 생긴다.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "kotip" / "v0.1"

# domain(9종)을 3개 그룹으로 나눠 grouped split 60/20/20 을 만든다.
# (같은 domain 은 한 split 에만 들어간다 → 템플릿 누수 차단)
_SPLIT_BY_DOMAIN = {
    # train (≈60%)
    "healthcare": "train", "legal": "train", "real_estate": "train",
    "logistics": "train", "ecommerce": "train",
    # dev (≈20%)
    "education": "dev", "hr": "dev",
    # test (≈20%)
    "it_helpdesk": "test", "insurance": "test",
}


def _find_field_containing(obj: Any, needle: str) -> str | None:
    """environment(중첩 dict/list) 안에서 needle 을 포함하는 문자열 값을 찾는다."""
    if isinstance(obj, str):
        return obj if needle and needle in obj else None
    if isinstance(obj, dict):
        for v in obj.values():
            hit = _find_field_containing(v, needle)
            if hit is not None:
                return hit
    elif isinstance(obj, list):
        for v in obj:
            hit = _find_field_containing(v, needle)
            if hit is not None:
                return hit
    return None


def project_row(row: dict[str, Any]) -> list[dict[str, Any]] | None:
    """한 에피소드 → [양성 예제, 음성 예제] 또는 None(추출 실패)."""
    inj = row.get("injection") or {}
    inj_text = (inj.get("injection_text") or "").strip()
    if not inj_text:
        return None

    field_val = _find_field_containing(row.get("environment"), inj_text)
    if field_val is None:
        return None  # 필드를 못 찾음 (드묾) → 스킵, 러너가 카운트

    clean = field_val.replace(inj.get("injection_text", ""), "").strip()
    meta = {
        "domain": row.get("domain"),
        "attack_category": row.get("attack_category"),
        "target_tool": row.get("target_tool"),
        "vector": row.get("injection_vector"),
        "split": _SPLIT_BY_DOMAIN.get(row.get("domain", ""), "train"),
        "source_id": row.get("id"),
    }
    examples = [
        {"text": field_val, "label": 1, "kind": "injected_field", **meta},
    ]
    # 음성: 정상 텍스트가 충분히 남을 때만 (제거 후 빈 문자열이면 음성 없음)
    if len(clean) >= 20:
        examples.append({"text": clean, "label": 0, "kind": "cleaned_field", **meta})
    return examples


def iter_examples(ds: Any) -> Iterator[dict[str, Any]]:
    for row in ds:
        got = project_row(row)
        if got:
            yield from got


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="상위 N행만 (0=전체)")
    ap.add_argument("--dry-run", action="store_true", help="파일 안 쓰고 통계만")
    args = ap.parse_args()

    from datasets import load_dataset

    ds = load_dataset("nvidia/Nemotron-RL-Agentic-Indirect-Prompt-Injection-v1")["train"]
    rows = list(ds)[: args.limit] if args.limit else list(ds)

    by_split: dict[str, list[dict]] = {"train": [], "dev": [], "test": []}
    n_rows = n_fail = n_pos = n_neg = 0
    for row in rows:
        n_rows += 1
        got = project_row(row)
        if not got:
            n_fail += 1
            continue
        for ex in got:
            by_split[ex["split"]].append(ex)
            if ex["label"] == 1:
                n_pos += 1
            else:
                n_neg += 1

    print(f"행 {n_rows} · 추출실패 {n_fail} · 양성 {n_pos} · 음성 {n_neg}")
    for s, items in by_split.items():
        pos = sum(1 for x in items if x["label"] == 1)
        print(f"  {s}: {len(items)}건 (양성 {pos} / 음성 {len(items)-pos})")

    if args.dry_run:
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for s, items in by_split.items():
        p = OUT_DIR / f"nemotron_rail.{s}.jsonl"
        with p.open("w", encoding="utf-8") as f:
            for ex in items:
                f.write(json.dumps(ex, ensure_ascii=False) + "\n")
        print(f"  → {p.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
