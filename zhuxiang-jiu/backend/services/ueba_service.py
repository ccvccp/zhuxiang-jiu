"""43号·AI智能安全管理 P2: UEBA 行为基线服务

实现方案: docs/43号P2_UEBA行为基线实现方案.md

架构(四层流水线):
    采集层: 网关顺带计数(member×hour×module 三维直方图, 零侵入)
            + admin_operation_logs 审计读取(管理端)
    基线层: BaselineBuilder(每日/手动重建, 30天窗口重算, 双层:
            个人基线 + 同角色全局基线冷启动兜底)
    检测层: D1时段偏离/D2频率偏离/D3功能偏离/D4试探偏离
    联动层: behavior_score 注入 threat_gate identity_risk 因子
            (只降不升) + behavior_alert 入事件流水(复用P1裁决链)

防误报铁律:
    - 冷启动(个人+全局基线均缺)完全豁免——新会员零误报
    - behavior_alert 默认 pending 走人工裁决, 不自动处置
    - 基线查询 60s 进程内缓存, 满足网关低延迟
"""

import logging
import math
import time

from core.helpers import ts
from repositories.security_repository import (
    Security43Repository, reputation_status,
)

logger = logging.getLogger(__name__)


def _ueba_env(name: str, default: str) -> str:
    import os
    return os.environ.get(name, default)


def get_ueba_mode() -> str:
    """UEBA 总开关(默认 on; off 跳过采集与检测)"""
    return _ueba_env("SECURITY_UEBA_MODE", "on").lower()


# ============================================================
# path → module 映射(方案 §2.1, 静态表)
# ============================================================

_PATH_MODULE_MAP = (
    ("/api/order", "order"),
    ("/api/payment", "payment"),
    ("/api/wallet", "payment"),
    ("/api/product", "product"),
    ("/api/points", "points"),
    ("/api/security", "security"),
    ("/api/admin", "admin"),
    ("/api/finance", "finance"),
    ("/api/member", "member"),
    ("/api/ride", "ride"),
    ("/api/logistics", "logistics"),
)

# D3 敏感功能模块(首次触碰即偏离, 方案 §4.3)
SENSITIVE_MODULES = ("admin", "finance")

# D1/D2 参数(环境变量可覆盖)
DEFAULT_HOUR_WEIGHT = 0.05    # 时段冷门阈值
DEFAULT_BURST_FACTOR = 3.0    # 频率突变系数


def path_to_module(path: str) -> str:
    """path → 行为模块(最长前缀匹配, 缺省 other)"""
    for prefix, module in _PATH_MODULE_MAP:
        if path.startswith(prefix):
            return module
    return "other"


def _percentile95(sorted_samples: list[float]) -> float:
    """P95(样本需升序; 空样本返回 0)"""
    if not sorted_samples:
        return 0.0
    idx = min(len(sorted_samples) - 1,
              int(math.ceil(0.95 * len(sorted_samples))) - 1)
    return float(sorted_samples[max(0, idx)])


class UebaService:
    """UEBA 行为基线服务(43号 P2)"""

    _BASELINE_CACHE: dict = {}   # actorKey → (expiry, baseline)
    _CACHE_TTL = 60.0            # 秒(网关低延迟口径)

    def __init__(self, repo: Security43Repository
                 = Security43Repository()):
        self.repo = repo

    # ========================================================
    # 采集层: 网关顺带计数(零侵入)
    # ========================================================

    async def record_behavior(self, member_id: int, path: str,
                              hour: int = None) -> int:
        """网关正常请求分支顺带记一次行为(UEBA off 则跳过)

        Returns: 该 hour×module 计数(检测层 D2 实时可查)
        """
        if not member_id or get_ueba_mode() != "on":
            return 0
        if hour is None:
            from datetime import datetime, UTC
            hour = datetime.now(UTC).hour
        module = path_to_module(path)
        return await self.repo.count_behavior(member_id, module, hour)

    # ========================================================
    # 基线层: BaselineBuilder(方案 §3)
    # ========================================================

    async def rebuild_baselines(self) -> dict:
        """重建全部基线(幂等; 数据源: 网关三维计数 + 管理端审计)

        管理端审计日志(admin_operation_logs)为只读补充——
        P2 口径: 会员端以网关计数为主(冷启动后持续积累),
        管理员基线由审计日志 action/createdAt 聚合。
        """
        self._BASELINE_CACHE.clear()
        personal, roles = [], {}

        # ① 会员端: 三维计数直方图 → 个人基线
        actors = await self.repo.list_behavior_actors()
        for actor_id in actors:
            counts = await self.repo.get_behavior(int(actor_id))
            if not counts:
                continue
            baseline = self._build_from_counts(counts, role="member")
            baseline["actorKey"] = f"member:{actor_id}"
            await self.repo.save_baseline(baseline)
            personal.append(baseline["actorKey"])
            roles.setdefault("member", []).append(baseline)

        # ② 管理端: 审计日志聚合(读取 admin_operation_logs)
        admin_baselines = await self._build_admin_baselines()
        for baseline in admin_baselines:
            await self.repo.save_baseline(baseline)
            personal.append(baseline["actorKey"])
            roles.setdefault("admin", []).append(baseline)

        # ③ 角色全局基线(同角色个人基线加权平均, 冷启动兜底)
        globals_built = []
        for role, bls in roles.items():
            merged = self._merge_role_baseline(role, bls)
            if merged is not None:
                await self.repo.save_baseline(merged)
                globals_built.append(merged["actorKey"])

        result = {"success": True,
                  "personal": len(personal),
                  "roleGlobals": len(globals_built),
                  "actorKeys": personal[:20]}
        logger.info("ueba_baselines_rebuilt personal=%s globals=%s",
                    len(personal), len(globals_built))
        return result

    def _build_from_counts(self, counts: dict, role: str) -> dict:
        """三维计数 → 基线记录

        Args:
            counts: {"hour|module": count, ...}
        """
        hours = [0.0] * 24
        module_dist = {}
        for field, count in counts.items():
            hour_s, module = field.split("|", 1)
            hours[int(hour_s) % 24] += int(count)
            module_dist[module] = module_dist.get(module, 0) + int(count)
        total = sum(hours)
        if total <= 0:
            total = 1
        norm_hours = [round(h / total, 6) for h in hours]
        # 逐小时样本(计数本身)求 P95: 单日口径退化为当前直方图
        samples = sorted([c for c in hours if c > 0] or [0])
        return {
            "actorKey": "",  # 调用方填充
            "role": role,
            "hours": norm_hours,
            "avgOpsPerHour": round(total / 24.0, 2),
            "p95OpsPerHour": _percentile95(samples),
            "moduleDist": module_dist,
            "sensitiveTouches": {
                m: module_dist.get(m, 0) for m in SENSITIVE_MODULES},
            "sampleDays": 1,   # 计数口径无日期分离, 视作1天样本
            "updatedAt": ts(),
        }

    async def _build_admin_baselines(self) -> list[dict]:
        """管理端审计日志 → 管理员个人基线(只读 admin_operation_logs)"""
        try:
            from repositories.admin_repository import AdminRepository
            logs = await AdminRepository().list_logs(limit=2000)
        except Exception as exc:  # 审计缺失不阻断基线重建
            logger.warning("ueba_admin_logs_skip: %s", exc)
            return []
        by_user = {}
        for log in logs or []:
            uid = log.get("userId") or log.get("userName")
            if uid is None:
                continue
            by_user.setdefault(str(uid), []).append(log)
        baselines = []
        for uid, user_logs in by_user.items():
            counts = {}
            for log in user_logs:
                created = str(log.get("createdAt") or "")
                try:
                    hour = int(created[11:13])
                except (ValueError, IndexError):
                    hour = 12
                module = str(log.get("module") or "admin").lower()
                field = f"{hour % 24}|{module}"
                counts[field] = counts.get(field, 0) + 1
            if not counts:
                continue
            baseline = self._build_from_counts(counts, role="admin")
            baseline["actorKey"] = f"admin:{uid}"
            baselines.append(baseline)
        return baselines

    def _merge_role_baseline(self, role: str,
                             baselines: list[dict]) -> dict | None:
        """同角色个人基线 → 角色全局基线(操作量加权平均)"""
        if not baselines:
            return None
        total_ops = sum(max(1.0, b.get("avgOpsPerHour") or 0) * 24
                        for b in baselines)
        hours = [0.0] * 24
        module_dist = {}
        for b in baselines:
            weight = max(1.0, b.get("avgOpsPerHour") or 0) * 24 / total_ops
            for h in range(24):
                hours[h] += (b.get("hours") or [0.0] * 24)[h] * weight
            for m, c in (b.get("moduleDist") or {}).items():
                module_dist[m] = module_dist.get(m, 0) + c
        return {
            "actorKey": f"role:{role}_global",
            "role": role,
            "hours": [round(h, 6) for h in hours],
            "avgOpsPerHour": round(total_ops / 24.0 / len(baselines), 2),
            "p95OpsPerHour": max(b.get("p95OpsPerHour") or 0
                                 for b in baselines),
            "moduleDist": module_dist,
            "sensitiveTouches": {
                m: module_dist.get(m, 0) for m in SENSITIVE_MODULES},
            "sampleDays": max(b.get("sampleDays") or 0
                              for b in baselines),
            "updatedAt": ts(),
        }

    # ========================================================
    # 基线查询(双层取数 + 冷启动豁免 + 60s 缓存)
    # ========================================================

    async def get_effective_baseline(self, member_id: int,
                                     role: str = "member") -> dict | None:
        """取生效基线: 个人 → 角色全局 → None(冷启动豁免)"""
        if get_ueba_mode() != "on" or not member_id:
            return None
        cache_key = f"member:{member_id}"
        cached = self._BASELINE_CACHE.get(cache_key)
        if cached and cached[0] > time.time():
            return cached[1]
        baseline = await self.repo.get_baseline(cache_key)
        if baseline is None or (baseline.get("sampleDays") or 0) < 1:
            baseline = await self.repo.get_baseline(
                f"role:{role}_global")
        if baseline is not None:
            self._BASELINE_CACHE[cache_key] = (
                time.time() + self._CACHE_TTL, baseline)
        return baseline

    # ========================================================
    # 检测层: D1-D4(方案 §4) → behavior_score
    # ========================================================

    async def compute_deviation(self, member_id: int, path: str,
                                hour: int = None,
                                current_hour_ops: int = None,
                                forbidden_hits: int = 0) -> dict | None:
        """四检测器合议 → 行为偏离画像(无基线返回 None 豁免)

        Args:
            member_id: 会员ID(0/None 直接豁免)
            path: 当前请求路径(D1/D3)
            hour: 请求时段(缺省当前)
            current_hour_ops: 该会员当前小时操作数(D2, 缺省实时查)
            forbidden_hits: 24h 内 403 堆积数(D4, 网关响应侧统计)

        Returns:
            {score, deviations: [{code, detail}], baseline} 或 None
        """
        if get_ueba_mode() != "on" or not member_id:
            return None
        if hour is None:
            from datetime import datetime, UTC
            hour = datetime.now(UTC).hour
        baseline = await self.get_effective_baseline(member_id)
        if baseline is None:
            return None   # 冷启动豁免

        hour_weight = float(_ueba_env("SECURITY_UEBA_HOUR_WEIGHT",
                                       str(DEFAULT_HOUR_WEIGHT)))
        burst = float(_ueba_env("SECURITY_UEBA_BURST_FACTOR",
                                str(DEFAULT_BURST_FACTOR)))
        module = path_to_module(path)
        deviations = []

        # D1 时段偏离: 基线权重 < 阈值的冷门时段操作
        weights = baseline.get("hours") or [0.0] * 24
        if len(weights) == 24 and weights[hour] < hour_weight:
            sensitive = module in SENSITIVE_MODULES
            deviations.append({
                "code": "D1_hour",
                "detail": f"时段{hour}时基线权重{weights[hour]:.3f}"
                          f"<{hour_weight}"
                          + ("(敏感功能)" if sensitive else "")})
        # D2 频率偏离: 当前小时操作数击穿 P95×突变系数
        if current_hour_ops is None:
            behavior_counts = await self.repo.get_behavior(member_id)
            current_hour_ops = behavior_counts.get(
                f"{hour % 24}|{module}", 0)
        p95 = float(baseline.get("p95OpsPerHour") or 0)
        if p95 > 0 and current_hour_ops > p95 * burst:
            deviations.append({
                "code": "D2_burst",
                "detail": f"小时{current_hour_ops}次>"
                          f"P95({p95:.0f})×{burst:.0f}"})
        # D3 功能偏离: 敏感功能首次触碰
        touches = baseline.get("sensitiveTouches") or {}
        if module in SENSITIVE_MODULES and touches.get(module, 0) == 0:
            deviations.append({
                "code": "D3_sensitive_first",
                "detail": f"首次触碰敏感功能{module}"})
        # D4 试探偏离: 403 堆积
        if forbidden_hits >= 3:
            deviations.append({
                "code": "D4_probe",
                "detail": f"24h内403堆积{forbidden_hits}次"})

        points = 0
        for d in deviations:
            points += (2 if d["code"] in ("D2_burst",
                                          "D3_sensitive_first",
                                          "D4_probe") else 1)
        score = max(0.0, 100.0 - points * 40.0)
        if not deviations:
            return None
        return {"score": score, "deviations": deviations,
                "baseline": baseline.get("actorKey")}

    # ========================================================
    # 查询端点支撑
    # ========================================================

    async def list_baselines(self, role: str = None,
                             actor: str = None) -> list[dict]:
        baselines = await self.repo.list_baselines()
        if role:
            baselines = [b for b in baselines
                         if b.get("role") == role]
        if actor:
            baselines = [b for b in baselines
                         if actor in str(b.get("actorKey"))]
        return baselines

    async def list_deviations(self, limit: int = 100) -> list[dict]:
        """近行为预警明细(从事件流水取 behavior 档)"""
        events = await self.repo.list_events(limit=500)
        alerts = [e for e in events
                  if e.get("action") == "behavior_alert"]
        return alerts[:limit]
