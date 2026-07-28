"""인증 주체(Principal) + 인가 — confused-deputy 를 구조적으로 차단.

핵심 아이디어(taint 휴리스틱이 아니라 구조적 바인딩):
  send_email 의 수신자는 **인증된 principal 이 담당하는 고객의 등록 이메일**이어야 한다.
  → 오염 KB 가 수신자를 attacker@customer.example.com 로 재지정해도, 그것은 어떤 등록 고객의
    이메일도 아니므로 차단된다(allowlist 도메인 안이어도). issue_refund 의 계좌도 마찬가지로
    담당 고객의 등록 계좌여야 한다.

인메모리 API-키 레지스트리(데모/파일럿). 실운영은 IdP/OAuth + 고객-상담원 배정 DB로 확장.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from .hooks import Block

DEMO_KEYS = ("demo-agent-key", "demo-approver-key", "demo-auditor-key")


@dataclass(frozen=True)
class Principal:
    id: str
    role: str = "agent"                                                   # agent | approver | auditor
    customer_ids: frozenset[str] = field(default_factory=frozenset)
    verified_emails: frozenset[str] = field(default_factory=frozenset)   # 발송 허용 수신자
    verified_accounts: frozenset[str] = field(default_factory=frozenset)  # 환불 허용 계좌
    verified_phones: frozenset[str] = field(default_factory=frozenset)   # 조회 허용 전화(읽기 스코프)
    verified_rrns: frozenset[str] = field(default_factory=frozenset)     # 조회 허용 주민번호(읽기 스코프)


def _norm_env(app_env: str | None) -> str:
    """APP_ENV 정규화 — 공백/대소문자 무시. server 와 authz 가 동일 규칙을 쓰게 하는 단일 파서(P0-5)."""
    return (app_env or "").strip().lower()


def _demo_registry(world: Any) -> dict[str, Principal]:
    """데모 키 3종 — 역할분리(직무분리): 에이전트 실행 / 승인 / 감사 키를 분리한다.
    ⚠️ 비-prod 전용. 실운영은 IdP/OAuth 역할 클레임 또는 주입 자격증명으로 교체."""
    custs = getattr(world, "customers", []) or []
    scope = dict(
        customer_ids=frozenset(c["id"] for c in custs),
        verified_emails=frozenset(c["email"] for c in custs if c.get("email")),
        verified_accounts=frozenset(c["account"] for c in custs if c.get("account")),
        verified_phones=frozenset(c["phone"] for c in custs if c.get("phone")),
        verified_rrns=frozenset(c["rrn"] for c in custs if c.get("rrn")),
    )
    return {
        "demo-agent-key": Principal(id="agent:demo", role="agent", **scope),
        "demo-approver-key": Principal(id="approver:demo", role="approver", **scope),
        "demo-auditor-key": Principal(id="auditor:demo", role="auditor", **scope),
    }


def _registry_from_injected(raw: str, world: Any) -> dict[str, Principal]:
    """주입 자격증명(env SAFEAGENT_API_KEYS, JSON)에서 레지스트리 구성.

    형식: {"<api-key>": {"id","role","customer_ids":[...],
                          "verified_emails":[...],"verified_accounts":[...]}}
    scope 필드 생략 시 customer_ids 로 world 고객에서 유도(테넌트/고객 스코프 강제).
    실운영은 이 JSON 을 Secret Manager/IdP 에서 주입한다(코드/이미지에 넣지 않음)."""
    custs = {c["id"]: c for c in (getattr(world, "customers", []) or [])}
    conf = json.loads(raw)
    reg: dict[str, Principal] = {}
    for key, v in conf.items():
        if key in DEMO_KEYS:
            raise RuntimeError(f"주입 자격증명에 데모 키({key})를 쓸 수 없습니다(prod 금지).")
        cids = frozenset(v.get("customer_ids", []))
        emails = v.get("verified_emails")
        accts = v.get("verified_accounts")
        # scope 미지정 시 담당 고객에서 유도(전체 flat scope 아님 — 지정 고객만).
        if emails is None:
            emails = [custs[c]["email"] for c in cids if c in custs and custs[c].get("email")]
        if accts is None:
            accts = [custs[c]["account"] for c in cids if c in custs and custs[c].get("account")]
        phones = [custs[c]["phone"] for c in cids if c in custs and custs[c].get("phone")]
        rrns = [custs[c]["rrn"] for c in cids if c in custs and custs[c].get("rrn")]
        reg[key] = Principal(id=v.get("id", "principal"), role=v.get("role", "agent"),
                             customer_ids=cids, verified_emails=frozenset(emails),
                             verified_accounts=frozenset(accts), verified_phones=frozenset(phones),
                             verified_rrns=frozenset(rrns))
    if not reg:
        raise RuntimeError("SAFEAGENT_API_KEYS 가 비어 있습니다.")
    return reg


def build_registry(world: Any, *, app_env: str | None = None) -> dict[str, Principal]:
    """환경별 principal 레지스트리. ★prod 는 데모 키를 절대 등록하지 않는다★.

    - prod: env SAFEAGENT_API_KEYS(주입 자격증명) 필수 — 없으면 부팅 실패(fail-closed).
    - 그 외(dev/test): 주입 있으면 사용, 없으면 데모 키 3종.
    """
    env = _norm_env(app_env if app_env is not None else os.getenv("APP_ENV"))
    injected = os.getenv("SAFEAGENT_API_KEYS")
    # ★fail-closed★: 데모 키는 오직 명시적 dev/test 에서만. prod 및 알 수 없는 값은 주입 자격증명 필수
    #   (공백·오타·대문자 APP_ENV 로 데모 키가 새는 fail-open 방지 — P0-5).
    if env in ("dev", "test", ""):
        if injected:
            return _registry_from_injected(injected, world)
        return _demo_registry(world)
    # prod 또는 미지정 허용목록 밖(오타 포함) → 주입 필수
    if not injected:
        raise RuntimeError(
            f"APP_ENV='{env}'(비 dev/test)인데 SAFEAGENT_API_KEYS(주입 자격증명)가 없습니다. "
            "데모 키 등록을 거부합니다 — Secret Manager/IdP 로 자격증명을 주입하거나 APP_ENV=dev 를 쓰세요.")
    return _registry_from_injected(injected, world)


def registry_from_world(world: Any) -> dict[str, Principal]:
    """하위호환 별칭 — 환경 인지 build_registry 로 위임(비-prod 는 데모 키)."""
    return build_registry(world)


def check_action(principal: Principal, tool: str, args: dict, tools_spec: dict) -> Block | None:
    """복원된(실행) 인자에 대해 principal 스코프를 강제. 위반 시 Block, 통과 시 None.

    ★egress allowlist 는 '인가'가 아니다(도메인만 봄) → 여기서 principal↔데이터주체를 바인딩.★
    """
    if tool == "send_email":
        to = args.get("to")
        if isinstance(to, str) and to not in principal.verified_emails:
            return Block(rule="authz.recipient_unverified",
                         reason=f"수신자 {to} 는 담당 고객의 등록 이메일이 아님(confused-deputy 차단)",
                         decision="BLOCK")
    elif tool == "issue_refund":
        acct = args.get("account")
        if isinstance(acct, str) and acct not in principal.verified_accounts:
            return Block(rule="authz.account_unverified",
                         reason="환불 계좌가 담당 고객의 등록 계좌가 아님",
                         decision="BLOCK")
    return None
