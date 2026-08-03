"""정규화본 스팬 → 원문 스팬 역매핑. 설계의 최대 구멍이었다.

각 정규화 단계가 '출력 인덱스 → 입력 인덱스' 맵을 함께 낸다.
체인 끝에서 합성하면 정규화본의 어느 위치든 원문 위치를 찾을 수 있다.
"""
import unicodedata as ud, re, base64

ZW = set("​‌‍⁠﻿")
CONF = {"а":"a","е":"e","о":"o","р":"p","с":"c","х":"x","і":"i"}
CHO = list("ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ")
JUNG = list("ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ")

def step_drop(s, pred):
    """문자 삭제. 반환: (출력, 출력i→입력i)"""
    out, m = [], []
    for i, c in enumerate(s):
        if not pred(c): out.append(c); m.append(i)
    return "".join(out), m

def step_map1(s, table):
    """1:1 치환. 길이 불변이므로 맵은 항등."""
    return "".join(table.get(c, c) for c in s), list(range(len(s)))

def step_jamo(s):
    """자모 2~3개 → 음절 1개. 출력 인덱스는 '첫 자모의 입력 인덱스'를 가리킨다."""
    out, m, i = [], [], 0
    while i < len(s):
        if s[i] in CHO and i+1 < len(s) and s[i+1] in JUNG:
            out.append(chr(0xAC00 + CHO.index(s[i])*588 + JUNG.index(s[i+1])*28))
            m.append(i); i += 2
        else:
            out.append(s[i]); m.append(i); i += 1
    return "".join(out), m

def compose(maps):
    """여러 단계의 맵을 합성해 '최종 출력 i → 원문 i'를 만든다."""
    cur = maps[0]
    for nxt in maps[1:]:
        cur = [cur[j] for j in nxt]
    return cur

def normalize_with_map(text):
    maps, s = [], text
    s, m = step_drop(s, lambda c: c in ZW);   maps.append(m)
    s, m = step_map1(s, CONF);                maps.append(m)
    s, m = step_jamo(s);                      maps.append(m)
    return s, compose(maps)

def to_source_span(norm_span, omap, src_len):
    """정규화본 스팬 → 원문 스팬. end는 다음 문자의 시작으로 잡는다."""
    a, b = norm_span
    src_a = omap[a]
    src_b = omap[b] if b < len(omap) else src_len
    return (src_a, src_b)

# ── 검증 ──────────────────────────────────────────────────
CASES = [
 ("제로위드", "주민번호 900101​-​1234567 입니다", r"\d{6}-\d{7}"),
 ("homoglyph", "키는 АKIAIOSFODNN7EXAMPLE 입니다".replace("А","А"), r"[A-ZА][A-Z0-9]{15,}"),
 ("자모분해", "명령 ㅁㅜㅅㅣ 하라", r"무시"),
 ("복합", "주민​번호 900101-1234567", r"\d{6}-\d{7}"),
]
print(f"{'케이스':<12} {'정규화본에서 찾은 값':<26} {'→ 원문 스팬':<12} {'원문에서 잘라낸 값'}")
print("-"*88)
ok = 0
for label, src, pat in CASES:
    norm, omap = normalize_with_map(src)
    m = re.search(pat, norm)
    if not m:
        print(f"{label:<12} (정규화본에서 미발견: {norm!r})"); continue
    sa, sb = to_source_span(m.span(), omap, len(src))
    cut = src[sa:sb]
    # 원문에서 잘라낸 값이 정규화하면 같은 값이 되는가
    good = normalize_with_map(cut)[0].replace("​","") == m.group(0)
    ok += good
    print(f"{label:<12} {m.group(0):<26} {str((sa,sb)):<12} {cut!r} {'OK' if good else 'FAIL'}")
print(f"\n{ok}/{len(CASES)} — 역매핑으로 잘라낸 원문이 정규화하면 발견값과 일치")
print("\n★ base64 디코드는 예외: 디코드 결과 안의 PII는 원문에 대응 오프셋이 없다.")
print("  → 그 경우 span은 base64 덩어리 전체를 가리키고, Finding에 decoded_from 을 붙인다.")
