"""76号·短剧引流智能模型 DP0（剧集编排层, 2026-10-05）

《短剧引流智能模型_创新规划优化设计方案》DP0：把 sv73 现有单集
独白内容组织成「剧集」——ep1-N 外部平台引流钩子（免费）+ 卡点集
（站内注册解锁, DP1 接通）+ 会员集占位。

口径:
    - 集 = 既有 contentId(一条 sv73 成片), 编排层零改生产链
    - 存储: PromoRepository 通用表 drama76_series(Redis 权威,
      rebuild 安全——storyboards 事故教训)
    - video_url 为 dist 相对路径, 文件由 drama76_build.py 上传
    - 解锁状态 DP0 静态: 免费集 unlocked / 卡点集 locked
      (unlock_methods 提示位, DP1 接真解锁)
"""

import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class DramaStatus(str, Enum):
    DRAFT = "draft"
    ONLINE = "online"
    OFFLINE = "offline"


class EpisodeRef(BaseModel):
    episode_id: str = Field(..., description="= contentId 复用")
    episode_number: int
    title: str = ""
    video_url: str = ""          # /videos/drama/xxx.mp4 (dist 相对)
    duration_seconds: int = 0
    cliff_note: str = ""         # 本集钩子(上集悬念→本集)


class DramaSeries(BaseModel):
    drama_id: str
    title: str
    description: str = ""
    persona: str = "zhuxiaomei"
    cover_url: str = ""
    tags: list[str] = []
    free_episodes: int = 2       # 外部平台完整免费集数
    episodes: list[EpisodeRef] = []
    status: DramaStatus = DramaStatus.DRAFT
    created_at: str = ""
    updated_at: str = ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class UserUnlock(BaseModel):
    """注册解锁(DP1): 剧级——一次解锁本剧全部后段(「注册领后段」口径)"""
    unlock_id: str
    user_id: str
    drama_id: str
    episode_id: str           # 触发解锁的卡点集
    method: str = "register"  # register | invite
    src: str = ""             # rLink 短码归因(增长漏斗)
    created_at: str = ""


class SuperMember(BaseModel):
    """超级会员(DP2): 全站短剧永久免费; source=admin 手动开通/
    purchase 支付(DP2b 接 pay60)/invite(DP3); expire_at 空=永久"""
    user_id: str
    source: str = "admin"
    expire_at: str = ""       # ISO; 空=永久
    created_at: str = ""


SUPER_PLANS = [
    {"plan_id": "super_forever", "name": "超级会员·永久卡",
     "duration_days": 0, "price": 199.0, "currency": "CNY",
     "benefits": ["全站短剧永久免费", "新剧抢先看"]},
    {"plan_id": "super_month", "name": "超级会员·月卡",
     "duration_days": 30, "price": 29.9, "currency": "CNY",
     "benefits": ["全站短剧免费 30 天"]},
]


class DramaSeriesService:
    """剧集注册表(Redis 通用表) + 分集解锁状态计算"""

    TABLE = "drama76_series"
    UNLOCK_TABLE = "drama76_unlocks"

    def __init__(self, repo=None) -> None:
        if repo is None:
            from repositories.promo_repository import PromoRepository
            repo = PromoRepository()
        self.repo = repo

    def _ensure_table(self) -> None:
        """内存模式新表初始化(Redis 模式无此需要)"""
        store = getattr(self.repo, "store", None)
        if isinstance(store, dict):
            store.setdefault(self.TABLE, {})

    async def _list_all(self) -> list[dict]:
        self._ensure_table()
        rows = await self.repo._list(self.TABLE, 200)
        return [r for r in rows if isinstance(r, dict)]

    async def create(self, body: dict) -> DramaSeries:
        """组剧(admin): episodes 按序组装, 编号自动生成"""
        eps = []
        for i, e in enumerate(body.get("episodes") or [], 1):
            eps.append(EpisodeRef(
                episode_id=str(e.get("episode_id") or ""),
                episode_number=i,
                title=str(e.get("title") or f"第{i}集"),
                video_url=str(e.get("video_url") or ""),
                duration_seconds=int(e.get("duration_seconds") or 0),
                cliff_note=str(e.get("cliff_note") or "")))
        if not eps:
            raise ValueError("episodes 为空")
        drama = DramaSeries(
            drama_id=str(body.get("drama_id")
                         or f"drama_{int(datetime.now().timestamp()):x}"),
            title=str(body.get("title") or "未命名短剧"),
            description=str(body.get("description") or ""),
            persona=str(body.get("persona") or "zhuxiaomei"),
            cover_url=str(body.get("cover_url") or ""),
            tags=list(body.get("tags") or []),
            free_episodes=max(0, int(body.get("free_episodes") or 2)),
            episodes=eps,
            status=DramaStatus(
                str(body.get("status") or "online")),
            created_at=_now(), updated_at=_now())
        if drama.free_episodes >= len(eps):
            raise ValueError("free_episodes 必须小于总集数(至少一集卡点)")
        self._ensure_table()
        await self.repo._save(self.TABLE, drama.drama_id,
                              drama.model_dump())
        logger.info("drama76_series_created id=%s eps=%d free=%d",
                    drama.drama_id, len(eps), drama.free_episodes)
        return drama

    async def list_online(self) -> list[dict]:
        rows = await self._list_all()
        return [r for r in rows if r.get("status") == "online"]

    async def get(self, drama_id: str) -> Optional[dict]:
        self._ensure_table()
        return await self.repo._get(self.TABLE, drama_id)

    def episode_states(self, drama: dict, is_member: bool = False,
                       unlocked_dramas: set | None = None
                       ) -> list[dict[str, Any]]:
        """分集+解锁状态:
        编号<=free → free; 会员 → member_only; 用户已解锁本剧
        (unlocked_dramas 含 drama_id) → 全集 unlocked; 其余 locked"""
        free_n = int(drama.get("free_episodes") or 0)
        drama_unlocked = bool(unlocked_dramas
                              and drama.get("drama_id") in unlocked_dramas)
        out = []
        for e in drama.get("episodes") or []:
            n = int(e.get("episode_number") or 0)
            if n <= free_n:
                status, unlocked = "free", True
            elif drama_unlocked or is_member:
                status, unlocked = ("member_only" if is_member and
                                    not drama_unlocked else "unlocked"), True
            else:
                status, unlocked = "locked", False
            out.append({
                "episode_id": e.get("episode_id"),
                "episode_number": n,
                "title": e.get("title"),
                "video_url": (e.get("video_url") if unlocked else ""),
                "duration_seconds": e.get("duration_seconds"),
                "status": status,
                "is_unlocked": unlocked,
                "is_checkpoint": (n == free_n + 1),
                "unlock_methods": (["register", "invite", "member"]
                                   if not unlocked else []),
            })
        return out

    # ---------- DP1: 注册解锁 ----------

    async def unlock(self, user_id: str, episode_id: str,
                     method: str = "register", src: str = ""
                     ) -> tuple[dict, dict]:
        """解锁本剧后段(剧级): 幂等(同用户同剧返回既有记录);
        Returns: (drama, unlock_record); KeyError=集不存在"""
        self._ensure_table()
        dramas = await self._list_all()
        drama = next((d for d in dramas
                      if any(e.get("episode_id") == episode_id
                             for e in d.get("episodes") or [])), None)
        if not drama:
            raise KeyError(episode_id)
        free_n = int(drama.get("free_episodes") or 0)
        ep = next(e for e in drama["episodes"]
                  if e.get("episode_id") == episode_id)
        if int(ep.get("episode_number") or 0) <= free_n:
            raise ValueError("该集免费无需解锁")
        # 幂等: 同用户同剧已解锁 → 返回既有
        existing = await self._find_unlock(user_id, drama["drama_id"])
        if existing:
            return drama, existing
        rec = UserUnlock(
            unlock_id=f"ul_{user_id}_{drama['drama_id']}",
            user_id=user_id, drama_id=drama["drama_id"],
            episode_id=episode_id,
            method=method if method in ("register", "invite")
            else "register",
            src=src[:32], created_at=_now()).model_dump()
        store = getattr(self.repo, "store", None)
        if isinstance(store, dict):
            store.setdefault(self.UNLOCK_TABLE, {})
        await self.repo._save(self.UNLOCK_TABLE, rec["unlock_id"], rec)
        logger.info("drama76_unlocked user=%s drama=%s method=%s src=%s",
                    user_id, drama["drama_id"], method, src)
        return drama, rec

    async def _find_unlock(self, user_id: str,
                           drama_id: str) -> Optional[dict]:
        store = getattr(self.repo, "store", None)
        if isinstance(store, dict):
            store.setdefault(self.UNLOCK_TABLE, {})
        rows = await self.repo._list(self.UNLOCK_TABLE, 500)
        for r in rows:
            if (r.get("user_id") == user_id
                    and r.get("drama_id") == drama_id):
                return r
        return None

    async def unlocked_dramas_of(self, user_id: str) -> set:
        store = getattr(self.repo, "store", None)
        if isinstance(store, dict):
            store.setdefault(self.UNLOCK_TABLE, {})
        rows = await self.repo._list(self.UNLOCK_TABLE, 500)
        return {r["drama_id"] for r in rows
                if r.get("user_id") == user_id}

    # ---------- DP2: 超级会员 ----------

    SUPER_TABLE = "drama76_super"

    async def grant_super(self, user_id: str, days: int = 0,
                          source: str = "admin") -> dict:
        """开通超级会员(days=0 永久); 幂等覆盖"""
        from datetime import timedelta
        expire = ""
        if days and days > 0:
            expire = (datetime.now(timezone.utc)
                      + timedelta(days=days)).isoformat()
        rec = SuperMember(user_id=str(user_id), source=source,
                          expire_at=expire, created_at=_now()).model_dump()
        store = getattr(self.repo, "store", None)
        if isinstance(store, dict):
            store.setdefault(self.SUPER_TABLE, {})
        await self.repo._save(self.SUPER_TABLE, str(user_id), rec)
        logger.info("drama76_super_granted user=%s days=%s source=%s",
                    user_id, days or "forever", source)
        return rec

    async def revoke_super(self, user_id: str) -> bool:
        store = getattr(self.repo, "store", None)
        if isinstance(store, dict):
            store.setdefault(self.SUPER_TABLE, {})
        rec = await self.repo._get(self.SUPER_TABLE, str(user_id))
        if not rec:
            return False
        if isinstance(store, dict):
            store.get(self.SUPER_TABLE, {}).pop(str(user_id), None)
        return True

    async def is_super(self, user_id: str) -> bool:
        """过期判定: expire_at 非空且已过 → False"""
        if not user_id:
            return False
        store = getattr(self.repo, "store", None)
        if isinstance(store, dict):
            store.setdefault(self.SUPER_TABLE, {})
        rec = await self.repo._get(self.SUPER_TABLE, str(user_id))
        if not rec:
            return False
        exp = rec.get("expire_at") or ""
        if not exp:
            return True
        try:
            return datetime.fromisoformat(exp) > datetime.now(timezone.utc)
        except ValueError:
            return False

    async def user_view(self, user_id: str, drama: dict
                        ) -> list[dict[str, Any]]:
        """登录态全集视图: 会员/已解锁合并计算"""
        member = await self.is_super(user_id)
        unlocked = await self.unlocked_dramas_of(user_id)
        return self.episode_states(drama, is_member=member,
                                   unlocked_dramas=unlocked)

    # ---------- DP2b: pay60 支付下单 + 回调发放 ----------

    SUPER_PURCHASE_PREFIX = "DSUPER-"   # orderId: DSUPER-{plan_id}-{单号}

    @staticmethod
    def _plan_of(plan_id: str) -> Optional[dict]:
        return next((p for p in SUPER_PLANS
                     if p["plan_id"] == plan_id), None)

    async def create_super_purchase_pay(
            self, user_id: str, plan_id: str,
            pay_channel: str = "wechat",
            pay_method: Optional[str] = None) -> dict:
        """超级会员购买支付单(铁律: 只创建支付单不直接开通——
        开通由支付回调分发 payment_service._dispatch_business 完成)
        Raises: KeyError=套餐不存在"""
        plan = self._plan_of(plan_id)
        if not plan:
            raise KeyError(plan_id)
        from services.payment_service import (
            PaymentService, default_pay_method)
        from services.tx_utils import gen_no
        if pay_method is None:
            pay_method = default_pay_method(pay_channel)
        return await PaymentService().create_pay(
            user_id=user_id,
            # plan_id 编进 orderId, 回调分发时解析(支付单无业务扩展字段)
            order_id=f"{self.SUPER_PURCHASE_PREFIX}{plan_id}-"
                     f"{gen_no(str(user_id) + '-')}",
            order_type="drama_super",
            total_amount=float(plan["price"]),
            pay_channel=pay_channel,
            pay_method=pay_method,
            scene_type="drama_super")

    async def grant_super_by_order(self, order: dict) -> dict:
        """支付回调发放(_dispatch_business 调用): 解析 orderId 中
        plan_id → 开通/续期。月卡在既有有效到期日上累加(重复购买
        不吃亏); 永久卡覆盖。"""
        order_id = str(order.get("orderId") or "")
        plan_id = order_id.split("-", 2)[1] \
            if order_id.startswith(self.SUPER_PURCHASE_PREFIX) else ""
        plan = self._plan_of(plan_id)
        if not plan:
            raise ValueError(f"支付单业务信息缺失: {order_id}")
        days = int(plan.get("duration_days") or 0)
        if days <= 0:   # 永久卡: 覆盖
            rec = await self.grant_super(order.get("userId"),
                                         days=0, source="purchase")
            return {"action": "purchased", "plan_id": plan_id,
                    "days": 0, "expire_at": rec["expire_at"]}
        # 月卡: 有效期内累加
        from datetime import timedelta
        user_id = str(order.get("userId"))
        store = getattr(self.repo, "store", None)
        if isinstance(store, dict):
            store.setdefault(self.SUPER_TABLE, {})
        existing = await self.repo._get(self.SUPER_TABLE, user_id) or {}
        base = datetime.now(timezone.utc)
        exp = existing.get("expire_at") or ""
        if exp:
            try:
                old = datetime.fromisoformat(exp)
                if old > base:
                    base = old
            except ValueError:
                pass
        expire = (base + timedelta(days=days)).isoformat()
        rec = SuperMember(user_id=user_id, source="purchase",
                          expire_at=expire, created_at=_now()).model_dump()
        await self.repo._save(self.SUPER_TABLE, user_id, rec)
        logger.info("drama76_super_purchased user=%s plan=%s days=%d "
                    "expire=%s payNo=%s", user_id, plan_id, days, expire,
                    order.get("payNo"))
        return {"action": "purchased", "plan_id": plan_id,
                "days": days, "expire_at": expire}

    # ---------- DP4: 共鸣驱动卡点动态排序 ----------

    # 门控: 共鸣快照 ≥N 会话才开动态(N=0 恒开——测试/灰度);
    # 未达标回退 DP1 固定排序(register 优先)
    DP4_GATE_SESSIONS = int(
        __import__("os").environ.get("DRAMA76_DP4_GATE", "500"))
    STICKINESS_TOP = 0.85        # 高粘性线(设计方案 §1.4)
    NEGATIVE_TOP = 0.5           # 高犹豫线(anxiety/confusion)
    PLEASURE_TOP = 0.5           # 高意向线

    # 分组 → unlock_methods 优先序(设计方案 §1.4 表)
    GROUP_ORDER = {
        # 高犹豫: 零门槛邀请优先
        "hesitant": ["invite", "register", "member"],
        # 高意向: 注册直解, 会员紧随
        "eager": ["register", "member", "invite"],
        # 高粘性: 超级会员置顶 + 彩蛋话术
        "sticky": ["member", "register", "invite"],
        # 默认(无信号/门控未开): DP1 固定序
        "default": ["register", "invite", "member"],
    }

    @classmethod
    def dp4_group_of(cls, snapshot: dict | None) -> str:
        """共鸣快照 → 用户分组(快照缺失=无信号→default)"""
        if not snapshot:
            return "default"
        emo = snapshot.get("emotion") or {}
        intent = snapshot.get("intent") or {}
        stick = float(intent.get("stickiness_score") or 0.0)
        if stick > cls.STICKINESS_TOP:
            return "sticky"
        e = str(emo.get("emotion") or "")
        if (e in ("anxious", "confused", "frustrated")
                or float(emo.get("anxiety_score") or 0) >= cls.NEGATIVE_TOP
                or float(emo.get("confusion_score") or 0) >= cls.NEGATIVE_TOP):
            return "hesitant"
        if (e == "pleasant"
                or float(emo.get("pleasure_score") or 0) >= cls.PLEASURE_TOP):
            return "eager"
        return "default"

    def order_unlock_methods(self, methods: list[dict],
                             session_id: str = "") -> tuple[list[dict], dict]:
        """卡点解锁方式动态排序(共鸣消费)。

        门控未开(快照 < DRAMA76_DP4_GATE)或无信号 → 固定序;
        每次动态排序写影子留痕(drama76_checkpoint_shadow.jsonl,
        为 75号 testing 技能化累积对照指标)。
        Returns: (ordered_methods, meta) meta 带 group/dynamic/reason。
        """
        meta = {"group": "default", "dynamic": False,
                "reason": "", "session_id": session_id}
        group = "default"
        try:
            from services.agent75_resonance_service import ResonanceService
            rs = ResonanceService()
            total = rs.count_sessions()
            gate = self.DP4_GATE_SESSIONS
            if gate > 0 and total < gate:
                meta["reason"] = f"gate_not_met({total}/{gate})"
            else:
                snap = rs.session_snapshot(session_id)
                group = self.dp4_group_of(snap)
                meta["dynamic"] = group != "default"
                meta["reason"] = ("no_snapshot" if not snap
                                  else f"emotion={snap.get('emotion', {}).get('emotion')}"
                                  f"/stick={snap.get('intent', {}).get('stickiness_score')}")
        except Exception as exc:   # noqa: BLE001 共鸣不可用不阻断卡点
            meta["reason"] = f"resonance_unavailable: {exc}"
        meta["group"] = group
        order = self.GROUP_ORDER[group]
        by_type = {m.get("type"): dict(m) for m in methods}
        ordered = [by_type[t] for t in order if t in by_type]
        ordered += [m for m in methods
                    if m.get("type") not in order]   # 未知类型殿后
        if group == "sticky":
            for m in ordered:
                if m.get("type") == "member":
                    m["reward_hint"] = "老朋友彩蛋：现在开通立享全站永久免费"
        self._shadow_log_order(session_id, group, ordered, meta)
        return ordered, meta

    @staticmethod
    def _shadow_log_order(session_id: str, group: str,
                          ordered: list[dict], meta: dict) -> None:
        """影子留痕: 每次排序决策一行(75号技能化前的对照基线)"""
        try:
            from services.agent75_resonance_service import AGENT_TASK_DIR
            import json as _json
            AGENT_TASK_DIR.mkdir(parents=True, exist_ok=True)
            rec = {"session_id": session_id, "group": group,
                   "order": [m.get("type") for m in ordered],
                   "dynamic": meta.get("dynamic"),
                   "reason": meta.get("reason"),
                   "created_at": _now()}
            with open(AGENT_TASK_DIR / "drama76_checkpoint_shadow.jsonl",
                      "a", encoding="utf-8") as f:
                f.write(_json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:   # noqa: BLE001 留痕失败不影响主链
            pass
