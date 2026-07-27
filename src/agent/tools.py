"""mock 도구 5종.

전부 부작용이 없다. 외부로 나가는 척만 하고 결과를 트레이스에 남긴다.
`search_kb` 와 `escalate_to_human` 의 출력은 **비신뢰**(인젝션 벡터)로 표시된다.

D1 에서는 CRM/문서스토어를 최소 스텁으로 둔다. 실제 합성 한국어 CRM 과
오염 KB 문서는 D2(toolworld.py)에서 채운다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .taint import Tainted


@dataclass
class ToolOutput:
    """도구 실행 결과. untrusted=True 면 after_tool 에서 Tainted 로 래핑된다."""

    value: Any
    untrusted: bool = False
    source: str = ""            # taint 출처 라벨 (예: "search_kb#0")


# ── mock 구현 ─────────────────────────────────────────────────────────────
# 실제 데이터는 toolworld 가 주입한다. 여기서는 계약과 신뢰 경계만 확정한다.


def lookup_customer(rrn: str = "", phone: str = "", *, world: Any = None) -> ToolOutput:
    """내부 신뢰 도구 — 실제 PII 를 반환한다(내부 rehydrate 대상)."""
    rec = world.lookup(rrn=rrn, phone=phone) if world else {"name": "홍길동", "found": True}
    return ToolOutput(value=rec, untrusted=False)


def search_kb(query: str = "", *, world: Any = None) -> ToolOutput:
    """지식베이스 검색 — 출력은 비신뢰(인젝션 벡터)."""
    docs = world.search(query) if world else [{"title": "샘플", "body": "정상 문서"}]
    return ToolOutput(value=docs, untrusted=True, source="search_kb")


def issue_refund(account: str = "", amount: int = 0, reason: str = "",
                 *, world: Any = None, idempotency_key: str = "") -> ToolOutput:
    """비가역 외부 싱크 — 환불 실행. 게이트를 통과해야만 여기 도달한다.
    world 가 부작용을 지원(ServiceWorld)하면 원장 기록+idempotency, 아니면 mock."""
    if hasattr(world, "issue_refund"):
        return ToolOutput(value=world.issue_refund(account, amount, reason, idempotency_key),
                          untrusted=False)
    return ToolOutput(value={"ok": True, "account": account, "amount": amount}, untrusted=False)


def send_email(to: str = "", subject: str = "", body: str = "",
               *, world: Any = None, idempotency_key: str = "") -> ToolOutput:
    """외부 싱크 — 이메일 발송. world 가 부작용을 지원하면 outbox 기록, 아니면 mock."""
    if hasattr(world, "send_email"):
        return ToolOutput(value=world.send_email(to, subject, body, idempotency_key),
                          untrusted=False)
    return ToolOutput(value={"sent": True, "to": to, "subject": subject}, untrusted=False)


def escalate_to_human(ticket_id: str = "", note: str = "",
                      *, world: Any = None) -> ToolOutput:
    """상급자 에스컬레이션 — supervisor_note 가 2차 인젝션 벡터라 출력은 비신뢰."""
    reply = world.supervisor(ticket_id) if world else {"supervisor_note": "확인함"}
    return ToolOutput(value=reply, untrusted=True, source="escalate_to_human")


REGISTRY: dict[str, Callable[..., ToolOutput]] = {
    "lookup_customer": lookup_customer,
    "search_kb": search_kb,
    "issue_refund": issue_refund,
    "send_email": send_email,
    "escalate_to_human": escalate_to_human,
}


def execute(name: str, args: dict[str, Any], call_id: str, *, world: Any = None,
            taint_enabled: bool = False) -> Any:
    """도구를 실행하고, 비신뢰 출력이면 (taint on 시) Tainted 로 래핑한다."""
    fn = REGISTRY.get(name)
    if fn is None:
        raise KeyError(f"알 수 없는 도구: {name}")
    if name in ("issue_refund", "send_email"):     # 비가역/외부 싱크는 idempotency_key(=call_id) 전달
        out = fn(**args, world=world, idempotency_key=call_id)
    else:
        out = fn(**args, world=world)
    if out.untrusted and taint_enabled:
        src = f"{out.source or name}#{call_id.split('#')[-1] if '#' in call_id else 0}"
        return Tainted(value=out.value, sources=frozenset({src}))
    return out.value
