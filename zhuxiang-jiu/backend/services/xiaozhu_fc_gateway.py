"""49号·小竹可信函数调用深化 FC 网关(P0 骨架)

计划(docs/49号_小竹可信函数调用深化实施计划.md §五 ②):
    FC 网关 = 执行器(48号 P2 沙箱)的可信外壳:
    [校验① consent_token]──写/高敏必须──→ 403 话术
    [校验② 隐私预算]───────────────────→ 429 话术+偏好引导
    [执行] 沙箱白名单执行器(三级/幂等/冷静期全部继承)
    [校验③ explainability_ref]─────────→ 写响应缺失→阻断
    [审计] voice48_fc_audit 六字段流水落库

P0 口径(骨架):
    - 校验①: 占位实现(50号 P1 双因子 consent_token 落地
      后接管——当前高敏仍走 48号 confirmToken 屏幕码流,
      网关透传不拦截)
    - 校验②: 占位实现(预算检查函数返回 True——P2 隐私
      预算落地后接管)
    - 校验③: 占位实现(ref 生成——P3 可解释性绑定落地后
      由 45号 attribution/46号回放充实)
    - 审计: 完整落地(P0 核心交付——六字段铁律)

设计红线:
    - fail-soft: 审计落库失败不阻断业务(记 warning)
    - 失败安全降级: 执行异常 → safe_message(不编结果)
      + kind=fallback 审计 + 人工转接选项
    - 网关只包写/高敏动作(只读直达不走网关——零摩擦)
"""

import hashlib
import logging
import time

from core.helpers import ts

from repositories.xiaozhu_repository import (
    Xiaozhu48Repository,
)
from services.xiaozhu_fc_registry import (
    TOOL_REGISTRY, safe_message_of, audit_fields,
    TIER_READONLY,
)

logger = logging.getLogger("xiaozhu_fc_gateway")


class XiaozhuFcGateway:
    """可信函数调用网关(三重校验管道 + 审计)"""

    def __init__(self,
                 repo: Xiaozhu48Repository = None):
        self.repo = repo or Xiaozhu48Repository()

    # --------------------------------------------------------
    # 网关入口
    # --------------------------------------------------------

    async def call_tool(self, session: dict, action: str,
                        params: dict,
                        member_id: int = None) -> dict:
        """FC 网关统一入口(工具调用可信管道)

        分流: 只读动作零摩擦直达(不进网关管道——日常
        90% 调用无感); 写/高敏走三重校验+审计。

        Returns:
            执行器回包(含 safeMessage 降级字段——异常时)
        """
        tool = TOOL_REGISTRY.get(action)
        if tool is None:
            raise ValueError(f"未知工具 {action}")
        member_id = (member_id if member_id is not None
                     else session.get("memberId"))
        if tool["tier"] == TIER_READONLY:
            # 只读零摩擦(审计仍落——调用量统计)
            await self._audit(session, action, params,
                              member_id, None, 0.0, "ok")
            return {"executed": False, "readonly": True,
                    "privacyCost": tool["privacyCost"]}
        # ---- 写/高敏: 三重校验管道 ----
        started = time.monotonic()
        consent_hash = None
        try:
            # 校验① consent_token(P0 占位透传——P1 接管)
            consent_hash = self._verify_consent(
                params.get("consentToken"), action, tool)
            # 校验② 隐私预算(P0 占位放行——P2 接管)
            self._check_privacy_budget(
                member_id, tool["privacyCost"])
            # 执行(48号沙箱——幂等/冷静期全继承)
            from services.xiaozhu_executor import (
                get_executor,
            )
            result = await get_executor().execute(
                session, action, params)
            latency = round((time.monotonic() - started)
                            * 1000, 1)
            kind = ("ok" if not result.get("duplicate")
                    else "duplicate")
            await self._audit(session, action, params,
                              member_id, consent_hash,
                              latency, kind)
            return result
        except Exception as exc:  # noqa: BLE001
            # 失败安全降级铁律: 不编结果——预定义话术
            latency = round((time.monotonic() - started)
                            * 1000, 1)
            await self._audit(session, action, params,
                              member_id, consent_hash,
                              latency, "fallback",
                              error=str(exc)[:120])
            logger.warning("voice49_fc_fallback %s: %s",
                           action, exc)
            return {"executed": False, "fallback": True,
                    "safeMessage": safe_message_of(action),
                    "action": action}

    # --------------------------------------------------------
    # 校验① consent_token(P0 占位——P1 双因子接管)
    # --------------------------------------------------------

    @staticmethod
    def _verify_consent(token: str, action: str,
                        tool: dict) -> str | None:
        """显式授权前置校验(P0 占位: 高敏仍由 48号
        confirmToken 屏幕码流守门——网关层不重复拦截)

        P1 落地后: 校验 TTL≤60s/一次性/action 匹配/
        声纹摘要绑定; 返回 token_hash 入审计。
        """
        if not tool.get("requiresConsent"):
            return None
        return XiaozhuFcGateway._hash_token(token)

    @staticmethod
    def _hash_token(token: str) -> str | None:
        """consent_token 哈希(明文不落库——审计红线)"""
        if not token:
            return None
        return hashlib.sha256(
            str(token).encode("utf-8")).hexdigest()[:32]

    # --------------------------------------------------------
    # 校验② 隐私预算(P0 占位放行——P2 接管)
    # --------------------------------------------------------

    @staticmethod
    def _check_privacy_budget(member_id: int,
                              cost: float) -> None:
        """隐私预算感知校验(P0 占位: 一律放行——P2 落地
        voice48_privacy_budget 表+原子扣减后接管)

        P2 落地后: 超限 raise ValueError(429 话术——
        "隐私预算不足(剩余 X, 需 Y), 请在设置中调整
        隐私偏好或明日再试"); 只读零成本永不降级。
        """
        return None

    # --------------------------------------------------------
    # 审计(六字段铁律——P0 完整交付)
    # --------------------------------------------------------

    async def _audit(self, session: dict, action: str,
                     params: dict, member_id: int,
                     consent_hash: str | None,
                     latency_ms: float, kind: str,
                     error: str = "") -> None:
        """FC 调用审计流水落库(只追加; fail-soft)

        六字段铁律(计划 §六): memberId/toolName/
        consentTokenHash/privacyCost/timestamp + kind
        (ok|duplicate|fallback)——漏一即测试失败。
        """
        try:
            static = audit_fields(action)
            fc_id = await self.repo._next_id(
                self.repo.TABLE_FC_AUDIT)
            await self.repo.save_record(
                self.repo.TABLE_FC_AUDIT, {
                    "fcId": fc_id,
                    "memberId": member_id or 0,
                    "sessionId": (
                        session.get("sessionId")
                        if isinstance(session, dict) else 0),
                    "action": action,
                    "toolName": static["toolName"],
                    "tier": static["tier"],
                    "consentTokenHash": consent_hash or "",
                    "privacyCost": static["privacyCost"],
                    "latencyMs": latency_ms or 0.0,
                    "kind": kind,
                    "error": error[:120],
                    "ts": ts(),
                })
        except Exception as exc:  # noqa: BLE001
            logger.warning("voice49_fc_audit_skip: %s", exc)

    # --------------------------------------------------------
    # 审计视图(管理端——看板/排查用)
    # --------------------------------------------------------

    async def audit_view(self, limit: int = 100,
                         member_id: int = None) -> dict:
        """FC 审计流水视图(时间倒序; 可按会员过滤)"""
        rows = await self.repo.list_records(
            self.repo.TABLE_FC_AUDIT, limit=limit)
        if member_id is not None:
            rows = [r for r in rows
                    if r.get("memberId") == member_id]
        rows.sort(key=lambda r: -(r.get("fcId") or 0))
        by_kind: dict = {}
        by_tool: dict = {}
        cost_total = 0.0
        for r in rows:
            k = r.get("kind") or "unknown"
            by_kind[k] = by_kind.get(k, 0) + 1
            t = r.get("toolName") or "unknown"
            by_tool[t] = by_tool.get(t, 0) + 1
            cost_total += float(r.get("privacyCost") or 0)
        return {"success": True, "total": len(rows),
                "byKind": by_kind, "byTool": by_tool,
                "privacyCostTotal": round(cost_total, 2),
                "records": rows[:limit],
                "note": "六字段铁律: memberId/toolName/"
                        "consentTokenHash/privacyCost/ts/kind"}
