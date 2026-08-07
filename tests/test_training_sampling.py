from __future__ import annotations

from garim.training.sampling import interleave_task_batches


def test_task_batches_follow_four_to_one_ratio_and_restart_attack_loader() -> None:
    batches = list(
        interleave_task_batches(
            ["p1", "p2", "p3", "p4", "p5", "p6", "p7", "p8"],
            lambda: ["a1"],
        )
    )
    assert batches == ["p1", "p2", "p3", "p4", "a1", "p5", "p6", "p7", "p8", "a1"]
