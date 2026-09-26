"""JWT 认证中间件(全局路由保护)

职责:
    1. 校验 Authorization: Bearer <accessToken>(JWT)
    2. 校验通过 → 注入 x-member-id / x-role 请求头(移除客户端伪造值)
    3. AUTH_MODE 控制无 Token 请求的处理策略

设计原则(遵循项目约定):
    - 纯 ASGI 中间件, 全 async/await
    - 纯 Python 标准库实现(与 core/auth.py 一致, 不依赖 starlette),
      可脱离 FastAPI 独立单元测试
    - 不修改任何路由文件: 现有 _require_member/_require_admin
      通过注入的 x-member-id / x-role 头自动生效
    - AUTH_MODE 运行时动态读取(不在模块级冻结)

请求处理流程:
    非 /api 路径(docs/openapi) ──────────────→ 放行
    OPTIONS 预检请求 ────────────────────────→ 放行(交给 CORS)
    公开路径白名单(登录/注册/健康检查/浏览) ──→ 放行
    携带 Authorization 头:
        Bearer Token 校验失败 ───────────────→ 401
        校验成功 → 注入身份头(覆盖伪造值) ──→ 放行
    无 Authorization 头:
        AUTH_MODE=compat(默认) ─────────────→ 放行(旧 X-Member-Id/X-Role 兼容)
        AUTH_MODE=strict ──────────────────→ 401

安全要点:
    - Token 有效时, 客户端自带的 X-Member-Id/X-Role 头会被移除并以
      JWT 声明为准, 杜绝伪造身份头绕过
    - Token 角色为 member 时访问管理接口, 注入的 x-role=member
      会被路由层 _require_admin 拒绝(403)
"""

import json
import logging
import os
import re

logger = logging.getLogger(__name__)


# ============================================================
# 路径保护策略
# ============================================================

# 完全公开(任何方法): 登录/注册/令牌管理/健康检查
PUBLIC_EXACT = {
    # 用户认证模块(JWT)
    "/api/auth/register",
    "/api/auth/login",
    "/api/auth/login/sms",       # P1-1 验证码登录
    "/api/auth/refresh",
    "/api/auth/logout",
    # 短信验证码(P1-1)
    "/api/sms/send",
    "/api/sms/verify",
    # 三方快捷登录(P1-2): 授权/回调/绑定手机号(携带票据与验证码自证)
    "/api/auth/oauth/wechat/url",
    "/api/auth/oauth/wechat/callback",
    "/api/auth/oauth/alipay/url",
    "/api/auth/oauth/alipay/callback",
    "/api/auth/oauth/qq/url",
    "/api/auth/oauth/qq/callback",
    "/api/auth/oauth/bind-phone",
    # 旧版登录/注册(存量前端兼容)
    "/api/member/login",
    "/api/member/register",
    "/api/member/login/bonus",
    # 后台管理登录
    "/api/admin/login",
    # AI智能网站入口管理模块(39号): 入口预判/统一登录/step_up/
    # 扫码全协议/注册归并(登录前的入口面, 全公开)
    "/api/entry/recognize",
    "/api/entry/login",
    "/api/entry/step-up/verify",
    "/api/entry/qr/create",
    "/api/entry/qr/scan",
    "/api/entry/qr/exchange",
    "/api/entry/qr/cancel",
    "/api/entry/registration-merge",
    # 39号 P1: 生物凭证挑战/验证(登录前, 凭一次性 challenge)
    "/api/entry/bio/challenge",
    "/api/entry/bio/verify",
    # 第三方服务器回调(微信/支付宝/物流平台服务器发起, 无本站 JWT;
    # 安全性由各回调自有验签保障: 微信APIv3 / 支付宝RSA2 /
    # 物流渠道签名, 详见 payment_routes._verify_callback 族)
    "/api/payment/callback/pay",      # 支付回调(资金链路)
    "/api/payment/callback/refund",   # 退款回调
    "/api/payment/callback/payout",   # 付款回调
    "/api/logistics/callback/track",  # 物流轨迹回调
    # 健康检查(Docker healthcheck / K8s 探针)
    "/api/decision/health",
    "/api/monitor/health",
    "/api/maintenance/health",
    # 开店申请城市/区县选择数据源(游客浏览申请页所需——344 市/3028 区县
    # 区划册只读; 时空情景感知 scene.html 切换城市同源)
    "/api/citystore/cities/available",
    "/api/citystore/districts/available",
}

# 公开前缀(仅 GET): 游客浏览类接口
# 注: GET 前缀公开后, 管理类子路径(如 site-theme/admin/*)仍由
# 路由层 _require_admin 拦截(无注入头 → 403), 安全性不降级
PUBLIC_GET_PREFIXES = (
    "/api/product",                 # 产品列表/详情/搜索/评价查看
    "/api/activity/list",           # 活动列表
    "/api/activity/stats/",         # 活动统计
    "/api/activity/leaderboard/",   # 擂台榜单
    "/api/promo-cover/",            # 36号笔记封面品牌卡片(确定性
                                    # Pillow 渲染——封面即发布
                                    # 内容, RPA 下载上传; 独立前缀
                                    # 不触及 admin 清单端点)
    "/api/ads",                     # 广告展示
    "/api/agreements",              # 条款协议查阅
    "/api/groupbuy/products",       # 团购商品
    "/api/groupbuy/tiers",          # 团购阶梯
    "/api/payment/channels/active", # 可用支付方式
    "/api/entry/qr/",               # 39号: 扫码状态轮询(GET, qrId 段)
    # strict 切换后 nginx 401 实证补录(2026-09-18, 游客浏览面):
    "/api/site-theme/",             # C端运行时换肤(active/icons/themes)
                                    # ——换肤铁律: 永不关停
    "/api/venue/partners",          # 酒店酒吧合作商列表(C端合作页浏览)
    "/api/venue/venues",           # 合作场地列表(C端浏览)
    "/api/location/stores",        # 附近门店/门店详情(C端浏览,
                                    # 401 日志持续新增实证 2026-09-18)
    "/api/zyh/",                  # 竹韵·智衡观测面 GET(75号知识内核
                                    # 公开事实锚点: 工艺宪法/图谱/守门
                                    # 规则——工艺科普游客可查; POST
                                    # 决策面仍 JWT+门控)
    "/api/scene/",                # 时空情景感知(P0: IP 城市解析+天气+
                                    # 代理权益+情景问候——游客可用,
                                    # 仅城市级粒度, IP 不落库)
    "/api/synapse/",              # 织智观测面 GET(76号人格经线/
                                    # 路由规则/指标/日记——品牌资产
                                    # 公示游客可查; POST 决策面
                                    # 仍 JWT+门控)
    "/api/zjian/",                # 竹鉴观测面 GET(77号质检典藏
                                    # 报告/指标目录——检测数据
                                    # 公示游客可查; POST 决策面
                                    # 仍 JWT+门控)
    "/api/activity/lottery/",     # 奖品池概率公示 GET(合规要求:
                                    # §3.3.3 游客可查; POST /draw
                                    # 抽奖不受 GET 前缀影响)
)

# 公开 GET 正则(动态路径, 无法用前缀表达)
# 活动详情 GET /api/activity/{id}——C端活动详情页游客可浏览
# 注意: 不能放宽为前缀 /api/activity/——那会把 my-registrations/
# prizes/mine 变为中间件层公开, 其路由层仅校验头存在性(无 JWT
# 注入对照), 存在伪造 X-Member-Id 越权读取的风险
PUBLIC_GET_PATTERNS = (
    re.compile(r"^/api/activity/\d+$"),
)


def is_public_path(path: str, method: str) -> bool:
    """判断路径是否公开(无需登录)"""
    if path in PUBLIC_EXACT:
        return True
    if method == "GET":
        if any(path.startswith(prefix) for prefix in PUBLIC_GET_PREFIXES):
            return True
        return any(p.match(path) for p in PUBLIC_GET_PATTERNS)
    return False


def get_auth_mode() -> str:
    """读取认证模式: compat(默认, 兼容旧头) / strict(强制 JWT)"""
    return os.environ.get("AUTH_MODE", "compat").lower()


# ============================================================
# ASGI 头工具
# ============================================================

def _get_header(scope: dict, name: str) -> str:
    """从 ASGI scope 读取请求头(大小写不敏感)"""
    for key, value in scope.get("headers", []):
        if key.decode("latin-1").lower() == name:
            return value.decode("latin-1")
    return ""


def _extract_bearer(authorization: str) -> str:
    """提取 Bearer Token, 格式错误返回空串"""
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        return ""
    return parts[1].strip()


def inject_identity(scope: dict, member: dict) -> None:
    """注入 x-member-id / x-role 头(安全关键: 先移除客户端伪造的同名头)

    ASGI scope 中同名头重复时 Starlette 会用逗号拼接,
    必须移除旧值后追加, 否则 X-Member-Id 会变成 "99999, 1" 破坏解析。
    """
    headers = [
        (key, value)
        for key, value in scope.get("headers", [])
        if key.decode("latin-1").lower() not in ("x-member-id", "x-role")
    ]
    headers.append((b"x-member-id", str(member["memberId"]).encode("latin-1")))
    headers.append((b"x-role", str(member.get("role", "member")).encode("latin-1")))
    scope["headers"] = headers


async def _send_json_error(send, status: int, detail: str):
    """以 JSON 响应终止请求(格式与 FastAPI HTTPException 一致)

    手工构建 ASGI 消息, 不依赖 starlette。
    """
    body = json.dumps({"detail": detail}, ensure_ascii=False).encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(body)).encode("latin-1")),
        ],
    })
    await send({"type": "http.response.body", "body": body})


# ============================================================
# 中间件主体
# ============================================================

class JWTAuthMiddleware:
    """JWT 认证中间件(纯 ASGI)

    挂载方式(main.py, 须先于 CORSMiddleware 添加, 使 CORS 位于外层):
        app.add_middleware(JWTAuthMiddleware)
        app.add_middleware(CORSMiddleware, ...)
    """

    def __init__(self, app):
        self.app = app
        # 延迟导入避免循环依赖(services 层依赖 core)
        from services.auth_service import AuthService
        self._service = AuthService()

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = scope.get("method", "GET").upper()

        # 非 API 路径与 CORS 预检请求直接放行
        if not path.startswith("/api") or method == "OPTIONS":
            await self.app(scope, receive, send)
            return

        if is_public_path(path, method):
            await self.app(scope, receive, send)
            return

        authorization = _get_header(scope, "authorization")
        if authorization:
            token = _extract_bearer(authorization)
            if not token:
                await _send_json_error(
                    send, 401, "Authorization 头格式错误(须为 Bearer <token>)")
                return

            member = await self._validate(send, token)
            if member is None:
                return  # 已发送错误响应
            inject_identity(scope, member)
        elif get_auth_mode() == "strict":
            await _send_json_error(
                send, 401, "未登录: 请提供 Authorization: Bearer <token>")
            return

        await self.app(scope, receive, send)

    async def _validate(self, send, token: str):
        """校验 Token, 失败时发送错误响应并返回 None"""
        from core.auth import AuthError, TokenExpiredError

        try:
            return await self._service.get_current_member(token)
        except TokenExpiredError as exc:
            await _send_json_error(
                send, 401, f"登录已过期, 请刷新令牌: {exc}")
            return None
        except AuthError as exc:
            await _send_json_error(send, 401, str(exc))
            return None
        except KeyError:
            # Token 签名有效但会员不存在(已注销)
            await _send_json_error(send, 401, "Token 无效: 会员不存在")
            return None
        except Exception as exc:  # 存储异常等系统错误
            logger.exception("auth_middleware_internal_error: %s", exc)
            await _send_json_error(send, 500, "认证服务内部错误")
            return None
