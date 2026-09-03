"""安全网关中间件(43号·AI智能安全管理 P0, 全局请求防线)

职责(设计文档 §2.1):
    1. 白名单快道(健康检查/OPTIONS/非 API 直接放行)
    2. 特征扫描(path/query/body/UA: SQLi/XSS/遍历/扫描器)
    3. ThreatGateScorer(第26档案)威胁评分
    4. 四档处置: allow | throttle(渐进延迟) | challenge | block
    5. 可疑请求事件留痕(仅可疑档, 防流水爆炸)

设计原则(遵循项目约定):
    - 纯 ASGI 中间件, 全 async/await(与 auth_middleware 同范式)
    - observe/shadow 灰度默认: 只留痕不处置(P0)
    - fail-open: 网关自身异常 → 放行 + 日志(安全组件不能锁死网站)
    - SECURITY_GATEWAY_MODE=off → 全放行(一键回退)
    - 不修改任何路由文件

挂载次序(main.py, 后添加者在外层):
    app.add_middleware(JWTAuthMiddleware)          # 最内
    app.add_middleware(SecurityGatewayMiddleware)  # 中间: 网关先于 JWT
    app.add_middleware(CORSMiddleware, ...)         # 最外
    → 请求流: CORS → SecurityGateway → JWT → 路由

    网关先于 JWT 的意义: 攻击请求在进入鉴权层之前就被评分拦截;
    网关层看到的 X-Member-Id/X-Role 为客户端原始头(JWT 尚未清洗),
    可作为身份风险信号(伪造身份头试探)。

请求处理流程:
    非 /api 路径 或 OPTIONS 预检 ──────────→ 放行
    健康检查白名单(快道, 零开销) ─────────→ 放行
    SECURITY_GATEWAY_MODE=off ────────────→ 放行(一键回退)
    body 预读(JSON 请求前 64KB, 扫描后原样回放)
    → Security43Service.process_request 评分决策
    → observe/shadow: 放行(事件已留痕)
    → enforce:
        allow ────────────────→ 放行
        throttle ─────────────→ 渐进延迟 1-3s 后放行
        challenge ───────────→ 401 + 挑战令牌(P1 验证端点)
        block ───────────────→ 403(封禁表 TTL 自动解封)
"""

import asyncio
import json
import logging

logger = logging.getLogger(__name__)

# body 预读上限(超限部分不扫描, 直接透传)
BODY_SCAN_LIMIT = 65536


def _get_header(scope: dict, name: str) -> str:
    """从 ASGI scope 读取请求头(大小写不敏感)"""
    for key, value in scope.get("headers", []):
        if key.decode("latin-1").lower() == name:
            return value.decode("latin-1")
    return ""


class _BodyPeeker:
    """请求体安全预读器: 缓冲首段 body 供扫描, 下游读取时原样回放

    流式安全: 只缓冲 BODY_SCAN_LIMIT 以内的 http.request 消息,
    超限或 disconnect 消息直接透传给下游, 不破坏请求语义。
    """

    def __init__(self, receive):
        self._receive = receive
        self._buffered = []

    async def peek(self, limit: int = BODY_SCAN_LIMIT) -> str:
        """缓冲首段请求体, 返回可扫描文本(非文本内容返回空)"""
        total = 0
        while total < limit:
            message = await self._receive()
            self._buffered.append(message)
            if message.get("type") == "http.request":
                total += len(message.get("body", b""))
                if not message.get("more_body"):
                    break
            else:  # http.disconnect 等
                break
        chunks = [m.get("body", b"")
                  for m in self._buffered
                  if m.get("type") == "http.request"]
        if not chunks:
            return ""
        try:
            return b"".join(chunks).decode("utf-8", errors="ignore")
        except Exception:
            return ""

    async def __call__(self):
        """回放缓冲消息, 耗尽后透传原始 receive"""
        if self._buffered:
            return self._buffered.pop(0)
        return await self._receive()


class SecurityGatewayMiddleware:
    """安全网关中间件(纯 ASGI, 43号设计文档 §2.1)"""

    def __init__(self, app):
        self.app = app
        # 延迟导入避免循环依赖(services 层依赖 core)
        from services.security_service import Security43Service
        self._service = Security43Service()

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = scope.get("method", "GET").upper()

        # 快道: CORS 预检 / 健康检查白名单 / 总开关 off
        # (注意: 非 /api 路径也过网关——探针路径 /.env /wp-admin
        #  多为非 API 探测, 须纳入扫描与 IP 信誉扣分)
        from services.security_service import (
            get_gateway_mode, GATEWAY_WHITELIST,
        )
        if (method == "OPTIONS" or path in GATEWAY_WHITELIST
                or get_gateway_mode() != "on"):
            await self.app(scope, receive, send)
            return

        # 提取请求要素
        client = scope.get("client") or ("", 0)
        ip = str(client[0] or "unknown")
        query = scope.get("query_string", b"").decode(
            "latin-1", errors="ignore")
        ua = _get_header(scope, "user-agent")
        member_raw = _get_header(scope, "x-member-id")
        try:
            member_id = int(member_raw) if member_raw else 0
        except ValueError:
            member_id = 0

        # body 预读(JSON/表单类请求扫描后回放)
        body_text = ""
        downstream_receive = receive
        content_type = _get_header(scope, "content-type").lower()
        if method in ("POST", "PUT", "PATCH", "DELETE") and (
                "json" in content_type or "form" in content_type
                or not content_type):
            peeker = _BodyPeeker(receive)
            try:
                body_text = await peeker.peek()
            except Exception as exc:
                logger.warning("security_body_peek_skip ip=%s: %s",
                               ip, exc)
            else:
                downstream_receive = peeker

        # 评分决策(fail-open: service 内部已兜底异常)
        result = await self._service.process_request(
            ip, method=method, path=path, query=query,
            body_text=body_text, ua=ua, member_id=member_id)
        action = result.get("action") or "allow"

        # observe/shadow 灰度: 只留痕不处置
        if not result.get("enforced"):
            await self.app(scope, downstream_receive, send)
            return

        # enforce 四档处置
        if action == "throttle":
            # 渐进延迟: 评分 50-70 → 1-3s(软性减速不拒绝)
            score = float((result.get("scoring") or {}).get("score")
                          or 60)
            delay = min(3.0, max(1.0, (70.0 - score) / 10.0))
            await asyncio.sleep(delay)
            await self.app(scope, downstream_receive, send)
            return
        if action == "challenge":
            await self._send_challenge(send, ip, result)
            return
        if action == "block":
            await self._send_block(send, ip, result)
            return

        await self.app(scope, downstream_receive, send)

    # --------------------------------------------------------
    # 响应构造(纯 ASGI, 不依赖 starlette)
    # --------------------------------------------------------

    async def _send_challenge(self, send, ip: str, result: dict):
        """挑战验证: 401 + 一次性挑战令牌(P1 verify 端点应答)"""
        scoring = result.get("scoring") or {}
        challenge_token = f"sec43-challenge-{ip}-{scoring.get('scoredAt', '')}"
        body = json.dumps({
            "detail": "请求存在可疑特征, 需完成安全验证",
            "challenge": {
                "token": challenge_token,
                "score": scoring.get("score"),
                "verify": "/api/security/challenge/verify (P1)",
            },
        }, ensure_ascii=False).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length",
                 str(len(body)).encode("latin-1")),
                (b"x-security-challenge", challenge_token.encode(
                    "latin-1", errors="ignore")),
            ],
        })
        await send({"type": "http.response.body", "body": body})

    async def _send_block(self, send, ip: str, result: dict):
        """封禁拦截: 403(TTL 自动解封, 误报申诉走 P1 通道)"""
        scoring = result.get("scoring") or {}
        body = json.dumps({
            "detail": "请求已被安全防护拦截(封禁将自动解除)",
            "block": {
                "ip": ip,
                "score": scoring.get("score"),
                "appeal": "/api/security/appeals (P1)",
            },
        }, ensure_ascii=False).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": 403,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length",
                 str(len(body)).encode("latin-1")),
            ],
        })
        await send({"type": "http.response.body", "body": body})
