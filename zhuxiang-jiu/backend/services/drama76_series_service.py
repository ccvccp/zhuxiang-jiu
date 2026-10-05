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


class DramaSeriesService:
    """剧集注册表(Redis 通用表) + 分集解锁状态计算"""

    TABLE = "drama76_series"

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

    def episode_states(self, drama: dict, is_member: bool = False
                       ) -> list[dict[str, Any]]:
        """分集+解锁状态(DP0 静态计算):
        编号<=free → free/unlocked; 会员 → member_only(超级会员可看,
        DP2 接权益); 其余 → locked(checkpoint, DP1 接解锁)"""
        free_n = int(drama.get("free_episodes") or 0)
        out = []
        for e in drama.get("episodes") or []:
            n = int(e.get("episode_number") or 0)
            if n <= free_n:
                status, unlocked = "free", True
            elif is_member:
                status, unlocked = "member_only", True
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
