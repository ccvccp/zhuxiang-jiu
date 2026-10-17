"""心境·酒签公开端点(首页 Agent AI 模块① 轻量版)

定位: 首页游客"心境→酒签"超级入口——不建聊天会话、不落会员身份,
直接走知识库 RAG(竹韵话术已入库), 输出即酒签文案。

安全口径:
- 仅此单端点在 PUBLIC_EXACT 放行(见 core/auth_middleware.py), 前缀不放宽;
- 请求体 mood 限 1..80 字, 输出仅回文本与置信, 不含内部条目结构;
- 进程内按 IP 滑动窗限流(单实例部署口径), 超限 429;
- 酒类合规: 未成年/过量相关敏感问法由知识库 compliance 条目自然接管。
"""
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from services.knowledge_service import KnowledgeService

router = APIRouter(tags=["心境酒签(首页Agent)"])

_service = KnowledgeService()

_RATE_WINDOW = 60.0   # 滑动窗(秒)
_RATE_MAX = 12        # 窗内每 IP 上限
_hits: dict[str, deque] = defaultdict(deque)

# unsolved 兜底文案(低置信不硬答, 引导逛选/深聊)
_FALLBACK = (
    "这个心境竹韵一时没斟准——不如先说说您今晚吃什么、和谁同饮？"
    "也可以先逛逛竹奕系列，看对了眼缘，我再来给您细讲。"
)

# 场景词→库内规范问句映射(unsolved 二次检索用; 长句 2-gram 余弦
# 稀释命中不了原条目, 见 2026-10-18 生产探针 Q1-Q6 全 unsolved 实证)
_SCENE_RULES = [
    (("送长辈", "送礼", "贺寿", "长辈", "父亲", "爸爸", "岳父",
      "领导", "礼盒", "体面"), "送长辈或重要的人，选什么酒合适？"),
    (("独酌", "一个人", "自己喝", "放松", "小酌", "微醺"),
     "自己喝选哪款比较好？"),
    (("聚会", "火锅", "朋友", "聚餐", "宴请", "待客", "同饮"),
     "自己喝选哪款比较好？"),
    (("温一壶", "煮酒", "热饮", "雨夜", "温饮"),
     "竹香酒是什么香型？口感如何？"),
    (("冰镇", "冰萃", "夏天", "夏日", "清爽", "解腻"),
     "有哪些规格？度数是多少？"),
    (("佐餐", "配菜", "小菜", "下酒"), "自己喝选哪款比较好？"),
    (("便宜", "优惠", "打折", "包邮"), "能再便宜一点吗？两件能包邮吗？"),
    (("未成年", "小孩", "孩子", "青少年"), "能帮未成年人下单买酒吗？"),
]


def _scene_question(mood: str) -> str | None:
    for keys, q in _SCENE_RULES:
        if any(k in mood for k in keys):
            return q
    return None


def _rate_ok(ip: str) -> bool:
    now = time.monotonic()
    q = _hits[ip]
    while q and now - q[0] > _RATE_WINDOW:
        q.popleft()
    if len(q) >= _RATE_MAX:
        return False
    q.append(now)
    return True


class MoodRequest(BaseModel):
    mood: str = Field(min_length=1, max_length=80)


@router.post("/api/mood/wine")
async def mood_wine(
    req: MoodRequest,
    x_real_ip: str = Header(None, alias="X-Real-IP"),
    x_forwarded_for: str = Header(None, alias="X-Forwarded-For"),
):
    """心境→酒签: RAG 检索竹韵话术, 返回推荐文案"""
    ip = (x_real_ip or "").strip() or \
        ((x_forwarded_for or "").split(",")[0].strip() or "local")
    if not _rate_ok(ip):
        raise HTTPException(429, "小酌勿急，稍候片刻再试")
    mood = req.mood.strip()
    r = await _service.rag_answer(mood, provider="rule")
    if r.get("mode") == "unsolved":
        # 场景映射二次检索(规范问句直击库内条目, 60s 缓存天然加速)
        mapped = _scene_question(mood)
        if mapped:
            r2 = await _service.rag_answer(mapped, provider="rule")
            if r2.get("mode") != "unsolved":
                r = r2
    reply = (r.get("answer") or "").strip() or _FALLBACK
    return {"success": True, "data": {
        "reply": reply,
        "mode": r.get("mode"),
        "confidence": r.get("confidence"),
    }}


def register_mood_routes(app) -> None:
    app.include_router(router)
