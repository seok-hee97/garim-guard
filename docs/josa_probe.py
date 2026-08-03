"""조사 이형태 교정 — 형태소 분석기 없이 규칙만으로 되는지 확인."""

JONGSEONG = lambda ch: (ord(ch) - 0xAC00) % 28 if 0xAC00 <= ord(ch) <= 0xD7A3 else -1

# (받침있음, 받침없음)
PAIRS = [("이","가"), ("을","를"), ("은","는"), ("과","와"), ("으로","로"), ("아","야"), ("이라","라")]

def fix_josa(word: str, josa: str) -> str:
    if not word: return josa
    j = JONGSEONG(word[-1])
    if j < 0: return josa            # 한글이 아니면 손대지 않는다
    has = j != 0
    for with_batchim, without in PAIRS:
        if josa in (with_batchim, without):
            # '으로/로'는 ㄹ 받침(j==8)일 때 '로'
            if (with_batchim, without) == ("으로","로") and j == 8:
                return "로"
            return with_batchim if has else without
    return josa

cases = [
    ("홍길동", "가", "이"),   # 받침 O → 이
    ("김민준", "이", "이"),   # 받침 O → 이
    ("이수아", "이", "가"),   # 받침 X → 가
    ("박서준", "를", "을"),
    ("최유나", "을", "를"),
    ("김철수", "은", "는"),   # 수 = 받침 없음
    ("서울", "으로", "로"),   # ㄹ 받침 → 로
    ("부산", "로", "으로"),   # ㄴ 받침 → 으로
    ("대구", "으로", "로"),   # 받침 X → 로
]
ok = True
print(f"{'단어':<8} {'모델출력':<6} {'교정':<6} {'기대':<6} 결과")
print("-"*44)
for word, given, expect in cases:
    got = fix_josa(word, given)
    good = got == expect
    ok &= good
    print(f"{word:<8} {given:<6} {got:<6} {expect:<6} {'OK' if good else 'FAIL'}")
print()
print("형태소 분석기 없이 종성 계산만으로:", "전부 통과" if ok else "실패 있음")
print("→ kiwipiepy(LGPL) 의존을 피할 수 있다.")
