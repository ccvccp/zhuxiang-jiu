"""抖音开放平台对接(2026-10-17 入驻收口)

小程序「竹香酒馆」tt90cde6700eb49cef01(企业主体·泰安市元禾生物)。
本模块 P0: access_token 生命周期(获取/缓存/提前刷新) + OpenAPI
客户端骨架 + 小程序信息探活; 视频发布/内容分发等能力待小程序
能力开通后扩展(见 routes/douyin_routes.py)。

凭据治理对齐 2026-10-17 模式: DOUYIN_APP_ID/DOUYIN_APP_SECRET
仅经环境变量注入(生产 /opt/zhuxiang/.env 已落库), 无明文默认。
token 接口已实证可用:
    GET https://developer.toutiao.com/api/apps/token  (7200s)
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://developer.toutiao.com"
# 官方有效期 7200s; 提前 300s 刷新避免边界失效
_REFRESH_MARGIN = 300

_TOKEN: dict | None = None          # {"access_token","_expire_at"}
_LOCK = asyncio.Lock()


class DouyinConfigError(RuntimeError):
    """凭据未配置(env 注入缺失)"""


class DouyinAPIError(RuntimeError):
    """平台返回 err_no != 0"""


def _creds() -> tuple[str, str]:
    appid = os.environ.get("DOUYIN_APP_ID", "").strip()
    secret = os.environ.get("DOUYIN_APP_SECRET", "").strip()
    if not (appid and secret):
        raise DouyinConfigError(
            "DOUYIN_APP_ID/DOUYIN_APP_SECRET 未配置"
            "(生产 /opt/zhuxiang/.env; 本地环境变量注入)")
    if not (appid.isalnum() and secret.isalnum()):
        raise DouyinConfigError("DOUYIN_APP_ID/SECRET 须字母数字")
    return appid, secret


def _http_get(url: str, timeout: int = 20) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        # 平台侧 404/403 等: 转 DouyinAPIError 而非裸 500
        body = e.read().decode("utf-8", "replace")[:80]
        raise DouyinAPIError(f"HTTP {e.code} {url.split('?')[0]}: {body}") from None


class DouyinOpenService:
    """抖音 OpenAPI 客户端(单例语义: 模块级 token 缓存)"""

    async def access_token(self, force: bool = False) -> str:
        """获取/复用 access_token(并发单飞, 提前 300s 刷新)"""
        global _TOKEN
        if (not force and _TOKEN
                and _TOKEN["_expire_at"] > time.time() + _REFRESH_MARGIN):
            return _TOKEN["access_token"]
        async with _LOCK:
            if (not force and _TOKEN
                    and _TOKEN["_expire_at"] > time.time() + _REFRESH_MARGIN):
                return _TOKEN["access_token"]
            appid, secret = _creds()
            qs = urllib.parse.urlencode({
                "appid": appid, "secret": secret,
                "grant_type": "client_credential"})
            data = await asyncio.to_thread(
                _http_get, f"{BASE}/api/apps/token?{qs}")
            tok = (data or {}).get("access_token") or ""
            if not tok:
                raise DouyinAPIError(
                    f"token 获取失败: {json.dumps(data, ensure_ascii=False)[:150]}")
            _TOKEN = {"access_token": tok,
                      "_expire_at": time.time()
                      + int(data.get("expires_in") or 7200)}
            return tok

    async def _call(self, path: str, params: dict | None = None) -> dict:
        """GET OpenAPI(自动附 access_token; err_no!=0 抛业务异常)"""
        p = dict(params or {})
        p["access_token"] = await self.access_token()
        qs = urllib.parse.urlencode(p)
        data = await asyncio.to_thread(_http_get, f"{BASE}{path}?{qs}")
        err = int(data.get("err_no") or 0)
        if err != 0:
            raise DouyinAPIError(
                f"抖音API {path} err_no={err}: "
                f"{str(data.get('err_tips'))[:150]}")
        return data.get("data") if "data" in data else data

    async def probe(self) -> dict:
        """凭据全链探活(强刷 token, 真实网络请求验证 appid+secret)

        注: 旧 ma_info 接口(/api/apps/ma_info)在 developer.toutiao.com
        与 minigame.zijieapi.com 均已 404(2026-10-18 实测), 探活改用
        token 强刷——凭据有效性即 token 可获取性, 等价验证。
        """
        appid, _ = _creds()
        tok = await self.access_token(force=True)
        return {"app_id": appid,
                "token_prefix": tok[:8],
                "token_expire_in": int(_TOKEN["_expire_at"] - time.time())}

    async def status(self) -> dict:
        """模块状态诊断(env 配置/token 有效期; 不主动刷新)"""
        out: dict = {"app_id": "", "configured": False,
                     "token_alive": False, "token_expire_in": 0}
        try:
            out["app_id"], _ = _creds()
            out["configured"] = True
        except DouyinConfigError:
            return out
        if _TOKEN and _TOKEN["_expire_at"] > time.time():
            out["token_alive"] = True
            out["token_expire_in"] = int(_TOKEN["_expire_at"] - time.time())
        return out
