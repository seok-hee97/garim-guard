"""생성기가 만든 양성을 L0 규칙이 실제로 탐지하는가 — 왕복 검증.

이 프로브가 없어서 같은 종류의 버그를 두 번 심었다.

  1차: 991332 (13월 32일). 체크섬은 통과하지만 생년월일 유효성에서 탈락.
  2차: '미할당 성별코드 9'. 9는 미할당이 아니라 1800년대생이고,
       정규식 [1-8]이 (생존자가 없으므로 옳게) 배제한다.

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
from fpr_probe import RRN_RE, BIZ_RE, rrn_full, is_gen3_sig

random.seed(20260804)


# ── L0 규칙 (DESIGN §2.1 참조 구현) ──────────────────────────
def l0_rrn(s):
    """§2.1 L-KR-01: 정규식 + 생년월일·성별코드 정합 + 체크섬(2세대 한정).
    §3.2.3 병합표: 체크섬 실패 + 3세대 서명이면 L1 결합 대상으로 살린다."""
    if not RRN_RE.fullmatch(s):
        return False, "정규식 불일치"
    if rrn_full(s):
        return True, "체크섬 통과"
    if is_gen3_sig(s):
        return True, "3세대 서명 (L1 결합 대상)"
    return False, "체크섬 실패 + 3세대 아님"


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


def gen_rrn_3rd():
    """3세대: 2020-10-05 이후 생년월일 + 성별 3/4 + 임의 6자리 (체크섬 없음)."""
    b = GEN3_START + dt.timedelta(
        days=random.randint(0, (_TODAY - GEN3_START).days))
    return (f"{b.year%100:02d}{b.month:02d}{b.day:02d}"
            f"{random.choice('34')}{random.randint(0,999999):06d}")


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
CASES = [
    ("2세대 (현행 명세)",   gen_rrn_2nd,     True),
    ("3세대 (현행 명세)",   gen_rrn_3rd,     True),
    ("폐기 991332",        gen_rrn_991332,  False),
    ("폐기 성별코드 9",     gen_rrn_sex9,    False),
]

print("생성기 출력 → L0 규칙 왕복 검증  (N=%d)" % N)
print(f"{'생성 방식':<22} {'L0 탐지율':>10}  {'사유(대표)':<28} {'기대':>6} {'판정'}")
print("-" * 84)
ok = True
for label, gen, expect_detect in CASES:
    vals = [gen() for _ in range(N)]
    res = [l0_rrn(v) for v in vals]
    rate = sum(d for d, _ in res) / N
    why = next((r for d, r in res if d == (rate < 0.5)), res[0][1])
    good = (rate > 0.99) if expect_detect else (rate < 0.01)
    ok &= good
    print(f"{label:<22} {rate:>9.1%}  {why:<28} {'탐지' if expect_detect else '미탐':>6} "
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
