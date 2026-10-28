#!/usr/bin/env python3
"""Apply reviewed Korean translations without changing the pre-split source IDs."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from garim.training.data import CorpusRecord, load_jsonl, validate_corpus, write_jsonl


def _translations(path: Path) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                record_id = str(row["id"])
                text = str(row["text"]).strip()
                method = str(row["method"]).strip()
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                raise ValueError(f"{path}:{line_number}: invalid translation row") from exc
            if not text or not method:
                raise ValueError(f"{path}:{line_number}: text and method are required")
            if record_id in result:
                raise ValueError(f"duplicate translation id: {record_id}")
            result[record_id] = {"text": text, "method": method}
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue", required=True)
    parser.add_argument("--translations", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    queue = load_jsonl(args.queue)
    translated = _translations(Path(args.translations))
    expected = {record.id for record in queue}
    missing, extra = expected - translated.keys(), translated.keys() - expected
    if missing or extra:
        raise ValueError(f"translation IDs mismatch: missing={len(missing)}, extra={len(extra)}")

    output: list[CorpusRecord] = []
    for record in queue:
        replacement = translated[record.id]
        meta = dict(record.meta)
        meta.update(
            {
                "requires_translation": False,
                "translation_method": replacement["method"],
                "original_text_sha256": hashlib.sha256(record.text.encode()).hexdigest(),
            }
        )
        output.append(record.model_copy(update={"text": replacement["text"], "meta": meta}))
    validate_corpus(output)
    count = write_jsonl(args.output, output)
    print(f"applied {count} split-preserving translations -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
