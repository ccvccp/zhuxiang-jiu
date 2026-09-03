"""44号·P1 API Key 消费方凭证网关(纯 ASGI 中间件, 默认 off)

计划(docs/44号_API智能管理模块实施计划.md §四③):
    - API_MANAGER_MODE=off(默认) → 全直通, 零影响
    - 开启后仅拦截 Key 面(台账 status=published 的 API——索引
      O(1) 判定 + 模板路径匹配 {param} 通配):
        · 无 X-Api-Key / X-App-Code 双头 → 401
        · 双头校验(摘要单键取 + appCode 匹配 + 状态/过期)失败
          → 401
        · 通过 → 移除客户端伪造身份头 + inject_identity 注入
          memberId(复用既有注入, 业务路由零感知) + 用量留痕
          (fire-and-forget 不阻塞响应)
    - Key 校验缓存 60s(吊销本进程秒级失效, 跨 worker 60s 收敛)
    - published 集缓存 60s(SMEMBERS 单命令, 非全表扫)
    - fail-open 铁律: 校验基础设施异常 → 放行并留痕
      (治理不阻断业务——与 43号网关同铁律)

挂载(main.py, add_middleware 顺序在 JWTAuth 之后=外一层):
    执行序 CORS → SecurityGateway(43号) → ApiKey(44号)
              → JWTAuth → 业务路由
    ——43号威胁评分覆盖全部流量(含 Key 面), ApiKey 先于 JWTAuth
    注入身份(compat 模式无 Authorization 头时透传注入值)。
"""

import asyncio
import json
import logging
import os
import re
import time

from core.auth_middleware import (
    _get_header, _send_json_error, inject_identity,
)

logger = logging.getLogger(__name__)

# published 集缓存(条目 (method, template, regex) 预编译)
_PUBLISHED_CACHE = {"at": 0.0, "templates": ()}
_PUBLISHED_TTL = 60.0


def api_manager_enabled() -> bool:
    """44号 Key 网关总开关(API_MANAGER_MODE=on 开启)"""
    return os.environ.get(
        "API_MANAGER_MODE", "off").strip().lower() == "on"


def invalidate_published_cache() -> None:
    """状态变更后失效 published 缓存(即时生效, 不等 TTL)"""
    _PUBLISHED_CACHE["at"] = 0.0


async def _send_rate_limited(send, detail: str,
                             retry_after: int) -> None:
    """429 响应(Retry-After 标准头 + JSON body 含 retryAfter)"""
    body = json.dumps(
        {"detail": detail, "retryAfter": retry_after},
        ensure_ascii=False).encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": 429,
        "headers": [
            (b"content-type",
             b"application/json; charset=utf-8"),
            (b"content-length",
             str(len(body)).encode("latin-1")),
            (b"retry-after",
             str(retry_after).encode("latin-1")),
        ],
    })
    await send({"type": "http.response.body", "body": body})


def _template_to_regex(template: str):
    """路由模板 → 匹配 regex({param} 段通配 [^/]+)"""
    parts = []
    for seg in template.split("/"):
        if not seg:
            continue
        if seg.startswith("{") and seg.endswith("}"):
            parts.append("[^/]+")
        else:
            parts.append(re.escape(seg))
    return re.compile("^/" + "/".join(parts) + "$")


class ApiKeyMiddleware:
    """44号消费方凭证网关(默认 off 全直通)"""

    def __init__(self, app):
        self.app = app
        self._service = None   # 延迟导入避免循环依赖

    def _svc(self):
        if self._service is None:
            from services.api_key_service import ApiKeyService
            self._service = ApiKeyService()
        return self._service

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        # 总开关(默认 off 全直通——零影响铁律)
        if not api_manager_enabled():
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = scope.get("method", "GET").upper()
        if not path.startswith("/api") or method == "OPTIONS":
            await self.app(scope, receive, send)
            return

        # 观测上下文(P3; 通过路径之外保持 None)
        observe = None
        try:
            if not await self._is_published(method, path):
                # 非 Key 面(published 之外不拦截——JWT/游客
                # 既有流程接管, Key 头被忽略)
                await self.app(scope, receive, send)
                return

            # Key 面: 双头凭证校验
            api_key = _get_header(scope, "x-api-key")
            app_code = _get_header(scope, "x-app-code")
            if not api_key or not app_code:
                await _send_json_error(
                    send, 401,
                    "该 API 需要 API Key 访问: 请提供 X-Api-Key "
                    "与 X-App-Code 双头凭证(管理面板自助申请)")
                return

            verdict = await self._svc().validate_key_cached(
                api_key, app_code)
            if not verdict.get("ok"):
                await _send_json_error(
                    send, 401,
                    verdict.get("reason", "API Key 无效"))
                return

            # P2: 双限检查(套餐/覆盖 → QPS 窗口 → 日配额;
            # 限流失败不消耗日配额, 被拒计入 QPS 窗口)
            from services.api_rate_limit_service import (
                check_rate_limit,
            )
            limits = await check_rate_limit(
                verdict.get("keyId"), verdict.get("tier"),
                verdict.get("customQps"),
                verdict.get("customDaily"))
            if not limits.get("allowed"):
                logger.info(
                    "api44_rate_limited keyId=%s type=%s "
                    "retryAfter=%s", verdict.get("keyId"),
                    limits.get("limitType"),
                    limits.get("retryAfter"))
                await _send_rate_limited(
                    send, limits["detail"],
                    limits["retryAfter"])
                return

            # 通过: 注入身份(先移除客户端伪造头——同 JWTAuth
            # 安全口径) + P3 观测上下文(send 包装捕获状态码)
            inject_identity(scope, {
                "memberId": verdict["memberId"],
                "role": "member"})
            key_id = verdict.get("keyId")
            template = self._match_template(method, path)
            observe = {"keyId": key_id, "template": template,
                       "start": time.monotonic()}
            asyncio.create_task(
                self._safe_record_usage(key_id))
        except Exception as exc:
            # fail-open 铁律: 治理基础设施异常放行并留痕
            logger.exception(
                "api44_key_gateway_fail_open path=%s: %s",
                path, exc)

        # P3: 观测路径用包装 send(状态码/延迟捕获);
        # 业务异常传播时补 500 留痕后重抛(500 响应由外层
        # ServerErrorMiddleware 用原始 send 发送, 不经包装)
        if observe is None:
            await self.app(scope, receive, send)
            return
        wrapped_send = self._wrap_send(
            send, observe["keyId"], observe["template"],
            observe["start"])
        try:
            await self.app(scope, receive, wrapped_send)
        except Exception:
            elapsed_ms = (time.monotonic()
                          - observe["start"]) * 1000
            asyncio.create_task(
                self._safe_record_event(
                    observe["keyId"], observe["template"],
                    elapsed_ms, 500))
            raise

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------

    async def _safe_record_usage(self, key_id) -> None:
        """异步用量留痕(异常吞掉不阻塞请求)"""
        try:
            if key_id is not None:
                await self._svc().record_usage(int(key_id))
        except Exception:
            logger.warning("api44_usage_record_skip keyId=%s",
                           key_id, exc_info=True)

    def _match_template(self, method: str, path: str) -> str:
        """实际路径 → 匹配的 published 模板(观测聚合键)"""
        for m, template, regex in _PUBLISHED_CACHE["templates"]:
            if m == method and regex.match(path):
                return template
        return path   # 兜底: 无匹配(理论不可达)用实际路径

    def _wrap_send(self, send, key_id, template, start):
        """P3: 包装 send 捕获响应状态码 → 观测留痕(异步)"""
        state = {"status": None}

        async def wrapped(message):
            if message.get("type") == "http.response.start":
                state["status"] = message.get("status", 500)
            await send(message)
            if message.get("type") == "http.response.body" \
                    and message.get("more_body") is not True:
                # 响应完成——异步留痕(不阻塞响应下发)
                elapsed_ms = (time.monotonic() - start) * 1000
                asyncio.create_task(
                    self._safe_record_event(
                        key_id, template, elapsed_ms,
                        state["status"] or 500))
        return wrapped

    async def _safe_record_event(self, key_id, template,
                                 elapsed_ms, status) -> None:
        """观测留痕(异常吞掉)"""
        try:
            from services.api_rate_limit_service import (
                record_usage_event,
            )
            await record_usage_event(
                key_id, template, elapsed_ms, status)
        except Exception:
            logger.warning("api44_usage_event_wrap_skip keyId=%s",
                           key_id, exc_info=True)

    async def _is_published(self, method: str, path: str) -> bool:
        """是否 Key 面(published 集缓存 + 模板匹配)"""
        now = time.monotonic()
        if now - _PUBLISHED_CACHE["at"] > _PUBLISHED_TTL:
            await self._refresh_published()
        for m, _tpl, regex in _PUBLISHED_CACHE["templates"]:
            if m == method and regex.match(path):
                return True
        return False

    async def _refresh_published(self) -> None:
        """刷新 published 集(SMEMBERS 单命令, 非全表扫)"""
        from repositories.api_manager_repository import (
            ApiManager44Repository,
        )
        members = await ApiManager44Repository().get_published()
        templates = []
        for member in members:
            m, sep, p = str(member).partition("|")
            if sep:
                templates.append((m, p, _template_to_regex(p)))
        _PUBLISHED_CACHE["templates"] = tuple(templates)
        _PUBLISHED_CACHE["at"] = time.monotonic()
