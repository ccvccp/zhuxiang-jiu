"""全局异常处理器"""

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from fastapi import status as http_status

from models import DecisionErrorCode


async def http_exception_handler(request, exc: HTTPException):
    """HTTP 异常统一格式化"""
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.detail if isinstance(exc.detail, dict) else {
            "success": False,
            "error": str(exc.detail),
        },
    )


async def general_exception_handler(request, exc: Exception):
    """未捕获异常兜底处理"""
    return JSONResponse(
        status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "success": False,
            "error": f"DECISION_010: 内部错误 - {exc}",
            "errorCode": DecisionErrorCode.e010.value,
        },
    )


def register_exception_handlers(app):
    """注册异常处理器到 FastAPI app"""
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(Exception, general_exception_handler)
