"""58号·AI智能优化意图识别路由(P0-P1)

端点(P0 5 + P1 6 = 11):
    GET  /api/ii58/registry            注册表自描述(admin, 观测面)
    POST /api/ii58/evaluate            意图识别评估(admin, 管理)
    GET  /api/ii58/evaluations         识别记录列表(admin, 观测面)
    GET  /api/ii58/evaluations/{id}    识别记录详情(admin, 观测面)
    GET  /api/ii58/model/status        模型状态(admin, 观测面)
    POST /api/ii58/corpus/ingest       语料登记(admin, 管理, P1)
    GET  /api/ii58/corpus              语料库列表(admin, 观测面, P1)
    POST /api/ii58/corpus/{id}/review  语料人工终审(admin, 终审, P1)
    POST /api/ii58/mine/positive       正样本挖掘(admin, 管理, P1)
    POST /api/ii58/mine/negative       负样本增强(admin, 管理, P1)
    GET  /api/ii58/confusables         易混淆对视图(admin, 观测面, P1)

鉴权: 管理面 X-Role: admin(43-57号同款口径)。
统一口径:
    - 观测面(registry/evaluations/model/status/
      corpus/confusables)不受 II58_MODE 影响
    - 决策面(evaluate/ingest/mine): off=拒绝
      (409——shadow/assist 开放)
    - review(语料终审): 不受开关影响
      (优化永不自动生效——人工铁律)
    - KeyError → 404 / ValueError → 409
"""

from fastapi import APIRouter, Header, HTTPException

router = APIRouter(prefix="/api/ii58",
                   tags=["AI智能优化意图识别(58号)"])


def _require_admin(x_role: str | None) -> str:
    if not x_role or x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要 X-Role: admin")


@router.get("/registry")
async def registry(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """注册表自描述(意图三位一体+置信度引擎口径
    ——观测面)"""
    _require_admin(x_role)
    from services.ii58_service import Ii58Service
    return Ii58Service.registry()


@router.post("/evaluate")
async def evaluate(
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """意图识别评估(L1 语料匹配→动态阈值→三态
    响应+归因链; 决策面 off 409)"""
    _require_admin(x_role)
    from services.ii58_service import Ii58Service
    try:
        member_id = body.get("memberId")
        return await Ii58Service().evaluate(
            str(body.get("text") or ""),
            member_id=int(member_id)
            if member_id is not None else None,
            member_role=str(
                body.get("memberRole")
                or "member"))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.get("/evaluations")
async def evaluations(
        intent_id: str = None,
        state: str = None,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """识别记录列表(置信度三态+归因链——观测面)"""
    _require_admin(x_role)
    from services.ii58_service import Ii58Service
    return await Ii58Service().list_evaluations(
        intent_id=intent_id, state=state)


@router.get("/evaluations/{eval_id}")
async def evaluation_detail(
        eval_id: int,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """识别记录详情(归因链——观测面)"""
    _require_admin(x_role)
    from repositories.ii58_repository import (
        Ii58Repository,
    )
    record = await Ii58Repository().get_evaluation(
        int(eval_id))
    if record is None:
        raise HTTPException(
            status_code=404,
            detail=f"识别记录 {eval_id} 不存在")
    return {
        "success": True,
        "evaluation": record,
        "note": "识别记录详情——置信度三态+"
                "归因链(无归因不计入有效服务)",
    }


@router.get("/model/status")
async def model_status(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """模型状态(第33档案 champion/challenger/八因子
    ——44号复用观测面)"""
    _require_admin(x_role)
    from services.ii58_service import Ii58Service
    return await Ii58Service().model_status()


@router.post("/corpus/ingest")
async def corpus_ingest(
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """语料登记(运营注册轨——对抗/越界/合成;
    pending 人工终审; 决策面, P1)"""
    _require_admin(x_role)
    from services.ii58_corpus_service import (
        Ii58CorpusService,
    )
    try:
        return await Ii58CorpusService().ingest(
            intent_id=str(
                body.get("intentId") or ""),
            text=str(body.get("text") or ""),
            sample_type=str(
                body.get("sampleType")
                or "positive"),
            weight=float(
                body.get("weight") or 1.0),
            confusable_target=body.get(
                "confusableTarget"),
            source=str(
                body.get("source")
                or "ops_register"))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.get("/corpus")
async def corpus_list(
        intent_id: str = None,
        sample_type: str = None,
        status: str = None,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """语料库列表(四类样本+版本化——观测面, P1)"""
    _require_admin(x_role)
    from services.ii58_corpus_service import (
        Ii58CorpusService,
    )
    return await Ii58CorpusService().list_corpus(
        intent_id=intent_id,
        sample_type=sample_type,
        status=status)


@router.post("/corpus/{corpus_id}/review")
async def corpus_review(
        corpus_id: int,
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """语料人工终审(pending→active 唯一出口
    ——不受开关影响的人工铁律; P1)"""
    _require_admin(x_role)
    from services.ii58_corpus_service import (
        Ii58CorpusService,
    )
    try:
        return await Ii58CorpusService().review(
            int(corpus_id),
            approve=bool(body.get("approve")),
            reviewer=str(
                body.get("reviewer") or "admin"),
            note=str(body.get("note") or ""))
    except KeyError as exc:
        raise HTTPException(status_code=404,
                            detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.post("/mine/positive")
async def mine_positive(
        body: dict = None,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """正样本挖掘(48号 turns 纯读取→脱敏去重
    入库 active 直通; 决策面, P1)"""
    _require_admin(x_role)
    from services.ii58_corpus_service import (
        Ii58CorpusService,
    )
    try:
        limit = int(
            (body or {}).get("limit") or 500)
        return await (
            Ii58CorpusService()
            .mine_positive(limit=limit))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.post("/mine/negative")
async def mine_negative(
        body: dict = None,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """负样本增强(48号 failures 三 kind 转化
    pending 人工复核; 决策面, P1)"""
    _require_admin(x_role)
    from services.ii58_corpus_service import (
        Ii58CorpusService,
    )
    try:
        limit = int(
            (body or {}).get("limit") or 500)
        return await (
            Ii58CorpusService()
            .mine_negative(limit=limit))
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.get("/confusables")
async def confusables(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """易混淆对视图(对抗样本覆盖度——观测面, P1)"""
    _require_admin(x_role)
    from services.ii58_corpus_service import (
        Ii58CorpusService,
    )
    return await (
        Ii58CorpusService().confusables_view())


def register_ii58_routes(app) -> None:
    """注册58号路由(main.py startup 调用)"""
    app.include_router(router)
