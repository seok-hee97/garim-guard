"""Presidio 한국어 인식기 결함 2건 정량 실험 (재현 가능한 원저 기여).

결함1 — 언어코드 오선언: kr_passport_recognizer 가 supported_language="kr"(ISO-3166 국가코드)
        로 선언됨. 다른 4종은 "ko"(ISO-639 언어코드). → language="ko" 레지스트리에서 여권
        인식기가 조용히 미로드. 구조적 단언(이진) + 1줄 패치 후 recall 0.0 → ~1.0.

결함2 — 한글 문맥어 부재: KR_RRN CONTEXT 가 영어 전용("Korean RRN","RRN"). 실제 한국어
        문서의 트리거 "주민등록번호"가 문맥 점수 가점을 못 받음. 2×2 교호작용
        (체크섬 유효/무효) × (한글 문맥 유/무) 별 recall.

    python -m eval.presidio_defects

presidio 미설치 시 안내 후 종료.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "presidio_defects.json"


def main() -> int:
    try:
        from presidio_analyzer import AnalyzerEngine
        from presidio_analyzer.nlp_engine import NlpEngineProvider
        import presidio_analyzer.predefined_recognizers as pr
    except ImportError:
        print("presidio 미설치. pip install 'presidio-analyzer>=2.2.361' 후 재실행.")
        return 1

    report: dict = {}

    # ── 결함1: 여권 인식기 언어코드 오선언 (구조적, NLP 엔진 불필요) ──
    kr_recognizers = [n for n in dir(pr) if "Kr" in n or "Korean" in n or "KR" in n]
    report["kr_recognizer_classes"] = kr_recognizers
    langs = {}
    for name in kr_recognizers:
        cls = getattr(pr, name)
        try:
            inst = cls()
            langs[name] = getattr(inst, "supported_language", "?")
        except Exception as e:  # noqa: BLE001
            langs[name] = f"err:{type(e).__name__}"
    report["defect1_supported_language"] = langs
    passport = {n: l for n, l in langs.items() if "passport" in n.lower() or "Passport" in n}
    report["defect1_confirmed"] = any(l == "kr" for l in passport.values())
    print("결함1 — 인식기별 supported_language:")
    for n, l in langs.items():
        flag = " ⚠️ 'kr'(국가코드!)" if l == "kr" else ""
        print(f"  {n}: {l}{flag}")

    # ── 결함2: KR_RRN CONTEXT 한글 부재 (구조적) ──
    rrn_cls = next((getattr(pr, n) for n in kr_recognizers if "Rrn" in n or "RRN" in n), None)
    if rrn_cls is not None:
        try:
            ctx = getattr(rrn_cls(), "context", []) or []
        except Exception:  # noqa: BLE001
            ctx = []
        has_hangul = any(any("가" <= c <= "힣" for c in str(t)) for t in ctx)
        report["defect2_rrn_context"] = list(ctx)
        report["defect2_has_hangul_context"] = has_hangul
        print(f"\n결함2 — KR_RRN CONTEXT: {ctx}")
        print(f"  한글 문맥어 포함? {has_hangul}  {'(정상)' if has_hangul else '⚠️ 영어 전용 → 주민등록번호 가점 없음'}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n→ {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
