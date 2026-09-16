"""网站条款及角色协议管理模块路由(15 端点)

鉴权:
    - 用户端(2 接口): 同意条款/检查同意(X-Member-Id)
    - 管理端(8 接口): X-Role: admin 头(条款CRUD/发布/角色协议/统计)

大模型二代·三态灰度(AGREEMENT_MODE, 全站范式):
    - 决策面(4 POST): @/_decision 门控——off 拒绝(409)
      + shadow/assist 响应标记(agreementMode)
    - 用户同意权豁免(1): consent——永不关停
      (签署条款是用户法律行为)
    - 观测面(6 GET): 永不关停
    - 控制面(4): mode/override/guard/resume

异常映射:
    - KeyError → 404(条款/协议不存在)
    - ValueError → 409(状态冲突/未发布/重复)
    - 权限校验 → 403(无权操作)

端点分布:
    - 条款(3):  创建/列表/详情
    - 发布(2):  发布生效/新版本
    - 同意(2):  用户同意/查询同意记录
    - 角色协议(2): 创建/列表
    - 历史(1):  条款历史版本
    - 统计(1):  管理端统计
    - 控制面(4): mode/override/guard/resume
"""


from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel as PydBaseModel, Field

from services.agreement_service import AgreementService


router = APIRouter()
_service = AgreementService()


# ============================================================
# 鉴权与异常映射辅助
# ============================================================

def _require_member_id(x_member_id: str | None) -> str:
    if not x_member_id:
        raise HTTPException(status_code=401, detail="未登录: 请提供 X-Member-Id 头")
    return x_member_id


def _require_admin(x_role: str | None):
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


def _map_key_error(exc: KeyError) -> HTTPException:
    msg = str(exc) if str(exc) else "资源不存在"
    if msg.startswith("'") and msg.endswith("'"):
        msg = msg[1:-1]
    return HTTPException(status_code=404, detail=msg)


def _map_value_error(exc: ValueError) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


def _handle(exc: Exception):
    if isinstance(exc, KeyError):
        raise _map_key_error(exc)
    if isinstance(exc, ValueError):
        raise _map_value_error(exc)
    raise HTTPException(status_code=500, detail=str(exc))


# ============================================================
# 大模型二代·三态灰度门控(AGREEMENT_MODE, 全站范式)
# ============================================================

async def _gate() -> dict:
    """决策面门槛(AGREEMENT_MODE=off → 409;
    shadow/assist 放行——大模型二代读取链:
    护栏暂停 > 运行时 override > env)"""
    from services.agreement_mode_service import (
        AgreementModeService,
    )
    return await AgreementModeService() \
        .require_decision_mode()


def _decision(fn=None, *, admin=True):
    """决策端点装饰器: 门控(off 409) +
    shadow/assist 标记(agreementMode)

    参数(鉴权优先——403 before 409, 小竹/钱包/信用范式):
        admin=True(默认) 管理端(X-Role 非 admin
                       放行函数体触发 403)

    用户同意权(consent)不加本装饰器——签署条款是
    用户法律行为, 永不关停。
    观测面(GET)不加——永不关停。
    """
    import functools
    from services.agreement_mode_service import (
        MODE_VALUES,
    )

    def deco(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            # 鉴权优先: 无效/缺失凭据放行
            # 函数体触发 403——不预判门控
            x_role = kwargs.get("x_role")
            gate_needed = not (admin and x_role != "admin")
            mode_state = None
            if gate_needed:
                try:
                    mode_state = await _gate()
                except ValueError as e:
                    raise HTTPException(
                        status_code=409,
                        detail=str(e)) from e
            result = await fn(*args, **kwargs)
            if isinstance(result, dict) \
                    and mode_state \
                    and mode_state.get("mode") \
                    in MODE_VALUES[1:]:
                result = {**result,
                          "agreementMode":
                              mode_state["mode"]}
            return result
        return wrapper

    return deco(fn) if fn else deco


# ============================================================
# 请求模型
# ============================================================

class CreateAgreementRequest(PydBaseModel):
    agreementNo: str = Field(..., description="条款编号(T01/T02...)")
    name: str = Field(..., description="条款名称")
    type: str = Field("term", description="类型: term/rule/contract")
    applicableRole: str = Field("user", description="适用角色: user/member/agent/merchant")
    content: str = Field("", description="条款内容")
    legalBasis: str = Field("", description="法律依据")
    changeLog: str = Field("", description="变更说明")


class PublishRequest(PydBaseModel):
    effectiveDate: str | None = None


class NewVersionRequest(PydBaseModel):
    content: str = Field(..., description="新版本内容")
    changeLog: str = Field("", description="变更说明")


class ConsentRequest(PydBaseModel):
    signMethod: str = Field("checkbox", description="签署方式: checkbox/popup/e-sign")
    ip: str = Field("", description="IP地址")
    device: str = Field("", description="设备信息")


class CreateProtocolRequest(PydBaseModel):
    role: str = Field(..., description="角色: user/member/agent/merchant")
    agreementId: int = Field(..., description="条款ID")
    required: bool = Field(True, description="是否必须同意")


# ============================================================
# 条款接口(3)
# ============================================================

@router.post("/api/agreements", tags=["条款协议模块"])
@_decision
async def create_agreement(
    data: CreateAgreementRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """创建条款(草稿状态)(管理端)"""
    _require_admin(x_role)
    try:
        result = await _service.create_agreement(
            agreement_no=data.agreementNo, name=data.name,
            atype=data.type, applicable_role=data.applicableRole,
            content=data.content, legal_basis=data.legalBasis,
            change_log=data.changeLog,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/agreements", tags=["条款协议模块"])
async def list_agreements(
    status: str = Query(None, description="按状态筛选"),
    type: str = Query(None, description="按类型筛选"),
    role: str = Query(None, description="按角色筛选"),
    limit: int = Query(100, ge=1, le=500, description="查询条数"),
):
    """查询条款列表(公开)"""
    try:
        result = await _service.list_agreements(status, type, role, limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


# 条款详情路由移至文件后部(所有单段字面路径之后)——
# 防 int 路径参数先注册吞掉 consents/role-protocols/
# mode 等字面量(422 路由冲突, 存量缺陷修复)





# ============================================================
# 发布与新版本接口(2)
# ============================================================

@router.post("/api/agreements/{agreement_id}/publish", tags=["条款协议模块"])
@_decision
async def publish_agreement(
    agreement_id: int,
    data: PublishRequest = None,
    x_role: str = Header(None, alias="X-Role"),
):
    """条款生效(发布)(管理端)"""
    _require_admin(x_role)
    try:
        effective_date = data.effectiveDate if data else None
        result = await _service.publish_agreement(agreement_id, effective_date)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.post("/api/agreements/{agreement_id}/versions", tags=["条款协议模块"])
@_decision
async def new_version(
    agreement_id: int,
    data: NewVersionRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """创建新版本(版本递增)(管理端)"""
    _require_admin(x_role)
    try:
        result = await _service.new_version(agreement_id, data.content,
                                              data.changeLog)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 用户同意接口(2)
# ============================================================

@router.post("/api/agreements/{agreement_id}/consent", tags=["条款协议模块"])
async def consent(
    agreement_id: int,
    data: ConsentRequest = None,
    x_member_id: str = Header(None, alias="X-Member-Id"),
):
    """用户同意(签署)条款(用户端)"""
    user_id = int(_require_member_id(x_member_id))
    try:
        sign_method = data.signMethod if data else "checkbox"
        ip = data.ip if data else ""
        device = data.device if data else ""
        result = await _service.consent(user_id, agreement_id,
                                         sign_method=sign_method,
                                         ip=ip, device=device)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/agreements/consents", tags=["条款协议模块"])
async def list_consents(
    user_id: int = Query(None, alias="user_id", description="按用户筛选"),
    agreement_id: int = Query(None, alias="agreement_id", description="按条款筛选"),
    limit: int = Query(100, ge=1, le=500, description="查询条数"),
    x_role: str = Header(None, alias="X-Role"),
):
    """查询同意记录(管理端)

    注: 本路由须先于 /{agreement_id} 注册——防 int 路径
    参数吞字面量(422 路由冲突, 存量缺陷修复)。
    """
    _require_admin(x_role)
    try:
        result = await _service.list_consents(user_id, agreement_id, limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


# ============================================================
# 角色协议接口(2)
# ============================================================

@router.post("/api/agreements/role-protocols", tags=["条款协议模块"])
@_decision
async def create_protocol(
    data: CreateProtocolRequest,
    x_role: str = Header(None, alias="X-Role"),
):
    """创建角色协议(关联条款与角色)(管理端)"""
    _require_admin(x_role)
    try:
        result = await _service.create_protocol(
            role=data.role, agreement_id=data.agreementId,
            required=data.required,
        )
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


@router.get("/api/agreements/role-protocols", tags=["条款协议模块"])
async def list_protocols(
    role: str = Query(None, description="按角色筛选"),
    status: str = Query(None, description="按状态筛选"),
    limit: int = Query(100, ge=1, le=500, description="查询条数"),
):
    """查询角色协议列表(公开)"""
    try:
        result = await _service.list_protocols(role, status, limit)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


# ============================================================
# 历史版本接口(1)
# ============================================================

@router.get("/api/agreements/{agreement_id}/history", tags=["条款协议模块"])
async def get_history(
    agreement_id: int,
):
    """查询条款历史版本"""
    try:
        result = await _service.get_version_history(agreement_id)
        return {"success": True, "data": result, "count": len(result)}
    except Exception as e:
        _handle(e)


# ============================================================
# 统计接口(1)
# ============================================================

@router.get("/api/agreements/stats/overview", tags=["条款协议模块"])
async def get_stats(
    x_role: str = Header(None, alias="X-Role"),
):
    """条款模块总览统计(管理端)"""
    _require_admin(x_role)
    try:
        result = await _service.get_stats()
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


# ============================================================
# 控制面(4, admin 门禁——大模型二代全站范式)
# ============================================================

@router.get("/api/agreements/mode", tags=["条款协议模块"])
async def agreement_mode_status(
    x_role: str = Header(None, alias="X-Role")):
    """灰度总览(模式/读取链/护栏/红线公示——观测面永不关停)"""
    _require_admin(x_role)
    from services.agreement_mode_service import (
        AgreementModeService,
    )
    return await AgreementModeService().status_view()


@router.post("/api/agreements/mode/override",
             tags=["条款协议模块"])
async def agreement_mode_override(
    data: dict = None,
    x_role: str = Header(None, alias="X-Role")):
    """运行时切档(免容器重建; 空 mode=清除 override)"""
    _require_admin(x_role)
    from services.agreement_mode_service import (
        AgreementModeService,
    )
    body = data or {}
    try:
        result = await AgreementModeService().set_override(
            str(body.get("mode") or ""),
            operator="admin")
        return {"success": True, "data": result}
    except ValueError as e:
        raise HTTPException(status_code=409,
                             detail=str(e)) from e


@router.post("/api/agreements/mode/guard",
              tags=["条款协议模块"])
async def agreement_mode_guard(
    data: dict = None,
    x_role: str = Header(None, alias="X-Role")):
    """护栏检查(三指标恶化 >3% 自动暂停)

    body: {agreementInactiveRate, protocolInactiveRate,
           versionMismatchRate, baseline?{同三键}}
    ——缺省时按条款协议仓储实时聚合(确定性, LLM 禁入)。
    """
    _require_admin(x_role)
    from services.agreement_mode_service import (
        AgreementModeService,
    )
    if not isinstance(data, dict) or not data:
        # 缺省: 巡检聚合(确定性)
        from services.agreement_scheduler import (
            run_guard_patrol,
        )
        r = await run_guard_patrol()
        return {"success": True,
                "data": {"metrics": r["metrics"],
                         "samples": r["samples"],
                         "breached": r["breached"],
                         "breaches": r["breaches"],
                         "pausedNow": r["pausedNow"]}}
    try:
        result = await AgreementModeService().guard_check(
            float(data.get("agreementInactiveRate") or 0),
            float(data.get("protocolInactiveRate") or 0),
            float(data.get("versionMismatchRate") or 0),
            baseline=data.get("baseline"))
        return {"success": True, "data": result}
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=409,
                             detail=str(e)) from e


@router.post("/api/agreements/mode/resume",
             tags=["条款协议模块"])
async def agreement_mode_resume(
    note: str = Query("", description="恢复备注(决策留痕)"),
    x_role: str = Header(None, alias="X-Role")):
    """人工恢复(护栏暂停解除——决策留痕)"""
    _require_admin(x_role)
    from services.agreement_mode_service import (
        AgreementModeService,
    )
    try:
        result = await AgreementModeService().resume(
            operator="admin", note=note)
        return {"success": True, "data": result}
    except ValueError as e:
        raise HTTPException(status_code=409,
                             detail=str(e)) from e


@router.get("/api/agreements/{agreement_id}", tags=["条款协议模块"])
async def get_agreement(
    agreement_id: int,
):
    """查询条款详情

    注: 位于全部单段字面路径(consents/role-protocols/
    mode 及控制面)之后注册——防 int 路径参数吞字面量
    (422 路由冲突, 存量缺陷修复)。
    """
    try:
        result = await _service.get_agreement(agreement_id)
        return {"success": True, "data": result}
    except Exception as e:
        _handle(e)


def register_agreement_routes(app):
    """注册条款协议管理模块路由"""
    app.include_router(router)
