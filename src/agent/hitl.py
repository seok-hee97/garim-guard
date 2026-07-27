"""HITL(Human-in-the-Loop) 승인 큐 — 비가역 도구(환불 등)를 사람 승인 후 실행.

에이전트가 requires_hitl 도구를 호출하면 에피소드는 hitl_pending 으로 일시중단되고,
그 액션(도구+복원된 인자)이 이 큐에 적재된다. 승인자가 approve 하면 executor 콜백으로
백엔드에 실제 실행(idempotent)되고, deny 하면 폐기된다.

인메모리 단일프로세스 구현(데모/파일럿). 실운영은 DB/redis 백엔드 + 승인 만료(TTL)로 확장.
⚠️ 승인 대기 액션은 실행에 필요한 원본 인자(예: 계좌)를 담는다 — API 응답에서는 마스킹한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Approval:
    id: str
    tool: str
    args: dict[str, Any]
    episode_id: str
    principal: str = ""
    rule: str = ""
    status: str = "pending"          # pending | approved | denied
    approver: str = ""
    result: dict[str, Any] | None = None

    def public(self) -> dict[str, Any]:
        """API 노출용 — 원본 PII(계좌 등)를 마스킹한다."""
        masked = {k: _mask(k, v) for k, v in self.args.items()}
        return {"id": self.id, "tool": self.tool, "args": masked, "status": self.status,
                "episode_id": self.episode_id, "principal": self.principal, "rule": self.rule,
                "approver": self.approver, "result": self.result}


def _mask(key: str, val: Any) -> Any:
    if key in ("account", "rrn") and isinstance(val, str) and len(val) > 4:
        return val[:3] + "***" + val[-2:]
    return val


class ApprovalQueue:
    """인메모리 승인 큐. 앱 기동 시 1개(싱글턴)."""

    def __init__(self) -> None:
        self._store: dict[str, Approval] = {}
        self._seq = 0

    def submit(self, tool: str, args: dict, episode_id: str,
               principal: str = "", rule: str = "") -> str:
        self._seq += 1
        aid = f"appr_{self._seq:05d}"
        self._store[aid] = Approval(id=aid, tool=tool, args=dict(args),
                                    episode_id=episode_id, principal=principal, rule=rule)
        return aid

    def list_pending(self) -> list[Approval]:
        return [a for a in self._store.values() if a.status == "pending"]

    def get(self, aid: str) -> Approval | None:
        return self._store.get(aid)

    def resolve(self, aid: str, approve: bool, approver: str = "",
                executor: Callable[[str, dict], dict] | None = None) -> Approval | None:
        """승인/거부. approve 시 executor(tool, args)로 백엔드에 실제 실행.

        ★트랜잭션적★: executor 를 먼저 실행해 성공해야만 approved 로 커밋한다. 실패하면
        status 를 pending 으로 유지해(자금·데이터 손실 방지) 재시도를 허용한다.
        """
        a = self._store.get(aid)
        if a is None or a.status != "pending":
            return a
        if not approve:
            a.status = "denied"
            a.approver = approver
            return a
        if executor is not None:
            try:
                a.result = executor(a.tool, a.args)   # idempotent 실행(백엔드)
            except Exception as e:  # noqa: BLE001 — 백엔드 실패 시 승인 미커밋(pending 유지→재시도)
                a.result = {"executed": False, "error": f"{type(e).__name__}: {e}"}
                return a          # status='pending' 유지
        a.status = "approved"     # executor 성공(또는 없음) 후에만 커밋
        a.approver = approver
        return a
