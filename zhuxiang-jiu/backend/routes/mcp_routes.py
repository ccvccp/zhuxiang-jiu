"""MCP 只读工具面路由(小竹蓝图 P4-⑧)

端点:
    - POST /mcp          单个 JSON-RPC 2.0 请求(外部超级入口
                         调用面; off 默认 403, Token 强制)
    - GET  /api/mcp/status  admin 白名单现状+计数概览(X-Role)

鉴权:
    - /mcp: MCP_SERVER_MODE=on + X-MCP-Token 匹配 MCP_SERVER_TOKEN
      (未配置 Token 时拒绝所有调用——error 明示需配置);
      开关/鉴权拒绝统一 403 + JSON-RPC error 说明
    - status: X-Role: admin(对齐 live_qa_routes._require_admin)

fail-soft: /mcp 全链异常均转 JSON-RPC error(含 -32700/-32600),
绝不 500。
"""

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from services.mcp_server_service import (
    AUTH_ERROR, check_token, handle_jsonrpc,
    list_tools_for_admin, mcp_enabled,
)

router = APIRouter()


def _require_admin(x_role: str | None):
    """校验管理员权限, 失败返回 403(对齐 live_qa_routes 写法)"""
    if x_role != "admin":
        raise HTTPException(status_code=403,
                            detail="需要管理员权限")


def _auth_rejected(message: str) -> JSONResponse:
    """开关/鉴权拒绝统一形态: 403 + JSON-RPC error 说明"""
    return JSONResponse(
        status_code=403,
        content={"jsonrpc": "2.0", "id": None,
                 "error": {"code": AUTH_ERROR,
                           "message": message}},
    )


@router.post("/mcp", tags=["MCP只读工具面"])
async def mcp_endpoint(
    request: Request,
    x_mcp_token: str = Header("", alias="X-MCP-Token"),
):
    """MCP Streamable HTTP 只读工具面(单请求; batch → -32600)

    方法: initialize / tools/list / tools/call;
    未知 -32601, 解析失败 -32700, 非法请求 -32600。
    """
    # ① off 默认(统一 403+说明)
    if not mcp_enabled():
        return _auth_rejected(
            "MCP 服务未开启: MCP_SERVER_MODE=off(默认 off, "
            "须显式配置 on 方可对外)")
    # ② Token 强制(未配置拒绝所有; 比对防时序)
    ok, message = check_token(x_mcp_token)
    if not ok:
        return _auth_rejected(f"MCP 鉴权失败: {message}")
    # ③ JSON-RPC(裸 body 自解析——解析失败转 -32700 不 422/500)
    body = await request.body()
    resp = await handle_jsonrpc(
        body.decode("utf-8", "replace"))
    return JSONResponse(resp)


@router.get("/api/mcp/status", tags=["MCP只读工具面"])
async def mcp_status(
    x_role: str = Header(None, alias="X-Role"),
):
    """MCP 面状态(admin; 模式/Token 配置态/白名单工具数
    与清单/进程级审计计数概览——Token 只报是否配置不回显)"""
    _require_admin(x_role)
    return {"code": 0, "msg": "ok",
            "data": list_tools_for_admin()}


def register_mcp_routes(app) -> None:
    """注册路由(遵循项目注册模式)"""
    app.include_router(router)
