"""정규화본 스팬 → 원문 스팬 역매핑. 설계의 최대 구멍이었다.

각 정규화 단계가 '출력 문자 → 원문 구간 (start, end)'을 함께 낸다.
체인 끝에서 합성하면 정규화본의 어느 위치든 원문 구간을 찾을 수 있다.

── 초판이 틀렸던 두 가지 (여기서 정정한다) ──────────────────────
1) 점(point) 맵을 썼다. NFKC는 1:N 확장을 한다(㈜→(주), ℡→TEL).
   점 맵은 IndexError로 죽거나, 출력길이 기준으로 고치면 확장 지점 뒤
   모든 스팬이 조용히 밀린다. DLP에서는 크래시보다 나쁘다 —
   주민번호 앞자리가 평문으로 남는데 로그에는 '마스킹함'으로 찍힌다.
   → 구간(interval) 맵으로 바꿨다.

2) NFKC를 문자열 통째로 걸면 정준결합까지 일어나 한 단계가
   1:1·1:N·N:1을 동시에 한다. ㅁㅜㅅㅣㅎㅐ가 여기서 무시해가 되어
   ④ 자모 조합의 고정밀 신호를 삼키고, ㅎㅏㄴㄱㅜㄱ은 '하ᄂ구ᄀ'으로
   복원 불가능하게 망가진다.
   → 문자 단위로 걸고 호환 자모는 ④에 넘긴다.

3) 초판의 homoglyph 케이스는 정규화가 발동조차 하지 않았다.
   CONF에 소문자 키릴(U+0430)만 있는데 테스트는 대문자(U+0410)를 썼다.
   그 단계를 통째로 삭제해도 4/4가 통과했다. → 대문자를 표에 넣었다.
"""
import unicodedata as ud, re

ZW = set("​‌‍⁠﻿")
CONF = {"а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x", "і": "i",
        "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H",
        "О": "O", "Р": "P", "С": "C", "Т": "T", "Х": "X"}
CHO = list("ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ")
JUNG = list("ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ")
JONG = list("ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ")
COMPAT_JAMO = range(0x3131, 0x318F)


def ident(n):
    """길이 n의 항등 구간 맵."""
    return [(i, i + 1) for i in range(n)]


def step_nfkc(s):
    """① NFKC — ★문자 단위. 1:1 또는 1:N이고 절대 N:1이 아니다.
    호환 자모는 건드리지 않는다(④가 받는다)."""
    out, imap = [], []
    for i, c in enumerate(s):
        r = c if ord(c) in COMPAT_JAMO else ud.normalize("NFKC", c)
        out.append(r)
        imap.extend([(i, i + 1)] * len(r))   # 확장분 전부가 같은 원문 구간
    return "".join(out), imap


def step_drop(s, pred):
    """삭제. 남은 문자의 원 구간만 기록."""
    out, imap = [], []
    for i, c in enumerate(s):
        if not pred(c):
            out.append(c)
            imap.append((i, i + 1))
    return "".join(out), imap


def step_map1(s, table):
    """1:1 치환."""
    return "".join(table.get(c, c) for c in s), ident(len(s))


def step_jamo(s):
    """④ 자모 N:1 조합. 형태가 아니라 위치로 판정한다 —
    초성형 자음이 뒤에 중성 없이 오면 종성으로 해석한다."""
    out, imap, i = [], [], 0
    while i < len(s):
        if s[i] in CHO and i + 1 < len(s) and s[i + 1] in JUNG:
            n = 2
            jong = 0
            # 다음 자음이 '뒤에 중성을 달지 않으면' 종성이다
            if (i + 2 < len(s) and s[i + 2] in JONG
                    and not (i + 3 < len(s) and s[i + 3] in JUNG)):
                jong = JONG.index(s[i + 2]) + 1
                n = 3
            out.append(chr(0xAC00 + CHO.index(s[i]) * 588
                           + JUNG.index(s[i + 1]) * 28 + jong))
            imap.append((i, i + n))          # 첫 자모 ~ 마지막 자모
            i += n
        else:
            out.append(s[i])
            imap.append((i, i + 1))
            i += 1
    return "".join(out), imap


def compose(a, b):
    """a: src→mid 구간맵, b: mid→out 구간맵 → src→out 구간맵"""
    return [(min(a[j][0] for j in range(ms, me)),
             max(a[j][1] for j in range(ms, me))) for ms, me in b]


def normalize_with_map(text):
    s, imap = step_nfkc(text)
    for fn in (lambda x: step_drop(x, lambda c: c in ZW),
               lambda x: step_map1(x, CONF),
               step_jamo):
        s, m = fn(s)
        imap = compose(imap, m)
    return s, imap


def to_source_span(norm_span, imap, src_len):
    """정규화본 스팬 → 원문 스팬. 항상 원문 구간의 상위집합이다 —
    과다 마스킹은 과소 마스킹보다 안전하다."""
    a, b = norm_span
    if a >= b or not imap:
        return (src_len, src_len)
    return (min(s for s, _ in imap[a:b]), max(e for _, e in imap[a:b]))


# ── 검증 ──────────────────────────────────────────────────
CASES = [
    ("제로위드",     "주민번호 900101​-​1234567 입니다", r"\d{6}-\d{7}"),
    ("homoglyph",  "키는 АKIAIOSFODNN7EXAMPLE 입니다", r"[A-Z][A-Z0-9]{15,}"),
    ("자모분해",     "명령 ㅁㅜㅅㅣ 하라",              r"무시"),
    ("자모+종성",    "ㅎㅏㄴㄱㅜㄱ 사람",                r"한국"),
    ("★NFKC 괄호",  "㈜가림 담당자 주민번호 900101-1234567", r"\d{6}-\d{7}"),
    ("★NFKC 연속",  "㈜가림 ㈜나라 ㈜다솜 900101-1234567",   r"\d{6}-\d{7}"),
    ("★NFKC 전화",  "℡ 010-1234-5678 로 연락",         r"\d{3}-\d{4}-\d{4}"),
    ("★NFKC 단위",  "㎡ 면적 문의 900101-1234567",       r"\d{6}-\d{7}"),
    ("복합",        "㈜가​림 ㅁㅜㅅㅣ 900101-1234567", r"\d{6}-\d{7}"),
]

print(f"{'케이스':<13} {'정규화본에서 찾은 값':<22} {'원문 스팬':<10} {'원문에서 잘라낸 값'}")
print("-" * 92)
ok = 0
for label, src, pat in CASES:
    norm, imap = normalize_with_map(src)
    m = re.search(pat, norm)
    if not m:
        print(f"{label:<13} (정규화본에서 미발견: {norm!r})")
        continue
    sa, sb = to_source_span(m.span(), imap, len(src))
    cut = src[sa:sb]
    # 잘라낸 원문을 정규화하면 발견값을 '포함'해야 한다 (상위집합이므로)
    good = m.group(0) in normalize_with_map(cut)[0]
    ok += good
    print(f"{label:<13} {m.group(0):<22} {str((sa,sb)):<10} {cut!r} {'OK' if good else 'FAIL'}")

print(f"\n{ok}/{len(CASES)} — 역매핑으로 잘라낸 원문이 발견값을 포함한다")

print("\n── 점 맵을 쓰면 무엇이 깨지는가 (초판 방식 재현) ──")
for src in ["㈜가림 담당자 주민번호 900101-1234567",
            "㈜가림 ㈜나라 ㈜다솜 900101-1234567",
            "℡ 010-1234-5678 로 연락"]:
    norm = "".join(ud.normalize("NFKC", c) for c in src)
    m = re.search(r"\d{3,6}-[\d-]{7,9}", norm)
    pt = m.span()[0]                       # 점 맵 = 항등이라 정규화본 인덱스 그대로
    print(f"  원문 {src[:18]!r}… → 잘라낸 값 {src[pt:pt+14]!r}  ← 밀림")

print("\n★ base64도 같은 표현으로 흡수된다: 디코드 결과 전체가 원문 덩어리 하나의")
print("  구간을 가리키므로 to_source_span이 자동으로 덩어리 전체를 반환한다.")
