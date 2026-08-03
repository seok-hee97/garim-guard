"""Token efficiency(BPE) 시크릿 탐지가 한국어에서 역전되는지 정량 검증.

Aikido/Betterleaks: eff = len(str)/len(BPE tokens), 임계 2.5 미만 = 시크릿 의심.
보고된 성능: TE 단독 P 57.3% / R 98.6% / F1 0.725 (CredData, 영어 코드 기준).
"""
import random, string, statistics as st, tiktoken
random.seed(7)
enc = tiktoken.get_encoding("cl100k_base")
eff = lambda s: len(s) / max(1, len(enc.encode(s)))

# ── 음성(negative) = 시크릿 아님 ──────────────────────────────
KO = ["고객님의 환불 요청을 확인했습니다. 영업일 기준 3일 이내에 처리됩니다.",
 "이번 분기 매출 보고서를 첨부합니다. 전년 동기 대비 증가하였습니다.",
 "계약서 초안을 검토해 주시고 수정 의견을 회신 부탁드립니다.",
 "배송 지연에 대해 사과드립니다. 재고 확보 후 즉시 발송하겠습니다.",
 "회의는 다음 주 화요일 오후 두 시로 변경되었음을 알려드립니다.",
 "해당 문의는 담당 부서로 이관하였으니 조금만 기다려 주세요.",
 "첨부한 자료를 참고하시어 검토 의견을 주시면 감사하겠습니다.",
 "시스템 점검으로 인해 서비스가 일시 중단될 예정입니다.",
 "신규 가입 고객을 대상으로 할인 쿠폰을 발급해 드렸습니다.",
 "요청하신 견적서는 내일 오전 중으로 보내드리겠습니다."]
EN = ["Please confirm the refund request and let me know if you need more information.",
 "The quarterly report is attached for your review and comments.",
 "We apologize for the shipping delay and will dispatch as soon as stock arrives.",
 "The meeting has been moved to Tuesday afternoon at two o'clock.",
 "Your inquiry has been forwarded to the responsible department.",
 "Please review the attached draft and send us your feedback.",
 "The system will be temporarily unavailable during scheduled maintenance.",
 "A discount coupon has been issued to all newly registered customers.",
 "We will send the requested quotation by tomorrow morning.",
 "Kindly check the deployment pipeline and rerun the failed stage."]

# ── 양성(positive) = 실제 시크릿 형태 ─────────────────────────
def r(n, alpha=string.ascii_letters+string.digits): return "".join(random.choice(alpha) for _ in range(n))
SECRETS = ([f"AKIA{r(16, string.ascii_uppercase+string.digits)}" for _ in range(10)]
 + [r(40, string.ascii_letters+string.digits+"/+") for _ in range(10)]      # AWS secret
 + [f"ghp_{r(36)}" for _ in range(10)]                                       # GitHub PAT
 + [f"sk-proj-{r(40)}" for _ in range(10)]                                   # OpenAI
 + [f"sk-ant-api03-{r(93)}AA" for _ in range(10)]                            # Anthropic
 + [f"AIzaSy{r(33)}" for _ in range(10)]                                     # Google
 + [r(32, string.hexdigits[:16].lower()) for _ in range(10)]                 # hex
 + [f"eyJ{r(30)}.{r(40)}.{r(43)}" for _ in range(10)])                       # JWT

def summarize(name, xs):
    v = [eff(x) for x in xs]
    return name, len(v), st.mean(v), min(v), max(v)

rows = [summarize("한국어 산문", KO), summarize("영어 산문", EN), summarize("시크릿", SECRETS)]
print(f"{'집단':<12} {'n':>4} {'평균eff':>8} {'최소':>7} {'최대':>7}")
print("-"*44)
for n, c, m, lo, hi in rows: print(f"{n:<12} {c:>4} {m:>8.2f} {lo:>7.2f} {hi:>7.2f}")

THR = 2.5
ko_fp = sum(eff(x) < THR for x in KO)
en_fp = sum(eff(x) < THR for x in EN)
tp    = sum(eff(x) < THR for x in SECRETS)
print(f"\n임계 {THR} 적용 결과")
print(f"  한국어 산문 오탐 : {ko_fp}/{len(KO)}  = {ko_fp/len(KO):.0%}   ← ★")
print(f"  영어 산문   오탐 : {en_fp}/{len(EN)}  = {en_fp/len(EN):.0%}")
print(f"  시크릿      탐지 : {tp}/{len(SECRETS)} = {tp/len(SECRETS):.0%}")

# 한국어를 음성 집단으로 넣었을 때의 정밀도
prec_ko = tp / (tp + ko_fp) if tp + ko_fp else 0
prec_en = tp / (tp + en_fp) if tp + en_fp else 0
print(f"\n  영어만 음성으로 볼 때 precision : {prec_en:.3f}")
print(f"  한국어만 음성으로 볼 때 precision: {prec_ko:.3f}")

# 분리도 — 한국어 평균이 시크릿 평균보다 낮은가?
ko_m = st.mean([eff(x) for x in KO]); sec_m = st.mean([eff(x) for x in SECRETS])
print(f"\n한국어 평균 {ko_m:.2f}  vs  시크릿 평균 {sec_m:.2f}")
print("→ " + ("★ 역전: 한국어가 시크릿보다 '더 시크릿처럼' 보인다" if ko_m < sec_m
              else "정상 방향(한국어가 더 높음)"))
overlap = sum(1 for x in KO if eff(x) < sec_m)
print(f"→ 시크릿 평균보다 낮은 한국어 문장: {overlap}/{len(KO)}")
