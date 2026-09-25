"""酒的问话专项·侍酒师能力(外部方案借鉴裁剪, 只动酒的问话)

借鉴《竹香酒垂直品类语音智能方案》四项裁剪落地:
    P-A 信任链直通: 真伪/质检问话 → 77号竹鉴(引证应答,
        指标域以 verify_chat 单一口径); 工艺叙事问话 →
        75号竹韵(守门 L1-L3 + 溯源引证原样继承)
    P-B 场景购酒顾问: 场景×预算解析 → 产品目录 scenes 面
        匹配 + 故事化话术(subtitle/taste 策展字段——零
        LLM 成本, 数字全部来自产品目录)
    P-C 合规红线: 理性饮酒提示(推荐轮尾部——下单确认轮
        由 xiaozhu_service 统一回包处拼接)
    P-D 评论精华: 好评率 + 酒友高频词(规则统计式, 零 LLM)

设计红线:
    - 77/75号决策面门槛原样继承(require_decision_mode
      ——off 挡时安全话术降级, 绝不旁路灰度)
    - 事实/数字只来自数据层(报告典藏/产品目录/评价库),
      本模块不产数字
    - 不引入向量库/外部 RAG——场景匹配走产品既有
      scenes 字段(目录 11 款, 确定性路由足够)
    - 让位原则: 短属性问("什么工艺/怎么酿")归既有产品
      属性轨不动; 本模块只收叙事句式("怎么酿出来的/
      工艺流程")——其他功能不变
    - 侍酒师语调: wine.verify/wine.craft 轮 mood 路由
      steady(×0.95)——见 joyvoice_service.mood_for_turn
"""

import re

# P-C 合规提示(酒类平台红线: 未成年人保护 + 理性饮酒提醒)
WINE_COMPLIANCE_LINE = "理性饮酒，未成年人禁止饮酒。"

# 场景词 → 产品目录 scenes 面(商务宴请/高端礼赠/老友小聚/
# 收藏投资/团购定制——repositories/product_repository 面位)
_SCENE_MAP = (
    (("商务", "宴请", "饭局", "应酬"), "商务宴请"),
    (("送长辈", "长辈", "送礼", "礼赠", "礼品", "孝敬",
      "探望", "礼盒"), "高端礼赠"),
    (("小聚", "朋友聚", "聚会", "聚餐", "自饮", "家宴",
      "自己喝", "日常喝"), "老友小聚"),
    (("收藏", "投资", "升值", "典藏"), "收藏投资"),
    (("团购", "定制", "公司采", "企业采"), "团购定制"),
)

# P-D 高频好评词(规则统计——只数出现次数, 不做情感推断)
_REVIEW_POSITIVE_WORDS = (
    "回甘", "绵柔", "绵甜", "顺喉", "竹香", "清雅",
    "醇厚", "好喝", "满意", "清香", "回味", "层次",
)

# 度数/容量语境排除(预算解析防误吞 "42度/500ml")
_NUM_TAIL_EXCLUDE = ("度", "°", "m", "毫升")


def extract_abv(text: str) -> float | None:
    """度数解析(02:46 实证「选一款56度的酒」返回 42/45 度商品
    ——度数完全被忽略): "56度" "42°" → 度数意向

    有效域 5~70(白酒/果酒域); "500ml"/"预算800元" 不误吞。
    """
    t = str(text or "")
    m = re.search(
        r"(\d{1,2})(?:\s*[~\-至]\s*\d{1,2})?\s*[度°]", t)
    if not m:
        return None
    v = float(m.group(1))
    return v if 5 <= v <= 70 else None


def match_scene(text: str) -> str | None:
    """问话 → 场景面(未中 None)"""
    t = str(text or "")
    for words, scene in _SCENE_MAP:
        if any(w in t for w in words):
            return scene
    return None


def extract_budget(text: str) -> int | None:
    """预算解析(带单位/预算前缀优先, 裸数兜底)

    例: "预算800左右" → 800; "42度" → None(度数排除);
    "500ml" → None(容量排除); 裸数 >= 50 视为预算。
    """
    t = str(text or "")
    cands = []
    for m in re.finditer(
            r"(?:预算\s*)?(\d{2,5})\s*(?:元|块)", t):
        cands.append(int(m.group(1)))
    for m in re.finditer(
            r"(\d{3,5})\s*(?:左右|以内|上下)", t):
        cands.append(int(m.group(1)))
    if not cands:
        for m in re.finditer(r"(\d{2,5})", t):
            n = int(m.group(1))
            tail = t[m.end():m.end() + 2]
            if tail.startswith(_NUM_TAIL_EXCLUDE):
                continue
            if n >= 50:
                cands.append(n)
    return max(cands) if cands else None


class XiaozhuWineService:
    """酒的问话执行器(全只读——四问话零写操作)"""

    # ---------- P-A 信任链直通 ----------

    async def verify(self, question: str) -> dict:
        """真伪/质检问话 → 77号竹鉴引证

        含指标词 → verify_chat 引证应答原样透传(数字红线);
        泛化真伪问(noMetricHit) → 双报告典藏信任摘要
        (结论/机构/编号全部来自 77号 REPORTS 典藏常量)。
        """
        from services.zjian_mode_service import (
            ZjianModeService,
        )
        from services.zjian_service import (
            REPORTS as ZJIAN_REPORTS,
        )
        from services.zjian_service import ZjianService
        try:
            await ZjianModeService() \
                .require_decision_mode()
            r = await ZjianService().verify_chat(
                str(question or ""))
        except ValueError:
            # 77号决策面 off/暂停——安全降级不旁路灰度
            return self._safe(
                "竹鉴质检通道暂时关闭, 稍后再试"
                "或说「转人工」。")
        except Exception:
            return self._safe(
                "质检问答暂时不可用, 请稍后再试。")
        if r.get("noMetricHit"):
            rep = ZJIAN_REPORTS[0]
            specs = "、".join(
                f"{x['spec']}(报告 {x['reportId']})"
                for x in ZJIAN_REPORTS)
            return {
                "reply": (
                    "竹香酒真伪有据可查: 双规格均有 "
                    f"{rep['agency']} 检验报告背书——"
                    f"{specs}。{rep['conclusion']}"
                    "可追问具体指标, 如「甲醇多少」"
                    "「52度酒精度」。"),
                "card": {
                    "type": "wine_verify",
                    "subject": "真伪有据·双报告典藏",
                    "report52": ZJIAN_REPORTS[0][
                        "reportId"],
                    "report42": ZJIAN_REPORTS[1][
                        "reportId"],
                    "agency": rep["agency"]},
            }
        cits = r.get("citations") or []
        return {
            "reply": r.get("content") or "",
            "card": {
                "type": "wine_verify",
                "subject": "竹鉴·质检引证",
                "report": ((cits[0] or {}).get("id", "")
                           if cits else "")},
        }

    async def craft(self, question: str,
                   member_id: int | None = None) -> dict:
        """工艺叙事问话 → 75号竹韵(守门 L1-L3 原样继承)"""
        from services.zyh_mode_service import (
            ZyhModeService,
        )
        from services.zyh_service import ZyhService
        try:
            await ZyhModeService() \
                .require_decision_mode()
            r = await ZyhService().chat(
                str(question or ""), member_id)
        except ValueError:
            return self._safe(
                "竹韵工艺问答通道暂时关闭, 稍后再试"
                "或说「转人工」。")
        except Exception:
            return self._safe(
                "工艺问答暂时不可用, 请稍后再试。")
        return {"reply": r.get("content") or "",
                "card": None}

    # ---------- P-B 场景购酒顾问 ----------

    async def recommend(self, text: str) -> dict:
        """场景×预算 → scenes 匹配 + 故事化推荐

        故事素材全部来自产品策展字段(subtitle/taste/series),
        价格/度数数字来自产品目录——零 LLM 成本。
        """
        from repositories.product_repository import (
            ProductRepository,
        )
        products = await ProductRepository().list_all()
        if not products:
            return self._safe(
                "产品目录暂时取不到, 请稍后再试。")
        scene = match_scene(text)
        budget = extract_budget(text)
        # v81 指名直入: 非场景问句+剥指令词后含明确商品词
        # ("推荐竹香珍藏")→全站定位该款直接推荐; miss 继续
        # 场景/度数/预算过滤(泛词"好喝的"零误报)
        from services.xiaozhu_service import (
            XiaozhuService,
        )
        _kw = XiaozhuService._extract_product_kw(text)
        _named_hit = False
        if not scene and len(_kw) >= 2:
            from services.product_service import (
                ProductService,
            )
            _r = await ProductService().search(
                _kw, page=1, page_size=1)
            _hit = ((_r.get("products")
                     or _r.get("items") or [])[:1]
                    or [None])[0]
            if _hit:
                pool = [_hit]
                _named_hit = True  # 指名最强信号, 跳过过滤
            else:
                pool = [p for p in products
                        if (p.get("status") or "on_sale")
                        == "on_sale"] or products
        else:
            pool = [p for p in products
                    if (p.get("status") or "on_sale")
                    == "on_sale"] or products
        if scene:
            hit = [p for p in pool
                   if scene in (p.get("scenes") or [])]
            # 场景 miss 不空手——回退全目录(热销序)
            pool = hit or pool
        if budget and not _named_hit:
            # 预算是意向非硬墙——+20% 宽容; 全 miss 取最近价
            inb = [p for p in pool
                   if float(p.get("price") or 0)
                   <= budget * 1.2]
            pool = inb or sorted(
                pool,
                key=lambda p: abs(float(
                    p.get("price") or 0) - budget))
        # 度数意向非硬墙(02:46 实证 56 度指令全目录热销
        # 返回 42/45 度): ±1 宽容; 全 miss 取度数最接近
        abv = extract_abv(text)
        if abv and not _named_hit:
            ina = [p for p in pool
                   if abs(float(p.get("alcohol") or 0)
                          - abv) <= 1]
            if ina:
                pool = sorted(
                    ina, key=lambda p: (
                        p.get("hot_rank") or 99))
            else:
                # 无命中: 度数接近为主键+热销次键——
                # 覆盖下方 hot_rank 排序(否则接近序被冲掉)
                pool = sorted(
                    pool,
                    key=lambda p: (
                        abs(float(p.get("alcohol") or 0)
                            - abv),
                        p.get("hot_rank") or 99))
                _abv_sorted = True
        else:
            _abv_sorted = False
        if not _abv_sorted:
            pool = sorted(pool, key=lambda p: (
                p.get("hot_rank") or 99))
        picks = pool[:2]
        stories = []
        for p in picks:
            taste = ((p.get("attributes") or {})
                     .get("taste") or "")
            stories.append(
                f"「{p.get('name')}」{p.get('subtitle')}"
                + (f", {taste}" if taste else "")
                + f", {p.get('alcohol')}度 "
                f"{p.get('price')} 元")
        scene_line = f"{scene}场景" if scene else "为您"
        abv_line = f"{abv:g}度附近" if abv else ""
        budget_line = (f"预算 {budget} 元内"
                       if budget else "")
        # v81 动态计数(指名直入 1 款/过滤后 1 款不再误称两款)
        count_line = ("挑了一款"
                      if len(picks) == 1 else "挑了两款")
        reply = (f"{scene_line}{abv_line}{budget_line}"
                 f"{count_line}: "
                 + "; ".join(stories)
                 + "。看中哪款说「来一件」即可。"
                 + WINE_COMPLIANCE_LINE)
        cards = [{
            "id": p.get("product_id") or p.get("id"),
            "name": p.get("name"),
            "price": p.get("price"),
            "subtitle": p.get("subtitle"),
            "image": ((p.get("images") or {})
                      .get("main") or ""),
            "alcohol": p.get("alcohol"),
            "volume": p.get("volume"),
        } for p in picks]
        return {
            "reply": reply,
            "card": {"type": "product_list",
                     "subject": (picks[0].get("name")
                                 if picks else None),
                     "items": cards},
            "suggest": ["来一件", "换一款", "问价格"],
        }

    # ---------- P-D 评论精华 ----------

    async def reviews(self, keyword: str) -> dict:
        """评论精华: 好评率 + 酒友高频词(规则统计式)"""
        from services.product_service import ProductService
        svc = ProductService()
        items = []
        try:
            r = await svc.search(str(keyword or "竹"),
                                 page=1, page_size=3)
            items = (r.get("products")
                     or r.get("items") or [])[:3]
            if not items:
                hot = await svc.get_hot_products(limit=3)
                items = (hot.get("products")
                         if isinstance(hot, dict) else hot
                         or [])[:3]
        except Exception:
            pass
        if not items:
            return self._safe(
                "暂时没取到产品口碑, 请稍后再试。")
        p = items[0]
        pid = str(p.get("product_id")
                 or p.get("productId") or p.get("id"))
        try:
            rv = await svc.list_reviews(pid, page=1,
                                        page_size=20)
        except Exception:
            return self._safe(
                "评价暂时读取失败, 请稍后再试。")
        avg = rv.get("ratingAvg") or 0
        cnt = rv.get("ratingCount") or 0
        rows = rv.get("reviews") or []
        words = {}
        for it in rows:
            c = str(it.get("content") or "")
            for w in _REVIEW_POSITIVE_WORDS:
                if w in c:
                    words[w] = words.get(w, 0) + 1
        top = "/".join(w for w, _ in sorted(
            words.items(), key=lambda x: -x[1])[:3])
        good = sum(1 for it in rows
                   if int(it.get("rating") or 0) >= 4)
        bits = [f"「{rv.get('productName')
                     or p.get('name')}」口碑: "
                f"平均 {avg} 分(共 {cnt} 条评价)"]
        if rows:
            bits.append(f"4 星以上占 "
                        f"{round(100 * good / len(rows))}%")
        if top:
            bits.append(f"酒友高频词: {top}")
        return {
            "reply": ", ".join(bits) + "。可说「来一件」带走。",
            "card": {"type": "product_detail",
                     "subject": p.get("name"),
                     "price": p.get("price"),
                     "items": [dict(p)]},
        }

    @staticmethod
    def _safe(msg: str) -> dict:
        return {"reply": msg, "card": None}
