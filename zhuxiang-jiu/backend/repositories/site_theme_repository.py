"""网站图标智能管理模块数据访问层(双模式: 内存 + Redis)

表清单:
    theme_themes       主题方案(配色组 colors + 图标组 icons, draft/active/archived)
    theme_icons        图标资源库(emoji/url, tab/grid 分类)
    theme_change_logs  变更审计(before/after 快照, 支持回滚)

设计对齐:
    - 双模式存储: is_redis_mode() 切换内存字典/Redis Hash
    - 序列号: 内存计数器 / Redis INCR
"""

import json
from datetime import datetime, UTC

from repositories.backend import is_redis_mode, get_redis_client, get_in_memory_store, _k


# 默认激活主题(竹绿经典, 与 taro-app theme.scss 对齐)
DEFAULT_THEME = {
    "themeId": 1,
    "name": "竹绿经典",
    "description": "竹香酒品牌默认配色, 竹韵雅致",
    "colors": {
        "primary": "#355c44",         # 主色(竹绿)
        "primaryLight": "#4a7c59",    # 主色浅
        "navBar": "#355c44",          # 导航栏背景
        "tabSelected": "#355c44",     # tabBar 选中色
        "tabColor": "#999999",        # tabBar 未选中色
        "tabBg": "#ffffff",           # tabBar 背景
        "textOnPrimary": "#ffffff",   # 主色上文本
    },
    "icons": {
        "tabHome": "home",
        "tabProducts": "products",
        "tabMine": "mine",
        "quickGrid": {},              # 金刚区 key→emoji 覆盖(空=用默认)
    },
    "aiScoreLatest": 95,
    "status": "active",
    "createdAdminId": 0,
    "activatedAt": "",
}

# 预设主题模板(AI 季节推荐候选池)
PRESET_THEMES = [
    {"name": "竹绿经典", "season": "all",
     "colors": DEFAULT_THEME["colors"], "brandFit": 1.0},
    {"name": "新春红金", "season": "spring_festival",
     "colors": {"primary": "#b03a2e", "primaryLight": "#d35f52",
                "navBar": "#b03a2e", "tabSelected": "#b03a2e",
                "tabColor": "#999999", "tabBg": "#ffffff",
                "textOnPrimary": "#ffffff"},
     "brandFit": 0.4},
    {"name": "中秋金棕", "season": "autumn_festival",
     "colors": {"primary": "#8c6a3f", "primaryLight": "#b08d5f",
                "navBar": "#8c6a3f", "tabSelected": "#8c6a3f",
                "tabColor": "#999999", "tabBg": "#ffffff",
                "textOnPrimary": "#ffffff"},
     "brandFit": 0.5},
    {"name": "夏日竹青", "season": "summer",
     "colors": {"primary": "#2e7d6b", "primaryLight": "#4a9c88",
                "navBar": "#2e7d6b", "tabSelected": "#2e7d6b",
                "tabColor": "#999999", "tabBg": "#ffffff",
                "textOnPrimary": "#ffffff"},
     "brandFit": 0.9},
    {"name": "国庆中国红", "season": "national_day",
     "colors": {"primary": "#a93226", "primaryLight": "#cd5c5c",
                "navBar": "#a93226", "tabSelected": "#a93226",
                "tabColor": "#999999", "tabBg": "#ffffff",
                "textOnPrimary": "#ffffff"},
     "brandFit": 0.4},
]

# 图标资源库种子(grid 分类: 金刚区可选 emoji, 主题编辑器拉取展示)
_SEED_GRID_ICONS = [
    "✅", "📅", "🎯", "⭐", "🖊️", "🏆", "🎖️", "💎",
    "🛒", "🛍️", "🎁", "🧺", "🏷️", "🎉", "🎊", "🎈",
    "💰", "💵", "🪙", "💳", "📈", "📊", "🧧", "🏦",
    "🤝", "📱", "🔗", "👥", "📢", "📣", "💌", "🤲",
    "✋", "💪", "🖐️", "🫱", "👑", "🏅", "📜", "🗺️",
    "📦", "📋", "🧾", "🚚", "📮", "🎧", "💬", "📞",
    "🛎️", "🙋", "🍶", "🍇", "🌾", "🌿", "🍵", "🏮",
]

_INT_FIELDS = ("themeId", "createdAdminId", "logId", "iconId", "aiScoreLatest")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class SiteThemeRepository:
    """网站图标智能管理模块数据访问层"""

    def __init__(self, store: dict = None):
        self.store = store if store is not None else get_in_memory_store()

    # ============================================================
    # 存储初始化 / 序列号
    # ============================================================

    def _ensure_store(self):
        for key in ("theme_themes", "theme_icons", "theme_change_logs"):
            self.store.setdefault(key, {})
        # 首次初始化默认主题(竹绿经典 active)
        if not self.store["theme_themes"]:
            self.store["theme_themes"][1] = dict(DEFAULT_THEME)
            self.store["_theme_seq"] = 1
        # 首次初始化图标资源库(grid 分类, 供主题编辑器选择)
        if not self.store["theme_icons"]:
            for idx, emoji in enumerate(_SEED_GRID_ICONS, start=1):
                self.store["theme_icons"][idx] = {
                    "iconId": idx, "name": f"grid_{idx}",
                    "emoji": emoji, "category": "grid",
                }
            self.store["_theme_icon_seq"] = len(_SEED_GRID_ICONS)

    async def next_theme_id(self) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(_k("theme", "theme", "seq"))
        self._ensure_store()
        seq = self.store.get("_theme_seq", 1) + 1
        self.store["_theme_seq"] = seq
        return seq

    async def next_log_id(self) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(_k("theme", "log", "seq"))
        self._ensure_store()
        seq = self.store.get("_theme_log_seq", 0) + 1
        self.store["_theme_log_seq"] = seq
        return seq

    async def next_icon_id(self) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(_k("theme", "icon", "seq"))
        self._ensure_store()
        seq = self.store.get("_theme_icon_seq", 0) + 1
        self.store["_theme_icon_seq"] = seq
        return seq

    # ============================================================
    # 序列化辅助(Redis 模式)
    # ============================================================

    @staticmethod
    def _serialize(record: dict) -> dict:
        out = {}
        for k, v in record.items():
            if isinstance(v, (dict, list)):
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
            elif isinstance(v, str) and v.startswith(("{", "[")):
                try:
                    record[k] = json.loads(v)
                except ValueError:
                    record[k] = v
            else:
                record[k] = v
        return record

    # ============================================================
    # 主题方案 CRUD
    # ============================================================

    async def save_theme(self, theme: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(_k("theme", "themes", theme["themeId"]),
                              mapping=self._serialize(theme))
            return theme
        self._ensure_store()
        self.store["theme_themes"][theme["themeId"]] = theme
        return theme

    async def get_theme(self, theme_id: int) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(_k("theme", "themes", theme_id))
            return self._deserialize(data) if data else None
        self._ensure_store()
        return self.store["theme_themes"].get(theme_id)

    async def list_themes(self, status: str = None,
                          limit: int = 100) -> list[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k("theme", "themes", "*"))
            result = []
            for key in keys:
                data = await client.hgetall(key)
                theme = self._deserialize(data)
                if status and theme.get("status") != status:
                    continue
                result.append(theme)
            return sorted(result, key=lambda x: x.get("themeId", 0))[:limit]
        self._ensure_store()
        result = [t for t in self.store["theme_themes"].values()
                  if (not status or t.get("status") == status)]
        return sorted(result, key=lambda x: x.get("themeId", 0))[:limit]

    async def get_active_theme(self) -> dict | None:
        themes = await self.list_themes(status="active")
        return themes[0] if themes else None

    async def update_theme(self, theme_id: int, fields: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(_k("theme", "themes", theme_id),
                              mapping=self._serialize(fields))
            return await self.get_theme(theme_id)
        self._ensure_store()
        theme = self.store["theme_themes"].get(theme_id)
        if not theme:
            raise KeyError(theme_id)
        theme.update(fields)
        return theme

    # ============================================================
    # 变更审计 CRUD
    # ============================================================

    async def save_log(self, log: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(_k("theme", "logs", log["logId"]),
                              mapping=self._serialize(log))
            return log
        self._ensure_store()
        self.store["theme_change_logs"][log["logId"]] = log
        return log

    async def get_log(self, log_id: int) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(_k("theme", "logs", log_id))
            return self._deserialize(data) if data else None
        self._ensure_store()
        return self.store["theme_change_logs"].get(log_id)

    async def list_logs(self, theme_id: int = None,
                        limit: int = 50) -> list[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k("theme", "logs", "*"))
            result = []
            for key in keys:
                data = await client.hgetall(key)
                log = self._deserialize(data)
                if theme_id and log.get("themeId") != theme_id:
                    continue
                result.append(log)
            return sorted(result, key=lambda x: x.get("logId", 0),
                          reverse=True)[:limit]
        self._ensure_store()
        result = [l for l in self.store["theme_change_logs"].values()
                  if (not theme_id or l.get("themeId") == theme_id)]
        return sorted(result, key=lambda x: x.get("logId", 0),
                      reverse=True)[:limit]

    # ============================================================
    # 图标资源库 CRUD
    # ============================================================

    async def save_icon(self, icon: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(_k("theme", "icons", icon["iconId"]),
                              mapping=self._serialize(icon))
            return icon
        self._ensure_store()
        self.store["theme_icons"][icon["iconId"]] = icon
        return icon

    async def list_icons(self, category: str = None,
                         limit: int = 200) -> list[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k("theme", "icons", "*"))
            result = []
            for key in keys:
                data = await client.hgetall(key)
                icon = self._deserialize(data)
                if category and icon.get("category") != category:
                    continue
                result.append(icon)
            return sorted(result, key=lambda x: x.get("iconId", 0))[:limit]
        self._ensure_store()
        result = [i for i in self.store["theme_icons"].values()
                  if (not category or i.get("category") == category)]
        return sorted(result, key=lambda x: x.get("iconId", 0))[:limit]
