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
            "note": "P0 底座: S1-S8"
                    "刚性规则+六态状态机"
                    "+意图路由+信值准入"
                    "(P1 内容工坊完整"
                    "交付)",
        })
        return view

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

    async def _questions(self,
                         category: str
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
