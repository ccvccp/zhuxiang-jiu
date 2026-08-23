"""AI 语义评分层·第三批路由(1 端点)

为最后一个 B 级模块(30用户认证)暴露 AI 登录风控评分接口。

鉴权:
    - 内部/管理端能力: X-Role: admin 头(与其余 13 个评分端点约定一致)

端点:
    POST /api/ai-scoring/auth-risk  认证风控评分(登录决策)

混合模式说明: 确定性安全核心(JWT 校验/密码哈希/令牌吊销)保持规则引擎;
本评分器仅作用于登录决策(放行/二次验证/强核验/拦截), 可由 auth_service.login
在密码校验通过后调用, 也可由风控系统独立调用。
"""

from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel as PydBaseModel, Field

from services.ai_scoring_auth_service import AuthRiskScorer

router = APIRouter()

_auth_scorer = AuthRiskScorer()


# ============================================================
# 鉴权与异常映射(对齐项目约定)
# ============================================================

def _require_admin(x_role: Optional[str]):
    if x_role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")


def _handle(exc: Exception):
    """ValueError → 409, 其余 → 500"""
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=409, detail=str(exc))
    raise HTTPException(status_code=500, detail=str(exc))


# ============================================================
# 请求模型
# ============================================================

class AuthRiskRequest(PydBaseModel):
    failedAttempts: Optional[int] = Field(None, ge=0, description="近1小时登录失败次数")
    distanceKm: Optional[float] = Field(None, ge=0, description="与上次登录地距离(公里)")
    hoursSinceLastLogin: Optional[float] = Field(None, ge=0, description="距上次登录小时数")
    newDevice: Optional[bool] = Field(None, description="是否新设备")
    ipRiskType: Optional[str] = Field(None, description="IP 信誉(clean/proxy/vpn/tor/blacklist)")
    loginHour: Optional[int] = Field(None, ge=0, le=23, description="登录小时(0-23)")
    accountAgeDays: Optional[float] = Field(None, ge=0, description="账户年龄(天)")
    passwordStatus: Optional[str] = Field(None, description="密码状态(strong/medium/weak/breached)")
    behaviorDeviationScore: Optional[float] = Field(None, ge=0, le=100, description="行为偏离度(0-100)")


# ============================================================
# 端点
# ============================================================

@router.post("/api/ai-scoring/auth-risk", tags=["AI语义评分层"])
async def score_auth_risk(data: AuthRiskRequest,
                          x_role: Optional[str] = Header(None, alias="X-Role")):
    """认证风控评分(模块30): 8因子 → 风险分 → allow/step_up/challenge/block

    黑名单 IP / 已泄露密码命中硬约束时无论总分直接拦截(hardBlocked=true)。
    """
    _require_admin(x_role)
    try:
        return await _auth_scorer.score(data.model_dump(exclude_none=True))
    except Exception as exc:
        _handle(exc)


def register_ai_scoring_auth_routes(app) -> None:
    """注册 AI 语义评分层·第三批路由"""
    app.include_router(router)
