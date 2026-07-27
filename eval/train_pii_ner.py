"""R6 — 한국어 PII NER 파인튜닝 (klue/roberta-small 토큰분류). RunPod GPU.

⚠️ torch/transformers 필요. 로컬 미실행(지연 임포트).

검증 반영:
 - openpii ko 슬라이스를 parquet 변환 브랜치에서 DuckDB 로 로드(4.27GB 풀다운 회피).
 - privacy_mask(스팬 라벨) → BIO. 학습 대상 9개체만, 로케일 오류 3종 제외.
 - 서브워드 정렬: 첫 서브워드에만 라벨, 나머지 -100(loss 무시).
 - klue/roberta-small 라이선스 미명시 → 가중치 재배포 금지, 수치만 보고.
"""

from __future__ import annotations

import json

TRAIN_ENTITIES = ["GIVENNAME", "SURNAME", "CITY", "STREET", "BUILDINGNUM",
                  "ZIPCODE", "EMAIL", "DATE", "AGE"]
EXCLUDE = {"SOCIALNUM", "TELEPHONENUM", "IDCARDNUM"}
BASE_MODEL = "klue/roberta-small"
HP = dict(lr=3e-5, epochs=3, batch=16, max_len=256, subsample=20000, seed=42)

LABELS = ["O"] + [f"{p}-{e}" for e in TRAIN_ENTITIES for p in ("B", "I")]
L2I = {l: i for i, l in enumerate(LABELS)}


def _load_openpii_ko(n: int):
    import duckdb
    base = ("hf://datasets/ai4privacy/pii-masking-openpii-1.5m"
            "@refs%2Fconvert%2Fparquet/default")
    con = duckdb.connect(); con.execute("INSTALL httpfs; LOAD httpfs;")
    rows = con.execute(
        f"SELECT source_text, privacy_mask FROM read_parquet('{base}/train/*.parquet') "
        f"WHERE language='ko' LIMIT {n}").fetchall()
    return rows


def _char_bio(text: str, spans: list[dict]) -> list[tuple[int, int, str]]:
    """privacy_mask 스팬 → (start,end,label) 리스트 (학습 대상 개체만)."""
    out = []
    for s in spans or []:
        lab = s.get("label")
        if lab in EXCLUDE or lab not in TRAIN_ENTITIES:
            continue
        st, en = s.get("start"), s.get("end")
        if isinstance(st, int) and isinstance(en, int) and en > st:
            out.append((st, en, lab))
    return sorted(out)


def main() -> int:
    try:
        import numpy as np
        import torch  # noqa: F401
        from transformers import (AutoModelForTokenClassification, AutoTokenizer,
                                   DataCollatorForTokenClassification, Trainer, TrainingArguments)
        from datasets import Dataset
    except ImportError as e:
        print(f"GPU 스택 미설치({e}). RunPod: pip install torch transformers datasets duckdb")
        return 1

    rows = _load_openpii_ko(HP["subsample"])
    print(f"openpii ko 로드: {len(rows)}행")
    tok = AutoTokenizer.from_pretrained(BASE_MODEL)

    def encode(text: str, spans_json):
        spans = spans_json if isinstance(spans_json, list) else json.loads(spans_json or "[]")
        char_spans = _char_bio(text, spans)
        enc = tok(text, truncation=True, max_length=HP["max_len"], return_offsets_mapping=True)
        labels = []
        for (cs, ce) in enc["offset_mapping"]:
            if cs == ce:                     # 특수토큰
                labels.append(-100); continue
            tag = "O"
            for (ss, se, lab) in char_spans:
                if cs >= ss and ce <= se:
                    tag = ("B-" if cs == ss else "I-") + lab
                    break
            labels.append(L2I.get(tag, 0))
        enc.pop("offset_mapping")
        enc["labels"] = labels
        return enc

    data = [encode(t, m) for t, m in rows]
    ds = Dataset.from_list(data).train_test_split(test_size=0.2, seed=HP["seed"])

    model = AutoModelForTokenClassification.from_pretrained(
        BASE_MODEL, num_labels=len(LABELS),
        id2label={i: l for l, i in L2I.items()}, label2id=L2I)

    def metrics(eval_pred):
        from seqeval.metrics import f1_score, precision_score, recall_score
        logits, labels = eval_pred
        preds = np.argmax(logits, axis=-1)
        tp, tl = [], []
        for p_row, l_row in zip(preds, labels):
            pp, ll = [], []
            for p, l in zip(p_row, l_row):
                if l != -100:
                    pp.append(LABELS[p]); ll.append(LABELS[l])
            tp.append(pp); tl.append(ll)
        return {"span_precision": precision_score(tl, tp), "span_recall": recall_score(tl, tp),
                "span_f1": f1_score(tl, tp)}

    args = TrainingArguments(
        output_dir="train/artifacts/r6-pii-ner", learning_rate=HP["lr"],
        per_device_train_batch_size=HP["batch"], per_device_eval_batch_size=32,
        num_train_epochs=HP["epochs"], eval_strategy="epoch", seed=HP["seed"],
        logging_steps=50, report_to=[])
    trainer = Trainer(model=model, args=args, train_dataset=ds["train"], eval_dataset=ds["test"],
                      tokenizer=tok, data_collator=DataCollatorForTokenClassification(tok),
                      compute_metrics=metrics)
    trainer.train()
    ev = trainer.evaluate()
    print("R6 span eval:", {k: round(v, 4) for k, v in ev.items() if "span" in k})
    json.dump({"model": BASE_MODEL, "entities": TRAIN_ENTITIES, "hparams": HP,
               "eval": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in ev.items()}},
              open("results/r6_pii_ner.json", "w"), ensure_ascii=False, indent=2)
    print("비교(별도): Presidio-ko(vault_presidio, 결함패치) / vmaca123 개체 교집합.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
