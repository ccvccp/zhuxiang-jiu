"""全局异常处理器"""

import logging
import traceback

from fastapi import HTTPException
from fastapi.requests import Request
from fastapi.responses import JSONResponse
from fastapi import status as http_status

logger = logging.getLogger(__name__)


async def http_exception_handler(request: Request, exc: HTTPException):
    """HTTP 异常统一格式化"""
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.detail if isinstance(exc.detail, dict) else {
            "success": False,
            "error": str(exc.detail),
        },
    )


async def general_exception_handler(request: Request, exc: Exception):
    """未捕获异常兜底处理

    统一返回 500, 不向客户端泄露内部堆栈(安全),
    仅在服务端日志记录完整 traceback(可追溯)。
    """
    logger.error(
        "未捕获异常 path=%s method=%s: %s\n%s",
        request.url.path,
        request.method,
        exc,
        traceback.format_exc(),
    )
    return JSONResponse(
        status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "success": False,
            "error": "内部服务器错误",
            "path": request.url.path,
        },
    )


def register_exception_handlers(app):
    """注册异常处理器到 FastAPI app"""
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(Exception, general_exception_handler)
