"""수정안 검증: 조사 교정을 '복원된 토큰 바로 뒤'로만 한정 + 호격 아/야 제외."""
import re
JONG = lambda ch: (ord(ch)-0xAC00) % 28 if 0xAC00 <= ord(ch) <= 0xD7A3 else -1
# ★ 호격 (아/야) 제외 — 외래어 말음 '아'를 훼손한다
PAIRS = [("이","가"),("을","를"),("은","는"),("과","와"),("으로","로")]
JOSA_ALT = "|".join(sorted({j for p in PAIRS for j in p}, key=len, reverse=True))

def fix_josa(word, josa):
    j = JONG(word[-1])
    if j < 0: return josa
    has = j != 0
    for wb, wo in PAIRS:
        if josa in (wb, wo):
            if (wb, wo) == ("으로","로") and j == 8: return "로"
            return wb if has else wo
    return josa

TOK = re.compile(r"<\s*([A-Za-z_]+)\s*_\s*(\d+)\s*>")

class Vault:
    def __init__(self): self.v2t, self.t2v, self.n = {}, {}, {}
    def mask(self, text, spans):
        for s, e, ent in sorted(spans, key=lambda x: -x[0]):
            val = text[s:e]
            if val in self.v2t: tok = self.v2t[val]
            else:
                self.n[ent] = self.n.get(ent,0)+1
                tok = f"<{ent}_{self.n[ent]}>"; self.v2t[val], self.t2v[tok] = tok, val
            text = text[:s]+tok+text[e:]
        return text
    def restore(self, text):
        """★ 토큰을 복원하면서 그 자리에서 뒤따르는 조사만 교정한다."""
        def repl(m):
            key = f"<{m.group(1).upper()}_{m.group(2)}>"
            val = self.t2v.get(key)
            if val is None: return m.group(0)
            tail = text[m.end():]
            mj = re.match(rf"({JOSA_ALT})(?![가-힣])", tail)
            if mj and val and 0xAC00 <= ord(val[-1]) <= 0xD7A3:
                return val + "\x00" + fix_josa(val, mj.group(1))  # 마커로 원 조사 소비 표시
            return val
        out = TOK.sub(repl, text)
        # 마커 처리: 마커 뒤 교정 조사를 쓰고 원래 조사는 버린다
        def consume(m):
            fixed = m.group(1)
            return fixed
        out = re.sub(rf"\x00({JOSA_ALT})({JOSA_ALT})", lambda m: m.group(1), out)
        out = re.sub(rf"\x00({JOSA_ALT})", lambda m: m.group(1), out)
        return out

RESTORE = [
 ("홍길동", "<PERSON_1>가 문의하신 건", "홍길동이 문의하신 건"),
 ("이수아", "<PERSON_1>이 요청하신 환불", "이수아가 요청하신 환불"),
 ("김철수", "<PERSON_1>은 확인됨", "김철수는 확인됨"),
 ("박서준", "<person_1>를 배정", "박서준을 배정"),
 ("최유나", "< PERSON_1 >을 확인", "최유나를 확인"),
 ("서울", "<PLACE_1>으로 발송", "서울로 발송"),
 ("부산", "<PLACE_1>로 발송", "부산으로 발송"),
]
print("① 복원 + 조사 교정")
ok = 0
for val, resp, exp in RESTORE:
    ent = re.match(r"<\s*([A-Za-z_]+)\s*_", resp).group(1).upper()
    v = Vault(); v.mask(val, [(0, len(val), ent)])
    got = v.restore(resp); good = got == exp; ok += good
    print(f"  {'OK ' if good else 'FAIL'} {resp:<30} → {got:<26} {'' if good else '기대: '+exp}")
print(f"  {ok}/{len(RESTORE)} 통과")

print("\n② 과교정 위험 — 토큰이 없는 일반 텍스트는 손대지 않아야 한다")
RISK = ["이케아 매장에 다녀왔습니다","노키아 단말을 지원합니다","볼리비아 커피",
        "메타와 구글이 발표했다","충치가 생겼어요","먹이를 주었다"]
v = Vault(); bad = 0
for s in RISK:
    out = v.restore(s); same = out == s; bad += (not same)
    print(f"  {'OK ' if same else '★훼손'} {s:<26} → {out}")
print(f"  원문 훼손 {bad}/{len(RISK)}건")
print(f"\n결론: 토큰 뒤로 범위를 한정하니 복원 {ok}/{len(RESTORE)}, 훼손 {bad}건.")
