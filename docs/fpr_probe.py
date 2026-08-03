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
