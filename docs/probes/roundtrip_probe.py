"""생성기가 만든 양성을 L0 규칙이 실제로 탐지하는가 — 왕복 검증.

이 프로브가 없어서 같은 종류의 버그를 두 번 심었다.

  1차: 991332 (13월 32일). 체크섬은 통과하지만 생년월일 유효성에서 탈락.
  2차: '미할당 성별코드 9'. 9는 미할당이 아니라 1800년대생이고,
       정규식 [1-8]이 (생존자가 없으므로 옳게) 배제한다.
  3차: '3세대 서명 = 생년월일 2020-10-05 이후 + 성별 3/4/7/8'. 세대는
       **발급 시점**의 속성이고 발급일은 13자리에 없다. 번호변경자·성인
       귀화자(약 67,000명, 변경 사유의 83.9%가 유출 피해)와 성인
       외국인등록번호 보유자(약 160만 명)를 전원 0% 잡았다.

두 번 다 "실제 개인정보를 저장소에 넣지 않는다"를 지키려다 생긴 것이고,
두 번 다 생성기 출력을 L0에 되먹여 보기만 했으면 즉시 잡혔다.

── 그래서 드러난 진짜 문제 ──────────────────────────────────
'안전하면서 탐지되는' 주민등록번호는 원리적으로 없다. 탐지기가 잡는
영역은 정의상 실존 번호가 사는 영역이다. 회피 장치(불가능한 날짜,
미할당 성별코드)는 전부 탐지기도 함께 배제한다.

측정하면 이렇다 (아래 collision_math()):
  2세대 임의 1건이 실존 번호와 일치할 확률 ≈ 7.0e-3
  양성 15,000건이면 기대 충돌 ≈ 105건

이건 설계로 없앨 수 없다. 없앨 수 없으면 다루는 방법을 정해야 한다 —
DESIGN.md §5.3.5가 그 결정을 적는다. 이 프로브는 그 결정이 지켜지는지
검사한다.
"""
import sys, os, datetime as dt, random
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from checksum_probe import rrn_make, bizno_make, bizno_check, corp_make, corp_check
from fpr_probe import RRN_RE, BIZ_RE, rrn_full, rrn_datesex, has_anchor

random.seed(20260804)


# ── L0 규칙 (DESIGN §2.1 참조 구현) ──────────────────────────
def l0_rrn(s, ctx=None):
    """§2.1 L-KR-01 + §3.2.3 병합표.

    ★ 세대를 판정하지 않는다. 발급일은 13자리 어디에도 없다.
    분기는 '세대'가 아니라 '체크섬 정보의 유무'다.

    ctx = (전체 텍스트, 스팬). None이면 문맥 없음(무앵커·L1 없음)으로 본다.
    """
    if not RRN_RE.fullmatch(s):
        return False, "정규식 불일치"
    if rrn_full(s):
        return True, "체크섬 통과"                       # confidence 1.0
    if not rrn_datesex(s):
        return False, "날짜·성별 정합 실패"                # 문자열 단독 음성 확정
    if ctx and has_anchor(*ctx):
        return True, "무체크섬 + 근접 앵커"                # rrn_anchor, 0.6
    return False, "무체크섬 + 문맥 없음 (L1 결합 대상)"     # §7.7 F7


# ── §5.3.5 생성기 (현행 명세) ────────────────────────────────
GEN3_START = dt.date(2020, 10, 5)
_TODAY = dt.date(2026, 8, 4)


def gen_rrn_2nd():
    """2세대: 생존자 생년월일 + 성별 1~4 + 체크섬."""
    b = dt.date(1930, 1, 1) + dt.timedelta(
        days=random.randint(0, (_TODAY - dt.date(1930, 1, 1)).days))
    g = random.choice("12") if b.year < 2000 else random.choice("34")
    return rrn_make(f"{b.year%100:02d}{b.month:02d}{b.day:02d}{g}"
                    f"{random.randint(0,99999):05d}")


def gen_rrn_nock_birth():
    """무체크섬 (a): 2020-10-05 이후 출생. 3세대 150만의 96%."""
    b = GEN3_START + dt.timedelta(
        days=random.randint(0, (_TODAY - GEN3_START).days))
    return (f"{b.year%100:02d}{b.month:02d}{b.day:02d}"
            f"{random.choice('34')}{random.randint(0,999999):06d}")


def gen_rrn_nock_changed():
    """★ 무체크섬 (b): 번호 변경자·성인 귀화자. 약 67,000명.

    생년월일이 과거이고 성별코드도 1/2 그대로다 — 폐기된 is_gen3_sig는
    생년월일과 성별코드 **양쪽**으로 게이트했으므로 0% 잡았다.
    이 집단은 변경 사유의 83.9%가 유출·사기·신분도용이다. 즉 이 도구가
    존재하는 이유 그 자체인 사람들이다."""
    b = dt.date(1940, 1, 1) + dt.timedelta(days=random.randint(0, 23741))
    g = random.choice("12") if b.year < 2000 else random.choice("34")
    return (f"{b.year%100:02d}{b.month:02d}{b.day:02d}"
            f"{g}{random.randint(0,999999):06d}")


def gen_frn_adult():
    """★ 무체크섬 (c): 성인 외국인등록번호. 등록외국인 약 160만 명.

    §2.1.1이 L-KR-02를 'L-KR-01의 3세대 경로와 동일하게 취급'한다고 했는데,
    그 경로가 생년월일로 게이트되므로 성인 보유자 전원이 도달 불가였다."""
    b = dt.date(1950, 1, 1) + dt.timedelta(days=random.randint(0, 21000))
    g = random.choice("56") if b.year < 2000 else random.choice("78")
    return (f"{b.year%100:02d}{b.month:02d}{b.day:02d}"
            f"{g}{random.randint(0,999999):06d}")


# ── 폐기된 방식 (회귀 방지용으로 남긴다) ──────────────────────
def gen_rrn_991332():
    """폐기 1차: 존재 불가 생년월일."""
    return rrn_make("991332" + f"1{random.randint(0,99999):05d}")


def gen_rrn_sex9():
    """폐기 2차: 성별코드 9. 미할당이 아니라 1800년대생이다."""
    return rrn_make(f"90010" f"19{random.randint(0,99999):05d}")


def collision_math():
    days2 = (_TODAY - dt.date(1930, 1, 1)).days
    space2 = days2 * 2 * 10**5
    days3 = (_TODAY - GEN3_START).days
    space3 = days3 * 2 * 10**6
    return (49_600_000 / space2, 1_490_000 / space3)


# ── 검증 ──────────────────────────────────────────────────
N = 2000
# 앵커 문맥 — 실제 업무 문서에서 주민번호가 놓이는 자리
CTX_ANCHOR = "고객 주민등록번호 {} 확인 부탁드립니다."
CTX_NONE   = "확인 결과 {} 로 조회됩니다."
CTX_ANTI   = "주문번호 {} 배송 조회 부탁드립니다."

# 기대는 (하한, 상한). 무체크섬 값의 약 10%는 임의 6자리가 우연히 구식
# 체크섬을 만족하므로, '앵커 없음'의 기대는 0%가 아니라 그 우연분이다.
CASES = [
    ("체크섬 통과 (2세대)",       gen_rrn_2nd,          CTX_NONE,   (0.99, 1.00)),
    ("무체크섬 (a) 최근 출생",     gen_rrn_nock_birth,   CTX_ANCHOR, (0.99, 1.00)),
    ("★무체크섬 (b) 번호변경·귀화", gen_rrn_nock_changed, CTX_ANCHOR, (0.99, 1.00)),
    ("★무체크섬 (c) 성인 FRN",    gen_frn_adult,        CTX_ANCHOR, (0.99, 1.00)),
    ("무체크섬 — 앵커 없음",       gen_rrn_nock_changed, CTX_NONE,   (0.07, 0.13)),
    ("무체크섬 — 금지어(주문)",     gen_rrn_nock_changed, CTX_ANTI,   (0.07, 0.13)),
    ("폐기 991332",              gen_rrn_991332,       CTX_ANCHOR, (0.00, 0.00)),
    ("폐기 성별코드 9",            gen_rrn_sex9,         CTX_ANCHOR, (0.00, 0.00)),
]

print("생성기 출력 → L0 규칙 왕복 검증  (N=%d)" % N)
print(f"{'생성 방식':<24} {'L0 탐지율':>10}  {'최빈 사유':<30} {'기대':>8} {'판정'}")
print("-" * 92)
ok = True
import collections
for label, gen, tmpl, (lo, hi) in CASES:
    vals = [gen() for _ in range(N)]
    res = []
    for v in vals:
        text = tmpl.format(v)
        res.append(l0_rrn(v, (text, (text.index(v), text.index(v) + len(v)))))
    rate = sum(d for d, _ in res) / N
    why = collections.Counter(r for _, r in res).most_common(1)[0][0]   # 최빈 사유
    good = lo <= rate <= hi
    ok &= good
    print(f"{label:<24} {rate:>9.1%}  {why:<30} {f'{lo:.0%}~{hi:.0%}':>8} "
          f"{'OK' if good else 'FAIL'}")

print("\n사업자·법인·카드")
for label, gen, ck in [("사업자등록번호", lambda: bizno_make(f"{random.randint(0,10**9-1):09d}"), bizno_check),
                       ("법인등록번호",   lambda: corp_make(f"110111{random.randint(0,999999):06d}"), corp_check)]:
    vals = [gen() for _ in range(N)]
    rate = sum(ck(v) for v in vals) / N
    ok &= rate > 0.99
    print(f"  {label:<20} 체크섬 통과 {rate:>7.1%}  {'OK' if rate > 0.99 else 'FAIL'}")

p2, p3 = collision_math()
print(f"""
── 충돌 확률 — 설계로 없앨 수 없는 값 ────────────────────────
  2세대 임의 1건이 실존 번호와 일치할 확률   {p2:.2e}
  3세대 임의 1건                            {p3:.2e}
  양성 15,000건(§5.3.2 식별자 양성)의 기대 충돌  {15000*p2:.0f}건

  체크섬을 통과하면서 실존과 겹치지 않는 2세대 번호는 만들 수 없다.
  탐지기가 잡는 영역이 곧 실존 번호가 사는 영역이기 때문이다.
  → DESIGN.md §5.3.5가 이 사실을 인정하고 대응을 규정한다.
    (생성 값을 저장소에 커밋하지 않고 생성기+seed만 배포한다)
""")

print("전건 통과" if ok else "★ FAIL — 생성기와 L0 규칙이 어긋났다")
sys.exit(0 if ok else 1)
