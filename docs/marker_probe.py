"""기밀 표식 — 단순 substring vs 위치 가중치 비교 (DESIGN.md §2.5 검증).

⚠️ 이 스크립트는 **설계 방식**을 검증한다. 배포 코드를 검증하지 않는다.
아래 `weighted()` 는 참조 구현이고 `garim/detect/rules/markers.py` 와 별개다.
그 둘이 갈라진 적이 있다 — 프로브가 100%/0% 로 통과하는 동안 배포 코드는
사전 22개가 빠진 채 60%/40% 였다. 배포 코드의 회귀는 `tests/test_markers.py`
가 잡는다. 여기서는 garim 을 import 할 수 있으면 두 구현의 일치까지 확인한다.
"""
import re
MARKERS = ["대외비","기밀","영업비밀","극비","사내한","취급주의","CONFIDENTIAL","INTERNAL USE ONLY"]
MARK_RE = re.compile("|".join(map(re.escape, MARKERS)), re.I)

def naive(text): return bool(MARK_RE.search(text))

def weighted(text, thr=3):
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        s = ln.strip()
        for m in MARK_RE.finditer(s):
            tok, score = m.group(0), 0
            if i < 3: score += 2
            if s == tok or re.fullmatch(rf"[\[\(「【]?\s*{re.escape(tok)}\s*[\]\)」】]?", s): score += 3
            if re.search(rf"(보안\s*등급|분류)\s*[:：]\s*{re.escape(tok)}", s): score += 3
            if re.match(r"(인데|라서|이라|입니다|였|니까|지만)", s[m.end():m.end()+2]): score -= 3
            if score >= thr: return True
    return False

if __name__ == "__main__":
    CASES = [("대외비\n\n2026년 3분기 사업계획", True), ("「대외비」\n첨부: 원가 분석표", True),
     ("보안등급: 기밀\n프로젝트 개요", True), ("CONFIDENTIAL\nQ3 Financial Summary", True),
     ("영업비밀\n제조 공정 상세", True), ("이거 대외비인데 물어봐도 될까요?", False),
     ("그 자료 기밀이라서 공유가 어렵습니다", False), ("대외비 처리 절차가 궁금합니다", False),
     ("기밀 유지 서약서 양식을 찾고 있어요", False), ("일반 문의드립니다. 배송이 언제 되나요?", False)]
    pos = sum(1 for _, g in CASES if g); neg = len(CASES) - pos
    n_tp = sum(naive(t) for t,g in CASES if g);  n_fp = sum(naive(t) for t,g in CASES if not g)
    w_tp = sum(weighted(t) for t,g in CASES if g); w_fp = sum(weighted(t) for t,g in CASES if not g)
    print(f"단순 substring : 재현율 {n_tp/pos:.0%}  오탐율 {n_fp/neg:.0%}")
    print(f"위치 가중치     : 재현율 {w_tp/pos:.0%}  오탐율 {w_fp/neg:.0%}")

    try:
        from garim.detect.rules.markers import detect_markers
    except ImportError:
        print("배포 코드 대조  : 건너뜀 (garim 미설치 — tests/test_markers.py 가 담당)")
    else:
        shipped = [bool(detect_markers(t)) for t, _ in CASES]
        s_tp = sum(h for h, (_, g) in zip(shipped, CASES) if g)
        s_fp = sum(h for h, (_, g) in zip(shipped, CASES) if not g)
        print(f"배포 코드      : 재현율 {s_tp/pos:.0%}  오탐율 {s_fp/neg:.0%}")
        drift = [t for h, (t, _) in zip(shipped, CASES) if h != weighted(t)]
        if drift:
            raise SystemExit(f"참조 구현과 배포 코드가 갈라졌다: {drift}")
        print("참조/배포 일치  : 전건 통과")
