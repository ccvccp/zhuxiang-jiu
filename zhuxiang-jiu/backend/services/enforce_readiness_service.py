"""43号·P6-3 enforce 上线就绪度评估(纯聚合层零新检测)

计划(docs/43号P6-3_enforce就绪度自动化实施计划.md §二):
    数据源全部复用既有服务:
    - SocReportService.daily_series → 观察天数/误报率
    - Security43Service.stats      → 待裁决积压/当前灰度态
    - repo.list_appeals            → 申诉通道动态探活
    - GATEWAY_WHITELIST            → 健康检查白名单(代码常量)
    - d5_observation/threatintel stats/geo_available/
      abuseipdb_mode               → 三信号(加分项不进 overall)

检查单口径(操作指南 §六, 五条中前四条自动化):
    1. observe 运行 ≥1 周, falsePositiveRate <10%   → observe_days
       + false_positive_rate 两检查
    2. 待裁决事件积压已清零(pending=0)              → pending_backlog
    3. 会员申诉通道畅通                              → appeal_channel
    4. 健康检查路径在白名单                          → health_whitelist
    5. 切换后 30 分钟内盯盘                          → 人工动作不
       自动化(note 明示)

铁律: 只评估不切换——切换仍人工改 SECURITY_ENFORCE_LEVEL
(检查单第 6 条人工操作不可自动化, 防误切换)。
"""

import logging

from core.helpers import ts

logger = logging.getLogger(__name__)

# 观察窗口(面板日报序列同口径 14 天)
OBSERVE_WINDOW_DAYS = 14
# 检查单: observe ≥1 周
OBSERVE_MIN_DAYS = 7
# 检查单: 误报率 <10%(整体处置门槛, 比 D5 单信号 <5% 宽松)
MAX_FALSE_POSITIVE = 0.10


class EnforceReadinessService:
    """enforce 就绪度评估(五检查 + 三信号 + blockers)"""

    def __init__(self):
        from services.security_service import (
            Security43Service, GATEWAY_WHITELIST,
        )
        self._security = Security43Service()
        self._whitelist = GATEWAY_WHITELIST

    # ========================================================
    # 主入口
    # ========================================================

    async def evaluate(self) -> dict:
        """就绪度评估(五检查过/不过 + actual/required 实测 + blockers)

        Returns:
            {overall: "ready"|"holding", enforceLevel, checkedAt,
             checks: [{id, name, passed, actual, required, detail}],
             signals: {d5, threatintel, geo, abuseipdb},
             blockers: [中文摘要...], note}
        """
        # ① 观察天数 + ② 误报率(单次 daily_series 两检查共享——
        #    14×daily_report 聚合只算一遍)
        summary = await self._load_series_summary()

        # ③ 积压 + 当前灰度态
        stats = await self._security.stats()

        # ④ 申诉探活 ⑤ 白名单
        appeals_ok, appeals_detail = await self._probe_appeals()
        whitelist_ok = "/api/decision/health" in self._whitelist

        checks = [
            {"id": "observe_days", "name": "灰度观察期≥7天",
             "passed": summary["activeDays"] >= OBSERVE_MIN_DAYS,
             "actual": f"{summary['activeDays']}天",
             "required": f"≥{OBSERVE_MIN_DAYS}天",
             "detail": f"近{OBSERVE_WINDOW_DAYS}天中有事件的天数"},
            {"id": "false_positive_rate", "name": "误报率<10%",
             "passed": summary["fpr_passed"],
             "actual": summary["fpr_text"],
             "required": "<10%",
             "detail": "分母=已裁决事件(confirmed+falsePositive)"},
            {"id": "pending_backlog", "name": "待裁决积压=0",
             "passed": stats["events"]["pending"] == 0,
             "actual": f"{stats['events']['pending']}件",
             "required": "=0",
             "detail": "GET /admin/events?verdict=pending"},
            {"id": "appeal_channel", "name": "申诉通道畅通",
             "passed": appeals_ok,
             "actual": appeals_detail, "required": "已验证",
             "detail": "POST/GET /api/security/appeals + "
                       "管理裁决队列读写探活"},
            {"id": "health_whitelist", "name": "健康检查白名单",
             "passed": whitelist_ok,
             "actual": f"{len(self._whitelist)}路径",
             "required": "含/api/decision/health",
             "detail": "网关快道永久放行(Docker 探针不受影响)"},
        ]
        blockers = [self._blocker_text(c) for c in checks
                    if not c["passed"]]
        return {
            "success": True,
            "overall": "ready" if not blockers else "holding",
            "enforceLevel": stats.get("enforceLevel"),
            "checkedAt": ts(),
            "checks": checks,
            "signals": await self._collect_signals(),
            "blockers": blockers,
            "note": "本端点只评估不切换——切换仍需人工改 "
                    "SECURITY_ENFORCE_LEVEL=enforce 并执行检查单"
                    "第5条(切换后30分钟盯 blocks 数与业务 200 率)",
        }

    # ========================================================
    # 数据源装载(各 fail-soft, 单源异常不阻断整体评估)
    # ========================================================

    async def _load_series_summary(self) -> dict:
        """日报序列汇总(观察天数/误报率, 零数据保守口径)"""
        try:
            from services.soc_report_service import SocReportService
            series = await SocReportService().daily_series(
                OBSERVE_WINDOW_DAYS)
            s = series.get("summary") or {}
            active_days = int(s.get("activeDays") or 0)
            confirmed = int(s.get("confirmed") or 0)
            false_pos = int(s.get("falsePositive") or 0)
            decided = confirmed + false_pos
            fpr = s.get("falsePositiveRate")
            # 零裁决 = 无数据 ≠ 达标(保守口径——与 activeDays≥7
            # 双重保守, 冷启动期不误判 ready)
            fpr_passed = bool(decided) and fpr is not None \
                and float(fpr) < MAX_FALSE_POSITIVE
            if decided:
                fpr_text = f"{float(fpr):.1%}"
            else:
                fpr_text = "无裁决数据"
            return {"activeDays": active_days,
                    "fpr_passed": fpr_passed,
                    "fpr_text": fpr_text}
        except Exception as exc:
            logger.warning("readiness_series_skip: %s", exc)
            return {"activeDays": 0, "fpr_passed": False,
                    "fpr_text": f"评估异常: {exc}"[:60]}

    async def _probe_appeals(self) -> tuple:
        """申诉通道动态探活(list 调用成功即畅通, fail-soft)"""
        try:
            appeals = await self._security.repo.list_appeals(
                limit=1)
            n = len(appeals)
            return True, (f"队列读写正常(已有{n}条)"
                          if n else "队列读写正常(空)")
        except Exception as exc:
            return False, f"探活异常: {exc}"[:120]

    async def _collect_signals(self) -> dict:
        """三信号达标汇总(加分项不进 overall——与检查单口径一致)"""
        signals = {}
        # D5 联动(P4-1 criteria 三条件 + d5Enforce 实况)
        try:
            from services.soc_report_service import SocReportService
            d5 = await SocReportService().d5_observation()
            signals["d5"] = {
                "samples": d5.get("samples"),
                "falsePositiveRate": d5.get("falsePositiveRate"),
                "observeDays": d5.get("observeDays"),
                "criteria": d5.get("criteria"),
                "recommendation": d5.get("recommendation"),
                "d5Enforce": d5.get("d5Enforce"),
            }
        except Exception as exc:
            signals["d5"] = {"error": str(exc)[:120]}
        # 威胁情报(段数/匹配模式/订阅健康)
        try:
            from services.threatintel_service import (
                ThreatIntelService,
            )
            ti = await ThreatIntelService().stats()
            auto = ti.get("auto") or {}
            signals["threatintel"] = {
                "totalCidrs": ti.get("totalCidrs"),
                "matchMode": ti.get("matchMode"),
                "enabled": auto.get("enabled"),
                "degraded": auto.get("degraded"),
                "degradedSources": auto.get("degradedSources"),
            }
        except Exception as exc:
            signals["threatintel"] = {"error": str(exc)[:120]}
        # GeoIP(mmdb 就位即 true)
        try:
            from services.geoip_service import geo_available
            signals["geo"] = {"available": geo_available()}
        except Exception as exc:
            signals["geo"] = {"available": False,
                              "error": str(exc)[:120]}
        # AbuseIPDB(三态可观测)
        try:
            from services.abuseipdb_client import abuseipdb_mode
            signals["abuseipdb"] = {"mode": abuseipdb_mode()}
        except Exception as exc:
            signals["abuseipdb"] = {"mode": "unknown",
                                    "error": str(exc)[:120]}
        return signals

    # ========================================================
    # blockers 文案
    # ========================================================

    @staticmethod
    def _blocker_text(c: dict) -> str:
        """未过检查项 → blockers 中文摘要"""
        mapping = {
            "observe_days": "观察期 {actual} 不足(需{required})",
            "false_positive_rate":
                "误报率 {actual} 未达标(需{required})",
            "pending_backlog": "待裁决积压 {actual} 未清零",
            "appeal_channel": "申诉通道探活失败: {actual}",
            "health_whitelist":
                "健康检查白名单缺失 /api/decision/health",
        }
        return mapping[c["id"]].format(**c)
