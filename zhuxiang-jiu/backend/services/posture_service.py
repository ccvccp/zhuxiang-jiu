"""43号·AI智能安全管理 P2b: 防御态势三态(peace/alert/wartime)

设计文档 §2.5(自我学习与智能自主升级):
    peace     攻击密度 EMA 低于阈值 → 频次宽松(×1.5), 几乎不挑战
    alert     密度超阈值 → 频次收紧(×1.0), 挑战比例提高
    wartime   密度跳变/单IP高危激增 → 频次×0.3, 可疑即封

升降级防抖(滞后设计): 连续 2 个窗口超阈才升级, 避免抖动;
降级同样需连续 2 个窗口回落。管理端可手动钉住(pinned 优先于自动)。

频次缩放: 网关 process_request 的 rate_limit 按当前态势乘系数。
"""

import logging
import time

from core.helpers import ts
from repositories.security_repository import Security43Repository

logger = logging.getLogger(__name__)


def _env(name: str, default: str) -> str:
    import os
    return os.environ.get(name, default)


def get_posture_mode() -> str:
    """态势模式: auto(自动升降级, 默认) / manual(仅手动)"""
    return _env("SECURITY_POSTURE_MODE", "auto").lower()


# 态势常量
POSTURE_PEACE = "peace"
POSTURE_ALERT = "alert"
POSTURE_WARTIME = "wartime"
POSTURES = (POSTURE_PEACE, POSTURE_ALERT, POSTURE_WARTIME)

# 各态势的频次缩放系数(rate_limit × factor)
POSTURE_RATE_FACTOR = {
    POSTURE_PEACE: 1.5,
    POSTURE_ALERT: 1.0,
    POSTURE_WARTIME: 0.3,
}

# 态势转换阈值(每窗口的可疑事件数 EMA)
ALERT_THRESHOLD = 5.0     # peace → alert
WARTIME_THRESHOLD = 20.0   # alert → wartime(密度跳变)
DEMOTE_FACTOR = 0.5       # 降级阈值倍数(alert←wartime 等)

# 攻击密度 EMA 平滑系数(窗口粒度)
DENSITY_ALPHA = 0.3
# 升降级防抖: 连续 N 个窗口越线才转换
CONSECUTIVE_REQUIRED = 2
# 窗口时长(秒): 密度统计与转换评估的节拍
POSTURE_WINDOW = 300


def _severity_order(posture: str) -> int:
    return {POSTURE_PEACE: 0, POSTURE_ALERT: 1,
            POSTURE_WARTIME: 2}.get(posture, 0)


class PostureService:
    """防御态势服务(43号 P2b)"""

    def __init__(self, repo: Security43Repository
                 = Security43Repository()):
        self.repo = repo

    # --------------------------------------------------------
    # 查询/初始化
    # --------------------------------------------------------

    async def get_or_init(self) -> dict:
        """取态势记录(冷启动 peace), 不存在则初始化"""
        record = await self.repo.get_posture()
        if record is not None:
            return record
        record = {
            "posture": POSTURE_PEACE,
            "mode": get_posture_mode(),
            "pinned": False,
            "densityEma": 0.0,
            "consecutiveWindows": 0,
            "pendingDirection": "",   # 升/降级防抖中的方向
            "lastWindowAt": ts(),
            "history": [],            # 转换历史(最近10条)
            "updatedAt": ts(),
        }
        await self.repo.save_posture(record)
        return record

    async def current(self) -> dict:
        """对外口径: 态势+频次系数(网关每窗口读一次并缓存)"""
        record = await self.get_or_init()
        # mode 实时反映环境变量(记录值仅留痕)
        record["mode"] = get_posture_mode()
        await self.repo.save_posture(record)
        return {
            "posture": record["posture"],
            "mode": record.get("mode") or get_posture_mode(),
            "pinned": record.get("pinned", False),
            "rateFactor": POSTURE_RATE_FACTOR.get(
                record["posture"], 1.0),
            "densityEma": round(
                float(record.get("densityEma") or 0), 2),
            "updatedAt": record.get("updatedAt"),
        }

    # --------------------------------------------------------
    # 自动升降级(每个统计窗口调用一次)
    # --------------------------------------------------------

    async def observe_window(self,
                             suspicious_events: int) -> dict:
        """一个窗口的攻击密度观测 → EMA 更新 + 升降级评估

        Args:
            suspicious_events: 本窗口可疑事件数(challenge+block+
                behavior_alert 档, 由调用方统计)

        pinned 或 manual 模式: 只更新 EMA 不转换。
        防抖: 同方向连续 CONSECUTIVE_REQUIRED 个窗口越线才转换。
        """
        record = await self.get_or_init()
        ema = float(record.get("densityEma") or 0)
        ema = (DENSITY_ALPHA * float(suspicious_events)
               + (1 - DENSITY_ALPHA) * ema)
        record["densityEma"] = round(ema, 4)

        changed = False
        direction = ""
        if (not record.get("pinned")
                and get_posture_mode() == "auto"):
            direction = self._evaluate(record, ema)
            if direction:
                record["consecutiveWindows"] = \
                    int(record.get("consecutiveWindows") or 0) + 1
                if record["consecutiveWindows"] >= CONSECUTIVE_REQUIRED:
                    changed = True
            else:
                record["consecutiveWindows"] = 0
                record["pendingDirection"] = ""

        if changed:
            record["posture"] = self._target_posture(
                record["posture"], record["pendingDirection"])
            record["consecutiveWindows"] = 0
            record["pendingDirection"] = ""
            record["history"] = (record.get("history") or [])[-9:] + [{
                "posture": record["posture"],
                "densityEma": record["densityEma"],
                "at": ts(),
            }]
            logger.info("security_posture_changed posture=%s "
                        "ema=%.1f", record["posture"], ema)
        elif direction:
            record["pendingDirection"] = direction

        record["lastWindowAt"] = ts()
        record["updatedAt"] = ts()
        await self.repo.save_posture(record)
        return {"posture": record["posture"],
                "densityEma": record["densityEma"],
                "changed": changed,
                "pendingDirection":
                    record.get("pendingDirection") or ""}

    def _evaluate(self, record: dict, ema: float) -> str:
        """EMA → 期望方向(升/降/空)"""
        posture = record.get("posture") or POSTURE_PEACE
        if _severity_order(posture) < _severity_order(
                POSTURE_WARTIME) and ema >= self._upper_threshold(
                posture):
            return "up"
        if _severity_order(posture) > _severity_order(
                POSTURE_PEACE) and ema < self._lower_threshold(
                posture):
            return "down"
        return ""

    def _upper_threshold(self, posture: str) -> float:
        if posture == POSTURE_PEACE:
            return ALERT_THRESHOLD
        return WARTIME_THRESHOLD

    def _lower_threshold(self, posture: str) -> float:
        if posture == POSTURE_WARTIME:
            return WARTIME_THRESHOLD * DEMOTE_FACTOR
        return ALERT_THRESHOLD * DEMOTE_FACTOR

    @staticmethod
    def _target_posture(current: str, direction: str) -> str:
        order = {"down": -1, "up": 1}.get(direction, 0)
        idx = _severity_order(current) + order
        idx = max(0, min(len(POSTURES) - 1, idx))
        return POSTURES[idx]

    # --------------------------------------------------------
    # 手动切换/钉住
    # --------------------------------------------------------

    async def set_posture(self, posture: str,
                          pin: bool = None) -> dict:
        """手动切换态势(可选同时钉住)

        Raises:
            ValueError: 非法态势值
        """
        if posture not in POSTURES:
            raise ValueError(f"未知态势 {posture}, "
                             f"可选: {'/'.join(POSTURES)}")
        record = await self.get_or_init()
        record["posture"] = posture
        if pin is not None:
            record["pinned"] = bool(pin)
        record["history"] = (record.get("history") or [])[-9:] + [{
            "posture": posture,
            "densityEma": record.get("densityEma") or 0,
            "at": ts(),
            "manual": True,
        }]
        record["updatedAt"] = ts()
        await self.repo.save_posture(record)
        logger.info("security_posture_set posture=%s pin=%s",
                    posture, record["pinned"])
        return await self.current()

    async def set_pinned(self, pinned: bool) -> dict:
        """钉住/解除钉住(钉住=不受自动升降级影响)"""
        record = await self.get_or_init()
        record["pinned"] = bool(pinned)
        record["updatedAt"] = ts()
        await self.repo.save_posture(record)
        return await self.current()
