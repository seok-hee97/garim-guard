"""제안 수정: ASCII 게이트 + 길이 게이트 후에만 token efficiency 적용.

논리: 시크릿은 정의상 ASCII 문자열이다. 한국어 음절이 섞인 토큰은
     애초에 시크릿 후보가 아니므로 검사 대상에서 제외한다.
"""
import random, string, re, statistics as st, tiktoken
random.seed(7)
enc = tiktoken.get_encoding("cl100k_base")
eff = lambda s: len(s) / max(1, len(enc.encode(s)))

TOKEN_RE = re.compile(r"[A-Za-z0-9_\-./+=]{12,}")   # ASCII 후보만, 12자 이상

def candidates(text):
    """ASCII 게이트: 한글·공백을 넘어 ASCII 덩어리만 후보로."""
    return TOKEN_RE.findall(text)

def scan(text, thr=2.5):
    """수정된 스캐너: 후보 추출 → 각 후보에만 TE 적용."""
    return [(c, eff(c)) for c in candidates(text) if eff(c) < thr]

KO_PLAIN = ["고객님의 환불 요청을 확인했습니다. 영업일 기준 3일 이내에 처리됩니다.",
 "이번 분기 매출 보고서를 첨부합니다. 전년 동기 대비 증가하였습니다.",
 "계약서 초안을 검토해 주시고 수정 의견을 회신 부탁드립니다.",
 "배송 지연에 대해 사과드립니다. 재고 확보 후 즉시 발송하겠습니다.",
 "회의는 다음 주 화요일 오후 두 시로 변경되었음을 알려드립니다."]

def r(n, a=string.ascii_letters+string.digits): return "".join(random.choice(a) for _ in range(n))
KO_WITH_SECRET = [
 f"AWS 키를 공유드립니다. AKIA{r(16, string.ascii_uppercase+string.digits)} 로 접속하세요.",
 f"깃허브 토큰은 ghp_{r(36)} 입니다. 외부 공유 금지.",
 f"OpenAI 키 sk-proj-{r(40)} 를 환경변수에 넣어주세요.",
 f"토스 시크릿키 test_sk_{r(30)} 확인 바랍니다.",
 f"카카오 REST 키: KakaoAK {r(32)}",
]

print("=== 수정 전 (문장 전체에 TE 적용) ===")
fp = sum(eff(t) < 2.5 for t in KO_PLAIN)
print(f"  순수 한국어 문장 오탐: {fp}/{len(KO_PLAIN)} = {fp/len(KO_PLAIN):.0%}")

print("\n=== 수정 후 (ASCII 게이트 + 12자 이상 후보에만 TE) ===")
fp2 = sum(1 for t in KO_PLAIN if scan(t))
print(f"  순수 한국어 문장 오탐: {fp2}/{len(KO_PLAIN)} = {fp2/len(KO_PLAIN):.0%}")
for t in KO_PLAIN:
    hits = scan(t)
    if hits: print(f"    ⚠️ {t[:30]}... → {hits}")

tp2 = sum(1 for t in KO_WITH_SECRET if scan(t))
print(f"\n  한국어+시크릿 탐지: {tp2}/{len(KO_WITH_SECRET)} = {tp2/len(KO_WITH_SECRET):.0%}")
for t in KO_WITH_SECRET:
    hits = scan(t)
    mark = "탐지" if hits else "미탐 ⚠️"
    top = f"{hits[0][0][:28]}… eff={hits[0][1]:.2f}" if hits else "-"
    print(f"    {mark}  {top}")

print(f"\n결론: ASCII 게이트가 한국어 오탐을 {fp/len(KO_PLAIN):.0%} → {fp2/len(KO_PLAIN):.0%} 로 제거하고")
print(f"      한국어 문장 속 시크릿은 {tp2/len(KO_WITH_SECRET):.0%} 탐지한다.")
