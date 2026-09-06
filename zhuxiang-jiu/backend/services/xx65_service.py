"""65号·网店及商品AI智能管理 核心服务
(xx65_service, P0)

计划(§四.1):
    ① 意图解析: 确定性路由
       (关键词→类目模板匹配,
        匹配失败回退 general)
    ② 信值准入预检: S2 双维校验
       (23号 creditLevel×47号 tier
        动态门槛+45号档案存在性)
    ③ 开店申请: applying→
       prechecked(预检过留痕)
    ④ 一键认领: prechecked→
       claimed(模板初始化: 店名/
       Logo/简介/首页布局模板+
       合规承诺问答留痕)
    ⑤ 激活: claimed→active
       (合规承诺全答题后)
    ⑥ 关店/冻结: 六态状态机
       服务端强制

铁律:
    - LLM 不进判定链(意图路由/
      准入全确定性——S3)
    - 23号/45号/47号纯读取
      (零改动宪法)
    - 决策面 off 409(shadow=
     开店观察期留痕不初始化;
      assist=开店开放)
"""

import hashlib
import logging
import time

from core.helpers import ts

from repositories.xx65_repository import (
    Xx65Repository,
)

logger = logging.getLogger("xx65_svc")

MODEL_VERSION = "v1-xx65-service"

SCORER_ID = "shop_operation"


def current_mode() -> str:
    """模块开关(XX65_MODE——
    同 registry)"""
    from services.xx65_registry import (
        current_mode as _mode,
    )
    return _mode()


def require_active_mode() -> None:
    """决策面门槛(off 拒绝)"""
    mode = current_mode()
    if mode == "off":
        raise ValueError(
            f"XX65_MODE={mode}(默认 off"
            f"——决策面关闭, 观测面"
            f"不受影响)")


def _fingerprint(*parts) -> str:
    """溯源指纹(S8——哈希指纹链
    对齐 62号范式)"""
    raw = "|".join(str(p) for p in parts)
    return "sha256:" + hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()[:32]


class Xx65Service:
    """65号智能开店底座(P0——
    意图路由+准入预检+六态)"""

    def __init__(self):
        self.repo = Xx65Repository()

    # ============================================================
    # ① 意图解析(确定性路由)
    # ============================================================

    async def parse_intent(
            self, owner_id: int,
            text: str,
            audience: str = ""
    ) -> dict:
        """开店意图解析(关键词→类目
        模板——同输入同输出)

        Args:
            owner_id: 超级会员 memberId
            text: 意图描述(如"我想用
                祖传手艺做定制木雕")
            audience: 目标客户(可选
                ——存档供 P1 风格适配)

        Raises:
            ValueError: off 态/
                意图为空
        """
        require_active_mode()
        owner_id = int(owner_id or 0)
        text = str(text or "").strip()
        if owner_id <= 0:
            raise ValueError(
                "ownerId 必填")
        if not text or len(text) > 500:
            raise ValueError(
                "意图描述必填(1-500 字符)")

        from services.xx65_registry import (
            CATEGORY_FALLBACK,
            CATEGORY_TEMPLATES,
            level_rank,
        )
        # 确定性路由(遍历模板
        # 关键词——命中即类目;
        # 多命中取 minLevel 最高)
        matched = None
        hits = []
        for key, tpl in \
                CATEGORY_TEMPLATES.items():
            kws = [k for k in
                   tpl["keywords"]
                   if k in text]
            if kws:
                hits.append(
                    (key, len(kws),
                     level_rank(
                         tpl["minLevel"])))
                if matched is None or \
                        len(kws) > \
                        matched[1]:
                    matched = (key,
                               len(kws))
        if matched:
            category = matched[0]
            fallback = False
        else:
            category = CATEGORY_FALLBACK
            fallback = True
        tpl = CATEGORY_TEMPLATES.get(
            category, {
                "label": "综合",
                "complianceQuestions": (
                    "是否涉及品牌授权?",),
                "minLevel": "L3",
            })
        intent_id = await \
            self.repo.next_intent_id()
        record = {
            "intentId": intent_id,
            "ownerId": owner_id,
            "text": text[:500],
            "audience": str(
                audience or "")[:200],
            "category": category,
            "categoryLabel":
                tpl["label"],
            "minLevel": tpl["minLevel"],
            "matchedKeywords":
                [k for k in
                 tpl["keywords"]
                 if k in text],
            "fallback": fallback,
            "complianceQuestions":
                list(tpl[
                    "complianceQuestions"]),
            "createdAt": ts(),
        }
        await self.repo.save_intent(
            record)
        await self._track("intent", {
            "action": "parse",
            "intentId": intent_id,
            "ownerId": owner_id,
            "category": category,
            "fallback": fallback,
        })
        return {
            "success": True,
            "intentId": intent_id,
            "category": category,
            "categoryLabel":
                tpl["label"],
            "minLevel": tpl["minLevel"],
            "fallback": fallback,
            "complianceQuestions":
                record[
                    "complianceQuestions"],
            "note": "意图解析——确定性"
                    "关键词路由(同输入"
                    "同输出; 回退 "
                    + ("general "
                       "人工确认类目"
                       if fallback
                       else "精确匹配"),
            "createdAt": ts(),
        }

    # ============================================================
    # ② 信值准入预检(S2 双维)
    # ============================================================

    async def admission_precheck(
            self, owner_id: int,
            trust_id: int = None
    ) -> dict:
        """开店信值准入预检
        (23号 creditLevel×47号 tier
        动态门槛+45号档案存在性)

        Raises:
            KeyError: 45号档案不存在
        """
        owner_id = int(owner_id or 0)
        trust_id = int(
            trust_id or owner_id)
        # ① 45号档案存在性(纯读取)
        from repositories.trust_value_repository import (
            TrustValue45Repository,
        )
        profile = await (
            TrustValue45Repository()
            .get_profile(trust_id))
        if not profile:
            raise KeyError(
                f"45号信值档案 "
                f"{trust_id} 不存在"
                f"(先建档)")
        # ② 23号信用等级(纯读取)
        credit_level = "L1"
        credit_score = 0.0
        try:
            from repositories.credit_repository import (
                CreditRepository,
            )
            acct = await (
                CreditRepository()
                .get_or_create_score(
                    owner_id))
            credit_level = str(
                acct.get("creditLevel")
                or "L1")
            credit_score = float(
                acct.get("score") or 0)
        except Exception as exc:
            logger.warning(
                "xx65_credit_read_skip: %s",
                exc)
        # ③ 47号 tier(纯读取
        #    fail-soft standard)
        tier = "standard"
        try:
            from services.trust_risk_profile_service import (
                TrustRiskProfileService,
            )
            tp = await (
                TrustRiskProfileService()
                .get_profile(trust_id))
            tier = tp.get("tier") \
                or "standard"
        except Exception as exc:
            logger.warning(
                "xx65_tier_read_skip: %s",
                exc)
        # ④ 动态门槛
        from services.xx65_registry import (
            level_rank, quota_tier,
            required_level,
        )
        req_level = required_level(tier)
        checks = {
            "S2_TRUST": True,
            "S2_TIER": tier in (
                "trusted", "standard"),
            "S2_CREDIT": level_rank(
                credit_level)
            >= level_rank(req_level),
        }
        passed = all(
            checks.values())
        advice = ""
        if not passed:
            if not checks["S2_CREDIT"]:
                advice = (
                    f"当前信用 {credit_level}"
                    f" 低于开店门槛 "
                    f"{req_level}——建议"
                    f"提升信用分或申请"
                    f"新手扶持信用包"
                    f"(46号审批)")
            elif not checks["S2_TIER"]:
                advice = (
                    f"47号 tier={tier}"
                    f" 触发准入加严"
                    f"(门槛升至 "
                    f"{req_level})——"
                    f"提升经营信誉后"
                    f"再申请")
        return {
            "success": True,
            "ownerId": owner_id,
            "trustId": trust_id,
            "creditLevel": credit_level,
            "creditScore": credit_score,
            "tier": tier,
            "requiredLevel": req_level,
            "quotaTier": quota_tier(
                credit_level),
            "checks": checks,
            "passed": passed,
            "advice": advice,
            "note": "信值准入预检——S2"
                    "双维(信用等级×tier"
                    "动态门槛, 23/45/47号"
                    "纯读取)",
            "generatedAt": ts(),
        }

    # ============================================================
    # ③ 开店申请(applying→prechecked)
    # ============================================================

    async def apply_shop(self,
                         owner_id: int,
                         trust_id: int = None,
                         intent_id: int = None
                         ) -> dict:
        """开店申请(意图关联+准入
        预检——过即 prechecked)

        Raises:
            KeyError: 意图不存在/
                45号档案不存在
            ValueError: off 态/
                准入不满足/重复开店
        """
        require_active_mode()
        owner_id = int(owner_id or 0)
        trust_id = int(
            trust_id or owner_id)
        if intent_id:
            intent = await self.repo \
                .get_intent(int(intent_id))
            if not intent:
                raise KeyError(
                    f"意图 {intent_id}"
                    f" 不存在")
        # 重复开店(同 owner 已有
        # 非 closed 店铺)
        existing = await self.repo \
            .list_shops(owner_id=owner_id,
                        limit=10)
        if any(s.get("status")
               != "closed"
               for s in existing):
            raise ValueError(
                f"会员 {owner_id} 已有"
                f"经营中店铺(先关店"
                f"再申请)")
        # 准入预检
        check = await \
            self.admission_precheck(
                owner_id, trust_id)
        if not check["passed"]:
            failed = [k for k, v in
                      check["checks"]
                      .items() if not v]
            raise ValueError(
                f"信值准入不满足: "
                f"{'/'.join(failed)}"
                f"——{check['advice']}")
        # 类目(意图关联或缺省
        # general)
        category = "general"
        min_level = "L3"
        if intent_id:
            intent = await self.repo \
                .get_intent(
                    int(intent_id))
            category = intent.get(
                "category") or "general"
            min_level = intent.get(
                "minLevel") or "L3"
        shop_id = await \
            self.repo.next_shop_id()
        record = {
            "shopId": shop_id,
            "ownerId": owner_id,
            "trustId": trust_id,
            "intentId": int(
                intent_id or 0),
            "category": category,
            "minLevel": min_level,
            "status": "prechecked",
            # S2 准入快照(可审计)
            "precheckSnapshot": {
                "creditLevel": check[
                    "creditLevel"],
                "tier": check["tier"],
                "requiredLevel":
                    check[
                        "requiredLevel"],
                "quotaTier": check[
                    "quotaTier"],
            },
            "quotaTier": check[
                "quotaTier"],
            "complianceAnswers": {},
            "activated": False,
            "createdAt": ts(),
            "updatedAt": ts(),
        }
        await self.repo.save_shop(record)
        await self._track("shop", {
            "action": "apply",
            "shopId": shop_id,
            "ownerId": owner_id,
            "category": category,
        })
        return {
            "success": True,
            "shopId": shop_id,
            "status": "prechecked",
            "category": category,
            "quotaTier": check[
                "quotaTier"],
            "complianceQuestions":
                await self._questions(
                    category),
            "note": "开店申请通过"
                    "信值准入——预检"
                    "快照已留痕",
            "createdAt": ts(),
        }

    # ============================================================
    # ④ 一键认领(prechecked→claimed)
    # ============================================================

    async def claim_shop(
            self, shop_id: int,
            answers: dict = None
    ) -> dict:
        """一键认领+初始化
        (场景化合规承诺问答+
        模板生成: 店名/简介/
        首页布局/支付物流配置)

        Raises:
            KeyError: 店铺不存在
            ValueError: 状态机拒绝/
                合规问答未全答
        """
        require_active_mode()
        shop = await self._get_shop(
            int(shop_id))
        from services.xx65_registry import (
            SHOP_TRANSITIONS,
        )
        if "claimed" not in \
                SHOP_TRANSITIONS.get(
                    shop.get("status"),
                    ()):
            raise ValueError(
                f"店铺状态 "
                f"{shop.get('status')}"
                f" 不可认领"
                f"(须 prechecked)")
        # 场景化合规承诺
        # (S1 前置——全答才可初始化)
        questions = await self.\
            _questions(
                shop.get("category"))
        answers = dict(
            answers or {})
        unanswered = [
            q for q in questions
            if q not in answers]
        if unanswered:
            raise ValueError(
                f"合规承诺未全答: "
                f"{unanswered}"
                f"(S1 合规前置)")
        bad = [
            q for q in questions
            if str(answers.get(q)
                   or "").strip()
            .lower() not in
            ("否", "no", "n", "没有",
             "不涉及", "false")]
        if bad:
            raise ValueError(
                f"合规承诺存疑项需人工"
                f"核实: {bad}"
                f"(转人工审核通道)")
        # 模板初始化(确定性——
        # 类目模板+指纹)
        category = shop.get(
            "category")
        init = {
            "shopName": f"{category}"
                        f"小店-"
                        f"{shop['shopId']}",
            "intro": "本店已通过信值"
                     "准入与合规承诺——"
                     "诚信经营, 信值友好",
            "layout": "standard",
            "payConfig": "default",
            "logisticsConfig":
                "default",
        }
        fingerprint = _fingerprint(
            shop_id, category,
            sorted(answers.keys()))
        shop.update({
            "status": "claimed",
            "complianceAnswers":
                answers,
            "template": init,
            "fingerprint":
                fingerprint,
            "updatedAt": ts(),
        })
        await self.repo.save_shop(
            shop, create=False)
        await self._track("shop", {
            "action": "claim",
            "shopId": int(shop_id),
            "fingerprint": fingerprint,
        })
        return {
            "success": True,
            "shopId": int(shop_id),
            "status": "claimed",
            "template": init,
            "fingerprint": fingerprint,
            "note": "认领完成——模板"
                    "初始化+合规承诺"
                    "留痕(S8 溯源指纹)",
            "claimedAt": ts(),
        }

    # ============================================================
    # ⑤ 激活(claimed→active)
    # ============================================================

    async def activate_shop(self,
                            shop_id: int
                            ) -> dict:
        """店铺激活(claimed→active
        ——可经营)

        Raises:
            KeyError: 店铺不存在
            ValueError: 状态机拒绝
        """
        require_active_mode()
        shop = await self._get_shop(
            int(shop_id))
        from services.xx65_registry import (
            SHOP_TRANSITIONS,
        )
        if "active" not in \
                SHOP_TRANSITIONS.get(
                    shop.get("status"),
                    ()):
            raise ValueError(
                f"店铺状态 "
                f"{shop.get('status')}"
                f" 不可激活"
                f"(须 claimed)")
        shop.update({
            "status": "active",
            "activated": True,
            "activatedAt": ts(),
            "updatedAt": ts(),
        })
        await self.repo.save_shop(
            shop, create=False)
        await self._track("shop", {
            "action": "activate",
            "shopId": int(shop_id),
        })
        return {
            "success": True,
            "shopId": int(shop_id),
            "status": "active",
            "note": "店铺激活——可经营"
                    "(P1 起 AI 内容工坊"
                    "开放)",
            "activatedAt": ts(),
        }

    # ============================================================
    # ⑥ 关店/冻结(管理面)
    # ============================================================

    async def close_shop(self,
                         shop_id: int,
                         closed_by: str = "member"
                         ) -> dict:
        """自主关店(active/suspended
        /其他非 closed→closed)

        Raises:
            KeyError: 店铺不存在
            ValueError: 已 closed
        """
        shop = await self._get_shop(
            int(shop_id))
        if shop.get("status") \
                == "closed":
            raise ValueError(
                "店铺已关闭(勿重复)")
        shop.update({
            "status": "closed",
            "closedBy": str(
                closed_by or "member"),
            "closedAt": ts(),
            "updatedAt": ts(),
        })
        await self.repo.save_shop(
            shop, create=False)
        await self._track("shop", {
            "action": "close",
            "shopId": int(shop_id),
            "closedBy": closed_by,
        })
        return {
            "success": True,
            "shopId": int(shop_id),
            "status": "closed",
            "note": "店铺已关闭",
            "closedAt": ts(),
        }

    # ============================================================
    # 观测面
    # ============================================================

    async def shop_detail(self,
                          shop_id: int
                          ) -> dict:
        """店铺详情+健康度摘要
        (观测面——不受开关影响)"""
        shop = await self._get_shop(
            int(shop_id))
        return {
            "success": True,
            "shop": shop,
            "note": "店铺详情(观测面)",
            "generatedAt": ts(),
        }

    async def shops_list(self,
                         owner_id: int = None,
                         status: str = None,
                         limit: int = 50
                         ) -> dict:
        """店铺列表(观测面)"""
        shops = await self.repo.list_shops(
            owner_id=owner_id,
            status=status,
            limit=limit)
        return {
            "success": True,
            "total": len(shops),
            "shops": shops,
            "note": "店铺列表(观测面)",
            "generatedAt": ts(),
        }

    # ============================================================
    # 模型状态(44号观测面)
    # ============================================================

    async def model_status(self) -> dict:
        """第39档案状态(44号
        ai_learning 观测)"""
        from services.xx65_registry import (
            registry_view,
        )
        view = registry_view()
        view.update({
            "scorer": {
                "scorerId": SCORER_ID,
                "factors": 8,
                "decisions": ("observe",
                               "optimize",
                               "urgent"),
            },
            "note": "P1 内容工坊: S1-S8"
                    "刚性规则+店铺/草稿双"
                    "状态机+合规三道防线"
                    "+下单窗口双轨展示"
                    "(P2 营销中枢待交付)",
        })
        return view

    # ============================================================
    # P1·AI 内容工坊
    # ============================================================

    async def create_draft(
            self, shop_id: int,
            product_name: str,
            description: str = "",
            price: float = 0.0
    ) -> dict:
        """内容草稿生成(防御①:
        LLM/rule 文案+禁词实时替换
        +替换记录留痕——S1/S7/S8)

        Raises:
            KeyError: 店铺不存在
            ValueError: off 态/店铺
                非 active/配额超限/
                参数非法
        """
        require_active_mode()
        shop = await self._get_shop(
            int(shop_id))
        if shop.get("status") != "active":
            raise ValueError(
                f"店铺状态 "
                f"{shop.get('status')}"
                f" 不可生成内容(须 active)")
        product_name = str(
            product_name or "").strip()
        description = str(
            description or "").strip()
        if not product_name or \
                len(product_name) > 60:
            raise ValueError(
                "商品名必填(1-60 字符)")
        if len(description) > 1000:
            raise ValueError(
                "商品描述超长"
                "(≤1000 字符)")
        price = float(price or 0)
        if price <= 0:
            raise ValueError(
                "价格必须为正数")

        # S7 配额检查(生成次数
        # 与店铺信值等级绑定)
        from services.xx65_registry import (
            AI_QUOTA_TIERS,
        )
        tier = shop.get("quotaTier") \
            or "starter"
        limit = AI_QUOTA_TIERS.get(
            tier, AI_QUOTA_TIERS[
                "starter"])["contentGen"]
        used = int(
            shop.get("quotaGen") or 0)
        if used >= limit:
            raise ValueError(
                f"S7 生成配额已满"
                f"({used}/{limit}, "
                f"{tier} 档)——守信扩容,"
                f"违规降级(配额与信值"
                f"等级动态绑定)")

        # 文案生成(rule 轨确定性
        # /LLM 轨三级降级——
        # LLM 输出仍过禁词替换)
        title, copy, track = \
            self._generate_copy(
                shop, product_name,
                description)
        # 防御①: 禁词实时替换
        # (同输入同输出——记录
        # 替换明细供溯源)
        title, t_repl = \
            self._apply_replacements(
                title)
        copy, c_repl = \
            self._apply_replacements(
                copy)
        replacements = \
            [{"field": "title",
              "from": r["from"],
              "to": r["to"]}
             for r in t_repl] + \
            [{"field": "copy",
              "from": r["from"],
              "to": r["to"]}
             for r in c_repl]
        scan = self._compliance_scan(
            f"{title}\n{copy}")

        draft_id = await \
            self.repo.next_draft_id()
        fingerprint = _fingerprint(
            "draft", draft_id,
            shop["shopId"],
            product_name, title)
        requires_review = bool(
            scan["severeHits"])
        record = {
            "draftId": draft_id,
            "shopId": int(shop_id),
            "productId": 0,
            "productName": product_name,
            "description": description,
            "generatedTitle": title,
            "generatedCopy": copy,
            "llmTrack": track,
            "cashPrice": round(
                price, 2),
            "trustQuota": round(
                price * 0.30, 2),
            "replacements": replacements,
            "wordHits": len(
                replacements),
            "compliance": scan,
            "status": "draft",
            "requiresHumanReview":
                requires_review,
            "reviewNote": "",
            "fingerprint": fingerprint,
            "createdAt": ts(),
            "updatedAt": ts(),
        }
        await self.repo.save_draft(
            record)
        # S7 配额计数
        shop["quotaGen"] = used + 1
        shop["updatedAt"] = ts()
        await self.repo.save_shop(
            shop, create=False)
        # 合规事件(防御①留痕)
        await self._compliance_event(
            shop_id=int(shop_id),
            draft_id=draft_id,
            line="gen_filter",
            hits=scan["severeHits"]
            + [r["from"]
               for r in replacements],
            disposition=(
                "flagged_human_review"
                if requires_review
                else "auto_replaced"))
        await self._track("draft", {
            "action": "create",
            "draftId": draft_id,
            "shopId": int(shop_id),
            "llmTrack": track,
            "wordHits": len(replacements),
            "requiresHumanReview":
                requires_review,
        })
        return {
            "success": True,
            "draftId": draft_id,
            "shopId": int(shop_id),
            "productName": product_name,
            "title": title,
            "copy": copy,
            "llmTrack": track,
            "cashPrice": record[
                "cashPrice"],
            "trustQuota": record[
                "trustQuota"],
            "replacements": replacements,
            "wordHits": len(replacements),
            "compliance": scan,
            "status": "draft",
            "requiresHumanReview":
                requires_review,
            "note": "草稿已生成——防御①"
                    "(禁词实时替换+留痕);"
                    "发布须过二次校验"
                    "(S1 终审不可跳过)",
            "fingerprint": fingerprint,
            "createdAt": ts(),
        }

    async def get_draft(
            self, draft_id: int
    ) -> dict:
        """草稿详情+替换记录
        (观测面——不受开关影响)"""
        draft = await self._get_draft(
            int(draft_id))
        return {
            "success": True,
            "draft": draft,
            "note": "草稿详情(观测面——"
                    "替换记录可溯源 S1)",
            "generatedAt": ts(),
        }

    async def publish_draft(
            self, draft_id: int,
            confirmed: bool = False
    ) -> dict:
        """草稿发布(防御②: 合规
        二次校验+人工确认——S1
        终审不可跳过; draft→
        published 生成商品)

        Raises:
            KeyError: 草稿不存在
            ValueError: 未确认/
                合规不过/状态机拒绝
        """
        require_active_mode()
        draft = await self._get_draft(
            int(draft_id))
        from services.xx65_registry import (
            DRAFT_TRANSITIONS,
        )
        if "published" not in \
                DRAFT_TRANSITIONS.get(
                    draft.get("status"),
                    ()):
            raise ValueError(
                f"草稿状态 "
                f"{draft.get('status')}"
                f" 不可发布(须 draft/"
                f"pending_review)")
        if not confirmed:
            raise ValueError(
                "发布须人工确认"
                "(confirmed=true——"
                "S1 终审不可跳过)")
        # 防御②: 发布前独立二次
        # 校验(重新扫描——防
        # 人工/系统旁路改动)
        text = f"{draft.get('generatedTitle', '')}\n" \
            f"{draft.get('generatedCopy', '')}"
        scan = self._compliance_scan(
            text)
        if scan["severeHits"] or \
                not scan["passed"]:
            raise ValueError(
                f"S1 合规二次校验未过"
                f"(严重词 {scan['severeHits']}"
                f"/得分 {scan['score']})"
                f"——禁止发布, 可转人工"
                f"审核通道核实")
        # 生成商品(S3/S4: 双轨
        # 价格仅展示, 结算走 64号)
        product_id = await \
            self.repo.next_product_id()
        product = {
            "productId": product_id,
            "shopId": draft["shopId"],
            "draftId": int(draft_id),
            "productName": draft[
                "productName"],
            "title": draft[
                "generatedTitle"],
            "copy": draft[
                "generatedCopy"],
            "cashPrice": draft[
                "cashPrice"],
            "trustQuota": draft[
                "trustQuota"],
            "status": "published",
            "complianceFlag": False,
            "fingerprint": draft.get(
                "fingerprint"),
            "createdAt": ts(),
        }
        await self.repo.save_product(
            product)
        draft.update({
            "status": "published",
            "productId": product_id,
            "publishedAt": ts(),
            "updatedAt": ts(),
        })
        await self.repo.save_draft(
            draft, create=False)
        await self._compliance_event(
            shop_id=draft["shopId"],
            draft_id=int(draft_id),
            product_id=product_id,
            line="publish_recheck",
            hits=scan["bannedHits"],
            disposition="passed")
        await self._track("draft", {
            "action": "publish",
            "draftId": int(draft_id),
            "shopId": draft["shopId"],
            "productId": product_id,
        })
        return {
            "success": True,
            "draftId": int(draft_id),
            "productId": product_id,
            "status": "published",
            "compliance": scan,
            "note": "发布成功——防御②"
                    "过审; 双轨价格仅展示"
                    "(S3/S4 结算以 64号"
                    "规则引擎为准)",
            "publishedAt": ts(),
        }

    async def human_review(
            self, draft_id: int,
            note: str = "",
            action: str = None,
            reviewer: str = "member"
    ) -> dict:
        """人工兜底通道(S6——不受
        开关影响: 转人工/审批双轨)

        Args:
            action: None=转人工申请
                (member); approve/
                reject=人工终审(admin
                ——终审人工铁律)
        """
        draft = await self._get_draft(
            int(draft_id))
        from services.xx65_registry import (
            DRAFT_TRANSITIONS,
        )
        note = str(note or "").strip()
        if action is None:
            # 转人工申请
            if "pending_review" not in \
                    DRAFT_TRANSITIONS.get(
                        draft.get("status"),
                        ()):
                raise ValueError(
                    f"草稿状态 "
                    f"{draft.get('status')}"
                    f" 不可转人工"
                    f"(须 draft)")
            draft.update({
                "status":
                    "pending_review",
                "reviewNote": note,
                "updatedAt": ts(),
            })
            await self.repo.save_draft(
                draft, create=False)
            await self._compliance_event(
                shop_id=draft["shopId"],
                draft_id=int(draft_id),
                line="gen_filter",
                hits=[],
                disposition=(
                    "human_review"
                    "_requested"))
            await self._track("draft", {
                "action":
                    "human_review",
                "draftId": int(draft_id),
                "shopId":
                    draft["shopId"],
                "note": note,
            })
            return {
                "success": True,
                "draftId": int(draft_id),
                "status":
                    "pending_review",
                "note": "已转人工审核"
                        "(S6 兜底通道——"
                        "不受开关影响)",
                "updatedAt": ts(),
            }
        if action not in ("approve",
                         "reject"):
            raise ValueError(
                "action 仅支持 "
                "approve/reject")
        if reviewer != "admin":
            raise ValueError(
                "人工终审须 admin "
                "(S6 终审人工铁律)")
        target = "published" \
            if action == "approve" \
            else "rejected"
        if target not in \
                DRAFT_TRANSITIONS.get(
                    draft.get("status"),
                    ()):
            raise ValueError(
                f"草稿状态 "
                f"{draft.get('status')}"
                f" 不可终审"
                f"(须 pending_review)")
        if action == "reject":
            draft.update({
                "status": "rejected",
                "reviewNote": note,
                "updatedAt": ts(),
            })
            await self.repo.save_draft(
                draft, create=False)
            await self._compliance_event(
                shop_id=draft["shopId"],
                draft_id=int(draft_id),
                line="gen_filter",
                hits=[],
                disposition=(
                    "human_rejected"))
            await self._track("draft", {
                "action": "reject",
                "draftId": int(draft_id),
                "shopId":
                    draft["shopId"],
            })
            return {
                "success": True,
                "draftId": int(draft_id),
                "status": "rejected",
                "note": "人工终审: 驳回",
                "updatedAt": ts(),
            }
        # 人工 approve——终审放行
        # (留痕+全量审计; 内容仍受
        # 防御③上架后巡检监控)
        text = f"{draft.get('generatedTitle', '')}\n" \
            f"{draft.get('generatedCopy', '')}"
        scan = self._compliance_scan(
            text)
        product_id = await \
            self.repo.next_product_id()
        product = {
            "productId": product_id,
            "shopId": draft["shopId"],
            "draftId": int(draft_id),
            "productName": draft[
                "productName"],
            "title": draft[
                "generatedTitle"],
            "copy": draft[
                "generatedCopy"],
            "cashPrice": draft[
                "cashPrice"],
            "trustQuota": draft[
                "trustQuota"],
            "status": "published",
            "complianceFlag": bool(
                scan["severeHits"]),
            "fingerprint": draft.get(
                "fingerprint"),
            "createdAt": ts(),
        }
        await self.repo.save_product(
            product)
        draft.update({
            "status": "published",
            "productId": product_id,
            "reviewNote": note,
            "publishedAt": ts(),
            "updatedAt": ts(),
        })
        await self.repo.save_draft(
            draft, create=False)
        await self._compliance_event(
            shop_id=draft["shopId"],
            draft_id=int(draft_id),
            product_id=product_id,
            line="gen_filter",
            hits=scan["severeHits"],
            disposition=(
                "human_override"))
        await self._track("draft", {
            "action": "approve",
            "draftId": int(draft_id),
            "shopId": draft["shopId"],
            "productId": product_id,
            "humanOverride": True,
        })
        return {
            "success": True,
            "draftId": int(draft_id),
            "productId": product_id,
            "status": "published",
            "compliance": scan,
            "complianceFlag": product[
                "complianceFlag"],
            "note": "人工终审放行——"
                    "留痕可溯, 防御③上架后"
                    "巡检持续监控",
            "publishedAt": ts(),
        }

    async def products_list(
            self, shop_id: int = None,
            status: str = None,
            limit: int = 50
    ) -> dict:
        """商品列表(观测面)"""
        products = await \
            self.repo.list_products(
                shop_id=shop_id,
                status=status,
                limit=limit)
        return {
            "success": True,
            "total": len(products),
            "products": products,
            "note": "商品列表(观测面——"
                    "双轨价格仅展示 S4)",
            "generatedAt": ts(),
        }

    async def order_window(
            self, product_id: int,
            trust_id: int
    ) -> dict:
        """下单窗口智能构建(S4 双轨
        展示+额度进度条——只读对接
        64号观测面, 不写 64号任何表)

        Raises:
            KeyError: 商品不存在/
                45号信值档案不存在
        """
        product = await \
            self.repo.get_product(
                int(product_id))
        if not product:
            raise KeyError(
                f"商品 {product_id}"
                f" 不存在")
        if product.get("status") \
                != "published":
            raise ValueError(
                f"商品状态 "
                f"{product.get('status')}"
                f" 未上架(下单窗口"
                f"仅对 published 开放)")
        price = float(
            product.get("cashPrice")
            or 0.0)
        trust_id = int(trust_id or 0)
        # S4 双轨展示(对齐 64号
        # R1 口径——仅展示)
        from services.xx65_registry import (
            ORDER_WINDOW_CUMULATIVE_WARN,
            ORDER_WINDOW_SINGLE_WARN,
            POINTS_PER_TRUST_DISPLAY,
            TRUST_DISPLAY_PORTION,
        )
        trust_value = round(
            price * TRUST_DISPLAY_PORTION,
            2)
        cash_value = round(
            price - trust_value, 2)
        # 64号限额观测(纯读取——
        # 65号不写 64号任何表)
        from services.xx64_service import (
            Xx64Service,
        )
        quota = await (
            Xx64Service()
            .quota_status(trust_id))
        single_quota = float(
            quota.get("singleQuota")
            or 0.0)
        window_used = float(
            quota.get("windowUsed")
            or 0.0)
        cum_quota = float(
            quota.get("cumulativeQuota")
            or 0.0)
        # 额度进度条+预警
        # (展示层口径: 单次≥15%
        # /累计≥35% 触发二次确认)
        single_ratio = round(
            trust_value / single_quota,
            4) if single_quota > 0 \
            else 1.0
        cumulative_ratio = round(
            (window_used + trust_value)
            / cum_quota, 4) \
            if cum_quota > 0 else 1.0
        warnings = []
        if single_ratio >= \
                ORDER_WINDOW_SINGLE_WARN:
            warnings.append(
                f"单次信值占比 "
                f"{single_ratio:.0%}"
                f"≥{ORDER_WINDOW_SINGLE_WARN:.0%}"
                f"(R4 限额预警)")
        if cumulative_ratio >= \
                ORDER_WINDOW_CUMULATIVE_WARN:
            warnings.append(
                f"累计信值占比 "
                f"{cumulative_ratio:.0%}"
                f"≥{ORDER_WINDOW_CUMULATIVE_WARN:.0%}"
                f"(R5 窗口预警)")
        # 无障碍(老年受众大字版
        # +语音导购提示——店铺
        # 意图受众确定性匹配)
        elder = False
        try:
            shop = await \
                self.repo.get_shop(
                    int(product.get(
                        "shopId") or 0))
            if shop:
                intent = await \
                    self.repo.get_intent(
                        int(shop.get(
                            "intentId")
                            or 0))
                if intent:
                    audience = str(
                        intent.get(
                            "audience")
                        or "")
                    from services.xx65_registry import (
                        ELDER_AUDIENCE_MARKERS,
                    )
                    elder = any(
                        m in audience
                        for m in
                        ELDER_AUDIENCE_MARKERS)
        except Exception:
            elder = False
        return {
            "success": True,
            "productId": int(product_id),
            "productName": product.get(
                "productName"),
            "dualTrack": {
                "cashValue": cash_value,
                "trustValue": trust_value,
                "note": "双轨展示——"
                        "扣减以 64号规则"
                        "引擎为准(S3)",
            },
            "quotaProgress": {
                "balance": quota.get(
                    "balance"),
                "singleQuota":
                    single_quota,
                "windowUsed":
                    window_used,
                "cumulativeQuota":
                    cum_quota,
                "singleRatio":
                    single_ratio,
                "cumulativeRatio":
                    cumulative_ratio,
            },
            "warnings": warnings,
            "confirmRequired": bool(
                warnings),
            "pointsHint": {
                "pointsPerTrust":
                    POINTS_PER_TRUST_DISPLAY,
                "estimatedPoints": int(
                    trust_value
                    * POINTS_PER_TRUST_DISPLAY),
                "note": "积分兑换入口"
                        "提示(100:1 对齐"
                        "64号 R6 口径)",
            },
            "accessibility": {
                "largeFont": elder,
                "voiceGuide": elder,
                "note": "老年受众自动"
                        "大字版+语音导购"
                        "(确定性受众匹配)"
                if elder else
                    "标准版(非老年"
                    "受众)",
            },
            "note": "下单窗口——双轨价格"
                    "+额度进度条+二次确认"
                    "预警(只读对接 64号"
                    "观测面, 65号不做结算)",
            "generatedAt": ts(),
        }

    async def inspect_products(
            self, shop_id: int = None
    ) -> dict:
        """防御③: 上架后巡检
        (published 商品全量重扫
        ——命中即标记+合规事件
        留痕; 惩罚性下架永不
        自动执行, 须人工处置 S6)

        不受开关影响(合规防线
        永不关停——宪法口径)
        """
        products = await \
            self.repo.list_products(
                shop_id=shop_id,
                status="published",
                limit=500)
        findings = []
        for p in products:
            text = f"{p.get('title', '')}\n" \
                f"{p.get('copy', '')}"
            scan = self._compliance_scan(
                text)
            if scan["severeHits"] or \
                    not scan["passed"]:
                # 标记(观测面警示——
                # 不自动下架, 人工处置)
                p["complianceFlag"] = True
                p["updatedAt"] = ts()
                await self.repo.save_product(
                    p, create=False)
                await self._compliance_event(
                    shop_id=p["shopId"],
                    product_id=p[
                        "productId"],
                    draft_id=p.get(
                        "draftId") or 0,
                    line="post_inspect",
                    hits=scan[
                        "severeHits"]
                    + scan["bannedHits"],
                    disposition=(
                        "flagged_manual"
                        "_takedown"))
                findings.append({
                    "productId": p[
                        "productId"],
                    "shopId": p[
                        "shopId"],
                    "severeHits": scan[
                        "severeHits"],
                    "bannedHits": scan[
                        "bannedHits"],
                    "score": scan[
                        "score"],
                })
        return {
            "success": True,
            "scanned": len(products),
            "flagged": len(findings),
            "findings": findings,
            "note": "防御③上架后巡检——"
                    "命中仅标记+留痕,"
                    "下架须人工处置"
                    "(S6 终审人工铁律)",
            "inspectedAt": ts(),
        }

    # ============================================================
    # P2·智能营销中枢
    # ============================================================

    async def recommend_campaign(
            self, shop_id: int,
            product_id: int = None
    ) -> dict:
        """活动策略推荐(观测面——
        三因子确定性规则库+64号
        流动性感知+ROI 信值双算)

        三因子: 店铺信值(0.40)+
        商品热度(0.35)+季节趋势
        (0.25)——同输入同输出;
        流动性信号(64号 anchors/
        LIQ-CRUNCH 纯读取)仅
        调整策略排序权重, 不改
        数字口径(S3 服务端权威)

        Raises:
            KeyError: 店铺/商品不存在
        """
        shop = await self._get_shop(
            int(shop_id))
        product = None
        if product_id:
            product = await \
                self.repo.get_product(
                    int(product_id))
            if not product:
                raise KeyError(
                    f"商品 {product_id}"
                    f" 不存在")
            if product.get("shopId") \
                    != int(shop_id):
                raise ValueError(
                    f"商品 {product_id}"
                    f" 不属于店铺 "
                    f"{shop_id}")

        from services.xx65_registry import (
            CAMPAIGN_CHANNELS,
            CAMPAIGN_FACTOR_WEIGHTS,
            CAMPAIGN_STRATEGIES,
            CATEGORY_COMPLEMENTS,
            ROI_BASE_SALES,
            SEASON_TRENDS,
        )
        category = shop.get(
            "category") or "general"

        # ① 店铺信值因子(quotaTier
        #    映射: starter 0.4/
        #    growth 0.7/premium 1.0)
        tier_map = {
            "starter": 0.4,
            "growth": 0.7,
            "premium": 1.0,
        }
        f_trust = tier_map.get(
            shop.get("quotaTier")
            or "starter", 0.4)

        # ② 商品热度因子(价格带
        #    确定性代理: 无商品
        #    中性 0.5; 低价 0-50
        #    元=引流款 1.0; 50-200
        #    主力款 0.7; 200+ 旗舰
        #    款 0.4——活动适配差异)
        if product:
            price = float(
                product.get("cashPrice")
                or 0.0)
            if price <= 0:
                f_heat = 0.5
            elif price <= 50:
                f_heat = 1.0
            elif price <= 200:
                f_heat = 0.7
            else:
                f_heat = 0.4
        else:
            f_heat = 0.5

        # ③ 季节趋势因子(当月类目
        #    命中=1.0, 未命中=0.5)
        import datetime as _dt
        month = _dt.datetime.now(
        ).month
        f_season = 1.0 if category \
            in SEASON_TRENDS.get(
                month, ()) else 0.5

        heat_detail = (
            f"价格带热度 {product.get('cashPrice')}"
            if product else "无商品(中性)")
        season_hit = f_season > 0.5
        factors = {
            "shop_trust": {
                "score": f_trust,
                "weight":
                    CAMPAIGN_FACTOR_WEIGHTS[
                        "shop_trust"],
                "detail": f"店铺配额档 "
                          f"{shop.get('quotaTier')}"
                          f" 信值基线"},
            "product_heat": {
                "score": f_heat,
                "weight":
                    CAMPAIGN_FACTOR_WEIGHTS[
                        "product_heat"],
                "detail": heat_detail},
            "season_trend": {
                "score": f_season,
                "weight":
                    CAMPAIGN_FACTOR_WEIGHTS[
                        "season_trend"],
                "detail": f"{month} 月类目 "
                          f"{category} 趋势"
                          f"{'命中' if season_hit else '未命中'}"},
        }
        base_score = round(
            sum(f["score"] * f["weight"]
                for f in
                factors.values()), 4)

        # ④ 64号流动性感知(纯读取
        #    ——fail-soft 中性)
        liq = await \
            self._liquidity_signals()

        # 策略排序(确定性: 基础分
        # +流动性信号加成)
        ranked = []
        for key in \
                CAMPAIGN_STRATEGIES:
            boost = 0.0
            signals = []
            if key == "trust_exclusive" \
                    and liq["anchorHigh"]:
                boost += 0.20
                signals.append(
                    "anchors 购买力指数高"
                    "→促信值消耗")
            if key == "small_high_freq" \
                    and liq["tension"]:
                boost += 0.25
                signals.append(
                    "LIQ-CRUNCH 口径紧张"
                    "→小额高频")
            if key == "seasonal" \
                    and f_season > 0.5:
                boost += 0.15
                signals.append(
                    "当季类目命中")
            if key == "new_customer" \
                    and f_trust >= 0.7:
                boost += 0.10
                signals.append(
                    "店铺信值扩张期")
            score = round(
                base_score + boost, 4)
            ranked.append(
                (key, score, signals))
        ranked.sort(
            key=lambda x: (-x[1],
                           x[0]))

        # ROI 双算(确定性公式——
        # 数字全来自计算层)
        price = float(
            (product or {}).get(
                "cashPrice") or 0.0)
        recs = []
        for key, score, signals \
                in ranked[:3]:
            s = CAMPAIGN_STRATEGIES[
                key]
            gmv = round(
                price * ROI_BASE_SALES
                * (1 + s["roiCashLift"]),
                2)
            trust = round(
                gmv * s["trustPortion"],
                2)
            recs.append({
                "strategy": key,
                "label": s["label"],
                "score": score,
                "signals": signals,
                "roi": {
                    "estimatedGmv": gmv,
                    "estimatedTrust":
                        trust,
                    "cashLift":
                        s["roiCashLift"],
                    "trustPortion":
                        s["trustPortion"],
                    "formula":
                        "GMV=价格×"
                        f"{ROI_BASE_SALES}"
                        "×(1+lift); "
                        "信值=GMV×"
                        "占比",
                },
                "channels": [
                    {"channel": ch,
                     "label":
                         CAMPAIGN_CHANNELS[
                             ch]["label"],
                     "maxLength":
                         CAMPAIGN_CHANNELS[
                             ch][
                             "maxLength"]}
                    for ch in
                    s["channels"]],
                "note": s["note"],
            })

        # 跨店联动建议(类目互补
        # ——确定性映射, 仅建议
        # 执行经 46号)
        complements = list(
            CATEGORY_COMPLEMENTS.get(
                category, ()))
        cross_shop = []
        if complements:
            shops = await \
                self.repo.list_shops(
                    status="active",
                    limit=200)
            cross = [
                {"shopId": s["shopId"],
                 "category": s.get(
                     "category"),
                 "complementWith":
                     category}
                for s in shops
                if s.get("category")
                in complements
                and s["shopId"]
                != int(shop_id)]
            cross_shop = cross[:3]

        return {
            "success": True,
            "shopId": int(shop_id),
            "productId":
                int(product_id or 0),
            "category": category,
            "factors": factors,
            "baseScore": base_score,
            "liquidity": liq,
            "recommendations": recs,
            "crossShopSuggestions":
                cross_shop,
            "note": "活动策略推荐——"
                    "三因子确定性加权"
                    "(0.40/0.35/0.25)+"
                    "64号流动性感知(纯"
                    "读取); ROI 双算数字"
                    "全来自计算层(S3)",
            "generatedAt": ts(),
        }

    async def create_campaign(
            self, shop_id: int,
            product_id: int,
            strategy: str,
            name: str = "",
            discount_rate: float = 0.0
    ) -> dict:
        """创建营销活动(决策面——
        S7 活动配额+R2 互斥声明嵌入
        +S1 合规扫描+S5 撤销窗口)

        Raises:
            KeyError: 店铺/商品不存在
            ValueError: off 态/参数
                非法/配额超限/合规
                不过/未知策略
        """
        require_active_mode()
        shop = await self._get_shop(
            int(shop_id))
        if shop.get("status") != "active":
            raise ValueError(
                f"店铺状态 "
                f"{shop.get('status')}"
                f" 不可创建活动"
                f"(须 active)")
        product = await \
            self.repo.get_product(
                int(product_id))
        if not product:
            raise KeyError(
                f"商品 {product_id}"
                f" 不存在")
        if product.get("shopId") \
                != int(shop_id):
            raise ValueError(
                f"商品 {product_id}"
                f" 不属于店铺 "
                f"{shop_id}")
        if product.get("status") \
                != "published":
            raise ValueError(
                "商品未上架"
                "(须 published)")
        from services.xx65_registry import (
            AI_QUOTA_TIERS,
            CAMPAIGN_STRATEGIES,
            REVOKE_WINDOW_SECONDS,
        )
        if strategy not in \
                CAMPAIGN_STRATEGIES:
            raise ValueError(
                f"未知策略 {strategy}"
                f"(支持: {sorted(
                    CAMPAIGN_STRATEGIES)})")
        name = str(name or "").strip()
        if not name:
            s = CAMPAIGN_STRATEGIES[
                strategy]
            name = (
                f"{product.get('productName', '')}"
                f"·{s['label']}")
        if len(name) > 60:
            raise ValueError(
                "活动名超长(≤60 字符)")
        discount_rate = float(
            discount_rate or 0.0)
        if not 0 <= discount_rate \
                < 0.50:
            raise ValueError(
                "折扣率域非法"
                "(0≤rate<0.50——"
                "营销让利上限 50%)")

        # S7 活动配额
        tier = shop.get("quotaTier") \
            or "starter"
        limit = AI_QUOTA_TIERS.get(
            tier, AI_QUOTA_TIERS[
                "starter"])["campaigns"]
        used = int(
            shop.get("quotaCampaign")
            or 0)
        active = [
            c for c in
            await self.repo
            .list_campaigns(
                shop_id=int(shop_id),
                status="active",
                limit=100)]
        if len(active) >= limit:
            raise ValueError(
                f"S7 活动配额已满"
                f"({len(active)}/"
                f"{limit}, {tier} 档)")

        # S1 合规扫描(营销承诺
        # 也是内容——三道防线
        # 同口径)
        scan = self._compliance_scan(
            name)
        if scan["severeHits"] or \
                not scan["passed"]:
            raise ValueError(
                f"S1 活动名合规未过"
                f"(严重词 "
                f"{scan['severeHits']}"
                f"/得分 "
                f"{scan['score']})")

        # ROI 双算快照(创建时
        # 固化——数字来自计算层)
        from services.xx65_registry import (
            ROI_BASE_SALES,
        )
        s = CAMPAIGN_STRATEGIES[
            strategy]
        price = float(
            product.get("cashPrice")
            or 0.0)
        gmv = round(
            price * ROI_BASE_SALES
            * (1 + s["roiCashLift"]),
            2)
        est_trust = round(
            gmv * s["trustPortion"],
            2)

        campaign_id = await \
            self.repo \
            .next_campaign_id()
        now = time.time()
        record = {
            "campaignId": campaign_id,
            "shopId": int(shop_id),
            "productId":
                int(product_id),
            "strategy": strategy,
            "name": name,
            "discountRate":
                discount_rate,
            # R2 互斥声明嵌入(订单级
            # ——信值支付订单不叠加
            # 其他优惠, 活动侧固化)
            "exclusive": True,
            "channels": list(
                s["channels"]),
            "roiCash": gmv,
            "roiTrust": est_trust,
            "estimatedGmv": gmv,
            "estimatedTrust":
                est_trust,
            "status": "active",
            "revocable": True,
            "revocableUntilTs":
                now
                + REVOKE_WINDOW_SECONDS,
            "revoked": False,
            "factors": {
                "strategy": strategy,
                "cashLift":
                    s["roiCashLift"],
                "trustPortion":
                    s["trustPortion"],
            },
            "createdAt": ts(),
            "updatedAt": ts(),
        }
        await self.repo.save_campaign(
            record)
        # S7 配额计数
        shop["quotaCampaign"] = \
            used + 1
        shop["updatedAt"] = ts()
        await self.repo.save_shop(
            shop, create=False)
        await self._track(
            "campaign", {
                "action": "create",
                "campaignId": campaign_id,
                "shopId": int(shop_id),
                "strategy": strategy,
                "exclusive": True,
            })
        return {
            "success": True,
            "campaignId": campaign_id,
            "shopId": int(shop_id),
            "productId":
                int(product_id),
            "strategy": strategy,
            "name": name,
            "status": "active",
            "discountRate":
                discount_rate,
            "exclusive": True,
            "r2Declaration":
                "信值支付订单整单"
                "互斥其他优惠"
                "(64号 R2——活动侧"
                "声明固化)",
            "channels": record[
                "channels"],
            "roi": {
                "estimatedGmv": gmv,
                "estimatedTrust":
                    est_trust,
            },
            "revocableUntilTs":
                record[
                    "revocableUntilTs"],
            "revokeWindowSeconds":
                REVOKE_WINDOW_SECONDS,
            "compliance": scan,
            "note": "活动创建——S7 "
                    "配额校验+R2 互斥"
                    "声明+S1 合规扫描"
                    "+S5 撤销窗口 "
                    f"{REVOKE_WINDOW_SECONDS}s",
            "createdAt": ts(),
        }

    async def revoke_campaign(
            self, campaign_id: int,
            operator: str = "member"
    ) -> dict:
        """撤销营销活动(S5——
        发布后 5 分钟内无理由撤销
        +撤销留痕不可抹除; 决策面)

        Raises:
            KeyError: 活动不存在
            ValueError: 窗口已过/
                状态机拒绝
        """
        require_active_mode()
        campaign = await \
            self._get_campaign(
                int(campaign_id))
        from services.xx65_registry import (
            CAMPAIGN_TRANSITIONS,
            REVOKE_WINDOW_SECONDS,
        )
        if "revoked" not in \
                CAMPAIGN_TRANSITIONS \
                .get(
                    campaign.get(
                        "status"), ()):
            raise ValueError(
                f"活动状态 "
                f"{campaign.get('status')}"
                f" 不可撤销(须 active)")
        now = time.time()
        until = float(
            campaign.get(
                "revocableUntilTs")
            or 0.0)
        if now > until:
            raise ValueError(
                f"S5 撤销窗口已过"
                f"(发布后 "
                f"{REVOKE_WINDOW_SECONDS}s"
                f" 内可无理由撤销; "
                f"当前超窗 "
                f"{round(now - until, 1)}s)"
                f"——人工处置通道"
                f"(S6)")
        campaign.update({
            "status": "revoked",
            "revoked": True,
            "revocable": False,
            "revokedAt": ts(),
            "revokedBy": str(
                operator or "member"),
            "updatedAt": ts(),
        })
        await self.repo.save_campaign(
            campaign, create=False)
        await self._track(
            "campaign", {
                "action": "revoke",
                "campaignId":
                    int(campaign_id),
                "shopId": campaign[
                    "shopId"],
                "revokedBy": operator,
                "windowSeconds":
                    REVOKE_WINDOW_SECONDS,
            })
        return {
            "success": True,
            "campaignId":
                int(campaign_id),
            "status": "revoked",
            "revokedBy": str(
                operator or "member"),
            "note": "活动撤销——S5 "
                    "窗口内无理由撤销"
                    "+留痕不可抹除",
            "revokedAt": ts(),
        }

    async def campaign_report(
            self, campaign_id: int
    ) -> dict:
        """效果归因复盘(观测面——
        GMV/信值消耗双口径+R2
        互斥声明+撤销审计)"""
        campaign = await \
            self._get_campaign(
                int(campaign_id))
        est_gmv = float(
            campaign.get(
                "estimatedGmv")
            or 0.0)
        est_trust = float(
            campaign.get(
                "estimatedTrust")
            or 0.0)
        return {
            "success": True,
            "campaignId":
                int(campaign_id),
            "shopId": campaign.get(
                "shopId"),
            "strategy": campaign.get(
                "strategy"),
            "status": campaign.get(
                "status"),
            "roi": {
                "estimated": {
                    "gmv": est_gmv,
                    "trustConsumed":
                        est_trust,
                },
                "actual": {
                    "gmv": 0.0,
                    "trustConsumed":
                        0.0,
                    "note":
                        "实际归因经 64号"
                        "订单回流(P4 "
                        "learn——orderId "
                        "1:1 幂等)",
                },
                "dualTrack":
                    "现金 GMV+信值消耗"
                    "双算——数字全来自"
                    "计算层(S3)",
            },
            "exclusive": campaign.get(
                "exclusive"),
            "r2Note":
                "信值支付订单整单互斥"
                "其他优惠(64号 R2)"
                if campaign.get(
                    "exclusive") else "",
            "revocation": {
                "windowSeconds": 300,
                "revoked": campaign.get(
                    "revoked"),
                "revokedAt": campaign.get(
                    "revokedAt"),
                "revokedBy": campaign.get(
                    "revokedBy"),
            },
            "note": "活动复盘——预估"
                    "双算固化于创建时; "
                    "实际归因待 P4 回流",
            "generatedAt": ts(),
        }

    async def campaigns_list(
            self, shop_id: int = None,
            status: str = None,
            limit: int = 50
    ) -> dict:
        """活动列表(观测面)"""
        campaigns = await \
            self.repo.list_campaigns(
                shop_id=shop_id,
                status=status,
                limit=limit)
        return {
            "success": True,
            "total": len(campaigns),
            "campaigns": campaigns,
            "note": "活动列表(观测面"
                    "——ROI 双算+R2 "
                    "声明留痕)",
            "generatedAt": ts(),
        }

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------

    async def _get_shop(self,
                        shop_id: int
                        ) -> dict:
        """读取店铺(KeyError 不存在)"""
        shop = await self.repo.get_shop(
            int(shop_id))
        if not shop:
            raise KeyError(
                f"店铺 {shop_id} 不存在")
        return shop

    async def _get_draft(
            self, draft_id: int
            ) -> dict:
        """读取草稿(KeyError 不存在)"""
        draft = await self.repo.get_draft(
            int(draft_id))
        if not draft:
            raise KeyError(
                f"草稿 {draft_id} 不存在")
        return draft

    async def _get_campaign(
            self, campaign_id: int
            ) -> dict:
        """读取活动(KeyError 不存在)"""
        campaign = await \
            self.repo.get_campaign(
                int(campaign_id))
        if not campaign:
            raise KeyError(
                f"活动 {campaign_id}"
                f" 不存在")
        return campaign

    async def _liquidity_signals(
            self) -> dict:
        """64号流动性感知(纯读取
        ——fail-soft 中性; 65号
        不写 64号任何表)

        两路信号:
        - anchors 购买力指数
          (Xx64AnchorService.
           anchors_view 观测面)
        - LIQ-CRUNCH 口径
          (Xx64RiskService.
           detect_liq_crunch
           只读推演)
        """
        from services.xx65_registry import (
            ANCHOR_TRUST_SINK_THRESHOLD,
            LIQUIDITY_TENSION_RATIO,
        )
        result = {
            "anchorHigh": False,
            "purchasingPower": None,
            "tension": False,
            "projectedRatio": None,
            "source": "xx64-read-only",
            "note": "64号流动性信号"
                    "(纯读取 fail-soft)",
        }
        # ① anchors 购买力指数
        try:
            from services.xx64_anchor_service import (
                Xx64AnchorService,
            )
            view = await (
                Xx64AnchorService()
                .anchors_view(limit=1))
            latest = (view or {}).get(
                "latest") or {}
            pp = latest.get(
                "purchasingPower")
            if pp is not None:
                pp = float(pp)
                result[
                    "purchasingPower"] = pp
                result["anchorHigh"] = (
                    pp
                    >= ANCHOR_TRUST_SINK_THRESHOLD)
        except Exception as exc:
            logger.warning(
                "xx65_anchor_signal"
                "_skip: %s", exc)
        # ② LIQ-CRUNCH 口径推演
        try:
            from services.xx64_risk_service import (
                Xx64RiskService,
            )
            finding = await (
                Xx64RiskService()
                .detect_liq_crunch())
            if finding:
                detail = (finding
                          .get("detail")
                          or {})
                ratio = detail.get(
                    "projectedRatio")
                if ratio is not None:
                    ratio = float(ratio)
                    result[
                        "projectedRatio"] \
                        = ratio
                    result["tension"] = (
                        ratio
                        >= LIQUIDITY_TENSION_RATIO)
            else:
                result["projectedRatio"] \
                    = 0.0
        except Exception as exc:
            logger.warning(
                "xx65_liq_signal"
                "_skip: %s", exc)
        return result

    async def _questions(
            self, category: str
            ) -> list:
        """类目合规承诺问题清单
        (确定性)"""
        from services.xx65_registry import (
            CATEGORY_TEMPLATES,
        )
        tpl = CATEGORY_TEMPLATES.get(
            str(category or "general"))
        if not tpl:
            return ["是否涉及品牌授权?"]
        return list(tpl[
            "complianceQuestions"])

    def _generate_copy(
            self, shop: dict,
            product_name: str,
            description: str
            ) -> tuple:
        """文案生成(rule 轨确定性
        模板/LLM 轨三级降级——
        LLM 仅润色位, 判定链
        全确定性)"""
        from services.xx65_registry import (
            CATEGORY_TEMPLATES,
            LLM_TRACK_FALLBACK,
            LLM_TRACK_PRIMARY,
            LLM_TRACK_RULE,
            llm_mode,
        )
        category = shop.get(
            "category") or "general"
        label = CATEGORY_TEMPLATES.get(
            category, {}).get(
                "label", "综合")
        # rule 轨确定性模板(同输入
        # 同输出)
        title = f"{product_name}·{label}甄选"
        base_copy = description or (
            f"{product_name}, 源自诚信"
            f"店铺{label}类目, 匠心"
            f"甄选、品质如实描述。")
        rule_copy = (
            f"{base_copy} 信值友好"
            f"店铺, 支持信值支付额度"
            f"展示(结算以平台规则为准)。")
        if llm_mode() != "on":
            return title, rule_copy, \
                LLM_TRACK_RULE
        # LLM 轨(glm 主档→备档
        # →rule 兜底——fail-soft)
        try:
            from services.llm_client import (
                provider_client,
            )
            system = (
                "你是电商商品文案策划。"
                "为商品输出一段 60-120 "
                "字的中文商品描述文案, "
                "合规红线: 不得出现"
                "医疗功效宣称与广告法"
                "极限词(如最好/第一/"
                "顶级)。只输出文案正文。")
            user = (
                f"商品名: {product_name}\n"
                f"类目: {label}\n"
                f"描述: {description or '无'}")
            for model in (
                    LLM_TRACK_PRIMARY,
                    LLM_TRACK_FALLBACK):
                reply = provider_client.chat(
                    system, user,
                    model=model)
                if reply and \
                        len(reply.strip()) \
                        >= 20:
                    return title, \
                        reply.strip(), model
        except Exception as exc:
            logger.warning(
                "xx65_llm_copy_failed: %s",
                exc)
        return title, rule_copy, \
            LLM_TRACK_RULE

    def _apply_replacements(
            self, text: str
            ) -> tuple:
        """禁词实时替换(确定性
        ——记录替换明细)"""
        from services.xx65_registry import (
            BANNED_REPLACEMENTS,
        )
        replacements = []
        out = str(text or "")
        for word, repl in \
                BANNED_REPLACEMENTS.items():
            if word in out:
                count = out.count(word)
                out = out.replace(
                    word, repl)
                replacements.append({
                    "from": word,
                    "to": repl,
                    "count": count,
                    "reason": "广告法极限词"
                              "自动替换(防御①)",
                })
        return out, replacements

    def _compliance_scan(
            self, text: str
            ) -> dict:
        """确定性合规扫描(三道
        防线统一口径)"""
        from services.xx65_registry import (
            BANNED_PENALTY,
            BANNED_REPLACEMENTS,
            COMPLIANCE_PASS_SCORE,
            SEVERE_PENALTY,
            SEVERE_WORDS,
        )
        text = str(text or "")
        severe_hits = [w for w in
                       SEVERE_WORDS
                       if w in text]
        banned_hits = [w for w in
                       BANNED_REPLACEMENTS
                       if w in text]
        score = 100 \
            - len(severe_hits) \
            * SEVERE_PENALTY \
            - len(banned_hits) \
            * BANNED_PENALTY
        score = max(0, min(100, score))
        passed = (not severe_hits
                  and not banned_hits
                  and score
                  >= COMPLIANCE_PASS_SCORE)
        return {
            "severeHits": severe_hits,
            "bannedHits": banned_hits,
            "score": score,
            "passed": passed,
            "passScore":
                COMPLIANCE_PASS_SCORE,
        }

    async def _compliance_event(
            self, shop_id: int,
            line: str,
            hits: list,
            draft_id: int = 0,
            product_id: int = 0,
            disposition: str = ""
    ) -> None:
        """合规事件留痕(三道防线
        统一落 xx65_compliance)"""
        try:
            event_id = await \
                self.repo \
                .next_compliance_id()
            await self.repo \
                .save_compliance({
                    "eventId": event_id,
                    "shopId": int(
                        shop_id or 0),
                    "draftId": int(
                        draft_id or 0),
                    "productId": int(
                        product_id or 0),
                    "line": line,
                    "findings": list(
                        hits or []),
                    "disposition": str(
                        disposition or ""),
                    "createdAt": ts(),
                })
        except Exception as exc:
            logger.warning(
                "xx65_compliance_event"
                "_failed: %s", exc)

    async def _track(self,
                     event_type: str,
                     detail: dict) -> None:
        """事件留痕(fail-soft)"""
        try:
            await self.repo.add_event({
                "eventId": await
                self.repo.next_event_id(),
                "shopId": int(
                    detail.get("shopId")
                    or detail.get(
                        "intentId")
                    or 0),
                "eventType": event_type,
                "detail": detail,
                "createdAt": ts(),
            })
        except Exception as exc:
            logger.warning(
                "xx65_track_failed: %s",
                exc)
