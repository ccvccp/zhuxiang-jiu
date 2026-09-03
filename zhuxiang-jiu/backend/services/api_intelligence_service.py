"""44号·P4 AI 智能自治(基线异常检测 + 配额推荐 + NL 助手)

计划(docs/44号_API智能管理模块实施计划.md §七):
    ① 流量异常检测(43号 UEBA 基线范式平移):
        基线: per-API 模板近 7 日日总量(μ/σ, ≥3 样本天)
        三检测器(只记录不处置):
        - 尖刺 spike:  当日 > μ+3σ 且 μ ≥ 最小阈值
        - 骤降 drop:   当日 < μ-3σ 且绝对量 ≥ 阈值(防冷启动误报)
        - 错误激增 error_burst: 错误率环比 ×3 且样本 ≥20
          (硬样本阈值——43号 D5 同口径)
    ② 智能配额推荐(规则+统计, 确定性优先非 LLM):
        近 7 日 P95 日用量 × 安全系数 1.3 → 推荐档/自定义阈值
    ③ NL API 助手(LLM 三态, 41/42号范式):
        mock(确定性模板——事实句由代码生成, 数字永远来自
        查询层) / real(llm_client.chat() 失败回退 mock 模板)

设计铁律: 事实句由代码生成, LLM 仅做编排与润色——
LLM 幻觉不进入数据(42号发票摘要同口径)。
"""

import logging
import math

from core.helpers import ts

from services.api_rate_limit_service import (
    load_usage_window, record_usage_event, TIERS,
)

logger = logging.getLogger(__name__)

# 基线窗口与阈值(43号 UEBA 同范式, 计划 §七)
BASELINE_WINDOW_DAYS = 7
BASELINE_MIN_DAYS = 3       # 有效基线最少样本天
SPIKE_SIGMA = 3.0            # 尖刺阈值(σ)
SPIKE_MIN_MEAN = 5.0         # μ 最小阈值(防低量噪声)
DROP_MIN_ABS = 20            # 骤降最小绝对量(防冷启动误报)
ERROR_BURST_RATIO = 3.0     # 错误率环比倍数
ERROR_BURST_MIN_SAMPLES = 20   # 硬样本阈值(43号 D5 同口径)

# 配额推荐
RECOMMEND_SAFETY_FACTOR = 1.3
RECOMMEND_WINDOW_DAYS = 7

# 事件状态(P5 裁决回流消费: pending → confirmed/false_positive)
ANOMALY_PENDING = "pending"


# ============================================================
# 历史(近 N 日)窗口读取——基线数据源
# ============================================================

async def load_history_days(days: int = BASELINE_WINDOW_DAYS
                           ) -> dict:
    """按日聚合历史统计({template: {day: {total, err}}})

    Redis: 直接扫历史日桶(stat 键含 yyyymmdd——P3 桶已按日
    切分, 无需另存); 内存模式: _MEM_USAGE 已含 day 维度。
    """
    from datetime import datetime, UTC, timedelta
    history: dict = {}
    today = datetime.now(UTC).date()

    from repositories.backend import (
        is_redis_mode, get_redis_client, _k,
    )
    if is_redis_mode():
        client = await get_redis_client()
        keys = []
        for i in range(days):
            day = (today - timedelta(days=i)).strftime("%Y%m%d")
            keys += await client.keys(_k(
                "api44", "stat", "*", day, "*"))
        pipe = client.pipeline(transaction=False)
        for k in keys:
            pipe.hgetall(k)
        for stat_key, stat in zip(keys, await pipe.execute()):
            if not stat:
                continue
            prefix = _k("api44", "stat") + ":"
            rest = stat_key[len(prefix):]
            key_str, day, template = rest.split(":", 2)
            try:
                key_id = int(key_str)
            except ValueError:
                continue
            t = history.setdefault(template, {})
            d = t.setdefault(day, {"total": 0, "err": 0,
                                   "keys": set()})
            d["total"] += int(stat.get("total") or 0)
            err_key = _k("api44", "err", key_id, day, template)
            d["keys"].add(key_id)
        # 错误数补读(单独键)
        for template, days_map in history.items():
            for day, d in days_map.items():
                total_err = 0
                for key_id in d.get("keys", set()):
                    e = await client.hget(_k(
                        "api44", "err", key_id, day, template),
                        "total")
                    total_err += int(e or 0)
                d["err"] = total_err
        return history

    # 内存模式
    import services.api_rate_limit_service as arls
    for key_id, buckets in arls._MEM_USAGE.items():
        for (day, template), b in buckets.items():
            t = history.setdefault(template, {})
            d = t.setdefault(day, {"total": 0, "err": 0,
                                   "keys": set()})
            d["total"] += b["total"]
            d["err"] += b["err"]
            d["keys"].add(key_id)
    return history


class ApiAnomalyService:
    """API 流量异常检测(P4; 只记录不处置)"""

    def __init__(self, repo=None):
        if repo is None:
            from repositories.api_manager_repository import (
                ApiManager44Repository,
            )
            repo = ApiManager44Repository()
        self.repo = repo

    async def detect(self) -> dict:
        """全量检测(基线 vs 当日)→ 异常事件落库

        Returns:
            {success, detected, events: [...](含中文归因)}
        """
        from datetime import datetime, UTC
        history = await load_history_days(
            BASELINE_WINDOW_DAYS)
        today = datetime.now(UTC).strftime("%Y%m%d")
        events = []
        for template, days_map in history.items():
            hist = {d: v for d, v in days_map.items()
                    if d != today}
            if len(hist) < BASELINE_MIN_DAYS:
                continue   # 样本不足(冷启动空窗评估)
            values = [v["total"] for v in hist.values()]
            mean = sum(values) / len(values)
            var = sum((x - mean) ** 2
                      for x in values) / len(values)
            std = math.sqrt(var)
            today_v = days_map.get(today) or {
                "total": 0, "err": 0}
            today_total = today_v["total"]
            today_err = today_v["err"]

            # 检测器①: 尖刺
            if mean >= SPIKE_MIN_MEAN and std > 0 \
                    and today_total > mean + SPIKE_SIGMA * std:
                ratio = today_total / mean if mean else 0
                events.append(self._event(
                    template, "spike", today_total, mean, std,
                    f"{template} 今日调用量 {today_total} 次, "
                    f"为基线 {ratio:.1f} 倍"
                    f"(μ={mean:.0f}, σ={std:.0f})"))

            # 检测器②: 骤降(绝对量防误报)
            elif std > 0 and today_total < mean - SPIKE_SIGMA * std \
                    and today_total >= DROP_MIN_ABS:
                events.append(self._event(
                    template, "drop", today_total, mean, std,
                    f"{template} 今日调用量 {today_total} 次, "
                    f"较基线(μ={mean:.0f})骤降 "
                    f"{(1 - today_total / mean):.0%}"))

            # 检测器③: 错误激增(样本硬阈值)
            today_rate = today_err / today_total \
                if today_total else 0.0
            hist_err = sum(v["err"] for v in hist.values())
            hist_total = sum(values)
            hist_rate = hist_err / hist_total if hist_total else 0
            if today_total >= ERROR_BURST_MIN_SAMPLES \
                    and hist_rate > 0 \
                    and today_rate >= hist_rate * ERROR_BURST_RATIO:
                events.append(self._event(
                    template, "error_burst", today_total,
                    mean, std,
                    f"{template} 今日错误率 "
                    f"{today_rate:.0%}({today_err}/"
                    f"{today_total}), 为基线 "
                    f"{today_rate / hist_rate:.1f} 倍"
                    f"(基线 {hist_rate:.0%})"))

        # 落库(今日事件幂等——同模板同类型当日一条;
        # 已存在(含已裁决)不覆盖——人工裁决真值保护,
        # 重复检测不得把 confirmed/false_positive 重置回 pending)
        for e in events:
            key = (f"{e['template']}|{e['kind']}|"
                   f"{e['day']}")
            if not await self._event_exists(key):
                await self._save_event(e)
        return {"success": True, "detected": len(events),
                "events": events}

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------

    def _event(self, template: str, kind: str, total: float,
                mean: float, std: float, summary: str) -> dict:
        return {"template": template, "kind": kind,
                "day": ts()[:10], "total": total,
                "baselineMean": round(mean, 1),
                "baselineStd": round(std, 1),
                "summary": summary, "status": ANOMALY_PENDING}

    async def _event_exists(self, key: str) -> bool:
        """事件是否已存在(同模板同类型当日幂等判定)"""
        if is_redis_mode_cached():
            client = await get_redis_client_cached()
            return bool(await client.exists(
                _k_cached("api44", "anomaly", key)))
        store = get_in_memory_store_cached()
        return key in store.get("api44_anomalies", {})

    async def _save_event(self, event: dict) -> None:
        """事件落库(自然键 template|kind|day 幂等)"""
        key = (f"{event['template']}|{event['kind']}|"
               f"{event['day']}")
        event_id = await self._event_id(key)
        # 回填 eventId: detect() 响应事件可直接裁决
        # (P4 面板「真异常/误报」按钮消费此字段)
        event["eventId"] = event_id
        if is_redis_mode_cached():
            client = await get_redis_client_cached()
            await client.hset(
                _k_cached("api44", "anomaly", key),
                mapping=event)
        else:
            store = get_in_memory_store_cached()
            store.setdefault("api44_anomalies", {})[key] = event

    async def _event_id(self, key: str) -> int:
        if is_redis_mode_cached():
            client = await get_redis_client_cached()
            existing = await client.hget(
                _k_cached("api44", "anomaly", key), "eventId")
            if existing:
                return int(existing)
            return await client.incr(
                _k_cached("api44", "anomaly", "seq"))
        store = get_in_memory_store_cached()
        bucket = store.setdefault("api44_anomalies", {})
        if key in bucket:
            return bucket[key]["eventId"]
        seq = store.get("_api44_anomaly_seq", 0) + 1
        store["_api44_anomaly_seq"] = seq
        return seq

    async def list_events(self, status: str = None) -> dict:
        """异常事件队列(P5 裁决回流消费)"""
        if is_redis_mode_cached():
            client = await get_redis_client_cached()
            keys = await client.keys(
                _k_cached("api44", "anomaly", "*"))
            events = []
            pipe = client.pipeline(transaction=False)
            valid_keys = [k for k in keys
                          if not k.endswith(":seq")]
            for k in valid_keys:
                pipe.hgetall(k)
            for data in await pipe.execute():
                if data:
                    events.append(data)
        else:
            store = get_in_memory_store_cached()
            events = [dict(v) for v in
                      store.get("api44_anomalies", {}).values()]
        events.sort(key=lambda e: -(int(e.get("eventId") or 0)))
        if status:
            events = [e for e in events
                      if e.get("status") == status]
        return {"success": True, "total": len(events),
                "events": events}

    async def decide_event(self, event_id: int,
                           confirmed: bool) -> dict:
        """事件裁决(P5 学习回流真值源: confirmed/false_positive)"""
        event = await self._find_event(event_id)
        if event is None:
            raise KeyError(f"异常事件 {event_id} 不存在")
        event["status"] = ("confirmed" if confirmed
                           else "false_positive")
        await self._save_event(event)
        return event

    async def _find_event(self, event_id: int) -> dict | None:
        if is_redis_mode_cached():
            client = await get_redis_client_cached()
            keys = await client.keys(
                _k_cached("api44", "anomaly", "*"))
            for k in keys:
                if k.endswith(":seq"):
                    continue
                data = await client.hgetall(k)
                if data and str(data.get("eventId")) == \
                        str(event_id):
                    data["_key"] = k
                    return data
            return None
        store = get_in_memory_store_cached()
        for key, e in store.get("api44_anomalies",
                                {}).items():
            if str(e.get("eventId")) == str(event_id):
                return e
        return None


# ============================================================
# 智能配额推荐
# ============================================================

class ApiRecommendService:
    """配额推荐(规则+统计, 确定性优先——非 LLM)"""

    def __init__(self, key_service=None):
        if key_service is None:
            from services.api_key_service import ApiKeyService
            key_service = ApiKeyService()
        self._keys = key_service

    async def recommend(self, key_id: int) -> dict:
        """近 7 日 P95 用量 × 1.3 → 推荐档位/阈值

        Raises:
            KeyError: keyId 不存在
            ValueError: 无历史用量(无法推荐)
        """
        d = await self._keys.repo.digest_by_key_id(key_id)
        if d is None:
            raise KeyError(f"Key {key_id} 不存在")
        rec = await self._keys.repo.get_key(d)

        # 该 Key 的历史日用量(load_history_days 全量后过滤)
        history = await load_history_days(
            RECOMMEND_WINDOW_DAYS)
        from datetime import datetime, UTC, timedelta
        today = datetime.now(UTC).strftime("%Y%m%d")
        daily_totals = []
        for template, days_map in history.items():
            for day, v in days_map.items():
                if day != today and key_id in v.get("keys", ()):
                    daily_totals.append(v["total"])
        if not daily_totals:
            raise ValueError(
                "无历史用量(推荐需近 7 日调用数据)")

        daily_totals.sort()
        n = len(daily_totals)
        # P95(最近似排序法; 样本≤7 天用线性插值)
        idx = min(n - 1, max(0, math.ceil(0.95 * n) - 1))
        p95 = daily_totals[idx]
        recommended = max(1, int(p95 * RECOMMEND_SAFETY_FACTOR))

        # 档位匹配(满足推荐量的最低档; 超三档 → pro + 自定义)
        tier = None
        for name in ("free", "basic", "pro"):
            if recommended <= TIERS[name]["daily"]:
                tier = name
                break
        if tier is None:
            tier = "pro"

        current_daily = rec.get("customDaily") or \
            TIERS.get(rec.get("tier") or "free",
                      TIERS["free"])["daily"]
        # 贴顶判定(连续贴顶提示)
        hit_rate = (sum(daily_totals) / len(daily_totals)
                    / current_daily) if current_daily else 0
        advice = (f"该 Key 近 7 日 P95 日用量 {p95} 次, "
                  f"× 安全系数 {RECOMMEND_SAFETY_FACTOR} → "
                  f"建议日配额 {recommended} 次")
        if hit_rate >= 0.9:
            advice += (f"(当前配额 {current_daily} 长期贴顶"
                       f"命中率 {hit_rate:.0%}, 建议升档)")
        elif recommended < current_daily * 0.5:
            advice += (f"(当前配额 {current_daily} 利用率低, "
                       f"可降档节省资源)")
        return {
            "success": True, "keyId": key_id,
            "tier": rec.get("tier"),
            "currentDaily": current_daily,
            "p95Daily": p95, "safetyFactor":
                RECOMMEND_SAFETY_FACTOR,
            "recommendedDaily": recommended,
            "recommendedTier": tier,
            "hitRate": round(hit_rate, 4), "advice": advice,
        }


# ============================================================
# NL 助手(mock 确定性模板 + real LLM 润色, 三态)
# ============================================================

class ApiAssistantService:
    """NL API 助手(数字永远来自查询层)"""

    async def answer(self, question: str,
                     member_id: int = None) -> dict:
        """问答(意图路由 → 事实句 → mock 直答/real LLM 润色)"""
        question = (question or "").strip()
        if not question:
            raise ValueError("问题不能为空")

        intent, fact = await self._route(question, member_id)
        mode = "mock"
        answer = fact["answer"]

        # real 轨: LLM_ENABLED=on 时润色(失败回退 mock——
        # 41/42号三态范式)
        from services.llm_client import llm_enabled
        if llm_enabled():
            try:
                from services.llm_client import provider_client
                reply = provider_client().chat(
                    system="你是 API 管理助手。用不超过 3 句"
                           "中文简洁回答。只使用用户提供的数据"
                           ", 不编造任何数字。",
                    user=f"问题: {question}\n"
                         f"已知事实(以此为准): {fact['answer']}")
                if reply and reply.strip():
                    answer = reply.strip()
                    mode = "real"
            except Exception as exc:
                logger.warning("api44_assistant_llm_skip: %s",
                               exc)

        return {"success": True, "intent": intent,
                "mode": mode, "answer": answer,
                "fact": fact["answer"],
                "note": "数字来自查询层, LLM 仅润色" if mode
                == "real" else "mock 确定性模板"}

    async def _route(self, question: str,
                     member_id: int) -> tuple:
        """意图路由(关键词匹配——确定性)"""
        from services.api_usage_service import ApiUsageService
        usage = ApiUsageService()
        q = question.lower()

        # 意图①: 最慢/延迟
        if any(k in q for k in ("慢", "延迟", "最慢", "latency")):
            views = await usage.usage_views()
            by_api = views.get("byApi") or []
            if not by_api:
                return ("latency", {"answer":
                        "当前无 API 调用观测数据(发布 API 并"
                        "产生 Key 面流量后可查询)"})
            slowest = max(by_api, key=lambda a: a.get("maxMs")
                          or 0)
            return ("latency", {"answer": (
                f"最慢的 API 是 {slowest['template']}: "
                f"峰值 {slowest['maxMs']}ms, 平均 "
                f"{slowest['avgMs']}ms, 今日调用 "
                f"{slowest['total']} 次"
                f"(错误率 {slowest['errorRate']:.1%})")})

        # 意图②: 最多/调用量
        if any(k in q for k in ("最多", "调用", "热门", "top")):
            views = await usage.usage_views()
            by_api = views.get("byApi") or []
            if not by_api:
                return ("top_api", {"answer":
                        "当前无 API 调用观测数据"})
            top = by_api[0]
            return ("top_api", {"answer": (
                f"调用量最高的 API 是 {top['template']}: "
                f"今日 {top['total']} 次"
                f"({top['callers']} 个消费方, 错误率 "
                f"{top['errorRate']:.1%})")})

        # 意图③: 我的用量(会员)
        if any(k in q for k in ("我的", "自己", "my")):
            if member_id is None:
                return ("my_usage", {"answer":
                        "请先登录(X-Member-Id)后查询个人用量"})
            my = await usage.my_usage(member_id)
            keys_desc = "; ".join(
                f"#{k['keyId']} {k['name'] or ''} "
                f"{k['total']} 次"
                for k in (my.get("keys") or {}).values()) \
                or "无"
            return ("my_usage", {"answer": (
                f"您今日总调用 {my.get('total') or 0} 次。"
                f"各 Key: {keys_desc}")})

        # 意图④: 搜索 API(台账检索)
        if any(k in q for k in ("有没有", "查找", "搜", "接口",
                                "api 列表")):
            return await self._search_api(question)

        # 兜底: 帮助
        return ("help", {"answer": (
            "我可以回答: 哪个接口最慢 / 哪个接口调用量最高 / "
            "我的用量 / 搜索接口(如「有没有查物流的接口」)。"
            "请用更具体的问题提问")})

    async def _search_api(self, question: str) -> tuple:
        """台账关键词检索(中文分词简化: 逐字匹配)"""
        from services.api_registry_service import (
            ApiRegistryService,
        )
        entries = (await ApiRegistryService().list_registry(
            limit=10000)).get("entries") or []
        # 提取问题关键词(去停用词——简化: 2 字以上片段)
        stopwords = ("有没有", "查找", "搜索", "接口", "api",
                     "哪个", "什么", "怎么", "如何")
        words = set()
        for i in range(len(question) - 1):
            frag = question[i:i + 2]
            if frag not in stopwords and \
                    not frag.isdigit():
                words.add(frag)
        hits = []
        for e in entries:
            text = f"{e.get('path', '')} " \
                   f"{e.get('module', '')} " \
                   f"{e.get('summary', '')}"
            if any(w in text for w in words):
                hits.append(e)
        if not hits:
            return ("search", {"answer": (
                f"台账中未找到与「{question}」相关的 API"
                "(共 {} 个已登记)".format(len(entries)))})
        lines = [f"找到 {len(hits)} 个相关 API:"
                 for _ in range(1)]
        for e in hits[:5]:
            lines.append(f"· {e.get('method')} "
                         f"{e.get('path')}({e.get('module')})")
        if len(hits) > 5:
            lines.append(f"… 共 {len(hits)} 个")
        return ("search", {"answer": "\n".join(lines)})


# ============================================================
# 模式工具(避免模块级循环导入)
# ============================================================

def is_redis_mode_cached() -> bool:
    from repositories.backend import is_redis_mode
    return is_redis_mode()


async def get_redis_client_cached():
    from repositories.backend import get_redis_client
    return await get_redis_client()


def get_in_memory_store_cached() -> dict:
    from repositories.backend import get_in_memory_store
    return get_in_memory_store()


def _k_cached(entity: str, *parts) -> str:
    from repositories.backend import _k
    return _k(entity, *parts)
