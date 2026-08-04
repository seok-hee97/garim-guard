"""체크섬의 FPR 감소가 '현실적 오탐원'에서도 성립하는가.
무작위 숫자열 기준 ~10배는 이미 확인했다. 실제 업무 문자열로 재시험한다."""
import re, random, datetime as dt
random.seed(11)

W = [2,3,4,5,6,7,8,9,2,3,4,5]
def rrn_ck(d):
    d = d.replace("-","")
    if len(d)!=13 or not d.isdigit(): return False
    return (11 - sum(int(d[i])*W[i] for i in range(12)) % 11) % 10 == int(d[12])
def rrn_full(d):
    """체크섬 + 생년월일 유효성 + 성별코드 정합 — 설계가 주장한 3중 검증"""
    s = d.replace("-","")
    if not rrn_ck(s): return False
    yy, mm, dd, g = int(s[:2]), int(s[2:4]), int(s[4:6]), int(s[6])
    if g not in (1,2,3,4,5,6,7,8): return False
    cent = {1:1900,2:1900,3:2000,4:2000,5:1900,6:1900,7:2000,8:2000}[g]
    try: dt.date(cent+yy, mm, dd)
    except ValueError: return False
    return True

BIZ_W = [1,3,7,1,3,7,1,3,5]
def biz_ck(d):
    d = d.replace("-","")
    if len(d)!=10 or not d.isdigit(): return False
    s = sum(int(d[i])*BIZ_W[i] for i in range(9)) + (int(d[8])*5)//10
    return (10 - s%10) % 10 == int(d[9])

RRN_RE = re.compile(r"\b\d{6}[-\s]?[1-8]\d{6}\b")
BIZ_RE = re.compile(r"\b\d{3}-?\d{2}-?\d{5}\b")

# ── 현실적 오탐원: 실제 업무에서 쓰이는 형식 ──────────────────
def order_no():  return f"{random.randint(200000,261231)}-{random.randint(1000000,9999999)}"      # 주문번호 YYMMDD-일련
def emp_id():    return f"{random.randint(800101,991231)}-{random.randint(1000000,4999999)}"      # 사번(입사일 기반)
def invoice():   return f"{random.randint(100000,999999)}-{random.randint(1000000,8999999)}"      # 송장번호
def serial():    return f"{random.randint(100,999)}-{random.randint(10,99)}-{random.randint(10000,99999)}"  # 제품 시리얼(사업자 형식)
def tel_biz():   return f"{random.randint(100,999)}-{random.randint(10,99)}-{random.randint(10000,99999)}"

N = 20000
groups = [("주문번호", [order_no() for _ in range(N)]),
          ("사번(입사일)", [emp_id() for _ in range(N)]),
          ("송장번호", [invoice() for _ in range(N)])]

print("주민등록번호 — 현실적 오탐원에서의 통과율")
print(f"{'오탐원':<14} {'정규식 통과':>12} {'+체크섬':>10} {'+생년월일·성별':>14} {'감소배수':>9}")
print("-"*66)
for name, xs in groups:
    re_hit = [x for x in xs if RRN_RE.fullmatch(x)]
    ck_hit = [x for x in re_hit if rrn_ck(x)]
    full   = [x for x in ck_hit if rrn_full(x)]
    r1, r2, r3 = len(re_hit)/N, len(ck_hit)/N, len(full)/N
    mult = r1/r3 if r3 else float("inf")
    print(f"{name:<14} {r1:>11.2%} {r2:>10.2%} {r3:>14.2%} {mult:>8.1f}x")

print("\n사업자등록번호 — 제품 시리얼/전화 형식과의 충돌")
sers = [serial() for _ in range(N)]
re_hit = [x for x in sers if BIZ_RE.fullmatch(x)]
ck_hit = [x for x in re_hit if biz_ck(x)]
r1, r2 = len(re_hit)/N, len(ck_hit)/N
print(f"{'제품시리얼':<14} {r1:>11.2%} {r2:>10.2%} {'':>14} {r1/r2 if r2 else 0:>8.1f}x")

print("\n※ 정규식 통과율이 100%가 아닌 이유: 오탐원이 정규식 형식을 항상 만족하진 않는다.")
print("   감소배수 = 정규식 단독 대비 3중 검증 후 남는 오탐의 비율.")


# ══════════════════════════════════════════════════════════════
# 2020-10 주민등록번호 3세대 (검증번호 폐지) — DESIGN §2.1.1 · §3.2.3
#
# 초안은 "체크섬 실패 → 탐지 아님"이었다. 3세대 번호는 검증번호가
# 배정되지 않으므로 그 규칙이 실재하는 주민등록번호를 통째로 버린다.
# 아래 셋을 측정한다.
#   (a) 3세대 번호가 구식 체크섬을 통과하는 비율   → §2.1.1
#   (b) 체크섬의 '한계 기여' 분해                  → 부록 A.1 둘째 표
#   (c) 3세대를 L0 단독으로 채택하면 FPR이 얼마나  → §3.2.3
# ══════════════════════════════════════════════════════════════
GEN3_START = dt.date(2020, 10, 5)     # 주민등록법 시행규칙 개정 시행일
_TODAY = dt.date(2026, 8, 4)          # 고정 — 실행 시점에 따라 값이 흔들리지 않게

def gen3_rrn():
    """3세대 주민등록번호 합성: 실재 생년월일 + 성별 3/4 + 임의 6자리."""
    span = (_TODAY - GEN3_START).days
    b = GEN3_START + dt.timedelta(days=random.randint(0, span))
    return (f"{b.year%100:02d}{b.month:02d}{b.day:02d}-"
            f"{random.choice('34')}{random.randint(0, 999999):06d}")

def is_gen3_sig(d):
    """★ 폐기됨. 회귀 방지용으로만 남긴다.

    '3세대 서명 = 생년월일 2020-10-05 이후 + 성별 3/4/7/8'은 틀렸다.
    3세대는 **발급·변경 시점**으로 정의되고 발급일은 13자리 어디에도 없다.
    1980년생이 2021년에 번호를 변경하면 생년월일은 과거이고 성별코드도
    1/2 그대로인데 뒤 6자리는 임의다 — 이 함수는 그런 진짜 번호를 0% 잡는다.
    성인 외국인등록번호(약 160만 명)도 전원 0%다.

    관측 불가능한 잠재변수(발급일)를 판정 조건으로 쓴 것이 결함의 뿌리였다.
    대체: rrn_datesex()로 형식 정합만 보고, 나머지는 문맥이 가른다(DESIGN §3.2.3).
    """
    raise NotImplementedError(
        "is_gen3_sig는 폐기됐다 — 문자열로 세대를 판정할 수 없다. "
        "DESIGN.md §3.2.3 참조. 형식 정합은 rrn_datesex()를 쓴다.")


ANCHORS = ("주민등록번호", "주민번호", "외국인등록번호", "내국인등록번호", "등록번호")
ANTI_ANCHORS = ("주문", "송장", "운송장", "사번", "사원번호", "시리얼", "일련번호")


def has_anchor(text, span):
    """근접 앵커 — L1 없이 도는 유일한 결정적 문맥 경로 (DESIGN §3.2.3).
    창: 스팬 시작 기준 좌 20자 / 우 5자. 한국어는 라벨이 값 앞에 온다."""
    a, b = span
    win = text[max(0, a - 20):min(len(text), b + 5)]
    if any(x in win for x in ANTI_ANCHORS):
        return False
    return any(x in win for x in ANCHORS)

def rrn_datesex(d):
    """생년월일 유효성 + 성별코드 정합만. 체크섬을 빼고 잰다 —
    rrn_full()은 체크섬에서 단락하므로 이 함수가 따로 필요하다."""
    s = d.replace("-", "")
    if len(s) != 13 or not s.isdigit():
        return False
    yy, mm, dd, g = int(s[:2]), int(s[2:4]), int(s[4:6]), int(s[6])
    if g not in range(1, 9):
        return False
    cent = {1: 1900, 2: 1900, 3: 2000, 4: 2000,
            5: 1900, 6: 1900, 7: 2000, 8: 2000}[g]
    try:
        dt.date(cent + yy, mm, dd)
    except ValueError:
        return False
    return True

N3 = 200_000

print("\n" + "="*70)
print("(a) 3세대 번호가 구식 체크섬을 통과하는가 — §2.1.1")
g3 = [gen3_rrn() for _ in range(N3)]
passed = sum(rrn_ck(x) for x in g3)
print(f"  N={N3:,}  통과 {passed/N3:>7.2%}  탈락 {1-passed/N3:>7.2%}")
print(f"  → 검증번호는 mod-11 잔여를 mod-10으로 접으므로 극한은 1/10 = 10.00%다.")
print(f"     (1/11 = 9.09% 가 아니다 — 잔여 11개가 숫자 10개로 매핑된다)")

print("\n(b) 체크섬의 한계 기여 분해 — 부록 A.1 둘째 표")
print(f"  {'오탐원':<14} {'정규식':>9} {'생년월일·성별만':>15} {'+체크섬':>9} {'한계기여':>9}")
print("  " + "-"*62)
for name, xs in groups:
    re_hit = [x for x in xs if RRN_RE.fullmatch(x)]
    ds     = [x for x in re_hit if rrn_datesex(x)]      # 체크섬 없이
    full   = [x for x in ds if rrn_ck(x)]
    r1, r2, r3 = len(re_hit)/N, len(ds)/N, len(full)/N
    print(f"  {name:<14} {r1:>8.2%} {r2:>15.2%} {r3:>9.2%} {r2/r3 if r3 else 0:>8.1f}x")
print("  → 250배의 대부분은 생년월일·성별 정합이 만들었다. 체크섬의 한계 기여는 그 뒤에 남는 몫뿐이다.")

print("\n(c) 체크섬 실패분을 L1 결합 후보로 넘기면 오탐 스트림이 얼마나 늘어나는가 — §3.2.3")
print("     후보 규칙: 체크섬 실패 + 날짜·성별코드 정합 통과 (세대 판정은 하지 않는다)")
print(f"  {'오탐원':<14} {'L0 확정':>13} {'L1 결합 후보':>12}")
print("  " + "-"*44)
tot_cur = tot_new = 0
BIG = [("주문번호",  [order_no() for _ in range(N3)]),
       ("사번(입사일)", [emp_id()  for _ in range(N3)]),
       ("송장번호",   [invoice()  for _ in range(N3)])]
for name, xs in BIG:
    re_hit = [x for x in xs if RRN_RE.fullmatch(x)]
    cur = sum(rrn_full(x) for x in re_hit)
    new = cur + sum(1 for x in re_hit if not rrn_ck(x) and rrn_datesex(x))
    tot_cur += cur/N3; tot_new += new/N3
    print(f"  {name:<14} {cur/N3:>12.2%} {new/N3:>12.2%}")
print(f"  {'평균':<14} {tot_cur/3:>12.2%} {tot_new/3:>12.2%}")
print("  → 체크섬이 실패하면 남는 것은 '유효 날짜 + 성별 + 균등난수 6자리'이고,")
print("     그것은 주문번호·송장번호와 분포가 같다. 잉여(redundancy)가 0이다.")
print("     ⚠️ 이 비율의 분모는 'RRN 형식 업무 문자열 1건'이지 음성 세그먼트가 아니다.")
print("        FPR ≈ d × (위 비율), d = 음성 세그먼트 중 RRN 형식 밀도. d는 W6b에 잰다.")
