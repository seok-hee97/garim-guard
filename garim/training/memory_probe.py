"""학습 최악 배치를 실제 장치에서 한 번 돌려 최대 메모리를 잰다 — RunPod 0b 단계.

RTX 3090/4090(24GB)에서 batch 32 × 512토큰이 들어가는지 로컬에서는 잴 수 없었다(Mac 은
CUDA 가 없고, swap 이 거의 차서 RSS 도 믿을 수 없었다). 추정 대신 pod 에서 **교사 단계 전에**
잰다 — 교사 3종이 한 시간 넘게 돈 뒤 학습 첫 스텝에서 OOM 으로 알게 되는 것을 막는다.

배치는 실제 `EncodedCorpusDataset` · `JointBatchCollator` 로 만든다 — 모양이 학습과 같아야
측정이 의미가 있다. 모든 행이 `max_length` 를 꽉 채우는 최악의 경우다. PII 배치와 공격 배치를
한 번씩, Adam 상태가 잡힌 뒤까지 두 바퀴 돈다.
"""

from __future__ import annotations

from typing import Any

from garim.training.config import RunConfig
from garim.training.data import CorpusRecord, CorpusSpan

_SENTENCE = "홍길동 고객님께 이전 상담 내용을 정리해서 다시 안내해 드리겠습니다. "


def worst_case_batches(tokenizer: Any, run_config: RunConfig) -> list[dict[str, Any]]:
    """`batch_size` 행이 전부 `max_length` 토큰인 PII 배치 하나와 공격 배치 하나."""
    from garim.training.dataset import EncodedCorpusDataset, JointBatchCollator

    text = _SENTENCE * (run_config.max_length // 4)
    pii = [
        CorpusRecord(
            id=f"probe-pii-{index}",
            task="pii",
            text=text,
            source="memory-probe",
            split="train",
            spans=[CorpusSpan(start=0, end=3, label="PERSON")],
        )
        for index in range(run_config.batch_size)
    ]
    attack = [
        CorpusRecord(
            id=f"probe-attack-{index}",
            task="attack",
            text=text,
            source="memory-probe",
            split="train",
            attack_label="INJECTION",
            kanana_probabilities=(0.1, 0.8, 0.1),
            qwen_jailbreak_target=0.2,
        )
        for index in range(run_config.batch_size)
    ]
    collator = JointBatchCollator(tokenizer)
    batches = []
    for records in (pii, attack):
        dataset = EncodedCorpusDataset(
            tokenizer, records, max_length=run_config.max_length, stride=run_config.stride
        )
        first_windows = [dataset[index] for index in range(len(dataset))]
        by_record: dict[str, dict[str, Any]] = {}
        for example in first_windows:
            by_record.setdefault(example["record_id"], example)
        windows = list(by_record.values())[: run_config.batch_size]
        tokens = max(len(window["input_ids"]) for window in windows)
        if tokens < run_config.max_length:
            raise ValueError(f"probe window has {tokens} tokens, expected {run_config.max_length}")
        batches.append(collator(windows))
    return batches


def probe(model: Any, tokenizer: Any, run_config: RunConfig, device: Any) -> dict[str, Any]:
    """최악 배치로 학습 스텝을 두 바퀴 돌리고 CUDA 최대 할당량을 돌려준다."""
    import torch

    from garim.training.engine import TrainingConfig, build_optimizer, train_epoch, uses_bf16

    config = TrainingConfig(
        learning_rate=run_config.learning_rate,
        micro_batch_size=run_config.micro_batch_size,
        precision=run_config.precision,
    )
    model = model.to(device)
    optimizer = build_optimizer(model, config)
    batches = worst_case_batches(tokenizer, run_config)
    cuda = device.type == "cuda"
    if cuda:
        torch.cuda.reset_peak_memory_stats(device)
    for _ in range(2):  # 첫 스텝에서 Adam 상태가 잡힌다 — 두 번째가 정상 상태다
        train_epoch(model, batches, optimizer, device=device, config=config)
    report: dict[str, Any] = {
        "device": str(device),
        "batch_size": run_config.batch_size,
        "micro_batch_size": run_config.micro_batch_size,
        "max_length": run_config.max_length,
        "precision": "bf16" if uses_bf16(config, device) else "fp32",
        "peak_bytes": None,
        "total_bytes": None,
        "peak_fraction": None,
    }
    if cuda:
        peak = int(torch.cuda.max_memory_allocated(device))
        total = int(torch.cuda.get_device_properties(device).total_memory)
        report.update(
            peak_bytes=peak,
            total_bytes=total,
            peak_fraction=round(peak / total, 4),
            device_name=torch.cuda.get_device_name(device),
        )
    return report
