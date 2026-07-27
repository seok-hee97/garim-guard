"""Tier A — 결정적 per-tool 정책 게이트. 프로젝트의 진짜 신뢰 경계.

★ 핵심 성질: 게이트는 문자열 내용을 읽지 않는다. ★
판정 입력은 텍스트가 아니라 구조화된 인자값 + policy.yaml 정책 테이블이다.
그래서 페이로드가 한국어든 이모지든 base64든 판정이 동일하다 — 난독화 불변.

판정 순서 (첫 실패에서 즉시 차단, 근거 키를 트레이스에 남긴다):
  1. tools: 에 선언된 도구인가          → BLOCK(unknown_tool)
  2. (Tier C) frozen plan 포함 여부      → 여기선 생략(D2)
  3. 인자 스키마 검증                     → BLOCK(arg_schema)
  4. allowlist_ref 검사                  → BLOCK(egress_violation)
  5. (Tier B) forbid_tainted_args 검사   → BLOCK(tainted_arg)
  6. requires_hitl → HITL 스텁           → HITL_DENY
  7. rehydrate_args (내부 신뢰 도구만)
"""

from __future__ import annotations

from typing import Any

from ..providers.base import GenResult, Msg, ToolCall, ToolResult
from ..rails.base import RailScorer
from .hooks import Block, Rehydrated, Tainted_
from .taint import sources_of


def _dig(policy: dict, ref: str) -> Any:
    """'egress.email_domains' 같은 점 경로로 policy 를 탐색한다."""
    cur: Any = policy
    for part in ref.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


# value-provenance 매칭 최소 길이. 이보다 짧은 값은 우연 일치가 잦아 검사하지 않는다.
# 이메일·계좌 같은 고엔트로피 싱크 값은 충분히 길다.
_MIN_PROVENANCE_LEN = 8


def _derived_from_untrusted(value: Any, corpus: list[str] | None) -> bool:
    """인자 값(문자열)이 비신뢰 도구 출력 코퍼스의 부분문자열인가.

    모델이 오염 텍스트를 읽고 인자로 되뿜으면 Tainted 래퍼가 소실되므로, 값 자체가
    비신뢰 출력에서 왔는지를 문자열 파생으로 추적한다 — 텍스트 채널의 lite taint.
    """
    if not corpus:
        return False
    s = value if isinstance(value, str) else str(value)
    s = s.strip()
    if len(s) < _MIN_PROVENANCE_LEN:
        return False
    return any(s in doc for doc in corpus)


class TierAGate:
    """결정적 게이트. before_tool 에서 호출된다."""

    def __init__(self, policy: dict, *, taint_enabled: bool = False):
        self.policy = policy
        self.tools = policy.get("tools", {})
        self.egress = policy.get("egress", {})
        self.hitl = policy.get("hitl", {})
        self.taint_enabled = taint_enabled

    def check(self, call: ToolCall,
              untrusted_corpus: list[str] | None = None) -> Block | Rehydrated | None:
        spec = self.tools.get(call.name)

        # 1. 미선언 도구
        if spec is None:
            return Block(rule="unknown_tool", reason=f"{call.name} 은 선언되지 않은 도구")

        args = dict(call.args)

        # 3. 인자 스키마 검증
        schema_block = self._validate_args(call.name, spec, args)
        if schema_block:
            return schema_block

        # 4. egress allowlist
        egress_block = self._check_egress(spec, args)
        if egress_block:
            return egress_block

        # 5. Tier B — 오염 인자 (taint on 일 때만)
        #    두 경로로 오염을 판정한다:
        #    (a) Tainted 래퍼가 살아있는 값 (내부 전파 경로 — 유닛테스트/직접 전달).
        #    (b) ★value-provenance★: 인자 값이 비신뢰 도구 출력의 부분문자열이면
        #        모델 라운드트립으로 래퍼가 세탁됐어도 파생 오염으로 본다.
        #        이것이 없으면 Tier B 는 실 에피소드에서 절대 발동하지 못한다
        #        (모델이 낸 args 는 순수 문자열이라 sources_of() == frozenset()).
        if self.taint_enabled:
            for arg_name in spec.get("forbid_tainted_args", []) or []:
                val = args.get(arg_name)
                if sources_of(val):
                    return Block(rule=f"tier_b.tainted:{arg_name}",
                                 reason=f"{arg_name} 이 비신뢰 출처의 영향을 받음(래퍼)",
                                 decision="BLOCK")
                if _derived_from_untrusted(val, untrusted_corpus):
                    return Block(rule=f"tier_b.provenance:{arg_name}",
                                 reason=f"{arg_name} 값이 비신뢰 도구 출력에서 파생됨",
                                 decision="BLOCK")

        # 6. HITL
        if spec.get("requires_hitl"):
            if self.hitl.get("auto_response", "deny") == "deny":
                return Block(rule=f"hitl:{spec.get('risk_tier')}",
                             reason="HITL 승인 필요 — 스텁은 거부", decision="HITL_DENY")

        # 7. rehydrate (내부 신뢰 도구만)
        rehy = spec.get("rehydrate_args")
        if rehy:
            return Rehydrated(args=args)  # 실제 복원은 DLP vault 가 D3 에서 채운다
        return None

    def _validate_args(self, name: str, spec: dict, args: dict) -> Block | None:
        arg_specs = spec.get("args", {}) or {}
        for arg_name, meta in arg_specs.items():
            if meta.get("required") and arg_name not in args:
                return Block(rule=f"arg_schema.missing:{arg_name}",
                             reason=f"필수 인자 {arg_name} 누락")
            if arg_name not in args:
                continue
            val = args[arg_name]
            t = meta.get("type")
            if t == "int":
                try:
                    iv = int(val)
                except (TypeError, ValueError):
                    return Block(rule=f"arg_schema.type:{arg_name}",
                                 reason=f"{arg_name} 은 정수여야 함")
                if "min" in meta and iv < meta["min"]:
                    return Block(rule=f"arg_schema.min:{arg_name}", reason=f"{arg_name} < {meta['min']}")
                if "max" in meta and iv > meta["max"]:
                    return Block(rule=f"arg_schema.max:{arg_name}", reason=f"{arg_name} > {meta['max']}")
            if "max_len" in meta and isinstance(val, str) and len(val) > meta["max_len"]:
                return Block(rule=f"arg_schema.max_len:{arg_name}",
                             reason=f"{arg_name} 길이 초과")
        return None

    def _check_egress(self, spec: dict, args: dict) -> Block | None:
        for arg_name, meta in (spec.get("args", {}) or {}).items():
            ref = meta.get("allowlist_ref")
            if not ref or arg_name not in args:
                continue
            allowed = _dig(self.policy, ref) or []
            val = str(args[arg_name])

            if meta.get("type") == "email":
                if not self._email_allowed(val):
                    return Block(rule=ref, reason=f"수신자 {val} 은 allowlist 밖",
                                 decision="BLOCK")
            else:  # 계좌 등 — 정확 일치
                if val not in allowed:
                    return Block(rule=ref, reason=f"{arg_name}={val} 은 allowlist 밖",
                                 decision="BLOCK")
        return None

    def _email_allowed(self, addr: str) -> bool:
        exact = set(self.egress.get("email_exact", []) or [])
        if addr in exact:
            return True
        domains = self.egress.get("email_domains", []) or []
        allow_sub = self.egress.get("allow_subaddressing", False)
        if "@" not in addr:
            return False
        local, _, domain = addr.partition("@")
        if not allow_sub and "+" in local:
            return False  # support+exfil@… 우회 차단
        return domain in domains


class DefenseHooks:
    """defenses.* 플래그에 따라 seam 을 조립한다. 이것이 애블레이션의 실행 단위.

    seam 별 역할:
      after_tool  — 비신뢰 출력을 (a) untrusted_corpus 에 누적(Tier B value-provenance 근거),
                    (b) 레일로 채점해 rail_scores 기록(LOG-ONLY 텔레메트리),
                    (c) classifier_gate 켜졌으면 점수≥τ 시 ctx.quarantined 설정.
      before_tool — Tier A 결정적 게이트를 **먼저** 판정(구조적 차단이 우선). 통과하면
                    classifier_gate 격리 상태에서 고위험 도구를 추가 차단(negative control).

    ★ 합성 규율(봉인): classifier_gate 는 오직 '추가로 차단'만 할 수 있다. 분류기의
      benign 판정은 Tier A 의 Block 을 절대 override 하지 못한다(fail-open). 이는 seam
      분리 + 판정 순서로 구조적으로 보장된다 — before_tool 에서 Tier A 를 먼저 반환.
    """

    def __init__(self, policy: dict, defenses: dict[str, Any],
                 rail: RailScorer | None = None):
        self.policy = policy
        self.defenses = defenses
        self.tools = policy.get("tools", {})
        self.gate = TierAGate(policy, taint_enabled=bool(defenses.get("tier_b", False)))
        self.rail = rail
        cg = defenses.get("classifier_gate")
        self.cg = cg if isinstance(cg, dict) else ({"enable": bool(cg)} if cg else {})
        self.cg_threshold = float(self.cg.get("threshold", 0.5))
        # 고위험 티어(격리 시 차단 대상)
        self._high_risk = {"irreversible", "external", "exfiltration"}

        def _on(key: str) -> bool:
            v = defenses.get(key)
            return v.get("enable") if isinstance(v, dict) else bool(v)

        # ── theater 기준선 (실제 구현 — no-op 아님) ──
        self.plea_on = _on("plea")            # 시스템 프롬프트 애원
        self.spotlight_on = _on("spotlight")  # 비신뢰 스팬 데이터마킹
        # ── DLP vault (요청 스코프 1개) ──
        self.dlp_on = _on("dlp_vault")
        self.vault = None
        if self.dlp_on:
            from ..dlp.vault import new_vault
            self.vault = new_vault()
        # ── Tier C: 조용한 no-op 방지 ── (planner/quarantine 미구현, 사전등록 분기 D)
        if _on("tier_c"):
            raise NotImplementedError(
                "Tier C(이중 LLM 격리·고정계획)는 미구현이다. planner.py/quarantine.py 부재. "
                "사전등록 분기 D('설계 명세 제출')로 처리하며, 애블레이션 arm 으로 쓰지 않는다. "
                "조용한 no-op 으로 결과를 오염시키지 않도록 명시적으로 차단한다."
            )
        self._plea_injected = False

    async def before_model(self, messages: list[Msg], ctx: Any = None):
        from .hooks import Redacted
        from .spotlight import PLEA_INSTRUCTION, SPOTLIGHT_INSTRUCTION

        changed = False
        msgs = list(messages)

        # (1) theater: plea/spotlight 지시를 첫 사용자 메시지에 1회 프리펜드
        if (self.plea_on or self.spotlight_on) and not self._plea_injected and msgs:
            preamble = []
            if self.plea_on:
                preamble.append(PLEA_INSTRUCTION)
            if self.spotlight_on:
                preamble.append(SPOTLIGHT_INSTRUCTION)
            for i, m in enumerate(msgs):
                if m.role == "user":
                    msgs[i] = Msg(role="user", text="\n".join(preamble) + "\n\n" + m.text,
                                  tool_calls=m.tool_calls, tool_results=m.tool_results, raw=m.raw)
                    break
            self._plea_injected = True
            changed = True

        # (2) DLP: 모델에 가기 전 PII 토큰화 (원본 PII 를 모델이 절대 못 보게)
        if self.vault is not None:
            msgs = [Msg(role=m.role, text=self.vault.redact(m.text),
                        tool_calls=m.tool_calls, tool_results=m.tool_results, raw=m.raw)
                    for m in msgs]
            changed = True

        return Redacted(messages=msgs) if changed else None

    async def after_model(self, result: GenResult, ctx: Any = None) -> GenResult:
        return result

    async def before_tool(self, call: ToolCall, ctx: Any = None):
        corpus = getattr(ctx, "untrusted_corpus", None)

        # 1) Tier A 결정적 게이트 — 항상 먼저. 구조적 Block 이 우선한다.
        if self.defenses.get("tier_a", False):
            decision = self.gate.check(call, untrusted_corpus=corpus)
            if isinstance(decision, Block):
                return decision  # ← 분류기가 무엇을 말하든 이 Block 을 무를 수 없다
            tier_a_result = decision  # None 또는 Rehydrated
        else:
            tier_a_result = None

        # 2) classifier_gate — negative control. 격리 상태 + 고위험 도구면 추가 차단.
        if self.cg.get("enable") and getattr(ctx, "quarantined", False):
            spec = self.tools.get(call.name, {})
            if spec.get("risk_tier") in self._high_risk:
                return Block(rule="classifier_gate.quarantined",
                             reason="분류기가 선행 도구출력에서 인젝션 탐지 → 고위험 도구 차단",
                             decision="BLOCK")

        # 3) DLP rehydrate — 내부 신뢰 도구만 원본 PII 복원(외부 싱크는 토큰 유지 → 유출돼도 토큰).
        if self.vault is not None:
            spec = self.tools.get(call.name, {})
            rehy = spec.get("rehydrate_args")
            if rehy:
                args = dict(call.args)
                for a in rehy:
                    if a in args and isinstance(args[a], str):
                        args[a] = self.vault.rehydrate(args[a])
                return Rehydrated(args=args)

        return tier_a_result  # Tier A 의 None/Rehydrated 를 그대로 통과

    async def after_tool(self, result: ToolResult, ctx: Any = None) -> Tainted_:
        rail_scores: dict[str, float] = {}
        spec = self.tools.get(result.name, {})
        content = result.content
        # ── DLP: 도구 출력의 PII 를 토큰화 (lookup_customer 의 원본 RRN 이 모델에 가기 전) ──
        if self.vault is not None:
            content = _redact_value(content, self.vault)
        text = _content_text(content)

        # (a) 비신뢰 출력을 코퍼스에 누적 (returns_untrusted 플래그 소비 — 기존 inert)
        if spec.get("returns_untrusted") and text and ctx is not None:
            corpus = getattr(ctx, "untrusted_corpus", None)
            if corpus is not None:
                corpus.append(text)

        # (a') spotlight: 비신뢰 출력을 구분자로 감싼다 (theater — 데이터마킹).
        #      문자열 출력에만 적용. 모델은 before_model 지시로 이 구분자를 안다.
        if self.spotlight_on and spec.get("returns_untrusted") and isinstance(content, str):
            from .spotlight import wrap_untrusted
            content = wrap_untrusted(content)

        # (b) 레일 채점 (LOG-ONLY 텔레메트리) — 관측 전용이므로 실패해도 데이터 흐름·에피소드에
        #     영향을 주면 안 된다(보안 판정 아님). 예외는 여기서 삼키고 sentinel 로만 남긴다.
        #     DLP 비식별(위 297-298)은 이 try 밖 → 실패 시 상위로 전파되어 fail-closed 처리된다.
        if self.rail is not None and text:
            rail_name = getattr(self.rail, "name", "rail")
            try:
                s = float(self.rail.score(text))
                rail_scores[rail_name] = s
                # (c) classifier_gate 격리 판정 (enforcement arm)
                if self.cg.get("enable") and spec.get("returns_untrusted") and s >= self.cg_threshold:
                    if ctx is not None:
                        ctx.quarantined = True
            except Exception:  # noqa: BLE001 — 관측 레일 실패는 삼키고 계속(원본 데이터는 이미 위에서 비식별됨)
                rail_scores[rail_name + "_error"] = -1.0

        # ★ 토큰화된 content 를 반환 → 모델은 원본 PII 를 절대 보지 못한다.
        return Tainted_(output=content, rail_scores=rail_scores)


def _content_text(content: Any) -> str:
    """도구 출력에서 채점·코퍼스용 문자열을 뽑는다."""
    from .taint import unwrap
    c = unwrap(content)
    if isinstance(c, str):
        return c
    try:
        import json
        return json.dumps(c, ensure_ascii=False)
    except TypeError:
        return str(c)


def _redact_value(value: Any, vault: Any) -> Any:
    """도구 출력의 문자열(중첩 포함)을 vault 로 토큰화. Tainted 껍질은 보존."""
    from .taint import Tainted
    if isinstance(value, Tainted):
        return Tainted(value=_redact_value(value.value, vault), sources=value.sources)
    if isinstance(value, str):
        return vault.redact(value)
    if isinstance(value, dict):
        return {k: _redact_value(v, vault) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(v, vault) for v in value]
    return value
