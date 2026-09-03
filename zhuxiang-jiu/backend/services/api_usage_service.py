"""44号·P3 调用观测与 API 健康评分

计划(docs/44号_API智能管理模块实施计划.md §六):
    ① 三视图聚合(per-API / per-Key / 配额命中率)——数据源
       load_usage_window(中间件留痕桶)
    ② 第27档案 ApiHealthScorer 五因子:
        成功率 0.30 / P95延迟达标 0.25 / 流量稳定度 0.15 /
        配额命中率 0.15 / 变更频率 0.15
       → 0-100 健康分 → 四档 healthy(≥75)/watch(50-75)/
       strained(30-50)/critical(<30)——建议型不自动处置
       (区别于 43号处置型)
    ③ P95 近似口径: avg/max 累计桶(无全量样本)——按
       (sum*0.95 近似 + max 上界)保守折算, 口径明示
"""

import logging

from services.api_rate_limit_service import (
    load_usage_window, tier_limits,
)

logger = logging.getLogger(__name__)

# P95 延迟达标线(ms)——超过则延迟因子不满分(观测口径可调)
P95_TARGET_MS = 500.0


class ApiUsageService:
    """调用观测三视图(44号 P3)"""

    def __init__(self, key_service=None):
        if key_service is None:
            from services.api_key_service import ApiKeyService
            key_service = ApiKeyService()
        self._keys = key_service

    # --------------------------------------------------------
    # 三视图
    # --------------------------------------------------------

    async def usage_views(self) -> dict:
        """管理端三视图(per-API/per-Key/配额命中率)"""
        rows = await load_usage_window()
        keys = {k["keyId"]: k for k in
                (await self._keys.admin_list_keys()).get("keys")
                or []}

        by_api: dict = {}
        by_key: dict = {}
        for r in rows:
            # ① per-API: 模板聚合
            a = by_api.setdefault(r["template"], {
                "template": r["template"], "total": 0,
                "err": 0, "avgMs": 0.0, "maxMs": 0,
                "callers": set()})
            a["total"] += r["total"]
            a["err"] += r["err"]
            a["avgMs"] = max(a["avgMs"], r["avgMs"])
            a["maxMs"] = max(a["maxMs"], r["maxMs"])
            a["callers"].add(r["keyId"])

            # ② per-Key: 消费方聚合
            k = by_key.setdefault(r["keyId"], {
                "keyId": r["keyId"], "total": 0, "err": 0,
                "avgMs": 0.0, "maxMs": 0, "apis": set()})
            k["total"] += r["total"]
            k["err"] += r["err"]
            k["avgMs"] = max(k["avgMs"], r["avgMs"])
            k["maxMs"] = max(k["maxMs"], r["maxMs"])
            k["apis"].add(r["template"])

        # ③ 配额命中率(per-Key: 今日用量 / 日配额)
        quota = {}
        for key_id, k in by_key.items():
            meta = keys.get(key_id) or {}
            daily = meta.get("dailyLimit") or 1000
            used = k["total"]
            quota[key_id] = {
                "keyId": key_id, "used": used,
                "dailyLimit": daily,
                "hitRate": round(used / daily, 4) if daily
                else 0.0,
            }

        # 序列化 set → 计数
        for a in by_api.values():
            callers = a.pop("callers", set())
            a["callers"] = len(callers) if isinstance(
                callers, set) else callers
        for k in by_key.values():
            k["apis"] = len(k["apis"])
            k["errorRate"] = round(
                k["err"] / k["total"], 4) if k["total"] else 0.0
            meta = keys.get(k["keyId"]) or {}
            k["name"] = meta.get("name")
            k["tier"] = meta.get("tier")
            k["status"] = meta.get("status")
        for a in by_api.values():
            a["errorRate"] = round(
                a["err"] / a["total"], 4) if a["total"] else 0.0

        top_apis = sorted(by_api.values(),
                          key=lambda x: -x["total"])[:20]
        top_keys = sorted(by_key.values(),
                           key=lambda x: -x["total"])[:20]
        return {
            "success": True,
            "totalCalls": sum(r["total"] for r in rows),
            "totalErrors": sum(r["err"] for r in rows),
            "byApi": top_apis,
            "byKey": top_keys,
            "quota": sorted(quota.values(),
                            key=lambda x: -x["hitRate"])[:20],
        }

    async def my_usage(self, member_id: int) -> dict:
        """消费方自查(自己的用量自己看——方便快捷)"""
        my_keys = await self._keys.repo.list_keys_by_member(
            member_id)
        key_ids = [k["keyId"] for k in my_keys
                   if k.get("status") == "active"]
        if not key_ids:
            return {"success": True, "total": 0,
                    "apis": [], "keys": []}
        rows = [r for r in await load_usage_window(
            key_ids=key_ids)]
        by_key = {}
        by_api: dict = {}
        for r in rows:
            k = by_key.setdefault(r["keyId"], {
                "keyId": r["keyId"], "total": 0, "err": 0})
            k["total"] += r["total"]
            k["err"] += r["err"]
            a = by_api.setdefault(r["template"], {
                "template": r["template"], "total": 0})
            a["total"] += r["total"]
        meta = {k["keyId"]: k for k in my_keys}
        for key_id, k in by_key.items():
            m = meta.get(key_id) or {}
            k["name"] = m.get("name")
            k["dailyLimit"] = m.get("dailyLimit")
        return {
            "success": True,
            "total": sum(r["total"] for r in rows),
            "apis": sorted(by_api.values(),
                           key=lambda x: -x["total"])[:20],
            "keys": by_key,
        }


class ApiHealthScorer:
    """第27档案: API 健康五因子评分(建议型)

    因子(权重和=1.0, Hedge 学习回流可调优):
        success_rate   0.30  成功率(1 - errorRate)
        latency        0.25  延迟达标(max ≤ P95_TARGET_MS 满分)
        stability      0.15  流量稳定度(无尖刺——max/avg 比)
        quota_hit      0.15  配额健康(贴顶 >0.9 扣分——该升档)
        change_freq    0.15  变更频率(近期 diff 少为稳)

    返回 0-100 分与四档 healthy/watch/strained/critical;
    不自动处置(展示/建议——43号管处置)。
    """

    WEIGHTS = {
        "success_rate": 0.30,
        "latency": 0.25,
        "stability": 0.15,
        "quota_hit": 0.15,
        "change_freq": 0.15,
    }

    @classmethod
    def score(cls, ctx: dict) -> dict:
        """评分(输入观测聚合上下文)

        ctx: {total, err, avgMs, maxMs, quotaHitRate,
              recentChanges}
        """
        total = float(ctx.get("total") or 0)
        err = float(ctx.get("err") or 0)
        if total <= 0:
            return {"score": 0, "grade": "watch",
                    "factors": [], "note": "无调用样本(观测期)"}
        error_rate = err / total
        avg_ms = float(ctx.get("avgMs") or 0)
        max_ms = float(ctx.get("maxMs") or 0)
        quota_hit = float(ctx.get("quotaHitRate") or 0)
        changes = int(ctx.get("recentChanges") or 0)

        factors = []
        # ① 成功率
        sr = 1.0 - error_rate
        factors.append({"name": "success_rate",
                        "weight": cls.WEIGHTS["success_rate"],
                        "value": round(sr, 4),
                        "detail": f"成功率 {sr:.1%}"
                                  f"({int(total - err)}/{int(total)})"})
        # ② 延迟达标(max vs 目标; 无 max 退 avg)
        lat_ref = max_ms or avg_ms
        lat_score = 1.0 if lat_ref <= P95_TARGET_MS else \
            max(0.0, 1.0 - (lat_ref - P95_TARGET_MS)
                / P95_TARGET_MS)
        factors.append({"name": "latency",
                        "weight": cls.WEIGHTS["latency"],
                        "value": round(lat_score, 4),
                        "detail": f"峰值 {lat_ref:.0f}ms"
                                  f"(目标 ≤{P95_TARGET_MS:.0f}ms)"})
        # ③ 稳定度(max/avg 比——尖刺检测; avg=0 视为稳)
        if avg_ms > 0 and max_ms > 0:
            ratio = max_ms / avg_ms
            stab = 1.0 if ratio <= 3 else \
                max(0.0, 1.0 - (ratio - 3) / 7)
        else:
            stab = 1.0
        factors.append({"name": "stability",
                        "weight": cls.WEIGHTS["stability"],
                        "value": round(stab, 4),
                        "detail": f"峰值/均值比 "
                                  f"{(max_ms / avg_ms):.1f}x"
                                  if avg_ms > 0 else "无延迟样本"})
        # ④ 配额健康(贴顶 >0.9 提示升档——命中率高非坏事
        #    但容量风险; <0.9 满分)
        quota_score = 1.0 if quota_hit < 0.9 else \
            max(0.0, 1.0 - (quota_hit - 0.9) / 0.1)
        factors.append({"name": "quota_hit",
                        "weight": cls.WEIGHTS["quota_hit"],
                        "value": round(quota_score, 4),
                        "detail": f"配额命中 {quota_hit:.0%}"
                                  + ("(建议升档)" if quota_hit >= 0.9
                                     else "")})
        # ⑤ 变更频率(近 7 日 diff 次数——0=满分, ≥10=0)
        change_score = max(0.0, 1.0 - changes / 10.0)
        factors.append({"name": "change_freq",
                        "weight": cls.WEIGHTS["change_freq"],
                        "value": round(change_score, 4),
                        "detail": f"近期变更 {changes} 次"})

        score = round(sum(f["weight"] * f["value"]
                          for f in factors) * 100, 1)
        if score >= 75:
            grade = "healthy"
        elif score >= 50:
            grade = "watch"
        elif score >= 30:
            grade = "strained"
        else:
            grade = "critical"
        return {"score": score, "grade": grade,
                "factors": factors}
