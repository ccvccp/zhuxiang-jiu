"""认证与角色权限守卫"""

from typing import Annotated, Optional

from fastapi import Depends, Header, HTTPException, status

from core.config import ROLE_LEVELS
from models import DecisionErrorCode


def get_current_role(
    x_role: Annotated[Optional[str], Header(alias="X-Role")] = None,
    authorization: Annotated[Optional[str], Header()] = None,
) -> str:
    """从请求头提取角色,Mock 模式不校验 token"""
    if x_role and x_role in ROLE_LEVELS:
        return x_role
    # Mock 模式: 未提供角色默认 guest
    return "guest"


def require_role(min_role: str):
    """角色权限守卫工厂,返回依赖函数"""
    min_level = ROLE_LEVELS.get(min_role, 0)

    def _check(role: str = Depends(get_current_role)) -> str:
        if ROLE_LEVELS.get(role, 0) < min_level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "success": False,
                    "error": "DECISION_003: 角色权限不足",
                    "errorCode": DecisionErrorCode.e003.value,
                },
            )
        return role

    return _check
