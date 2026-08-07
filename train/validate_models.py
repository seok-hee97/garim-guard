#!/usr/bin/env python3
"""Validate pinned model/teacher manifests without downloading weights."""

from __future__ import annotations

from garim.training.models import load_model_registry
from garim.training.teachers import load_teacher_policy


def main() -> int:
    registry = load_model_registry()
    teachers = load_teacher_policy()
    registered = {(asset.model_id, asset.revision) for asset in registry.manifest.models}
    for teacher in teachers.teachers.values():
        if (teacher.model_id, teacher.revision) not in registered:
            raise ValueError(f"teacher is not pinned in models.yaml: {teacher.model_id}")
    print(
        f"OK: {len(registry.manifest.models)} pinned models, "
        f"{len(teachers.teachers)} class-scoped teachers"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
