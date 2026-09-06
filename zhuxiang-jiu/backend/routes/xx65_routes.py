"""65号·网店及商品AI智能管理路由
(P0+P1)

端点(P0 9+P1 7=16; 全期规划 24):
    GET  /api/xx65/registry        刚性规则自描述(admin, 观测面)
    POST /api/xx65/intents/parse   意图解析(member/admin, 决策面 off 409)
    POST /api/xx65/shops/apply     开店申请+信值准入预检(member/admin, 决策面 off 409)
    POST /api/xx65/shops/{id}/claim 一键认领+初始化(member/admin, 决策面 off 409)
    POST /api/xx65/shops/{id}/activate 店铺激活(member/admin, 决策面 off 409)
    POST /api/xx65/shops/{id}/close    自主关店(member/admin, 不受开关影响·经营者权利)
    GET  /api/xx65/shops            店铺列表(admin, 观测面)
    GET  /api/xx65/shops/{id}       店铺详情(member/admin, 观测面)
    GET  /api/xx65/model/status     第39档案状态(admin, 观测面)
    --- P1·AI 内容工坊 ---
    POST /api/xx65/products/draft   内容草稿生成(member/admin, 决策面 off 409·防御①)
    GET  /api/xx65/drafts/{id}      草稿详情+替换记录(member/admin, 观测面)
    POST /api/xx65/drafts/{id}/publish 草稿发布(member/admin, 决策面 off 409·防御②+S1 确认)
    POST /api/xx65/drafts/{id}/human-review 人工兜底(member/admin, S6 不受开关影响)
    GET  /api/xx65/products        商品列表(member/admin, 观测面)
    GET  /api/xx65/products/{id}/order-window 下单窗口(观测面·只读对接 64号)
    POST /api/xx65/products/inspect 上架后巡检(admin, 防御③不受开关影响·合规防线永不关停)

鉴权: X-Role: admin 或 member(开店
面向超级会员——双角色口径)。
统一口径(计划 §七):
    - 观测面(registry/shops 列表
      与详情/model status/drafts
      详情/products/order-window)
      不受 XX65_MODE 影响
    - 决策面(意图解析/开店/认领/
      激活/草稿生成/发布): off=
      拒绝(409)
    - 关店不受开关影响(经营者
      退出权利); 人工兜底与巡检
      不受开关影响(S6 宪法+
      合规防线永不关停)
    - KeyError → 404 /
      ValueError → 409
"""

from fastapi import (APIRouter, Header,
                     HTTPException)

router = APIRouter(prefix="/api/xx65",
                   tags=["网店及商品AI智能"
                         "管理(65号)"])


def _require_role(x_role: str | None) -> str:
    """双角色鉴权(admin 管理面/
    member 超级会员面)"""
    if not x_role or x_role not in (
            "admin", "member"):
        raise HTTPException(
            status_code=403,
            detail="需要 X-Role: "
                   "admin 或 member")
    return x_role


def _require_admin(x_role: str | None) -> str:
    if x_role != "admin":
        raise HTTPException(
            status_code=403,
            detail="需要 X-Role: admin")


@router.get("/registry")
async def registry(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """刚性规则 S1-S8 自描述
    (观测面不受开关影响)"""
    _require_role(x_role)
    from services.xx65_registry import (
        registry_view,
    )
    return registry_view()


@router.post("/intents/parse")
async def parse_intent(
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """意图解析(确定性关键词路由
    →类目模板; 决策面 off 409)

    Body: {ownerId, text,
    audience?}"""
    _require_role(x_role)
    from services.xx65_service import (
        Xx65Service,
    )
    try:
        return await (
            Xx65Service().parse_intent(
                owner_id=body.get(
                    "ownerId"),
                text=body.get("text"),
                audience=body.get(
                    "audience") or ""))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc)) from exc


@router.post("/shops/apply")
async def apply_shop(
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """开店申请(意图关联+信值
    准入预检——过即 prechecked;
    决策面 off 409)

    Body: {ownerId, trustId?,
    intentId?}"""
    _require_role(x_role)
    from services.xx65_service import (
        Xx65Service,
    )
    try:
        return await (
            Xx65Service().apply_shop(
                owner_id=body.get(
                    "ownerId"),
                trust_id=body.get(
                    "trustId"),
                intent_id=body.get(
                    "intentId")))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc)) from exc


@router.post("/shops/{shop_id}/claim")
async def claim_shop(
        shop_id: int,
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """一键认领+初始化(合规承诺
    问答+模板生成+S8 溯源指纹;
    决策面 off 409)

    Body: {answers: {问题: 回答}}"""
    _require_role(x_role)
    from services.xx65_service import (
        Xx65Service,
    )
    try:
        return await (
            Xx65Service().claim_shop(
                shop_id=shop_id,
                answers=body.get(
                    "answers")))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc)) from exc


@router.post("/shops/{shop_id}/activate")
async def activate_shop(
        shop_id: int,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """店铺激活(claimed→active;
    决策面 off 409)"""
    _require_role(x_role)
    from services.xx65_service import (
        Xx65Service,
    )
    try:
        return await (
            Xx65Service()
            .activate_shop(shop_id))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc)) from exc


@router.post("/shops/{shop_id}/close")
async def close_shop(
        shop_id: int,
        body: dict = None,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """自主关店(六态→closed;
    不受开关影响——经营者退出
    权利)

    Body: {closedBy?}"""
    _require_role(x_role)
    from services.xx65_service import (
        Xx65Service,
    )
    body = body or {}
    try:
        return await (
            Xx65Service().close_shop(
                shop_id=shop_id,
                closed_by=body.get(
                    "closedBy")
                or x_role))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc)) from exc


@router.get("/shops")
async def shops_list(
        owner_id: int = None,
        status: str = None,
        limit: int = 50,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """店铺列表(admin, 观测面)"""
    _require_admin(x_role)
    from services.xx65_service import (
        Xx65Service,
    )
    return await (
        Xx65Service().shops_list(
            owner_id=owner_id,
            status=status,
            limit=limit))


@router.get("/shops/{shop_id}")
async def shop_detail(
        shop_id: int,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """店铺详情(member/admin,
    观测面)"""
    _require_role(x_role)
    from services.xx65_service import (
        Xx65Service,
    )
    try:
        return await (
            Xx65Service()
            .shop_detail(shop_id))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc)) from exc


@router.get("/model/status")
async def model_status(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """第39档案状态(44号观测面)"""
    _require_admin(x_role)
    from services.xx65_service import (
        Xx65Service,
    )
    return await Xx65Service() \
        .model_status()


# ============================================================
# P1·AI 内容工坊
# ============================================================

@router.post("/products/draft")
async def create_draft(
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """内容草稿生成(防御①: LLM/rule
    文案+禁词实时替换+留痕;
    决策面 off 409)

    Body: {shopId, productName,
    description?, price}"""
    _require_role(x_role)
    from services.xx65_service import (
        Xx65Service,
    )
    try:
        return await (
            Xx65Service().create_draft(
                shop_id=body.get("shopId"),
                product_name=body.get(
                    "productName"),
                description=body.get(
                    "description") or "",
                price=body.get("price")))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc)) from exc


@router.get("/drafts/{draft_id}")
async def get_draft(
        draft_id: int,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """草稿详情+禁词替换记录
    (观测面——不受开关影响)"""
    _require_role(x_role)
    from services.xx65_service import (
        Xx65Service,
    )
    try:
        return await (
            Xx65Service().get_draft(
                draft_id))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc)) from exc


@router.post("/drafts/{draft_id}/publish")
async def publish_draft(
        draft_id: int,
        body: dict = None,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """草稿发布(防御②: 合规二次
    校验+人工确认——S1 终审
    不可跳过; 决策面 off 409)

    Body: {confirmed: true}"""
    _require_role(x_role)
    from services.xx65_service import (
        Xx65Service,
    )
    body = body or {}
    try:
        return await (
            Xx65Service().publish_draft(
                draft_id=draft_id,
                confirmed=bool(
                    body.get("confirmed"))))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc)) from exc


@router.post("/drafts/{draft_id}/human-review")
async def human_review(
        draft_id: int,
        body: dict = None,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """人工兜底通道(S6——不受
    开关影响)

    Body: {note?, action?}
    - action 空: 转人工申请(member)
    - action approve/reject: 人工
      终审(admin——终审人工铁律)"""
    role = _require_role(x_role)
    from services.xx65_service import (
        Xx65Service,
    )
    body = body or {}
    try:
        return await (
            Xx65Service().human_review(
                draft_id=draft_id,
                note=body.get("note") or "",
                action=body.get("action"),
                reviewer=role))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc)) from exc


@router.get("/products")
async def products_list(
        shop_id: int = None,
        status: str = None,
        limit: int = 50,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """商品列表(观测面——双轨
    价格仅展示 S4)"""
    _require_role(x_role)
    from services.xx65_service import (
        Xx65Service,
    )
    return await (
        Xx65Service().products_list(
            shop_id=shop_id,
            status=status,
            limit=limit))


@router.get("/products/{product_id}/order-window")
async def order_window(
        product_id: int,
        trust_id: int,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """下单窗口智能构建(观测面——
    双轨价格+额度进度条+二次确认
    预警; 只读对接 64号观测面,
    65号不做结算 S3/S4)"""
    _require_role(x_role)
    from services.xx65_service import (
        Xx65Service,
    )
    try:
        return await (
            Xx65Service().order_window(
                product_id=product_id,
                trust_id=trust_id))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc)) from exc


@router.post("/products/inspect")
async def inspect_products(
        body: dict = None,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """防御③: 上架后巡检(admin——
    合规防线永不关停; 命中仅标记
    +留痕, 下架须人工处置 S6)

    Body: {shopId?}"""
    _require_admin(x_role)
    from services.xx65_service import (
        Xx65Service,
    )
    body = body or {}
    return await (
        Xx65Service().inspect_products(
            shop_id=body.get("shopId")))


def register_xx65_routes(app) -> None:
    """注册65号路由(main.py startup 调用)"""
    app.include_router(router)
