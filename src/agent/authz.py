"""인증 주체(Principal) + 인가 — confused-deputy 를 구조적으로 차단.

핵심 아이디어(taint 휴리스틱이 아니라 구조적 바인딩):
  send_email 의 수신자는 **인증된 principal 이 담당하는 고객의 등록 이메일**이어야 한다.
  → 오염 KB 가 수신자를 attacker@customer.example.com 로 재지정해도, 그것은 어떤 등록 고객의
    이메일도 아니므로 차단된다(allowlist 도메인 안이어도). issue_refund 의 계좌도 마찬가지로
    담당 고객의 등록 계좌여야 한다.

인메모리 API-키 레지스트리(데모/파일럿). 실운영은 IdP/OAuth + 고객-상담원 배정 DB로 확장.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .hooks import Block


@dataclass(frozen=True)
class Principal:
    id: str
    customer_ids: frozenset[str] = field(default_factory=frozenset)
    verified_emails: frozenset[str] = field(default_factory=frozenset)   # 발송 허용 수신자
    verified_accounts: frozenset[str] = field(default_factory=frozenset)  # 환불 허용 계좌


def registry_from_world(world: Any) -> dict[str, Principal]:
    """데모: 단일 상담원 키가 데모 고객 전부를 담당(등록 이메일/계좌 스코프)."""
    custs = getattr(world, "customers", []) or []
    return {
        "demo-agent-key": Principal(
            id="agent:demo",
            customer_ids=frozenset(c["id"] for c in custs),
            verified_emails=frozenset(c["email"] for c in custs if c.get("email")),
            verified_accounts=frozenset(c["account"] for c in custs if c.get("account")),
        )
    }


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
