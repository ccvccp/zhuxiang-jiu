"""40号 P7a/P7b·雷达2.0 数据访问层(双模式: 内存 + Redis)

表清单:
    radar_channels:    种子频道池(12 频道测试集——时政军事为主
                       构成 L4 合规测试语料)
    radar_events:      事件流(多模态字段/热度/情绪聚合/生命周期)
    radar_event_slots: 事件×槽位观测(热度/情绪密度时序——聚类聚合源)
    radar_scores:      三维评分快照(契合/安全/转化+分级, P7b)

设计对齐(《40号 P7 雷达2.0 规划方案》§3/§4/§8):
    - 事件指纹去重: SHA256(平台+频道+事件主题规范化)——同一事件
      跨槽位/跨平台聚合(P0 作品指纹范式升级为事件级)
    - 评分快照即转化统计源: radar_scores 携带漏斗结果字段
      (clicks/registered/activated, 归因回流填充——P7d/P7e 闭环),
      同类事件历史转化率由既往快照聚合(P7b 转化潜力口径)
    - bool 字段显式还原(P6g-4 Redis 实机教训)
    - 复杂字段(list/dict)注册序列化清单
"""

import json
import os

from repositories.backend import (
    is_redis_mode, get_redis_client, get_in_memory_store, _k,
)

# ============================================================
# 频道类别(种子测试集设计——类别驱动预期合规分级)
# ============================================================

CATEGORY_POLITICS = "politics"    # 时政评述→L4 倾向
CATEGORY_MILITARY = "military"    # 军事评论→L4 倾向
CATEGORY_FINANCE = "finance"      # 财经故事→L1/L2 候选
CATEGORY_HISTORY = "history"      # 历史评述→L3 观察
CATEGORY_CURRENT = "current"     # 时事分析→L3/L4 边界

CATEGORIES = (CATEGORY_POLITICS, CATEGORY_MILITARY,
              CATEGORY_FINANCE, CATEGORY_HISTORY, CATEGORY_CURRENT)

CHANNEL_STATUS_ACTIVE = "active"
CHANNEL_STATUS_PAUSED = "paused"

# 事件生命周期(P7c 分段依据, P7a 仅入库初值)
LIFECYCLE_NEW = "new"          # 首次侦测(未达分段样本)
LIFECYCLE_RISING = "rising"    # 爆发/发酵期(P7c 判定)
LIFECYCLE_PEAK = "peak"        # 峰值期
LIFECYCLE_DECAY = "decay"      # 衰退期

# 事件分级(P7b 三维价值评估产出; L4 屏蔽留痕永不静默丢弃)
GRADE_L1 = "L1"    # 紧急高价值(任务包→46号人工确认)
GRADE_L2 = "L2"    # 常规机会(待处理队列)
GRADE_L3 = "L3"    # 观察储备(知识库, 不推送)
GRADE_L4 = "L4"    # 风险屏蔽(原因留痕备查)
GRADES = (GRADE_L1, GRADE_L2, GRADE_L3, GRADE_L4)

# L1 任务状态(P7d 自主响应触发层)
TASK_STATUS_PENDING = "pending"      # 待人工确认(46号留痕已建)
TASK_STATUS_CONFIRMED = "confirmed"  # 已确认(派发失败留痕态)
TASK_STATUS_REJECTED = "rejected"   # 已否决(决策回流 P7e)
TASK_STATUS_DISPATCHED = "dispatched"  # 已派发(P6b 脚本回执)

# 情绪通道
EMOTION_POSITIVE = "positive"
EMOTION_NEGATIVE = "negative"
EMOTION_NEUTRAL = "neutral"


def _now_iso() -> str:
    from datetime import datetime, UTC
    return datetime.now(UTC).isoformat()


# 序列化类型清单(bool 陷阱还原——P6g-4 实机教训)
_INT_FIELDS = ("channelId", "eventId", "slotId", "scoreId",
               "taskId", "changeId", "dispatchScriptId",
               "reportId", "oldLine", "newLine",
               "funnelClicks", "funnelRegistered",
               "funnelActivated",
               "heatBase", "heatValue", "danmakuCount",
               "commentCount", "botClusterCount", "totalSlots",
               "clicks", "registered", "activated")
_FLOAT_FIELDS = ("emotionDensity", "botShare", "crowdEmotion",
                 "fit", "safety", "conversion", "valueScore",
                 "violationRate", "baselineRate", "hitRate",
                 "falsePositiveRate", "conversionRate")
_BOOL_FIELDS = ("botFiltered", "aggregated", "rehearsalPassed",
                "coordinatedHype", "dispatchExecuted",
                "violationMarked")


class RadarRepository:
    """40号 P7a·雷达2.0 数据访问层"""

    TABLE_CHANNELS = "radar_channels"
    TABLE_EVENTS = "radar_events"
    TABLE_SLOTS = "radar_event_slots"
    TABLE_SCORES = "radar_scores"
    TABLE_TASKS = "radar_tasks"
    TABLE_EFFICIENCY = "radar_efficiency"

    def __init__(self, store: dict = None):
        self.store = (store if store is not None
                     else get_in_memory_store())

    # ============================================================
    # 序列化(口径对齐 blogger_repository)
    # ============================================================

    @staticmethod
    def _serialize(record: dict) -> dict:
        out = {}
        for k, v in record.items():
            if v is None:
                out[k] = ""
            elif isinstance(v, bool):
                out[k] = 1 if v else 0
            elif isinstance(v, (dict, list)):
                out[k] = json.dumps(v, ensure_ascii=False)
            else:
                out[k] = v
        return out

    @staticmethod
    def _deserialize(data: dict) -> dict:
        record = {}
        for k, v in data.items():
            if k in _INT_FIELDS:
                try:
                    record[k] = int(v)
                except (TypeError, ValueError):
                    record[k] = v
            elif k in _FLOAT_FIELDS:
                try:
                    record[k] = float(v)
                except (TypeError, ValueError):
                    record[k] = v
            elif k in _BOOL_FIELDS:
                if v in ("0", 0):
                    record[k] = False
                elif v in ("1", 1):
                    record[k] = True
                else:
                    record[k] = bool(v)
            elif isinstance(v, str) and v.startswith(("{", "[")):
                try:
                    record[k] = json.loads(v)
                except ValueError:
                    record[k] = v
            else:
                record[k] = v
        return record

    def _ensure_store(self):
        for key in ("radar_channels", "radar_events",
                    "radar_event_slots", "radar_scores",
                    "radar_tasks", "radar_efficiency"):
            self.store.setdefault(key, {})

    async def next_id(self, kind: str) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(_k("radar", kind, "seq"))
        self._ensure_store()
        seq_key = f"_radar_{kind}_seq"
        seq = self.store.get(seq_key, 0) + 1
        self.store[seq_key] = seq
        return seq

    async def _save(self, table: str, record_id, record: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(_k("radar", table, record_id),
                              mapping=self._serialize(record))
            return record
        self._ensure_store()
        self.store[table][record_id] = record
        return record

    async def _get(self, table: str, record_id) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(_k("radar", table, record_id))
            return self._deserialize(data) if data else None
        self._ensure_store()
        return self.store[table].get(record_id)

    async def _list(self, table: str, limit: int = 200) -> list[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k("radar", table, "*"))
            result = []
            for key in keys:
                if key.endswith(":seq"):
                    continue
                data = await client.hgetall(key)
                if data:
                    result.append(self._deserialize(data))
        else:
            self._ensure_store()
            result = list(self.store[table].values())
        return result[:limit]

    async def _update(self, table: str, record_id,
                      fields: dict) -> dict:
        record = await self._get(table, record_id)
        if record is None:
            raise KeyError(record_id)
        record.update(fields)
        return await self._save(table, record_id, record)

    # ============================================================
    # 种子频道池
    # ============================================================

    async def save_channel(self, record: dict) -> dict:
        """保存频道({channelId, platform, name, displayName, category,
        domainTags, status, createdAt})"""
        return await self._save(self.TABLE_CHANNELS,
                                record["channelId"], record)

    async def get_channel(self, channel_id: int) -> dict | None:
        return await self._get(self.TABLE_CHANNELS, channel_id)

    async def list_channels(self, status: str = None,
                            limit: int = 100) -> list[dict]:
        records = await self._list(self.TABLE_CHANNELS, limit=1000)
        result = []
        for r in records:
            if status and r.get("status") != status:
                continue
            result.append(r)
        return sorted(result,
                      key=lambda x: x.get("channelId", 0))[:limit]

    async def find_channel_by_name(self, name: str) -> dict | None:
        for r in await self._list(self.TABLE_CHANNELS, limit=1000):
            if r.get("name") == name:
                return r
        return None

    # ============================================================
    # 事件流
    # ============================================================

    async def save_event(self, record: dict) -> dict:
        """保存事件({eventId, fingerprint, channelId, channelName,
        platform, title, summary, asrTranscript, ocrTags,
        bgmFingerprint, danmakuSample, category, heatBase,
        emotionDensity, crowdEmotion, botFiltered, botShare,
        lifecycle, totalSlots, firstSeenAt, lastSeenAt})"""
        return await self._save(self.TABLE_EVENTS,
                                record["eventId"], record)

    async def get_event(self, event_id: int) -> dict | None:
        return await self._get(self.TABLE_EVENTS, event_id)

    async def update_event(self, event_id: int,
                           fields: dict) -> dict:
        return await self._update(self.TABLE_EVENTS, event_id,
                                   fields)

    async def list_events(self, channel_id: int = None,
                          category: str = None,
                          lifecycle: str = None,
                          grade: str = None,
                          limit: int = 200) -> list[dict]:
        records = await self._list(self.TABLE_EVENTS, limit=2000)
        result = []
        for r in records:
            if channel_id is not None \
                    and r.get("channelId") != channel_id:
                continue
            if category and r.get("category") != category:
                continue
            if lifecycle and r.get("lifecycle") != lifecycle:
                continue
            if grade and r.get("grade") != grade:
                continue
            result.append(r)
        return sorted(result,
                      key=lambda x: (-int(x.get("heatBase") or 0),
                                     x.get("eventId", 0)))[:limit]

    async def find_event_by_fingerprint(self,
                                        fingerprint: str
                                        ) -> dict | None:
        for r in await self._list(self.TABLE_EVENTS, limit=5000):
            if r.get("fingerprint") == fingerprint:
                return r
        return None

    # ============================================================
    # 事件×槽位观测(聚类聚合源)
    # ============================================================

    async def save_slot(self, record: dict) -> dict:
        """保存槽位观测({slotId, eventId, fingerprint, slotKey,
        heatValue, danmakuCount, commentCount, botClusterCount,
        emotionDensity, createdAt})"""
        return await self._save(self.TABLE_SLOTS,
                                record["slotId"], record)

    async def get_slot(self, slot_id: int) -> dict | None:
        return await self._get(self.TABLE_SLOTS, slot_id)

    async def list_slots(self, event_id: int = None,
                         fingerprint: str = None,
                         limit: int = 500) -> list[dict]:
        records = await self._list(self.TABLE_SLOTS, limit=5000)
        result = []
        for r in records:
            if event_id is not None \
                    and r.get("eventId") != event_id:
                continue
            if fingerprint and r.get("fingerprint") != fingerprint:
                continue
            result.append(r)
        return sorted(result,
                      key=lambda x: (x.get("fingerprint", ""),
                                     x.get("slotKey", ""))
                      )[:limit]

    async def find_slot(self, fingerprint: str,
                        slot_key: str) -> dict | None:
        """按指纹+槽位键查观测(幂等——同槽位重复采集不重复入库)"""
        for r in await self._list(self.TABLE_SLOTS, limit=5000):
            if r.get("fingerprint") == fingerprint \
                    and r.get("slotKey") == slot_key:
                return r
        return None

    # ============================================================
    # 三维评分快照(P7b——同时是转化潜力的历史统计源)
    # ============================================================

    async def save_score(self, record: dict) -> dict:
        """保存评分快照({scoreId, eventId, fingerprint, category,
        title, fit, fitModules, safety, safetyReasons, conversion,
        conversionSamples, valueScore, grade, blockedReasons,
        lifecycle, heatBase, clicks, registered, activated,
        scoredAt})——clicks/registered/activated 为漏斗结果字段
        (归因回流填充, clicks>0 的历史快照构成转化统计样本)"""
        return await self._save(self.TABLE_SCORES,
                                record["scoreId"], record)

    async def get_score(self, score_id: int) -> dict | None:
        return await self._get(self.TABLE_SCORES, score_id)

    async def list_scores(self, event_id: int = None,
                          category: str = None,
                          grade: str = None,
                          limit: int = 200) -> list[dict]:
        records = await self._list(self.TABLE_SCORES, limit=5000)
        result = []
        for r in records:
            if event_id is not None \
                    and r.get("eventId") != event_id:
                continue
            if category and r.get("category") != category:
                continue
            if grade and r.get("grade") != grade:
                continue
            result.append(r)
        return sorted(result,
                      key=lambda x: (-int(x.get("scoreId") or 0))
                      )[:limit]

    # ============================================================
    # L1 任务包(P7d 自主响应触发层)
    # ============================================================

    async def save_task(self, record: dict) -> dict:
        """保存任务包({taskId, traceId, eventId, changeId(46号),
        status: pending/confirmed/rejected/dispatched, plan(预案
        四件套), decisionBasis(决策依据), downstream, dispatchError,
        dispatchScriptId, dispatchExecuted, requestedBy, confirmedBy,
        confirmNote, createdAt, confirmedAt})"""
        return await self._save(self.TABLE_TASKS,
                                record["taskId"], record)

    async def get_task(self, task_id: int) -> dict | None:
        return await self._get(self.TABLE_TASKS, task_id)

    async def update_task(self, task_id: int,
                         fields: dict) -> dict:
        return await self._update(self.TABLE_TASKS, task_id, fields)

    async def list_tasks(self, status: str = None,
                         limit: int = 200) -> list[dict]:
        records = await self._list(self.TABLE_TASKS, limit=2000)
        result = []
        for r in records:
            if status and r.get("status") != status:
                continue
            result.append(r)
        return sorted(result,
                      key=lambda x: x.get("taskId", 0))[:limit]

    async def find_task_by_event(self, event_id: int,
                                 statuses: tuple) -> dict | None:
        """按事件查活跃任务(幂等——同事件 pending/confirmed/
        dispatched 不重复建)"""
        for r in await self._list(self.TABLE_TASKS, limit=2000):
            if r.get("eventId") == event_id \
                    and r.get("status") in statuses:
                return r
        return None

    async def find_task_by_trace(self, trace_id: str) -> dict | None:
        """按溯源 ID 查任务(P7e 归因闭环锚点)"""
        for r in await self._list(self.TABLE_TASKS, limit=2000):
            if r.get("traceId") == trace_id:
                return r
        return None

    # ============================================================
    # 效能周报与阈值留痕(P7e——kind 分型共用表)
    # ============================================================

    async def save_efficiency(self, record: dict) -> dict:
        """保存效能记录({reportId, kind: weekly/threshold, ...})
        weekly: 触发数/命中率/误报率/漏报案例库;
        threshold: oldLine/newLine/违规率/基线/样本(只紧不松留痕)"""
        return await self._save(self.TABLE_EFFICIENCY,
                                record["reportId"], record)

    async def get_efficiency(self, report_id: int) -> dict | None:
        return await self._get(self.TABLE_EFFICIENCY, report_id)

    async def list_efficiency(self, kind: str = None,
                              limit: int = 100) -> list[dict]:
        records = await self._list(self.TABLE_EFFICIENCY,
                                   limit=2000)
        result = []
        for r in records:
            if kind and r.get("kind") != kind:
                continue
            result.append(r)
        return sorted(result,
                      key=lambda x: x.get("reportId", 0)
                      )[:limit]

    async def get_current_l1_line(self,
                                  default: int = 75) -> int:
        """当前 L1 价值阈值(最新 tighten 留痕的 newLine;
        无留痕=默认 75——阈值只紧不松, 放宽须 46号建议书)"""
        tightened = await self.list_efficiency(kind="threshold",
                                               limit=1000)
        if not tightened:
            return default
        latest = max(tightened,
                     key=lambda x: x.get("reportId", 0))
        return int(latest.get("newLine") or default)
