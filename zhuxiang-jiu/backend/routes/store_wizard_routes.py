"""一句话开店向导公开端点(同盟商城 Agent 方案① 轻量版)

定位: 同盟商城"一句话开店"超级入口——游客描述经营想法, 规则引擎
即时生成开店方案(品类/店名候选/定位/选品/风格/营销/七日清单),
登录后可一键提交入盟申请(POST /api/alliance/apply, 超级会员)。

安全口径: 与 /api/mood/wine 同范式——PUBLIC_EXACT 单点放行,
不放宽前缀; idea 限 1..120 字; 进程内按 IP 滑动窗限流(12/60s);
纯规则生成零外呼, 不落库不记身份。
"""
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(tags=["一句话开店向导(同盟商城)"])

_RATE_WINDOW = 60.0
_RATE_MAX = 12
_hits: dict[str, deque] = defaultdict(deque)

# 品类识别(关键词 -> 同盟八类; 顺序即优先级)
_CATEGORY_RULES = [
    ("tea", ("茶", "茗", "红茶", "绿茶", "白茶", "岩茶", "普洱")),
    ("water", ("水", "泉", "咖啡", "饮品", "饮料")),
    ("wine", ("酒", "酿", "醴", "微醺", "白酒", "黄酒", "果酒")),
    ("vessel", ("器", "杯", "壶", "陶瓷", "茶具", "酒具", "瓷器", "竹编")),
    ("venue", ("民宿", "雅间", "空间", "体验", "文旅", "研学", "场地")),
    ("fish", ("鱼", "海鲜", "虾", "蟹")),
    ("meat", ("肉", "腊", "火腿", "酱肉")),
    ("dish", ("菜", "食", "特产", "零食", "小吃", "糕", "伴手礼", "农产")),
]
_DEFAULT_CATEGORY = ("dish", "臻选好物")

# 品类方案模板(定位/选品/风格/营销, 竹韵调性)
_PLANS = {
    "tea": {
        "label": "🍵 好茶",
        "positioning": "一盏清茶待知音——以山场与工艺立店, 以茶会友慢生意",
        "picks": ["明前绿茶/高山乌龙两款引流", "私房口粮茶做复购主力",
                  "节气限定茶礼盒拉客单", "附茶样小包降低尝新门槛"],
        "style": "青灰+米白主色 / 宋楷标题 / 山场晨雾实拍",
        "marketing": ["茶山溯源短视频每周一条", "首单茶样价 + 好评晒罐返券",
                      "节气前 7 天预售造势"],
    },
    "water": {
        "label": "💧 好水",
        "positioning": "好水配好茶——以水源地故事立店, 订阅制慢生意",
        "picks": ["水源地瓶装水家庭装", "泡茶专用水细分款",
                  "会员月订套餐锁复购", "联名礼箱(水+茶)提客单"],
        "style": "湖蓝+砂白主色 / 细黑标题 / 水源地实拍",
        "marketing": ["水质检测报告全公开", "首月订阅半价体验",
                      "茶馆/民宿 B 端渠道联营"],
    },
    "wine": {
        "label": "🍶 好酒",
        "positioning": "微醺即止的东方酒馆——以竹香叙事立店, 场景化卖酒",
        "picks": ["竹香型主力款(自饮)", "礼盒装(长辈/商务)拉客单",
                  "低度果味款拓年轻客", "开瓶器/杯具搭售"],
        "style": "竹绿+鎏金主色 / 宋楷标题 / 雨夜温饮氛围图",
        "marketing": ["饮酒场景内容(火锅/雨夜/独酌)", "满赠酒盏做老客回馈",
                      "合规先行: 未成年人不售+适量倡导前置"],
    },
    "vessel": {
        "label": "🥃 酒器茶器",
        "positioning": "器物有灵——以手作温度立店, 一器一故事",
        "picks": ["入门手感杯壶套装", "匠人签名款做镇店",
                  "竹编/陶瓷跨界联名", "刻字定制服务提客单"],
        "style": "陶土+竹青主色 / 手写体点缀 / 微距质感图",
        "marketing": ["匠人手作过程短视频", "买器附养护卡+终身修补",
                      "节气新品小批量预售"],
    },
    "venue": {
        "label": "🏛 雅间体验",
        "positioning": "空间即产品——以场景体验立店, 线上预订线下享",
        "picks": ["茶叙/小酌双场景套餐", "包时段雅间(含茶水)",
                  "主题活动(品鉴/雅集)月更", "企业团建定制场"],
        "style": "暖木+月白主色 / 大留白 / 空间光影实拍",
        "marketing": ["体验官免费首访计划", "同城生活号探店联动",
                      "活动日历提前 30 天公示"],
    },
    "fish": {
        "label": "🐟 鱼鲜",
        "positioning": "从码头到餐桌——以产地直发立店, 鲜字当头",
        "picks": ["当季catch两款引流", "冰鲜礼盒(顺丰)做主力",
                  "干货/即食款补淡季", "会员周五到货日固定节奏"],
        "style": "海蓝+银灰主色 / 粗黑标题 / 码头实拍",
        "marketing": ["捕捞/发货全程直播切片", "坏单秒赔不扯皮",
                      "老客预定下一个渔汛"],
    },
    "meat": {
        "label": "🍖 腊味肉食",
        "positioning": "时间腌出的风味——以古法工艺立店, 节令卖全国",
        "picks": ["招牌腊味两款引流", "节令礼盒(年货/中秋)拉客单",
                  "轻盐健康新品拓年轻客", "搭配酱料包搭售"],
        "style": "酱红+原木主色 / 圆润标题 / 灶火晾晒实拍",
        "marketing": ["古法工艺纪录短片", "开袋即食菜谱内容",
                      "节前 30 天预售锁量"],
    },
    "dish": {
        "label": "🍲 臻选好物",
        "positioning": "一方水土一方味——以原产地直采立店, 甄选伴手礼",
        "picks": ["在地招牌特产 2-3 款", "组合伴手礼盒做主力",
                  "应季农产品月更", "轻便装试吃降低门槛"],
        "style": "竹绿+暖米主色 / 手写体标题 / 产地风物实拍",
        "marketing": ["产地故事图文每周两篇", "买贵就赔价保承诺",
                      "伴手礼场景(送礼/年会)专区"],
    },
}

# 七日开店清单(全品类通用, 竹韵口吻)
_CHECKLIST = [
    "D1 定店名/选店徽, 想清一句话定位",
    "D2 上架 5-8 款商品, 主图统一风格",
    "D3 写好每款商品的'三句话卖点'",
    "D4 定价格体系: 引流款+主力款+礼盒款",
    "D5 拍 3 条产地/工艺短视频",
    "D6 设首单礼遇(小样/满赠/券)",
    "D7 开张! 发第一条朋友圈与社群预告",
]

_NAME_SUFFIX = ("雅集", "小铺", "山房", "甄选")


def _rate_ok(ip: str) -> bool:
    now = time.monotonic()
    q = _hits[ip]
    while q and now - q[0] > _RATE_WINDOW:
        q.popleft()
    if len(q) >= _RATE_MAX:
        return False
    q.append(now)
    return True


def _detect_category(idea: str) -> tuple[str, str]:
    for cat, keys in _CATEGORY_RULES:
        if any(k in idea for k in keys):
            return cat, _PLANS[cat]["label"]
    return _DEFAULT_CATEGORY[0], _PLANS[_DEFAULT_CATEGORY[0]]["label"]


def _shop_names(idea: str, label: str) -> list[str]:
    """从想法抽 2-3 字主题词 + 后缀模板生成 3 个店名候选"""
    core = ""
    for ch in idea:
        if "\u4e00" <= ch <= "\u9fff" and ch not in "的了吗呢我想开一个卖要有面向和与并在":
            core += ch
        if len(core) >= 3:
            break
    core = core[:3] if core else "竹里"
    label_word = label[-2:].lstrip("🍙🍵💧🍶🥃🏛🐟🍖🍲 ")
    return [
        core + _NAME_SUFFIX[0],
        "竹隐·" + core,
        (core + label_word)[:6] + _NAME_SUFFIX[3],
    ]


class WizardRequest(BaseModel):
    idea: str = Field(min_length=1, max_length=120)


@router.post("/api/store/wizard")
async def store_wizard(
    req: WizardRequest,
    x_real_ip: str = Header(None, alias="X-Real-IP"),
    x_forwarded_for: str = Header(None, alias="X-Forwarded-For"),
):
    """一句话开店: 规则引擎生成开店方案"""
    ip = (x_real_ip or "").strip() or \
        ((x_forwarded_for or "").split(",")[0].strip() or "local")
    if not _rate_ok(ip):
        raise HTTPException(429, "心急吃不上热豆腐，稍候片刻再来")
    idea = req.idea.strip()
    cat, label = _detect_category(idea)
    p = _PLANS[cat]
    return {"success": True, "data": {
        "category": cat,
        "categoryLabel": label,
        "shopNames": _shop_names(idea, label),
        "positioning": p["positioning"],
        "picks": p["picks"],
        "style": p["style"],
        "marketing": p["marketing"],
        "checklist": _CHECKLIST,
        "idea": idea[:60],
    }}


def register_store_wizard_routes(app) -> None:
    app.include_router(router)
