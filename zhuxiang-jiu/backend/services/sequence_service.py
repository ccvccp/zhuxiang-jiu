"""43号·P3-4 登录序列建模(D5 跳步检测 + 撞库预警)

计划 §五(docs/43号P3_纵深增强实施计划.md):
    - auth_event 留痕: 登录成功/失败钩子(best-effort 火后不管)
      → security_events 增加 action="auth_event" 档(复用表, 不建新表)
    - 会话序列: 登录成功生成 sessionSeq; 网关请求记录最近 5 个
      module 环形缓冲(Redis List)
    - D5 跳步检测器: 登录后 N=3 个请求内直奔敏感端点且无常规
      浏览行为 → 跳步信号(正常用户登录后先看首页/订单列表)
    - 撞库预警: 同账号 24h 登录失败堆积 ≥5 → 直接生成
      security_events 预警

防误报铁律(计划 §五):
    - D5 仅降分不处置, 裁决通道兜底(对齐 behavior_alert 口径)
    - 上线 observe 至少两周统计误报率再参与联动
"""

import logging
from datetime import datetime, UTC

from core.helpers import ts
from repositories.security_repository import Security43Repository

logger = logging.getLogger(__name__)

# D5 参数(环境变量可覆盖)
DEFAULT_JUMP_WINDOW = 3        # 登录后 N 个请求内
LOGIN_FAIL_THRESHOLD = 5.0     # 24h 失败堆积阈值(撞库)
SESSION_TTL = 7200            # 会话序列窗口(与 JWT access 2h 对齐)
SEQ_BUFFER_SIZE = 5           # 环形缓冲长度


def _env(name: str, default: str) -> str:
    import os
    return os.environ.get(name, default)


def get_d5_mode() -> str:
    """D5 开关(默认 on; off 跳过跳步检测)"""
    return _env("SECURITY_D5_MODE", "on").lower()


# ============================================================
# P5-1: D5 强制联动开关(收口)
# ============================================================

# 默认挑战边界区(与 ThreatGateScorer 挑战档一致):
# 威胁分 ∈ [25, 50) → 处置升档为至少 challenge;
# 单一 D5 命中永不单独触发 block(行为信号非确定性攻击)
D5_ENFORCE_BAND_DEFAULT = "25-50"


def d5_enforce_on() -> bool:
    """D5 强制联动开关(默认 off——数据达标后人工开启)

    达标口径(GET /admin/reports/d5 三条件硬标准):
        观察期 ≥14 天 + D5 相关误报率 <5% + 样本 ≥20
    """
    return _env("SECURITY_D5_ENFORCE", "off").lower() == "on"


def d5_enforce_band() -> tuple[float, float]:
    """D5 强制联动的威胁分边界区(默认 25-50, 可调)

    Returns:
        (band_lo, band_hi)——威胁分 ∈ [lo, hi) 时升档 challenge
    """
    raw = _env("SECURITY_D5_ENFORCE_BAND", D5_ENFORCE_BAND_DEFAULT)
    try:
        lo, hi = (float(x) for x in raw.replace(
            "，", ",").replace(" ", "").split("-", 1))
        if lo < hi and 0 <= lo <= 100 and 0 < hi <= 100:
            return lo, hi
    except (ValueError, TypeError):
        pass
    return 25.0, 50.0


def _now_ts() -> float:
    return datetime.now(UTC).timestamp()


class SequenceService:
    """登录序列建模服务(43号 P3-4)"""

    def __init__(self, repo: Security43Repository
                 = Security43Repository()):
        self.repo = repo

    # ========================================================
    # ① auth_event 留痕(登录钩子, best-effort 火后不管)
    # ========================================================

    async def record_auth_event(self, member_id: int, ip: str,
                                success: bool,
                                method: str = "password",
                                device_id: str = "") -> dict | None:
        """登录成功/失败留痕入 security_events(action=auth_event)

        由 auth_service 钩子调用; 异常不阻断登录(调用方兜底)。
        失败事件同时累计撞库计数。
        """
        try:
            event_id = await self.repo.next_id("event")
            event = {
                "eventId": event_id,
                "ip": ip,
                "memberId": int(member_id or 0),
                "method": method,
                "path": "/api/auth/login",
                "query": "", "ua": "",
                "action": "auth_event",
                "score": None,
                "factors": [{"name": "auth_result", "label": "登录结果",
                             "score": 100.0 if success else 0.0,
                             "detail": ("成功" if success else "失败")
                             + (f"({method})" if method else "")}],
                "enforced": False,
                "verdict": "pending",
                "eventFed": False,
                "authSuccess": success,
                "deviceId": (device_id or "")[:64],
                "createdAt": ts(),
            }
            await self.repo.save_event(event)
            if not success:
                # 撞库计数 + 阈值预警
                count = await self.repo.count_auth_fail(
                    int(member_id or 0))
                if count >= LOGIN_FAIL_THRESHOLD:
                    await self._alert_credential_stuffing(
                        member_id, ip, count)
            else:
                # 登录成功: 开启会话序列
                await self.repo.start_session_seq(int(member_id))
            logger.info("security_auth_event member=%s success=%s "
                        "ip=%s", member_id, success, ip)
            return event
        except Exception as exc:
            logger.warning("security_auth_event_skip member=%s: %s",
                           member_id, exc)
            return None

    async def _alert_credential_stuffing(self, member_id: int,
                                         ip: str,
                                         count: float) -> None:
        """撞库预警: 失败堆积达阈值生成独立预警事件"""
        event_id = await self.repo.next_id("event")
        event = {
            "eventId": event_id,
            "ip": ip,
            "memberId": int(member_id or 0),
            "method": "POST",
            "path": "/api/auth/login",
            "query": "", "ua": "",
            "action": "behavior_alert",
            "score": 0.0,
            "factors": [{
                "name": "D5_stuffing", "label": "撞库预警",
                "score": 0.0,
                "detail": f"24h登录失败堆积{count:g}次"
                          f"(阈值{LOGIN_FAIL_THRESHOLD:g})"}],
            "enforced": False,
            "verdict": "pending",
            "eventFed": False,
            "createdAt": ts(),
        }
        await self.repo.save_event(event)
        logger.warning("security_credential_stuffing member=%s "
                       "ip=%s count=%s", member_id, ip, count)

    # ========================================================
    # ② 会话序列(最近 N 个 module 环形缓冲)
    # ========================================================

    async def record_sequence(self, member_id: int,
                              module: str) -> list[str]:
        """网关请求记录会话序列(返回含本次的最近 N 个 module)"""
        return await self.repo.push_session_seq(int(member_id), module)

    async def get_sequence(self, member_id: int) -> list[str]:
        return await self.repo.get_session_seq(int(member_id))

    async def has_session(self, member_id: int) -> bool:
        """会话是否存活(登录后 2h 内)"""
        return await self.repo.has_session_seq(int(member_id))

    # ========================================================
    # ③ D5 跳步检测(登录后 N 请求内直奔敏感端点)
    # ========================================================

    async def detect_jump(self, member_id: int,
                          current_module: str) -> dict | None:
        """D5: 登录后短序列直奔敏感端点且无常规浏览

        命中条件(全部满足):
            - 会话存在(登录后 2h 内)
            - 当前 module ∈ 敏感表(admin/finance)
            - 序列长度 ≤ N(3)——登录后前几个请求
            - 序列中无常规浏览模块(product/order/member 等)
        防误报: API 直调型正常用户(脚本化)会被命中——仅降分
        不处置, 人工裁决兜底; observe 两周后再联动。

        Returns:
            {hit: True, detail} 或 None(不命中)
        """
        if get_d5_mode() != "on" or not member_id:
            return None
        try:
            if not await self.has_session(member_id):
                return None
            # 真会话校验: 序列尾(最早元素)须为登录标记——
            # 未登录用户的请求记录(无 __login__ 起点)不构成会话,
            # 防止 D5 误检(网关对未登录流量也记序列缓冲)
            sequence = await self.get_sequence(member_id)
            if not sequence or sequence[-1] != "__login__":
                return None
            sensitive = current_module in ("admin", "finance")
            if not sensitive:
                return None
            window = int(_env("SECURITY_D5_JUMP_WINDOW",
                               str(DEFAULT_JUMP_WINDOW)))
            sequence = await self.get_sequence(member_id)
            # 序列含当前请求(网关先记录后检测), 长度≤window
            if len(sequence) > window:
                return None
            # 常规浏览模块: 登录后正常用户先看这些
            browsing = ("product", "order", "member", "points",
                        "activity", "other")
            has_browsing = any(m in browsing for m in sequence[:-1])
            if has_browsing:
                return None
            return {"hit": True,
                    "detail": f"登录后第{len(sequence)}个请求"
                              f"直奔敏感模块{current_module}"
                              f"(无常规浏览)"}
        except Exception as exc:
            logger.warning("security_d5_skip member=%s: %s",
                           member_id, exc)
            return None
