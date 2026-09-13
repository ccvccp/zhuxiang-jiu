"""72号·AI智能自动引流大模型 P1 感知跃迁服务
(attract72_p1_service)

规划(docs/72号_AI智能自动引流大模型_创新规划方案.md
§四 4.1/§七 P1):
    ① 渠道人格画像同步(traffic 博主+promotion
       会员 → 确定性分类: 品鉴/分享/优惠敏感/
       新晋四型+层级+信任分+置信度)
    ② 意图快照引擎(点击上下文 → 词表密度:
       意图标签/场景标签/犹豫信号/情绪基线
       ——确定性公式, LLM 禁入)
    ③ 外部信号总线(节日日历种子+40号 P7 雷达
       L1/L2 事件只读消费——ref 幂等去重)

铁律(规划 §九):
    - 72号永不写 40号/traffic/promotion/
      attract v1.0 表(叠加铁律——只读消费)
    - LLM 禁入判定链(画像分类/意图快照/
      信号权重=确定性查表公式)
    - 感知层为观测面——不受 ATTRACT72_MODE
      影响(off 档常开)
    - 指纹脱敏(不含 PII)

异常约定(71号口径):
    KeyError → 404(画像/点击/快照不存在)
    ValueError → 409(参数非法)
"""

import hashlib
import logging
from datetime import date, timedelta

from core.helpers import ts

from repositories.attract72_repository import (
    Attract72Repository,
)
from services.attract72_registry import (
    CONNOISSEUR_ORDER_AMOUNT,
    CONFIDENCE_FULL_SAMPLES,
    DWELL_BOUNCE_SECONDS, DWELL_HIGH_SECONDS,
    EMOTION_NEGATIVE_WORDS,
    EMOTION_POSITIVE_WORDS,
    FESTIVAL_CALENDAR,
    FESTIVAL_LOOKAHEAD_DAYS,
    FINGERPRINT_LENGTH,
    HESITATION_WORDS,
    INTENT_WORDS,
    MODEL_VERSION, PERSONA_HISTORY_WINDOW,
    PERSONA_MIN_SAMPLES,
    PERSONA_TYPES,
    RADAR_CATEGORY_CHANNELS,
    RADAR_CONSUME_GRADES,
    RADAR_INGEST_LIMIT,
    RADAR_MIN_VALUE,
    SCENE_WORDS,
    SHARER_CONVERSION_LINE,
    SUBJECT_TYPES,
    TRUST_BASE, TRUST_CEIL,
    TRUST_CONVERSION_CAP,
    TRUST_CONVERSION_WEIGHT,
    TRUST_ORDER_RATE_CAP,
    TRUST_ORDER_WEIGHT,
    _attract_channel_seeds,
    current_mode, is_kill,
    tier_for_followers, tier_for_members,
)

logger = logging.getLogger("attract72_p1_service")


def _safe_div(n: float, d: float) -> float:
    """确定性安全除法(d<=0 → 0)"""
    return round(n / d, 4) if d and d > 0 else 0.0


class Attract72P1Service:
    """72号 P1 感知跃迁(画像/意图/信号)"""

    def __init__(self):
        self.repo = Attract72Repository()

    # ============================================================
    # ① 渠道人格画像(观测面)
    # ============================================================

    async def sync_personas(self, today: str = "") -> dict:
        """感知面同步: 信号摄取 + 博主/会员画像生成

        - 博主: traffic 只读(list_influencers
          +平台账号粉丝数)
        - 会员: promotion 只读(active 码 owner)
        - 统计: attract v1.0 归因表只读
          (点击/注册/下单/GMV 四数)
        - 幂等: 同主体 upsert, 变更留痕入 history

        Returns:
            {modelVersion, mode, synced, created,
             updated, signalsIngested, personas}
        """
        signals = await self.ingest_signals(
            today=today or None)
        created, updated = 0, 0

        from repositories.attract_repository import (
            AttractRepository,
        )
        attract_repo = AttractRepository()
        # 点击流一次取全(含未注册匿名点击——
        # 真实样本口径, newcomer 判定依据)
        all_clicks = await attract_repo.list_clicks(
            limit=10000)

        # —— 博主画像(traffic 只读消费) ——
        from repositories.traffic_repository import (
            TrafficRepository,
        )
        traffic = TrafficRepository()
        for inf in await traffic.list_influencers(
                limit=1000):
            inf_id = inf.get("id")
            if inf_id is None:
                continue
            platforms = await traffic \
                .list_influencer_platforms(inf_id)
            follower = max(
                (p.get("followerCount") or 0
                 for p in platforms), default=0)
            verified = any(
                bool(p.get("verified"))
                for p in platforms)
            platform_names = sorted({
                p.get("platform") for p in platforms
                if p.get("platform")})
            clicks = [c for c in all_clicks
                      if c.get("influencerId") == inf_id]
            attrs = await attract_repo \
                .list_attributions(
                    influencer_id=inf_id, limit=5000)
            is_new = await self._upsert_persona(
                subject_type="influencer",
                subject_id=inf_id,
                name=inf.get("name", ""),
                platforms=platform_names,
                follower_count=int(follower or 0),
                verified=verified, clicks=clicks,
                attrs=attrs)
            created += 1 if is_new else 0
            updated += 0 if is_new else 1

        # —— 会员画像(promotion 只读消费) ——
        from repositories.promotion_repository import (
            PromotionRepository,
        )
        promotion = PromotionRepository()
        owner_ids = sorted({
            int(c.get("ownerMemberId"))
            for c in await promotion.list_codes(
                status="active", limit=1000)
            if c.get("ownerMemberId") is not None})
        for member_id in owner_ids:
            clicks = [c for c in all_clicks
                      if c.get("promoterId") == member_id]
            attrs = await attract_repo \
                .list_attributions(
                    promoter_id=member_id, limit=5000)
            is_new = await self._upsert_persona(
                subject_type="member",
                subject_id=member_id,
                name=f"会员{member_id}",
                platforms=[],
                follower_count=0, verified=False,
                clicks=clicks, attrs=attrs)
            created += 1 if is_new else 0
            updated += 0 if is_new else 1

        personas = await self.repo.list_personas()
        return {
            "modelVersion": MODEL_VERSION,
            "mode": current_mode(),
            "kill": is_kill(),
            "synced": len(personas),
            "created": created,
            "updated": updated,
            "signalsIngested":
                signals.get("ingested", 0),
            "radar": signals.get("radar", 0),
            "festival": signals.get("festival", 0),
            "personas": [
                self._persona_summary(p)
                for p in personas[:20]],
        }

    async def _upsert_persona(
            self, subject_type: str, subject_id: int,
            name: str, platforms: list,
            follower_count: int, verified: bool,
            clicks: list, attrs: list) -> bool:
        """画像 upsert(确定性分类+变更留痕)

        - clicks: 归属点击流(含未注册——样本口径)
        - attrs: 归因表(注册/下单/GMV 口径)

        Returns:
            True=新建 / False=更新
        """
        if subject_type not in SUBJECT_TYPES:
            raise ValueError(
                f"主体类型无效({subject_type})")
        click_count = len(clicks)
        registered = sum(
            1 for a in attrs if a.get("registeredAt"))
        orders = sum(
            1 for a in attrs if a.get("orderId"))
        gmv = round(sum(
            float(a.get("orderAmount") or 0)
            for a in attrs), 2)
        avg_amount = _safe_div(gmv, orders)

        engagement = _safe_div(registered, click_count)
        conversion = _safe_div(orders, click_count)
        order_rate = _safe_div(orders, registered)

        # 人格分类(确定性优先级: 品鉴>分享>
        # 优惠敏感; 样本不足→新晋)
        if click_count < PERSONA_MIN_SAMPLES:
            persona_type = "newcomer"
        elif (avg_amount >= CONNOISSEUR_ORDER_AMOUNT
                and orders >= 1):
            persona_type = "connoisseur"
        elif conversion >= SHARER_CONVERSION_LINE:
            persona_type = "sharer"
        else:
            persona_type = "bargain_hunter"

        # 层级(博主按粉丝/会员按注册数)
        tier = (tier_for_followers(follower_count)
                if subject_type == "influencer"
                else tier_for_members(registered))

        # 信任分(确定性公式, 上限 100)
        trust = min(TRUST_CEIL, round(
            TRUST_BASE
            + TRUST_ORDER_WEIGHT * min(
                1.0, order_rate / TRUST_ORDER_RATE_CAP)
            + TRUST_CONVERSION_WEIGHT * min(
                1.0,
                conversion / TRUST_CONVERSION_CAP)))

        # 置信度(样本量驱动)
        confidence = min(
            1.0, _safe_div(click_count,
                           CONFIDENCE_FULL_SAMPLES))

        existing = await self.repo \
            .find_persona_by_subject(
                subject_type, subject_id)
        stats = {
            "clickCount": click_count,
            "registeredCount": registered,
            "orderCount": orders,
            "gmv": gmv,
            "avgOrderAmount": avg_amount,
        }
        if existing is None:
            persona_id = await self.repo.next_id(
                "persona")
            record = {
                "personaId": persona_id,
                "subjectType": subject_type,
                "subjectId": subject_id,
                "name": name,
                "personaType": persona_type,
                "platforms": platforms,
                "followerTier": tier,
                "followerCount": follower_count,
                "verified": verified,
                "engagementRate": engagement,
                "conversionRate": conversion,
                "orderRate": order_rate,
                "trustScore": trust,
                "confidence": confidence,
                "stats": stats,
                "history": [],
                "createdAt": ts(),
                "updatedAt": ts(),
            }
            await self.repo.save_persona(record)
            return True

        # 变更留痕(人格/层级变化 → history)
        changed = (existing.get("personaType")
                   != persona_type
                   or existing.get("followerTier")
                   != tier)
        history = list(existing.get("history") or [])
        if changed:
            history.append({
                "personaType":
                    existing.get("personaType"),
                "followerTier":
                    existing.get("followerTier"),
                "confidence":
                    existing.get("confidence", 0.0),
                "at": existing.get("updatedAt", ""),
            })
            history = history[
                -PERSONA_HISTORY_WINDOW:]
        existing.update({
            "personaType": persona_type,
            "platforms": platforms,
            "followerTier": tier,
            "followerCount": follower_count,
            "verified": verified,
            "engagementRate": engagement,
            "conversionRate": conversion,
            "orderRate": order_rate,
            "trustScore": trust,
            "confidence": confidence,
            "stats": stats,
            "history": history,
            "updatedAt": ts(),
        })
        await self.repo.save_persona(existing)
        return False

    @staticmethod
    def _persona_summary(p: dict) -> dict:
        return {
            "personaId": p.get("personaId"),
            "subjectType": p.get("subjectType"),
            "subjectId": p.get("subjectId"),
            "name": p.get("name", ""),
            "personaType": p.get("personaType"),
            "followerTier": p.get("followerTier"),
            "trustScore": p.get("trustScore", 0),
            "confidence": p.get("confidence", 0.0),
            "conversionRate":
                p.get("conversionRate", 0.0),
        }

    async def list_personas(self, subject_type: str = None,
                            persona_type: str = None,
                            limit: int = 100) -> list[dict]:
        """画像列表(观测面)

        Raises:
            ValueError: 筛选域外
        """
        if subject_type and subject_type \
                not in SUBJECT_TYPES:
            raise ValueError(
                f"主体类型无效({subject_type})")
        if persona_type and persona_type \
                not in PERSONA_TYPES:
            raise ValueError(
                f"人格类型无效({persona_type})")
        return await self.repo.list_personas(
            subject_type=subject_type,
            persona_type=persona_type,
            limit=limit)

    async def get_persona(self, persona_id: int) -> dict:
        """画像详情(含 stats/history)

        Raises:
            KeyError: 画像不存在
        """
        persona = await self.repo.get_persona(
            persona_id)
        if persona is None:
            raise KeyError(
                f"画像不存在(personaId={persona_id})")
        return persona

    # ============================================================
    # ② 意图快照引擎(观测面——快环, 词表密度)
    # ============================================================

    async def enrich_click_intent(
            self, click_id: int,
            device_fingerprint: str = "",
            user_agent: str = "",
            dwell_seconds: float = 0.0,
            text: str = "") -> dict:
        """点击补意图快照(词表密度确定性解析)

        - text: 行为侧文本素材(评论/搜索词/
          咨询内容——可选)
        - dwell_seconds: 落地页停留秒数
        - 指纹: 显式传入或 UA 哈希脱敏
        - clickId 唯一 upsert(重复 enrich 以
          最新上下文覆盖)

        Raises:
            KeyError: 点击不存在(attract v1.0)
        """
        from repositories.attract_repository import (
            AttractRepository,
        )
        click = await AttractRepository() \
            .get_click(click_id)
        if click is None:
            raise KeyError(
                f"点击不存在(clickId={click_id})")

        raw = text or ""
        pos = sum(1 for w in
                  EMOTION_POSITIVE_WORDS
                  if w in raw)
        neg = sum(1 for w in
                  EMOTION_NEGATIVE_WORDS
                  if w in raw)
        # 情绪基线: (正-负)/总词频, 无词→0(中性)
        emotion = _safe_div(pos - neg,
                            pos + neg)

        scene_tags = sorted(
            tag for tag, words
            in SCENE_WORDS.items()
            if any(w in raw for w in words))
        hesitation = sorted(
            sig for sig, words
            in HESITATION_WORDS.items()
            if any(w in raw for w in words))
        intent_tags = sorted(
            tag for tag, words
            in INTENT_WORDS.items()
            if any(w in raw for w in words))
        # 停留时长信号(确定性阈值)
        if dwell_seconds >= DWELL_HIGH_SECONDS:
            intent_tags.append("high_engagement")
        elif 0 < dwell_seconds < DWELL_BOUNCE_SECONDS:
            intent_tags.append("bounce_risk")

        # 指纹脱敏(显式优先, 否则 UA 哈希)
        fingerprint = (device_fingerprint or
                      hashlib.sha256(
                          (user_agent or "")
                          .encode("utf-8",
                                  "replace"))
                      .hexdigest()
                      [:FINGERPRINT_LENGTH])

        record = {
            "snapshotId": click_id,  # click 唯一
            "clickId": click_id,
            "deviceFingerprint": fingerprint,
            "intentTags": sorted(set(intent_tags)),
            "sceneTags": scene_tags,
            "hesitationSignals": hesitation,
            "emotionBaseline": emotion,
            "dwellSeconds": round(
                float(dwell_seconds or 0), 2),
            "textLen": len(raw),
            "channel": click.get("channel", ""),
            "code": click.get("code", ""),
            "at": ts(),
        }
        await self.repo.save_snapshot(record)
        return record

    async def get_intent(self, click_id: int) -> dict:
        """意图快照查询

        Raises:
            KeyError: 快照不存在
        """
        snapshot = await self.repo \
            .get_snapshot_by_click(click_id)
        if snapshot is None:
            raise KeyError(
                f"意图快照不存在(clickId={click_id})")
        return snapshot

    # ============================================================
    # ③ 外部信号总线(观测面——ref 幂等)
    # ============================================================

    async def ingest_signals(
            self, today: str = None) -> dict:
        """信号摄取(节日日历+雷达 L1/L2 只读消费)

        - 节日: 前瞻窗口内入流(确定性日历)
        - 雷达: 40号 P7 事件 grade∈{L1,L2} 且
          valueScore≥下限 → 信号(ref=radar:
          {eventId} 幂等)
        - 只读铁律: 永不修改 40号雷达数据

        Returns:
            {ingested, radar, festival}
        """
        from repositories.radar_repository import (
            RadarRepository,
        )
        radar_repo = RadarRepository()

        radar_count = 0
        for grade in RADAR_CONSUME_GRADES:
            for ev in await radar_repo.list_events(
                    grade=grade,
                    limit=RADAR_INGEST_LIMIT):
                value = float(
                    ev.get("valueScore") or 0)
                if value < RADAR_MIN_VALUE:
                    continue
                ref = f"radar:{ev.get('eventId')}"
                if await self.repo \
                        .find_signal_by_ref(ref):
                    continue
                category = ev.get("category", "")
                payload = {
                    "eventId": ev.get("eventId"),
                    "title": ev.get("title", ""),
                    "category": category,
                    "platform":
                        ev.get("platform", ""),
                    "grade": ev.get("grade", ""),
                    "valueScore": value,
                    "heatBase":
                        ev.get("heatBase", 0),
                }
                await self._save_signal(
                    signal_type="radar_event",
                    ref=ref, payload=payload,
                    impact_channels=list(
                        RADAR_CATEGORY_CHANNELS.get(
                            category,
                            ("douyin",))),
                    weight=round(value / 100, 4))
                radar_count += 1

        # 节日日历(确定性窗口)
        today_d = self._parse_today(today)
        window_end = today_d + timedelta(
            days=FESTIVAL_LOOKAHEAD_DAYS)
        festival_count = 0
        for (name, month, day, label,
             weight) in FESTIVAL_CALENDAR:
            try:
                fdate = date(
                    today_d.year, month, day)
            except ValueError:
                continue
            if not (today_d <= fdate <= window_end):
                continue
            fdate_iso = fdate.isoformat()
            ref = f"festival:{name}:{fdate_iso}"
            if await self.repo \
                    .find_signal_by_ref(ref):
                continue
            payload = {
                "name": name, "label": label,
                "date": fdate_iso,
                "daysAhead":
                    (fdate - today_d).days,
            }
            await self._save_signal(
                signal_type="festival", ref=ref,
                payload=payload,
                impact_channels=list(
                    _attract_channel_seeds()),
                weight=weight)
            festival_count += 1

        return {
            "ingested": radar_count + festival_count,
            "radar": radar_count,
            "festival": festival_count,
        }

    async def _save_signal(self, signal_type: str,
                          ref: str, payload: dict,
                          impact_channels: list,
                          weight: float) -> dict:
        signal_id = await self.repo.next_id(
            "signal")
        record = {
            "signalId": signal_id,
            "type": signal_type,
            "ref": ref,
            "payload": payload,
            "impactChannels": impact_channels,
            "weight": weight,
            "consumed": False,
            "createdAt": ts(),
        }
        await self.repo.save_signal(record)
        return record

    @staticmethod
    def _parse_today(today: str | None) -> date:
        """今日解析(空→系统当前; 非法→409)"""
        if not today:
            return date.today()
        try:
            return date.fromisoformat(today)
        except ValueError as exc:
            raise ValueError(
                f"日期格式非法(须 YYYY-MM-DD): "
                f"{today}") from exc

    async def list_signals(self, signal_type: str = None,
                           limit: int = 100) -> list[dict]:
        """信号流列表(观测面)

        Raises:
            ValueError: 类型域外
        """
        from services.attract72_registry import (
            SIGNAL_TYPES,
        )
        if signal_type and signal_type \
                not in SIGNAL_TYPES:
            raise ValueError(
                f"信号类型无效({signal_type})")
        return await self.repo.list_signals(
            signal_type=signal_type, limit=limit)
