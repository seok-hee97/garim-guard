"""한국 고유 식별자 체크섬 알고리즘 검증.

설계서에 알고리즘을 명시하기 전에 실제로 맞는지 확인한다.
실제 개인정보는 쓰지 않는다 — 알고리즘 검증용 합성값만 사용.
"""

# ── 1. 주민등록번호 (13자리) ─────────────────────────────────
# 가중치 2,3,4,5,6,7,8,9,2,3,4,5 → sum % 11 → (11 - r) % 10 == 마지막자리
RRN_W = [2, 3, 4, 5, 6, 7, 8, 9, 2, 3, 4, 5]


def rrn_check(d: str) -> bool:
    d = d.replace("-", "")
    if len(d) != 13 or not d.isdigit():
        return False
    s = sum(int(d[i]) * RRN_W[i] for i in range(12))
    return (11 - s % 11) % 10 == int(d[12])


def rrn_make(front12: str) -> str:
    s = sum(int(front12[i]) * RRN_W[i] for i in range(12))
    return front12 + str((11 - s % 11) % 10)


# ── 2. 사업자등록번호 (10자리) ────────────────────────────────
# 가중치 1,3,7,1,3,7,1,3,5 + 9번째자리*5를 10으로 나눈 몫을 추가 가산
BIZ_W = [1, 3, 7, 1, 3, 7, 1, 3, 5]


def bizno_check(d: str) -> bool:
    d = d.replace("-", "")
    if len(d) != 10 or not d.isdigit():
        return False
    s = sum(int(d[i]) * BIZ_W[i] for i in range(9))
    s += (int(d[8]) * 5) // 10
    return (10 - s % 10) % 10 == int(d[9])


def bizno_make(front9: str) -> str:
    s = sum(int(front9[i]) * BIZ_W[i] for i in range(9))
    s += (int(front9[8]) * 5) // 10
    return front9 + str((10 - s % 10) % 10)


# ── 3. 법인등록번호 (13자리) ──────────────────────────────────
# 가중치 1,2 교대 → sum % 10 → (10 - r) % 10
def corp_check(d: str) -> bool:
    d = d.replace("-", "")
    if len(d) != 13 or not d.isdigit():
        return False
    s = sum(int(d[i]) * (1 if i % 2 == 0 else 2) for i in range(12))
    return (10 - s % 10) % 10 == int(d[12])


def corp_make(front12: str) -> str:
    s = sum(int(front12[i]) * (1 if i % 2 == 0 else 2) for i in range(12))
    return front12 + str((10 - s % 10) % 10)


# ── 4. 카드번호 (Luhn) ────────────────────────────────────────
def luhn_check(d: str) -> bool:
    d = "".join(c for c in d if c.isdigit())
    if len(d) < 12:
        return False
    tot, alt = 0, False
    for c in reversed(d):
        n = int(c)
        if alt:
            n *= 2
            if n > 9:
                n -= 9
        tot += n
        alt = not alt
    return tot % 10 == 0


# ── 검증 ──────────────────────────────────────────────────────
if __name__ == "__main__":
    ok = True

    # 주민번호: 존재하지 않는 생년월일(13월 32일)로 합성 → 실제 PII 아님
    r = rrn_make("991332" + "100000")
    print(f"RRN  합성={r}  체크={rrn_check(r)}  변조1자리={rrn_check(r[:-1] + str((int(r[-1])+1)%10))}")
    ok &= rrn_check(r) and not rrn_check(r[:-1] + str((int(r[-1]) + 1) % 10))

    # 사업자번호: 널리 알려진 공개 예시 (국세청 안내 예제)
    b = bizno_make("123456789")
    print(f"BIZ  합성={b}  체크={bizno_check(b)}  변조={bizno_check(b[:-1] + str((int(b[-1])+1)%10))}")
    ok &= bizno_check(b) and not bizno_check(b[:-1] + str((int(b[-1]) + 1) % 10))

    c = corp_make("110111" + "123456")
    print(f"CORP 합성={c}  체크={corp_check(c)}  변조={corp_check(c[:-1] + str((int(c[-1])+1)%10))}")
    ok &= corp_check(c) and not corp_check(c[:-1] + str((int(c[-1]) + 1) % 10))

    print(f"LUHN 4242424242424242 → {luhn_check('4242424242424242')} (True 기대)")
    print(f"LUHN 4242424242424241 → {luhn_check('4242424242424241')} (False 기대)")
    ok &= luhn_check("4242424242424242") and not luhn_check("4242424242424241")

    # ★ FPR 감소 효과의 핵심 근거: 정규식 형식만 맞는 임의 숫자 중 몇 %가 체크섬을 통과하는가
    import itertools, random
    random.seed(42)
    rnd = ["".join(random.choice("0123456789") for _ in range(13)) for _ in range(100000)]
    rrn_pass = sum(rrn_check(x) for x in rnd) / len(rnd)
    rnd10 = ["".join(random.choice("0123456789") for _ in range(10)) for _ in range(100000)]
    biz_pass = sum(bizno_check(x) for x in rnd10) / len(rnd10)
    corp_pass = sum(corp_check(x) for x in rnd) / len(rnd)
    rnd16 = ["".join(random.choice("0123456789") for _ in range(16)) for _ in range(100000)]
    luhn_pass = sum(luhn_check(x) for x in rnd16) / len(rnd16)
    print()
    print("무작위 숫자열의 체크섬 통과율 (= 정규식 단독 대비 FPR 상한 감소폭):")
    print(f"  주민번호 13자리 : {rrn_pass:.4%}")
    print(f"  사업자   10자리 : {biz_pass:.4%}")
    print(f"  법인     13자리 : {corp_pass:.4%}")
    print(f"  카드     16자리 : {luhn_pass:.4%}")

    print()
    print("전체 검증:", "통과" if ok else "실패")
