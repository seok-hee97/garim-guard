"""D0 데이터 스파이크 — 모든 외부 데이터셋을 실제로 뚫고 행 수를 **실측**한다.

카드/API 수치를 인용하지 않는다. `len()` 으로 직접 센 값만 manifest 에 남긴다.
(카드와 API 가 불일치하는 사례가 이미 확인되었다.)

    python -m eval.data_spike              # 전체
    python -m eval.data_spike --skip-heavy # openpii(4.27GB) 제외
    python -m eval.data_spike --only maps

산출: data/manifest.json — 기획서 3장(데이터 출처·라이선스·분할)이 이 파일에서 생성된다.
"""

from __future__ import annotations

import argparse
import json
import time
import traceback
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data" / "manifest.json"

# 라이선스는 2026-07-20 HF API + 카드 직접 조회로 확인한 값.
# ⚠️ 표기가 "none" 인 것은 퍼블릭 도메인이 아니라 all rights reserved 다.
LICENSES = {
    "nemotron_ipi": "cc-by-4.0",
    "maps_ko": "cc-by-4.0",
    "openpii_ko": "other (license_name=cc-by-4.0, link=null) ⚠️약관 직접 확인 필요",
    "kmhas": "cc-by-sa-4.0",
    "klue_ner": "cc-by-sa-4.0",
    "deepset_pi": "apache-2.0",
    "xstest_ko": "⚠️ NONE DECLARED — 수치 보고만, 재배포 금지",
}


# ─────────────────────────────────────────────────────────────────────────
# 개별 로더 — 각자 (행수 dict, 메모 dict) 를 반환한다
# ─────────────────────────────────────────────────────────────────────────


def load_nemotron() -> tuple[dict, dict]:
    """인젝션이 도구 출력(environment)에 있는 유일한 공개셋."""
    from datasets import load_dataset

    ds = load_dataset("nvidia/Nemotron-RL-Agentic-Indirect-Prompt-Injection-v1")
    counts = {k: len(v) for k, v in ds.items()}
    row = ds["train"][0]

    # 음성 클래스가 정말 0건인지 직접 센다 (카드 주장 검증)
    n_with_injection = sum(1 for r in ds["train"] if r.get("injection"))
    vectors = {}
    for r in ds["train"]:
        v = r.get("injection_vector")
        vectors[v] = vectors.get(v, 0) + 1

    return counts, {
        "columns": list(row.keys()),
        "n_with_injection": n_with_injection,
        "n_clean_negatives": counts["train"] - n_with_injection,
        "n_distinct_injection_vectors": len(vectors),
        "injection_keys": list(row["injection"].keys()) if row.get("injection") else None,
        "note": "음성(정상 도구출력) 0건이면 FPR 은 이 데이터셋만으로 산출 불가 — 자체 채굴 필요",
    }


def load_maps() -> tuple[dict, dict]:
    """한국어 ASB 공격 명세. load_dataset(split=) 이 아니라 파일 직독."""
    from huggingface_hub import hf_hub_download

    out, notes = {}, {}
    for fname in ("all_attack_tools.jsonl", "agent_task.jsonl"):
        p = hf_hub_download(
            repo_id="Fujitsu-FRE/MAPS",
            filename=f"data/korean/asb/{fname}",
            repo_type="dataset",
        )
        rows = [json.loads(l) for l in Path(p).read_text(encoding="utf-8").splitlines() if l.strip()]
        out[fname] = len(rows)
        if rows:
            notes[fname] = {"columns": list(rows[0].keys())}

    # ⚠️ 부분 번역 실측: Attacker Instruction 만 한국어이고 Description/Attack goal 은 영문
    attacks = out.get("all_attack_tools.jsonl", 0)
    if attacks:
        p = hf_hub_download(
            repo_id="Fujitsu-FRE/MAPS",
            filename="data/korean/asb/all_attack_tools.jsonl",
            repo_type="dataset",
        )
        rows = [json.loads(l) for l in Path(p).read_text(encoding="utf-8").splitlines() if l.strip()]

        def has_hangul(s: Any) -> bool:
            return isinstance(s, str) and any("가" <= c <= "힣" for c in s)

        notes["localization"] = {
            f: round(sum(has_hangul(r.get(f)) for r in rows) / len(rows), 3)
            for f in rows[0]
            if isinstance(rows[0].get(f), str)
        }
        notes["localization_note"] = "필드별 한글 포함 비율. 1.0 미만은 영문 잔존 = 부분 번역"

    return out, notes


def load_openpii_ko() -> tuple[dict, dict]:
    """DuckDB 로 language='ko' 만 푸시다운.

    ⚠️ 원 저장소는 parquet 이 아니라 data/{train,validation}.jsonl 이다(총 4.27GB).
       JSONL 은 컬럼 푸시다운이 불가능하므로 HF 자동 변환 parquet 브랜치를 쓴다.
       revision 은 URL 인코딩 필요: refs/convert/parquet → refs%2Fconvert%2Fparquet
    """
    import duckdb

    base = (
        "hf://datasets/ai4privacy/pii-masking-openpii-1.5m"
        "@refs%2Fconvert%2Fparquet/default"
    )
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")

    counts, entities = {}, {}
    for split in ("train", "validation"):
        n = con.execute(
            f"SELECT COUNT(*) FROM read_parquet('{base}/{split}/*.parquet') WHERE language='ko'"
        ).fetchone()[0]
        counts[split] = int(n)

    # 한국어 슬라이스의 개체 분포 — R6 학습 대상(9종) 확정과
    # SOCIALNUM/TELEPHONENUM 로케일 오류 확인의 근거.
    # UNNEST 는 SELECT 절에 직접 쓸 수 없으므로 서브쿼리로 감싼다.
    # privacy_mask 가 parquet 에서 네이티브 리스트일 수도, JSON 문자열일 수도 있어 둘 다 시도한다.
    variants = [
        ("native", f"SELECT UNNEST(privacy_mask).label AS lbl FROM read_parquet('{base}/train/*.parquet') WHERE language='ko'"),
        ("json", f"SELECT UNNEST(json_extract_string(privacy_mask, '$[*].label')) AS lbl FROM read_parquet('{base}/train/*.parquet') WHERE language='ko'"),
    ]
    for how, inner in variants:
        try:
            rows = con.execute(
                f"SELECT lbl, COUNT(*) AS n FROM ({inner}) GROUP BY lbl ORDER BY n DESC"
            ).fetchall()
            entities = {"_method": how, **{str(k): int(v) for k, v in rows}}
            break
        except Exception as e:  # noqa: BLE001 — 개체 분포는 부가 정보, 실패해도 행수는 유효
            entities = {"_error": f"{type(e).__name__}: {str(e)[:200]}"}

    return counts, {
        "method": "duckdb parquet 변환 브랜치 푸시다운 (WHERE language='ko')",
        "entity_distribution": entities,
        "note": "전량 사용. 26k 규모는 서브샘플할 크기가 아니다. "
                "SOCIALNUM/TELEPHONENUM/IDCARDNUM 은 한국 로케일 오류로 학습·평가 제외.",
    }


def load_kmhas() -> tuple[dict, dict]:
    """다중라벨. label 이 리스트인지 반드시 확인 — softmax 로 짜면 조용히 틀린다.

    ⚠️ 이 저장소는 로딩 스크립트(kmhas_korean_hate_speech.py) 기반인데
       datasets>=4 가 스크립트 지원을 제거했다. HF 자동 변환 parquet 브랜치를 쓴다.
    """
    from datasets import load_dataset

    ds = load_dataset("jeanlee/kmhas_korean_hate_speech", revision="refs/convert/parquet")
    counts = {k: len(v) for k, v in ds.items()}
    row = ds["train"][0]
    feat = ds["train"].features["label"]
    return counts, {
        "columns": list(row.keys()),
        "label_type": str(type(row["label"]).__name__),
        "label_is_list": isinstance(row["label"], list),
        "label_feature": str(feat),
        "n_classes": len(getattr(getattr(feat, "feature", None), "names", []) or []),
        "sample_label": row["label"],
        "load_path": "revision='refs/convert/parquet' (원 저장소는 스크립트 기반, datasets>=4 에서 로드 불가)",
        "note": "label 이 리스트면 BCEWithLogits + 라벨별 임계값 필수",
    }


def load_xstest_ko() -> tuple[dict, dict]:
    """과차단 프로브. split 명이 test 다 (train 아님)."""
    from datasets import load_dataset

    ds = load_dataset("heegyu/XSTest_ko")
    counts = {k: len(v) for k, v in ds.items()}
    split = next(iter(ds))
    row = ds[split][0]
    labels: dict[str, int] = {}
    for r in ds[split]:
        labels[str(r.get("label"))] = labels.get(str(r.get("label")), 0) + 1
    types: dict[str, int] = {}
    for r in ds[split]:
        types[str(r.get("type"))] = types.get(str(r.get("type")), 0) + 1
    return counts, {
        "columns": list(row.keys()),
        "label_breakdown": labels,
        "n_types": len(types),
        "types": types,
        "note": "figurative_language 는 번역으로 중의성이 소실됨 — D8 원어민 감수 대상",
    }


def load_klue_ner() -> tuple[dict, dict]:
    from datasets import load_dataset

    ds = load_dataset("klue/klue", "ner")
    counts = {k: len(v) for k, v in ds.items()}
    row = ds["train"][0]
    tags = ds["train"].features["ner_tags"].feature.names
    return counts, {"columns": list(row.keys()), "n_tags": len(tags), "tags": tags,
                    "note": "PII 개체 없음 — 보조 확인용, 학습셋 아님"}


def load_deepset() -> tuple[dict, dict]:
    from datasets import load_dataset

    ds = load_dataset("deepset/prompt-injections")
    counts = {k: len(v) for k, v in ds.items()}
    return counts, {"columns": list(ds["train"][0].keys()), "note": "단일 발화 스모크 전용"}


LOADERS: dict[str, tuple[Callable[[], tuple[dict, dict]], bool]] = {
    # name: (loader, is_heavy)
    "nemotron_ipi": (load_nemotron, False),
    "maps_ko": (load_maps, False),
    "kmhas": (load_kmhas, False),
    "xstest_ko": (load_xstest_ko, False),
    "klue_ner": (load_klue_ner, False),
    "deepset_pi": (load_deepset, False),
    "openpii_ko": (load_openpii_ko, True),
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-heavy", action="store_true", help="openpii(대용량) 제외")
    ap.add_argument("--only", help="특정 데이터셋만")
    args = ap.parse_args()

    # 기존 매니페스트를 이어받는다. --only / --skip-heavy 로 부분 실행해도
    # 이전에 실측한 항목이 사라지지 않아야 한다.
    manifest: dict[str, Any] = {
        "generated_for": "기획서 3장 (데이터 출처·라이선스·분할)",
        "method": "로컬 len() 실측 — 카드/API 수치 인용 금지",
        "datasets": {},
    }
    if MANIFEST.exists():
        try:
            prev = json.loads(MANIFEST.read_text(encoding="utf-8"))
            manifest["datasets"] = prev.get("datasets", {})
        except Exception:  # noqa: BLE001 — 손상된 매니페스트는 무시하고 새로 만든다
            pass
    failures = []

    for name, (fn, heavy) in LOADERS.items():
        if args.only and args.only != name:
            continue
        if args.skip_heavy and heavy:
            print(f"⏭  {name} (heavy, skipped)")
            continue

        print(f"⏳ {name} …", flush=True)
        t0 = time.perf_counter()
        try:
            counts, notes = fn()
            entry = {
                "ok": True,
                "license": LICENSES.get(name, "?"),
                "rows": counts,
                "total": sum(counts.values()),
                "load_s": round(time.perf_counter() - t0, 1),
                **notes,
            }
            print(f"✅ {name}: {counts} (총 {entry['total']:,}, {entry['load_s']}s)")
        except Exception as e:  # noqa: BLE001 — 실패도 manifest 에 남긴다
            entry = {
                "ok": False,
                "license": LICENSES.get(name, "?"),
                "error": f"{type(e).__name__}: {e}",
                "traceback": traceback.format_exc()[-800:],
                "load_s": round(time.perf_counter() - t0, 1),
            }
            failures.append(name)
            print(f"❌ {name}: {type(e).__name__}: {e}")
        manifest["datasets"][name] = entry

    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    ok = sum(1 for v in manifest["datasets"].values() if v["ok"])
    print(f"관통 {ok}/{len(manifest['datasets'])} · 매니페스트: {MANIFEST.relative_to(ROOT)}")
    if failures:
        print(f"실패: {', '.join(failures)}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
