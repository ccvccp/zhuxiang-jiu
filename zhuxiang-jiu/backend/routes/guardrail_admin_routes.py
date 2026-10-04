"""小竹合规引擎运营后台路由(2026-10-04 用户方案二期)

能力面(规则大盘/CRUD/发布热更/打标闭环/测试沙箱):
    GET  /overview            大盘: 今日拦截数/7日趋势/
                              Top10 词/误杀率/待复核/生效版本
    GET  /rules               规则列表(categoryId/status/
                              ruleType 筛选)
    POST /rules               新增规则(草稿, 发布后生效)
    PUT  /rules/{id}          修改规则
    DELETE /rules/{id}        删除(级联清白名单)
    POST /rules/batch         批量导入(去重校验)
    GET  /allowlist           白名单列表(ruleId 筛选)
    POST /allowlist           新增豁免(误杀治理)
    DELETE /allowlist/{id}    删除豁免
    GET  /hits                命中日志(feedbackStatus/
                              direction 筛选)
    POST /hits/{id}/feedback  人工打标(1 确认违规/2 误杀
                              ——误杀响应附白名单建议)
    POST /publish             发布(快照→热更新键→Pub/Sub)
    POST /sandbox             规则测试沙箱(命中词/分类/
                              话术/最终输出——配置防错闸)

鉴权: X-Role: admin 头(auth compat 模式, nexus74 同款)。
"""

import logging

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(
    prefix="/api/guardrail/admin",
    tags=["小竹合规运营后台"])


def register_guardrail_admin_routes(app) -> None:
    app.include_router(router)

logger = logging.getLogger("guardrail_admin")


def _require_admin(x_role):
    if x_role != "admin":
        raise HTTPException(
            status_code=401, detail="须 X-Role: admin")


def _map(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(404, "资源不存在")
    if isinstance(exc, ValueError):
        return HTTPException(409, str(exc))
    logger.exception("guardrail_admin_500: %s", exc)
    return HTTPException(500, f"服务异常: {exc}")


def _repo():
    from repositories.guardrail_repository import (
        get_guardrail_repo,
    )
    return get_guardrail_repo()


# ============================================================
# Pydantic 请求模型
# ============================================================

class RuleCreate(BaseModel):
    categoryId: int = Field(0, description="分类ID")
    ruleType: str = Field("BLOCK",
                          description="BLOCK/REPLACE")
    patternType: str = Field("EXACT",
                             description="EXACT/REGEX")
    patternValue: str = Field(..., min_length=1,
                              description="敏感词/正则")
    replaceValue: str = Field("", description="替换文本")
    customBlockResponse: str = Field(
        "", description="自定义拦截话术(优先分类默认)")
    riskLevel: int = Field(2, description="1低/2中/3高")
    priority: int = Field(100, description="优先级")
    status: int = Field(0, description="0草稿/1已发布")
    updatedBy: str = Field("", description="操作人")


class RulePatch(BaseModel):
    categoryId: int | None = None
    ruleType: str | None = None
    patternType: str | None = None
    patternValue: str | None = None
    replaceValue: str | None = None
    customBlockResponse: str | None = None
    riskLevel: int | None = None
    priority: int | None = None
    status: int | None = None
    updatedBy: str = Field("", description="操作人")


class RuleBatch(BaseModel):
    categoryId: int = Field(..., description="分类ID")
    ruleType: str = Field("BLOCK")
    words: str = Field(..., min_length=1,
                       description="词列表(逗号/换行分隔)")
    riskLevel: int = Field(2)
    updatedBy: str = Field("")


class AllowCreate(BaseModel):
    ruleId: int = Field(..., description="关联规则ID")
    allowPattern: str = Field(..., min_length=1,
                              description="豁免词")
    matchType: str = Field("CONTAINS",
                           description="CONTAINS/REGEX")


class FeedbackBody(BaseModel):
    feedbackStatus: int = Field(...,
                                description="1确认违规/2误杀")
    feedbackBy: str = Field("", description="复核人")


class PublishBody(BaseModel):
    by: str = Field("", description="发布人")


class SandboxBody(BaseModel):
    text: str = Field(..., min_length=1,
                      description="待测文本")


# ============================================================
# 大盘(Overview)
# ============================================================

@router.get("/overview")
async def overview(
        x_role: str = Header(
            default=None, alias="X-Role")):
    """规则大盘: 拦截统计/Top10/误杀率/待复核/生效版本"""
    try:
        _require_admin(x_role)
        repo = _repo()
        from core.helpers import ts as _ts
        today = str(_ts())[:10]
        hits = await repo.list_hits(limit=500)
        today_hits = [h for h in hits
                      if str(h.get("hitTime", ""))
                      .startswith(today)]
        word_count: dict[str, int] = {}
        for h in hits:
            w = h.get("ruleWord", "")
            if w:
                word_count[w] = \
                    word_count.get(w, 0) + 1
        top10 = sorted(
            word_count.items(), key=lambda kv: -kv[1]
        )[:10]
        reviewed = [h for h in hits
                    if h.get("feedbackStatus", 0) > 0]
        false_pos = [h for h in reviewed
                     if h.get("feedbackStatus") == 2]
        published = await repo.get_published() or {}
        rules = await repo.list_rules()
        return {"code": 0, "data": {
            "todayHits": len(today_hits),
            "totalHits": len(hits),
            "topWords": [
                {"word": w, "count": c}
                for w, c in top10],
            "reviewed": len(reviewed),
            "falsePositive": len(false_pos),
            "falsePositiveRate": round(
                len(false_pos) / len(reviewed) * 100, 2)
            if reviewed else 0.0,
            "pendingReview": len(hits) - len(reviewed),
            "published": published,
            "ruleStats": {
                "total": len(rules),
                "published": len(
                    [r for r in rules
                     if r.get("status") == 1]),
                "draft": len(
                    [r for r in rules
                     if r.get("status") == 0]),
            },
        }}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


# ============================================================
# 规则 CRUD
# ============================================================

@router.get("/rules")
async def list_rules(
        categoryId: int = 0,
        status: int = -1,
        ruleType: str = "",
        x_role: str = Header(
            default=None, alias="X-Role")):
    try:
        _require_admin(x_role)
        return {"code": 0, "data": await _repo()
                .list_rules(category_id=categoryId,
                            status=status,
                            rule_type=ruleType)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.post("/rules")
async def create_rule(
        body: RuleCreate,
        x_role: str = Header(
            default=None, alias="X-Role")):
    try:
        _require_admin(x_role)
        if body.ruleType not in ("BLOCK", "REPLACE"):
            raise ValueError("ruleType 须 BLOCK/REPLACE")
        if body.ruleType == "REPLACE" \
                and not body.replaceValue:
            raise ValueError("REPLACE 须提供 replaceValue")
        rec = await _repo().create_rule(
            body.model_dump())
        return {"code": 0, "data": rec}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.put("/rules/{rule_id}")
async def update_rule(
        rule_id: int,
        body: RulePatch,
        x_role: str = Header(
            default=None, alias="X-Role")):
    try:
        _require_admin(x_role)
        patch = {k: v for k, v in
                 body.model_dump().items()
                 if v is not None}
        rec = await _repo().update_rule(rule_id, patch)
        if not rec:
            raise KeyError("rule")
        return {"code": 0, "data": rec}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.delete("/rules/{rule_id}")
async def delete_rule(
        rule_id: int,
        x_role: str = Header(
            default=None, alias="X-Role")):
    try:
        _require_admin(x_role)
        ok = await _repo().delete_rule(rule_id)
        if not ok:
            raise KeyError("rule")
        return {"code": 0, "data": {"deleted": rule_id}}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.post("/rules/batch")
async def batch_rules(
        body: RuleBatch,
        x_role: str = Header(
            default=None, alias="X-Role")):
    """批量导入(逗号/换行分隔; 与库内词去重后入库草稿)"""
    try:
        _require_admin(x_role)
        repo = _repo()
        words = [w.strip() for w in
                 body.words.replace(
                     "\n", ",").split(",")
                 if w.strip()]
        if not words:
            raise ValueError("无有效词")
        existing = {
            r.get("patternValue", "")
            for r in await repo.list_rules()}
        created, skipped = [], []
        for w in words:
            if w in existing:
                skipped.append(w)
                continue
            rec = await repo.create_rule({
                "categoryId": body.categoryId,
                "ruleType": body.ruleType,
                "patternType": "EXACT",
                "patternValue": w,
                "riskLevel": body.riskLevel,
                "status": 0,
                "updatedBy": body.updatedBy,
            })
            created.append(rec["id"])
        return {"code": 0, "data": {
            "created": len(created),
            "skippedDuplicates": skipped,
        }}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


# ============================================================
# 白名单(误杀豁免)
# ============================================================

@router.get("/allowlist")
async def list_allowlist(
        ruleId: int = 0,
        x_role: str = Header(
            default=None, alias="X-Role")):
    try:
        _require_admin(x_role)
        return {"code": 0, "data": await _repo()
                .list_allowlist(rule_id=ruleId)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.post("/allowlist")
async def create_allowlist(
        body: AllowCreate,
        x_role: str = Header(
            default=None, alias="X-Role")):
    try:
        _require_admin(x_role)
        rule = await _repo().get_rule(body.ruleId)
        if not rule:
            raise KeyError("rule")
        rec = await _repo().create_allowlist(
            body.model_dump())
        return {"code": 0, "data": rec}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.delete("/allowlist/{al_id}")
async def delete_allowlist(
        al_id: int,
        x_role: str = Header(
            default=None, alias="X-Role")):
    try:
        _require_admin(x_role)
        ok = await _repo().delete_allowlist(al_id)
        if not ok:
            raise KeyError("allowlist")
        return {"code": 0, "data": {"deleted": al_id}}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


# ============================================================
# 命中日志 + 打标闭环(Evolution Engine 数据源)
# ============================================================

@router.get("/hits")
async def list_hits(
        feedbackStatus: int = -1,
        direction: str = "",
        limit: int = 100,
        x_role: str = Header(
            default=None, alias="X-Role")):
    try:
        _require_admin(x_role)
        return {"code": 0, "data": await _repo()
                .list_hits(
                    feedback_status=feedbackStatus,
                    direction=direction,
                    limit=min(limit, 500))}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.post("/hits/{hit_id}/feedback")
async def feedback_hit(
        hit_id: int,
        body: FeedbackBody,
        x_role: str = Header(
            default=None, alias="X-Role")):
    """人工打标: 1 确认违规 / 2 误杀

    误杀时响应附 suggestAllowlist(命中词+原文摘录)——
    运营一键加白名单(POST /allowlist)后发布即豁免。
    """
    try:
        _require_admin(x_role)
        if body.feedbackStatus not in (1, 2):
            raise ValueError(
                "feedbackStatus 须 1(确认违规)/2(误杀)")
        rec = await _repo().feedback_hit(
            hit_id, body.feedbackStatus,
            body.feedbackBy)
        if not rec:
            raise KeyError("hit")
        data = {"hit": rec, "suggestAllowlist": None}
        if body.feedbackStatus == 2:
            data["suggestAllowlist"] = {
                "ruleWord": rec.get("ruleWord", ""),
                "originalExcerpt":
                    (rec.get("originalText", "")
                     or "")[:60],
                "hint": "建议评估为该词添加白名单豁免"
                        "后 POST /publish 生效",
            }
        return {"code": 0, "data": data}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


# ============================================================
# 发布(热更新) + 沙箱
# ============================================================

@router.post("/publish")
async def publish(
        body: PublishBody,
        x_role: str = Header(
            default=None, alias="X-Role")):
    """发布规则: status=1 组装快照 → 热更新键 → Pub/Sub
    广播(引擎即时重建 DFA; 丢消息 60s 轮询自愈)"""
    try:
        _require_admin(x_role)
        result = await _repo().publish(by=body.by)
        return {"code": 0,
                "data": result["snapshot"]}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc


@router.post("/sandbox")
async def sandbox(
        body: SandboxBody,
        x_role: str = Header(
            default=None, alias="X-Role")):
    """规则测试沙箱: 输入文本 → 命中词/分类/话术/替换
    后输出(配置防错闸——发布前模拟验证, 引擎当前词库态)"""
    try:
        _require_admin(x_role)
        from services.local_guardrail_service import (
            get_guardrail,
        )
        gr = get_guardrail()
        verdict = gr.check_input(body.text)
        output = gr.filter_output(body.text)
        # 原文定位命中词(高亮定位——变体穿透场景定位
        # 不到时 span 为 -1, 前端按词渲染)
        spans = []
        start = 0
        for w in ([verdict["word"]]
                  if verdict["word"] else []):
            idx = body.text.find(w, start)
            spans.append({
                "word": w, "span": [
                    idx, idx + len(w)]
                if idx >= 0 else [-1, -1]})
        return {"code": 0, "data": {
            "input": body.text,
            "blocked": verdict["blocked"],
            "category": verdict["category"],
            "word": verdict["word"],
            "response": verdict["response"],
            "finalOutput": output,
            "highlights": spans,
            "engineVersion": gr._rules_version,
        }}
    except HTTPException:
        raise
    except Exception as exc:
        raise _map(exc) from exc
