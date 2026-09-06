"""58号·AI智能优化意图识别路由(P0-P4)

端点(P0 5 + P1 6 + P2 2 + P3 3 + P4 1 = 17):
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
    POST /api/ii58/threshold/calibrate 阈值校准(申请+终审双模, P2)
    GET  /api/ii58/thresholds          阈值全景(admin, 观测面, P2)
    POST /api/ii58/feedback           显式反馈(会员, 会员面, P3)
    GET  /api/ii58/labels              标注队列列表(admin, 观测面, P3)
    POST /api/ii58/labels/{id}/decide  标注人工终审(admin, 终审, P3)
    POST /api/ii58/feedback/collect    决策回流补标(admin, 回流, P4)

鉴权: 管理面 X-Role: admin(43-57号同款口径);
会员面 X-Member-Id(48号惯例)。
统一口径:
    - 观测面(registry/evaluations/model/status/
      corpus/confusables/thresholds/labels)不受
      II58_MODE 影响
    - 决策面(evaluate/ingest/mine/calibrate):
      off=拒绝(409——shadow/assist 开放)
    - 会员面(feedback): 需 assist
      (off/shadow 409)
    - review(语料终审)+calibrate 终审模
      (changeId 请求)+decide(标注终审)+
      collect(决策回流): 不受开关影响
      (优化永不自动生效——人工铁律/回流管理面)
    - KeyError → 404 / ValueError → 409
"""

from fastapi import APIRouter, Header, HTTPException

router = APIRouter(prefix="/api/ii58",
                   tags=["AI智能优化意图识别(58号)"])


def _require_admin(x_role: str | None) -> str:
    if not x_role or x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要 X-Role: admin")


def _require_member(x_member_id: str | None) -> int:
    """会员面鉴权(X-Member-Id——48号惯例)"""
    if not x_member_id:
        raise HTTPException(
            status_code=403,
            detail="需要 X-Member-Id")
    try:
        member_id = int(x_member_id)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=403,
            detail="X-Member-Id 需为整数")
    if member_id <= 0:
        raise HTTPException(
            status_code=403,
            detail="X-Member-Id 非法")
    return member_id


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
    响应+识别即合规+槽位预填+归因链;
    决策面 off 409; P2 扩 sessionId/currentPage)"""
    _require_admin(x_role)
    from services.ii58_service import Ii58Service
    try:
        member_id = body.get("memberId")
        session_id = body.get("sessionId")
        return await Ii58Service().evaluate(
            str(body.get("text") or ""),
            member_id=int(member_id)
            if member_id is not None else None,
            member_role=str(
                body.get("memberRole")
                or "member"),
            session_id=int(session_id)
            if session_id is not None else None,
            current_page=str(
                body.get("currentPage") or "")
            or None)
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


@router.post("/threshold/calibrate")
async def threshold_calibrate(
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """阈值校准双模端点(P2):
        - 申请模 {upper, lower, reason}:
          →46号审批总线留痕+镜像 pending
          (不直接生效; 决策面 off 409)
        - 终审模 {changeId, approve, reviewer,
          note}: pending→active 唯一出口
          (不受开关影响——人工铁律)"""
    _require_admin(x_role)
    from services.ii58_service import Ii58Service
    svc = Ii58Service()
    try:
        if body.get("changeId") is not None:
            return await svc.review_calibration(
                int(body.get("changeId")),
                approve=bool(body.get("approve")),
                reviewer=str(
                    body.get("reviewer") or "admin"),
                note=str(body.get("note") or ""))
        return await svc.calibrate(
            upper=body.get("upper"),
            lower=body.get("lower"),
            reason=str(body.get("reason") or ""),
            requested_by=str(
                body.get("requestedBy") or "admin"))
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail="无待终审的阈值校准(pending)")
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.get("/thresholds")
async def thresholds(
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """阈值全景(基线+各 tier 运行态计算值
    +pending 申请——观测面, P2)"""
    _require_admin(x_role)
    from services.ii58_service import Ii58Service
    return await Ii58Service().thresholds_view()


@router.post("/feedback")
async def feedback(
        body: dict,
        x_member_id: str | None = Header(
            default=None, alias="X-Member-Id")):
    """显式反馈(短期表单——识别后"不是这个";
    会员面需 assist, P3)

    Body: {evalId, text, correctedIntentId?,
    note?}——高优先级入标注队列"""
    member_id = _require_member(x_member_id)
    from services.ii58_feedback_service import (
        Ii58FeedbackService,
    )
    try:
        return await (
            Ii58FeedbackService().submit_feedback(
                member_id=member_id,
                eval_id=int(body.get("evalId") or 0),
                text=str(body.get("text") or ""),
                corrected_intent_id=body.get(
                    "correctedIntentId"),
                note=str(body.get("note") or "")))
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"识别记录 "
                   f"{body.get('evalId')} 不存在")
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.get("/labels")
async def labels(
        status: str = None,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """标注队列列表(主动学习+双通道反馈
    ——观测面, P3)"""
    _require_admin(x_role)
    from services.ii58_feedback_service import (
        Ii58FeedbackService,
    )
    return await (
        Ii58FeedbackService().list_labels(
            status=status))


@router.post("/labels/{label_id}/decide")
async def label_decide(
        label_id: int,
        body: dict,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """标注人工终审(pending→approved 语料回流
    /rejected——不受开关影响的人工铁律, P3)

    Body: {approve, reviewer?, note?,
    targetSampleType?(positive/negative/
    adversarial/boundary), targetIntentId?
    (意图修正)"""
    _require_admin(x_role)
    from services.ii58_feedback_service import (
        Ii58FeedbackService,
    )
    try:
        return await (
            Ii58FeedbackService().decide(
                int(label_id),
                approve=bool(body.get("approve")),
                reviewer=str(
                    body.get("reviewer") or "admin"),
                note=str(body.get("note") or ""),
                target_sample_type=str(
                    body.get("targetSampleType")
                    or "positive"),
                target_intent_id=body.get(
                    "targetIntentId")))
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"标注 {label_id} 不存在")
    except ValueError as exc:
        raise HTTPException(status_code=409,
                            detail=str(exc))


@router.post("/feedback/collect")
async def feedback_collect(
        body: dict = None,
        x_role: str | None = Header(default=None,
                                    alias="X-Role")):
    """决策回流补标(六类真值信号→44号池双写
    +高置信错误预警——幂等 evaluationId 1:1;
    回流管理面不受开关影响, P4)"""
    _require_admin(x_role)
    from services.ii58_learn_service import (
        Ii58LearnService,
    )
    limit = int((body or {}).get("limit") or 500)
    return await (
        Ii58LearnService().collect_feedback(
            limit=limit))


def register_ii58_routes(app) -> None:
    """注册58号路由(main.py startup 调用)"""
    app.include_router(router)
