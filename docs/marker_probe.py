"""기밀 표식 — 단순 substring vs 위치 가중치 비교 (DESIGN.md §2.5 검증)."""
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
