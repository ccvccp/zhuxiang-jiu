"""MCP(Model Context Protocol) 只读工具面
(小竹蓝图 P4-⑧ 与商城蓝图 V4.0 汇合项)

定位(docs/小竹AgentAI升级蓝图与现有实现对照分析.md §五 P4-8):
    把小竹 FC 注册表(services/xiaozhu_fc_registry)的
    **零隐私只读工具**暴露为标准 MCP tools, 服务外部超级
    入口(微信/车机/第三方 Agent)调试接入——FC 注册表本质是
    「私有协议的 MCP」, 此处只包一层标准协议面, 内部注册表
    与三重校验管道不动。

铁律(对齐项目纪律):
    - 先只读: 仅暴露零隐私只读子集(白名单制, 下方逐条注释)
    - 零新依赖: 自研 JSON-RPC 2.0 handler(不引 mcp SDK)
    - off 默认: MCP_SERVER_MODE=off 时端点 403(新能力 off 默认)
    - Token 强制: MCP_SERVER_TOKEN 未配置 → 拒绝所有调用;
      比对用 hmac.compare_digest(防时序攻击)
    - 零 member 语境: MCP 会话不携带会员上下文——白名单本就
      零隐私, 个体会员类工具(trust.*/privacy.budget/
      voice.score/explanation.report)一律不进白名单
    - fail-soft: 一切错误转 JSON-RPC error/isError, 不抛 HTTP 500

JSON-RPC 方法面(最小集):
    initialize / tools/list / tools/call; 未知方法 -32601;
    解析失败 -32700; 非法请求 -32600(含 batch——single only)。

审计路径选择(现场读 xiaozhu_fc_gateway 后的决策):
    不复用 call_tool——其只读分支(L79-84)只落审计并返回
    {"executed": False, "readonly": True} 标记, 根本不触达
    executor; 本面要求走 executor 沙箱只读直达(绕过 gateway
    的 member 语境), 故执行走 get_executor().execute(),
    审计直接调 XiaozhuFcGateway._audit(六字段铁律原样继承,
    sessionId 落 "mcp:<hex>" 通道标记, memberId=0)。
"""

import hmac
import json
import logging
import os
import time
import uuid

from services.xiaozhu_fc_registry import (
    TOOL_REGISTRY, TIER_READONLY, tool_actions,
)
from services.xiaozhu_executor import SAFE_READONLY, get_executor

logger = logging.getLogger("mcp_server")

# MCP 协议版本(Streamable HTTP 最小面——2025-03-26 规范)
PROTOCOL_VERSION = "2025-03-26"
SERVER_NAME = "zhuxiang-mcp"
SERVER_VERSION = "1.0.0"

# JSON-RPC 2.0 错误码(标准 + 服务端自定义 -32001 鉴权/开关)
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
AUTH_ERROR = -32001

# 进程级调用计数(status 端点概览用; 重启归零)
_STATS: dict = {"requests": 0, "ok": 0, "toolErrors": 0,
                "rpcErrors": 0}


# ============================================================
# 开关与 Token 鉴权(off 默认 + 强制 Token)
# ============================================================

def mcp_enabled() -> bool:
    """模式闸: MCP_SERVER_MODE=off|on, 默认 off(新能力 off 默认)"""
    mode = os.environ.get(
        "MCP_SERVER_MODE", "off").strip().lower()
    return mode in ("1", "on", "true")


def check_token(provided: str) -> tuple:
    """Token 鉴权(未配置→拒绝所有; compare_digest 防时序)

    Returns:
        (通过与否, 拒绝说明)
    """
    expected = os.environ.get(
        "MCP_SERVER_TOKEN", "").strip()
    if not expected:
        return False, ("MCP_SERVER_TOKEN 未配置——"
                       "拒绝所有 MCP 调用(须配置后重启生效)")
    provided = str(provided or "")
    # utf-8 bytes 比对(compare_digest 对非 ASCII str 会抛
    # TypeError——token 统一走 bytes 面)
    if not provided or not hmac.compare_digest(
            provided.encode("utf-8"),
            expected.encode("utf-8")):
        return False, "X-MCP-Token 无效或缺失"
    return True, ""


# ============================================================
# 零隐私只读工具白名单(人工甄别——21 条 readonly 逐条)
# ============================================================
# 甄别标准: 不涉及个体用户数据(产品/活动/帮助/知识/门店/
# 检索/纯函数测算类可暴露; 会员资产/隐私/归因类不暴露)。
# 最终生效面 = (内置白名单 ∪ MCP_TOOLS env 追加)
#              ∩ tool_actions() ∩ SAFE_READONLY 三重交集。
MCP_TOOL_WHITELIST: tuple = (
    # —— 产品/活动域(公共目录与全局配置, 零个体数据) ——
    "product.new",      # 新品列表——公共商品目录
    "product.price",    # 产品价格——公共商品目录
    "promo.query",      # 优惠活动——全局活动配置
    # —— 帮助/导航域(静态文案与路径映射, 零个体数据) ——
    "xiaozhu.help",     # 能力自描述——静态文案
    "nav.page",         # 页面导航——纯路径映射(外部入口所需)
    # —— 酒知识四问(公共知识/检测典藏/聚合统计) ——
    "wine.verify",      # 验真伪/质检——公共检测报告典藏引证
    "wine.craft",       # 工艺故事——公共工艺知识
    "wine.recommend",   # 场景荐酒——产品目录匹配(不读个体)
    "wine.reviews",     # 评论精华——聚合统计(好评率/高频词)
    # —— 门店/检索/测算域(公共 POI/全站检索/纯函数) ——
    "map.nearby",       # 附近门店——公共智图 POI 库
    "zs.search",        # 智搜问答——全站统一检索(默认角色)
    "coupon.best_combo",  # 满减测算——确定性引擎纯函数零读
)
# 不暴露(9 条 readonly, 逐条理由):
#   trust.score/trust.balance/trust.exchange/trust.repair
#     —— 会员信值资产/违约修复语境(个体隐私)
#   privacy.budget/voice.score/explanation.report
#     —— 个体隐私预算/语音积分/写操作归因(个体隐私)
#   chat.human —— 会话侧转接动作(非查询语义, MCP 零会话
#     语境无意义且触发坐席资源)
#   trust.bind —— tier 虽 readonly(沙箱口径)但为写语义的
#     个体身份绑定操作, 绝不外暴露


def _extra_tools() -> tuple:
    """MCP_TOOLS env 追加白名单(逗号分隔)

    追加项仍须 registry 存在 + tier=readonly + 沙箱只读白名单
    三重校验——敏感/写/不存在一律拒绝(留 warning 不进白名单)。
    """
    raw = os.environ.get("MCP_TOOLS", "").strip()
    if not raw:
        return ()
    ok = []
    for name in (x.strip() for x in raw.split(",")):
        if not name:
            continue
        tool = TOOL_REGISTRY.get(name)
        if tool is None \
                or tool.get("tier") != TIER_READONLY \
                or name not in SAFE_READONLY:
            logger.warning(
                "mcp_extra_tool_reject %s"
                "(须 registry readonly + 沙箱只读白名单)", name)
            continue
        ok.append(name)
    return tuple(ok)


def current_whitelist() -> tuple:
    """生效白名单 = (内置 ∪ env 追加) ∩ registry ∩ 沙箱只读
    (三重交集——内置清单虽为人工甄别, 运行期仍自防式复核)"""
    actions = set(tool_actions())
    merged = set(MCP_TOOL_WHITELIST) | set(_extra_tools())
    return tuple(sorted(
        a for a in merged
        if a in actions and a in SAFE_READONLY
        and TOOL_REGISTRY[a].get("tier") == TIER_READONLY))


def _resolve_action(name: str):
    """MCP 工具名 → 白名单 action

    tools/list 以 operationId 为 MCP name; tools/call 同时
    兼容 action 原名(两种形态都必须命中白名单)。
    Returns: action 或 None(不在白名单)
    """
    whitelist = current_whitelist()
    for action in whitelist:
        if TOOL_REGISTRY[action]["operationId"] == name:
            return action
    if name in whitelist:
        return name
    return None


# ============================================================
# MCP 工具描述映射(tools/list)
# ============================================================

def _mcp_tools() -> list:
    """白名单工具映射 MCP tools 格式

    FC 条目无参数 schema——宽松 object 透传, description 保留
    注册表护栏文案(❌禁令+🔒privacy_cost 约束内化铁律)。
    """
    tools = []
    for action in current_whitelist():
        t = TOOL_REGISTRY[action]
        tools.append({
            "name": t["operationId"],
            "description": (
                f"{t['summary']}。{t['description']}"
                "(参数以小竹指令文档为准——inputSchema 为宽松 "
                "object, 参数原样透传)"),
            "inputSchema": {
                "type": "object", "properties": {},
                "additionalProperties": True,
            },
        })
    return tools


# ============================================================
# 审计(复用 49号 FC 网关六字段铁律——memberId=0 无个体语境)
# ============================================================

_GATEWAY = None


def _gateway():
    """FC 网关懒加载单例(仅用其 _audit 审计通道)"""
    global _GATEWAY
    if _GATEWAY is None:
        from services.xiaozhu_fc_gateway import XiaozhuFcGateway
        _GATEWAY = XiaozhuFcGateway()
    return _GATEWAY


# ============================================================
# JSON-RPC 2.0 自研 handler(fail-soft——错误不抛 HTTP)
# ============================================================

def _rpc_result(req_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": req_id,
            "result": result}


def _rpc_error(req_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id,
            "error": {"code": code, "message": message}}


async def _tools_call(req_id, params) -> dict:
    """tools/call: 白名单内 → executor 沙箱只读直达

    会话构造: 最小零隐私语境 {sessionId: "mcp:<hex>",
    channel: "mcp"}——不携带 member 上下文; execute 所需
    session 键已现场核实(只读分支不读 memberId, 幂等键走
    sessionId)。
    """
    if not isinstance(params, dict):
        params = {}
    name = params.get("name")
    arguments = params.get("arguments")
    if not isinstance(name, str) or not name.strip():
        return _rpc_error(
            req_id, INVALID_PARAMS,
            "Invalid params: 缺少工具 name")
    if not isinstance(arguments, dict):
        arguments = {}
    action = _resolve_action(name.strip())
    if action is None:
        _STATS["rpcErrors"] += 1
        return _rpc_error(
            req_id, INVALID_PARAMS,
            f"Invalid params: 工具 {name} 不在 MCP 白名单"
            "(只读零隐私子集——会员隐私/写/高敏工具均不暴露)")
    session = {"sessionId": f"mcp:{uuid.uuid4().hex[:12]}",
               "channel": "mcp"}
    started = time.monotonic()
    try:
        result = await get_executor().execute(
            session, action, arguments)
        latency = round(
            (time.monotonic() - started) * 1000, 1)
        # 审计留痕(六字段铁律——fail-soft 不阻断)
        await _gateway()._audit(
            session, action, arguments, None, None,
            latency, "ok")
        _STATS["ok"] += 1
        return _rpc_result(req_id, {
            "content": [{
                "type": "text",
                "text": json.dumps(
                    result, ensure_ascii=False)}],
            "isError": False,
        })
    except Exception as exc:  # noqa: BLE001
        # 工具执行异常 → MCP isError=True(不抛 HTTP——
        # ValueError/KeyError 语义为业务参数/越权拒绝)
        latency = round(
            (time.monotonic() - started) * 1000, 1)
        try:
            await _gateway()._audit(
                session, action, arguments, None, None,
                latency, "fallback", error=str(exc)[:120])
        except Exception:  # noqa: BLE001
            pass
        _STATS["toolErrors"] += 1
        logger.warning("mcp_tool_error %s: %s", action, exc)
        return _rpc_result(req_id, {
            "content": [{"type": "text",
                         "text": str(exc)}],
            "isError": True,
        })


async def handle_jsonrpc(body_text: str) -> dict:
    """单个 JSON-RPC 请求处理(统一 fail-soft 入口)

    支持: initialize / tools/list / tools/call;
    batch 数组不支持(single only)→ -32600。
    """
    _STATS["requests"] += 1
    try:
        payload = json.loads(body_text or "")
    except (ValueError, TypeError):
        _STATS["rpcErrors"] += 1
        return _rpc_error(
            None, PARSE_ERROR,
            "Parse error: 请求体 JSON 解析失败")
    if isinstance(payload, list):
        _STATS["rpcErrors"] += 1
        return _rpc_error(
            None, INVALID_REQUEST,
            "Invalid Request: 仅支持单个 JSON-RPC 请求"
            "(single only, batch 不支持)")
    if not isinstance(payload, dict) \
            or payload.get("jsonrpc") != "2.0" \
            or not isinstance(payload.get("method"), str) \
            or not payload.get("method"):
        _STATS["rpcErrors"] += 1
        return _rpc_error(
            None, INVALID_REQUEST,
            "Invalid Request: 须为含 jsonrpc=2.0 与 method "
            "的单个请求对象")
    req_id = payload.get("id")   # 通知形态缺省回 null
    method = payload["method"]
    params = payload.get("params")
    try:
        if method == "initialize":
            _STATS["ok"] += 1
            return _rpc_result(req_id, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": SERVER_NAME,
                    "version": SERVER_VERSION},
            })
        if method == "tools/list":
            _STATS["ok"] += 1
            return _rpc_result(
                req_id, {"tools": _mcp_tools()})
        if method == "tools/call":
            return await _tools_call(req_id, params)
        _STATS["rpcErrors"] += 1
        return _rpc_error(req_id, METHOD_NOT_FOUND,
                          "Method not found")
    except Exception as exc:  # noqa: BLE001
        # 兜底 fail-soft(正常路径不应到达——防御性双保险)
        _STATS["rpcErrors"] += 1
        logger.warning("mcp_internal_error: %s", exc)
        return _rpc_error(
            req_id, -32603,
            f"Internal error(fail-soft): {exc}")


# ============================================================
# admin 状态视图(白名单现状 + 进程级计数概览)
# ============================================================

def list_tools_for_admin() -> dict:
    """admin 端点: 模式/Token 配置态/白名单清单/调用计数

    不回显 Token 值(只报是否配置)。
    """
    whitelist = current_whitelist()
    return {
        "enabled": mcp_enabled(),
        "mode": os.environ.get(
            "MCP_SERVER_MODE", "off").strip().lower(),
        "tokenConfigured": bool(os.environ.get(
            "MCP_SERVER_TOKEN", "").strip()),
        "toolCount": len(whitelist),
        "tools": [{
            "action": a,
            "name": TOOL_REGISTRY[a]["operationId"],
            "summary": TOOL_REGISTRY[a]["summary"],
            "privacyCost": TOOL_REGISTRY[a]["privacyCost"],
        } for a in whitelist],
        "stats": dict(
            _STATS, note="进程级计数(重启归零); "
                         "ok=协议成功+工具成功, toolErrors="
                         "工具执行异常, rpcErrors=协议层拒绝"),
    }
