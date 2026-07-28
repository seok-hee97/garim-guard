"""R7 — 한국어 유해성 레일 파인튜닝 (K-MHaS 다중라벨). RunPod GPU.

⚠️ torch/transformers 필요. 로컬 미실행(지연 임포트 → 안내 후 종료).

검증 반영 (workflow):
 - label 은 **가변길이 int 리스트** → **9차원 multi-hot 확장 필수**(idx8=not_hate).
 - problem_type="multi_label_classification" + **BCEWithLogitsLoss**(softmax 금지).
 - 추론: 라벨별 sigmoid + 라벨별 threshold. 메트릭 Macro-F1.
 - 로드: revision='refs/convert/parquet'(스크립트 로더 회피, datasets>=3 기본 비활성).
 - klue/roberta-base 라이선스 미명시 → 가중치 재배포 금지, 수치만 보고.
"""

from __future__ import annotations

import json
from pathlib import Path

LABELS = ["origin", "physical", "politics", "profanity", "age",
          "gender", "race", "religion", "not_hate_speech"]
BASE_MODEL = "klue/roberta-base"
HP = dict(lr=2e-5, epochs=3, batch=16, max_len=128, subsample=20000, seed=42)


def _multi_hot(label_list, n=len(LABELS)):
    v = [0.0] * n
    for i in label_list:
        if 0 <= int(i) < n:
            v[int(i)] = 1.0
    return v


def main() -> int:
    try:
        import numpy as np
        import torch
        from datasets import load_dataset
        from transformers import (AutoModelForSequenceClassification, AutoTokenizer,
                                   DataCollatorWithPadding, Trainer, TrainingArguments)
        import sklearn.metrics  # noqa: F401 — compute_metrics 가 지연 임포트하므로 학습 전 선검증
    except ImportError as e:
        print(f"GPU 스택 미설치({e}). RunPod: pip install torch transformers datasets scikit-learn")
        return 1

    ds = load_dataset("jeanlee/kmhas_korean_hate_speech", revision="refs/convert/parquet")
    tok = AutoTokenizer.from_pretrained(BASE_MODEL)

    def prep(split, n=0):
        d = ds[split]
        if n:
            d = d.select(range(min(n, len(d))))
        # ★ 원본 text/label 컬럼 제거 — 안 하면 가변길이 label 리스트가 콜레이터 텐서화를 깬다.
        d = d.map(lambda r: {**tok(r["text"], truncation=True, max_length=HP["max_len"]),
                             "labels": _multi_hot(r["label"])},
                  remove_columns=d.column_names)
        return d

    train = prep("train", HP["subsample"])
    test = prep("test")

    model = AutoModelForSequenceClassification.from_pretrained(
        BASE_MODEL, num_labels=len(LABELS), problem_type="multi_label_classification")

    def metrics(eval_pred):
        from sklearn.metrics import f1_score
        logits, labels = eval_pred
        probs = 1 / (1 + np.exp(-logits))
        preds = (probs >= 0.5).astype(int)
        return {"macro_f1": f1_score(labels, preds, average="macro", zero_division=0),
                "micro_f1": f1_score(labels, preds, average="micro", zero_division=0)}

    args = TrainingArguments(
        output_dir="train/artifacts/r7-kmhas", learning_rate=HP["lr"],
        per_device_train_batch_size=HP["batch"], per_device_eval_batch_size=32,
        num_train_epochs=HP["epochs"], eval_strategy="epoch", seed=HP["seed"],
        logging_steps=50, report_to=[])
    trainer = Trainer(model=model, args=args, train_dataset=train, eval_dataset=test,
                      tokenizer=tok, data_collator=DataCollatorWithPadding(tok),
                      compute_metrics=metrics)
    trainer.train()
    ev = trainer.evaluate()
    print("R7 eval:", ev)

    # ★ 저장 ★ — R6 와 대칭. (a) 산출 지표 JSON, (b) 모델 가중치(로컬 전용).
    #   klue/roberta-base 라이선스 미명시 → train/artifacts/ 는 gitignore, 재배포 금지·수치만 보고.
    out_dir = "train/artifacts/r7-kmhas"
    trainer.save_model(out_dir)        # config+가중치
    tok.save_pretrained(out_dir)       # 추론 재현용 토크나이저
    Path("results").mkdir(exist_ok=True)
    json.dump({"model": BASE_MODEL, "data": "K-MHaS", "labels": LABELS, "hparams": HP,
               "test_n": len(test),
               "macro_f1": round(float(ev.get("eval_macro_f1", 0.0)), 4),
               "micro_f1": round(float(ev.get("eval_micro_f1", 0.0)), 4),
               "artifact": out_dir},
              open("results/r7_harm.json", "w"), ensure_ascii=False, indent=2)
    print(f"→ results/r7_harm.json · 모델 {out_dir} (재배포 금지·로컬 전용)")
    print("비교: kanana-safeguard-8b 제로샷(7클래스 단일라벨 → 조야한 harm-vs-not 만). "
          "K-MHaS 9클래스 ↔ Kanana S1-7 매핑표 공개 + 손실성 명시.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
