"""상태 있는 서비스 백엔드 — 실 운영 도구의 부작용을 가진 세계.

평가용 mock ToolWorld(부작용 없음)와 달리, 서비스는 이 ServiceWorld 를 써서
환불·이메일 발송에 **실제 부작용(원장 기록·outbox)**과 **idempotency(중복 실행 방지)**를 갖는다.
실 외부 API(Stripe/SMTP) 연결 지점은 issue_refund/send_email 내부의 TODO 로 표시.

- 비가역 도구(issue_refund)는 idempotency_key(=call_id/승인 id)로 중복 환불을 막는다.
- 모든 부작용은 원장/outbox 에 남아 /v1/audit 로 관측된다.
- lookup/search/supervisor 는 ToolWorld 를 상속(데모 데이터 재사용).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .toolworld import ToolWorld, default_world


@dataclass
class ServiceWorld(ToolWorld):
    """부작용을 가진 운영 세계. 요청 간 공유되는 싱글턴(앱 기동 시 1개)."""

    refunds: dict[str, dict] = field(default_factory=dict)   # idempotency_key → refund record
    outbox: list[dict] = field(default_factory=list)         # 발송된 이메일 기록
    _sent_keys: set = field(default_factory=set)             # send_email dedup(idempotency)
    _refund_seq: int = 0

    def issue_refund(self, account: str = "", amount: int = 0, reason: str = "",
                     idempotency_key: str = "") -> dict[str, Any]:
        """비가역 환불 — idempotency_key 로 중복 실행 방지. 실 결제 API 연결 지점."""
        key = idempotency_key or f"{account}:{amount}"
        if key in self.refunds:                              # 이미 처리됨 → 같은 결과 반환(이중환불 방지)
            return {**self.refunds[key], "idempotent_replay": True}
        self._refund_seq += 1
        rec = {"ok": True, "refund_id": f"rf_{self._refund_seq:05d}",
               "account": account, "amount": amount, "reason": reason, "status": "issued"}
        # TODO(실운영): 여기서 Stripe 등에 Idempotency-Key=key 헤더로 실제 환불 호출.
        self.refunds[key] = rec
        return rec

    def send_email(self, to: str = "", subject: str = "", body: str = "",
                   idempotency_key: str = "") -> dict[str, Any]:
        """이메일 발송 — outbox 에 기록(idempotency_key 로 중복 발송 방지). 실 SMTP/ESP 연결 지점."""
        if idempotency_key and idempotency_key in self._sent_keys:
            return {"sent": True, "to": to, "subject": subject, "idempotent_replay": True}
        rec = {"sent": True, "to": to, "subject": subject, "body": body,
               "idempotency_key": idempotency_key}
        # TODO(실운영): 여기서 SMTP/SES 등으로 실제 발송.
        self.outbox.append(rec)
        if idempotency_key:
            self._sent_keys.add(idempotency_key)
        return {"sent": True, "to": to, "subject": subject}

    def audit(self) -> dict[str, Any]:
        """관측용 — 처리된 환불·발송 이메일 요약. ★계좌 등 원본 PII 는 마스킹★(approvals 와 동일)."""
        from .hitl import _mask
        return {
            "refunds": [{"refund_id": r["refund_id"], "account": _mask("account", r["account"]),
                         "amount": r["amount"], "status": r["status"]}
                        for r in self.refunds.values()],
            "outbox": [{"to": _mask("to", e["to"]), "subject": e["subject"]} for e in self.outbox],
            "refund_count": len(self.refunds), "email_count": len(self.outbox),
        }


def service_world() -> ServiceWorld:
    """데모 데이터(고객/KB)를 재사용하되 부작용을 가진 서비스 세계를 만든다."""
    base = default_world()
    return ServiceWorld(customers=base.customers, kb_docs=base.kb_docs,
                        supervisor_notes=base.supervisor_notes)
