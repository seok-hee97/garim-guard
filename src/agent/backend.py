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
    _sent: dict[str, dict] = field(default_factory=dict)     # idempotency_key → 원본 발송 결과(replay 정직성)
    _refund_seq: int = 0

    def issue_refund(self, account: str = "", amount: int = 0, reason: str = "",
                     idempotency_key: str = "", customer_id: str = "", principal_id: str = "") -> dict[str, Any]:
        """비가역 환불 — idempotency_key 로 중복 실행 방지. 실 결제 API 연결 지점.

        ★transactional-outbox 상태 모델(P1)★: 실 API 호출 '전에' status='submitted' 로 레코드를
        먼저 기록(durable-write-first)하고, 성공 시 'completed' 로 전이한다. 그 사이 크래시/타임아웃이
        나면 레코드가 'submitted'(=미확인)로 남아 재조정(reconcile)이 가능하다 — 유실도 이중실행도 아님.
        (현재는 인메모리·동기. 실운영은 이 레코드를 DB 에 쓰고 별도 워커가 completed 로 전이한다.)"""
        key = idempotency_key or f"{account}:{amount}"
        if key in self.refunds:                              # 이미 처리됨 → 같은 결과 반환(이중환불 방지)
            return {**self.refunds[key], "idempotent_replay": True}
        self._refund_seq += 1
        rec = {"ok": True, "refund_id": f"rf_{self._refund_seq:05d}",
               "account": account, "amount": amount, "reason": reason, "status": "submitted",
               "customer_id": customer_id, "principal_id": principal_id}   # 불변 소유권 메타(감사 scope)
        self.refunds[key] = rec                              # 1) durable write-first(submitted)
        # TODO(실운영): 2) 여기서 Stripe 등에 Idempotency-Key=key 헤더로 실제 환불 호출(별도 워커 가능).
        rec["status"] = "completed"                          # 3) 성공 전이(실패 시 submitted 로 잔존)
        return rec

    def send_email(self, to: str = "", subject: str = "", body: str = "",
                   idempotency_key: str = "", customer_id: str = "", principal_id: str = "") -> dict[str, Any]:
        """이메일 발송 — outbox 에 기록(idempotency_key 로 중복 발송 방지). 실 SMTP/ESP 연결 지점.

        ★재생(replay)은 재시도 인자가 아니라 '저장된 원본 발송 결과'를 반환한다(P0-2)★ —
        같은 키로 다른 본문을 재시도해도 실제 발송된 것(원본)을 정직하게 보고한다."""
        if idempotency_key and idempotency_key in self._sent_keys:
            orig = self._sent.get(idempotency_key, {})
            return {"sent": True, "to": orig.get("to"), "subject": orig.get("subject"),
                    "idempotent_replay": True}
        rec = {"sent": True, "to": to, "subject": subject, "body": body,
               "idempotency_key": idempotency_key, "status": "submitted",
               "customer_id": customer_id, "principal_id": principal_id}   # 불변 소유권 메타(감사 scope)
        self.outbox.append(rec)                       # 1) durable write-first(submitted)
        # TODO(실운영): 2) 여기서 SMTP/SES 등으로 실제 발송(별도 워커 가능).
        rec["status"] = "completed"                   # 3) 성공 전이(실패 시 submitted 잔존→재조정)
        if idempotency_key:
            self._sent_keys.add(idempotency_key)
            self._sent[idempotency_key] = {"to": to, "subject": subject}   # 원본 발송 결과 보존
        return {"sent": True, "to": to, "subject": subject}

    def audit(self, customer_ids: frozenset[str] | None = None) -> dict[str, Any]:
        """관측용 — 처리된 환불·발송 이메일 요약. ★계좌 등 원본 PII 는 마스킹★(approvals 와 동일).

        ★테넌트 scope(R3-3 강화)★ — 각 레코드에 **실행 시점 각인된 불변 소유권 메타
        (record['customer_id'])**로 필터한다. 이메일·계좌 역매핑은 중복/재사용 시 마지막
        매핑 고객에 오귀속돼 교차노출이 재발하므로 쓰지 않는다. customer_id 미기록(구식/빈)
        레코드는 scope 감사자에게 **노출하지 않는다(fail-closed)**. None 이면 전체(글로벌 감사)."""
        from .hitl import _mask

        def _in(owner: str) -> bool:
            return customer_ids is None or (bool(owner) and owner in customer_ids)

        refunds = [r for r in self.refunds.values() if _in(r.get("customer_id", ""))]
        outbox = [e for e in self.outbox if _in(e.get("customer_id", ""))]
        return {
            "refunds": [{"refund_id": r["refund_id"], "account": _mask("account", r["account"]),
                         "amount": r["amount"], "status": r["status"]} for r in refunds],
            "outbox": [{"to": _mask("to", e["to"]), "subject": e["subject"],
                        "status": e.get("status", "completed")} for e in outbox],
            "refund_count": len(refunds), "email_count": len(outbox),
            # 미확인(submitted) 부작용 수 — 재조정 대상(0 이면 모두 완료).
            "unconfirmed": (sum(1 for r in refunds if r.get("status") == "submitted")
                            + sum(1 for e in outbox if e.get("status") == "submitted")),
        }


def service_world() -> ServiceWorld:
    """데모 데이터(고객/KB)를 재사용하되 부작용을 가진 서비스 세계를 만든다."""
    base = default_world()
    return ServiceWorld(customers=base.customers, kb_docs=base.kb_docs,
                        supervisor_notes=base.supervisor_notes)
