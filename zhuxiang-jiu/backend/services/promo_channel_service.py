"""36号·AI智能推广模块·P2 发布通道与百度 SEO 提交服务

核心职责(设计文档 §3.6 P2):
    - 真实平台 API 适配器: PROMO_CHANNEL_MODE=real 且
      PROMO_CHANNEL_{PLATFORM}_KEY 配置时走平台开放 API 发布;
      未配置/调用失败回退确定性 mock 回执(mode=mock_fallback),
      产出永不中断(Mock-first, 同 P1-2 OAuth / P1-3 实名惯例)
    - 微信公众号专用轨(2026-09-27 接入, wechat_mp): 认证服务号
      官方群发 API(素材上传+草稿+群发, 完全合规)——多步协议
      stable_token(Redis 缓存) → 封面 add_material(multipart)
      → draft/add → mass/preview|sendall, 月度配额闸门
      (认证服务号自然月 4 次); 凭证 PROMO_CHANNEL_WECHAT_MP_KEY
      格式 appid|secret
    - 百度普通收录推送: BAIDU_PUSH_SITE/TOKEN 配置时 POST
      data.zz.baidu.com/urls 主动推送; 未配置走确定性 mock 回执
    - 推送幂等: 同 URL 当日不重推(dateKey 维度去重)

对接:
    - promo_service.process_publish_queue: 发布出队时调 publish_to_platform
    - attract: sitemap URL 结构复用({SITE_BASE_URL}/r/{code})
"""

import asyncio
import contextlib
import html as html_lib
import json
import logging
import os
import time
import urllib.error
import urllib.request
import urllib.parse
from datetime import datetime, UTC
from zoneinfo import ZoneInfo

from repositories.promo_repository import (
    PromoRepository,
    PROMO_PLATFORMS, PROMO_CHANNEL_API_KEY_ENV,
    PROMO_PLATFORM_WECHAT_MP,
    SEO_PUSH_STATUS_OK, SEO_PUSH_STATUS_FAILED,
)
from repositories.backend import is_redis_mode, get_redis_client, _k
from repositories.attract_repository import SITE_BASE_URL

logger = logging.getLogger(__name__)

CHANNEL_MODE_REAL = "real"
CHANNEL_MODE_MOCK = "mock"
# 通道未配置/失败回退的 mock 回执标记(可观测降级)
CHANNEL_MODE_MOCK_FALLBACK = "mock_fallback"
# 网络类异常(超时/连接中断)导致平台侧结果无法判定的回执标记:
# 既不计失败回退(平台侧可能已执行成功, 自动重试会双发),
# 也不标成功(可能没发出去)——冻结待人工核对
CHANNEL_MODE_UNKNOWN = "unknown"
# RPA 通道平台集(官方无发布 API——创作者中心网页版浏览器
# 自动化发布, 小红书 2026-09-26 立项 / 抖音图文 2026-09-27
# 接入 / 微博 CLI 桥 2026-09-27 接入 / 视频号 2026-09-27 接入):
# 无凭证时回 rpa_pending 回执, 由对话内 browser agent 执行 +
# 回执登记端点闭环。
# 形态差异: 微博走本机官方 weibo-cli(网关 token 自管); 视频号
# 上游为视频形态, 产线 build_promo_video.py(四页品牌卡→ffmpeg
# 轮播 16s)在开发机产出 mp4, 发布走 channels.weixin.qq.com
# 网页版 RPA(需用户视频号扫码登录)。
# 前向兼容: 微博元禾企业认证后配 PROMO_CHANNEL_WEIBO_KEY
# 自动切经典 API 轨, RPA 分支天然让位(仅无 key 时 rpa_pending)
CHANNEL_MODE_RPA_PENDING = "rpa_pending"
CHANNEL_MODE_RPA = "rpa"   # RPA 执行完成态(回执登记成功)
RPA_PLATFORMS = {"xiaohongshu", "douyin", "weibo",
                 "wechat_channels"}

# ============================================================
# 平台认证风格(2026-09-02 实测校准)
# ============================================================
AUTH_STYLE_HEADER = "header"  # access-token 请求头(抖音/小红书开放平台惯例)
AUTH_STYLE_QUERY = "query"    # access_token 查询参数(微信系惯例)
AUTH_STYLE_FORM = "form"      # 表单字段 access_token+status(微博开放 API)

PLATFORM_AUTH_STYLES = {
    "douyin": AUTH_STYLE_HEADER,
    "xiaohongshu": AUTH_STYLE_HEADER,
    "wechat_moments": AUTH_STYLE_QUERY,
    # 实测: POST share.json 无凭证返回 403 {"error":"auth by Null spi!"}
    # 证明端点与协议正确, 配 access_token 表单字段即可用
    "weibo": AUTH_STYLE_FORM,
    "wechat_channels": AUTH_STYLE_QUERY,
    # 公众号: access_token 查询参数(微信系惯例)——但凭证为
    # appid|secret, 须先经 stable_token 换取(专用轨, 不走通用
    # _publish_real 单端点范式)
    "wechat_mp": AUTH_STYLE_QUERY,
}

# 真实平台开放 API 端点映射(默认值; 可经 PROMO_CHANNEL_{X}_URL 覆盖)
# 实测备注(2026-09-02):
#   - weibo: 真实有效(403 鉴权拦截, 协议正确)
#   - baidu: 真实有效(400 {"error":400,"message":"token invalid"})
#   - douyin: 官方无简单文本发布 API(返回 HTML 页面), 默认值为
#     占位, 资质就绪后配 PROMO_CHANNEL_DOUYIN_URL 指向开放平台
#     视频发布接口(OAuth 头鉴权)或自建代理
#   - xiaohongshu/wechat_moments/wechat_channels: 无公开第三方发布
#     API, 默认值为占位, 资质就绪后经 _URL 环境变量校准
#   - wechat_mp(2026-09-27): 官方群发 API 域(api.weixin.qq.com)，
#     值为 API base(stable_token/material/draft/mass 多端点拼前缀，
#     PROMO_CHANNEL_WECHAT_MP_URL 可整体切换代理)
PLATFORM_API_ENDPOINTS = {
    "douyin": "https://open.douyin.com/api/promotion/v1/content/publish",
    "xiaohongshu": "https://edith.xiaohongshu.com/api/sns/web/v1/note/publish",
    "wechat_moments": "https://api.weixin.qq.com/cgi-bin/moments/publish",
    "weibo": "https://api.weibo.com/2/statuses/share.json",
    "wechat_channels": "https://api.weixin.qq.com/cgi-bin/channels/publish",
    "wechat_mp": "https://api.weixin.qq.com",
}

# 各平台发布回执 ID 字段别名(响应解析兼容)
PUBLISH_ID_ALIASES = ("publish_id", "idstr", "id", "note_id", "spu_id")

# 百度普通收录推送端点(POST, body=URL 列表)
BAIDU_PUSH_ENDPOINT = "http://data.zz.baidu.com/urls"
_HTTP_TIMEOUT = 10


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def channel_mode() -> str:
    """发布通道总模式(real / mock)——运行时动态读环境变量"""
    return (os.environ.get("PROMO_CHANNEL_MODE", "mock")
            or "mock").strip().lower()


def channel_key(platform: str) -> str:
    """读取平台通道凭证(空=未配置; 运行时动态读)"""
    env = PROMO_CHANNEL_API_KEY_ENV.get(platform, "")
    if not env:
        return ""
    return os.environ.get(env, "").strip()


def platform_endpoint(platform: str) -> str:
    """平台发布端点解析(运行时动态读)

    环境变量优先: 与凭证同名的 PROMO_CHANNEL_{X}_URL(如
    PROMO_CHANNEL_WEIBO_URL), 资质就绪后免改代码即可校准端点或
    切自建代理; 未配置回落代码内默认映射。
    """
    env = PROMO_CHANNEL_API_KEY_ENV.get(platform, "")
    if env:
        override = (os.environ.get(env[:-4] + "_URL", "")
                    or "").strip()
        if override:
            return override
    return PLATFORM_API_ENDPOINTS.get(platform, "")


def _http_error_detail(exc: urllib.error.HTTPError) -> str:
    """HTTPError 响应体细节提取(保留平台真实报错信息)

    实测口径: 百度 400 → {"error":400,"message":"token invalid"};
    微博 403 → {"error":"auth by Null spi!","error_code":21301}。
    """
    raw = ""
    with contextlib.suppress(Exception):
        raw = exc.read().decode("utf-8", errors="replace")[:200]
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                for field in ("message", "error", "errmsg"):
                    if parsed.get(field):
                        return f"HTTP {exc.code} {parsed[field]}"
        except Exception:
            pass
    return f"HTTP {exc.code} {raw or exc.reason}".strip()


def _extract_publish_id(body) -> str:
    """各平台发布回执 ID 提取(字段别名兼容)

    weibo → idstr/id; 微信系 → publish_id; 小红书 → note_id;
    抖音 → data.publish_id。
    """
    if not isinstance(body, dict):
        return ""
    data = (body.get("data") if isinstance(body.get("data"), dict)
            else {})
    for alias in PUBLISH_ID_ALIASES:
        value = body.get(alias) or data.get(alias)
        if value:
            return str(value)
    return ""


def baidu_push_config() -> tuple[str, str]:
    """百度推送站点与 token(运行时动态读; 须同时配置才走真实轨)"""
    site = os.environ.get("BAIDU_PUSH_SITE", "").strip()
    token = os.environ.get("BAIDU_PUSH_TOKEN", "").strip()
    return site, token


# ============================================================
# 微信公众号群发 API 通道(wechat_mp, 2026-09-27 接入)
# ============================================================
# 认证服务号官方群发 API——完全合规免费, 自然月群发配额 4 次
# (与发布队列日上限 PROMO_DAILY_CAP 相互独立的第二道闸门)。
# 协议四步: stable_token(缓存) → 封面 add_material(永久素材)
# → draft/add(草稿) → mass/preview|sendall(预览|群发)。
WECHAT_MP_SEND_MODE_PREVIEW = "preview"   # 预览(指定 openid 试收, 不计配额)
WECHAT_MP_SEND_MODE_SEND = "send"         # 正式群发(全体关注者, 计月度配额)
# 认证服务号自然月群发上限(平台硬限 4 次/月)
WECHAT_MP_MONTHLY_CAP_DEFAULT = 4
# access_token Redis 缓存键(有效期约 7200s, 提前 200s 失效)
_WECHAT_MP_TOKEN_KEY = _k("promo", "wechat_mp", "token")
# 内存回退态(STORE_MODE=asyncio 单测环境): token 缓存 + 月度配额计数
_WECHAT_MP_MEM: dict = {"token": "", "tokenExpiresAt": 0.0, "quota": {}}
# 微信 access_token 失效类 errcode(凭证被官方提前刷新/多实例竞争):
# 命中即删缓存重取 token 重试一次, 而非最长 7000s 持续失败
_WECHAT_MP_TOKEN_INVALID_ERRCODES = (40001, 42001)


class WechatMpAPIError(ValueError):
    """公众号 API 业务错(errcode 随异常携带, 供 token 失效自愈判别)"""

    def __init__(self, errtext: str, errcode: int = 0):
        super().__init__(errtext)
        self.errcode = errcode


def wechat_mp_send_mode() -> str:
    """公众号发送形态: preview(默认, 试收验证) / send(正式群发)

    安全默认 preview——凭证到位后先给运营者 openid 试收,
    人工确认版式后再切 send 正式群发。
    """
    mode = (os.environ.get("PROMO_WECHAT_MP_SEND_MODE", "")
            or WECHAT_MP_SEND_MODE_PREVIEW).strip().lower()
    return (mode if mode in (WECHAT_MP_SEND_MODE_PREVIEW,
                             WECHAT_MP_SEND_MODE_SEND)
            else WECHAT_MP_SEND_MODE_PREVIEW)


def wechat_mp_monthly_cap() -> int:
    """月度群发配额上限(默认 4=认证服务号平台硬限)"""
    try:
        return max(1, int(os.environ.get("PROMO_WECHAT_MP_MONTHLY_CAP",
                                        WECHAT_MP_MONTHLY_CAP_DEFAULT)))
    except ValueError:
        return WECHAT_MP_MONTHLY_CAP_DEFAULT


def _wechat_mp_month_key() -> str:
    """自然月键(配额按月结算)

    微信配额按北京时间自然月结算——用 UTC 会在每月 1 日
    00:00-08:00 错位到上月, 导致配额提前耗尽/延迟恢复。
    """
    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m")


def _wechat_mp_errtext(body) -> str:
    """公众号 errcode 业务错提取(微信惯例: HTTP 200 + errcode)

    40164=出口 IP 不在白名单——access_token 硬要求: 生产出口 IP 须加入
    mp.weixin.qq.com 设置与开发→基本配置→IP 白名单, 否则 stable_token
    直接被拒。出口 IP 从 PROMO_WECHAT_MP_EGRESS_IP 环境变量读取
    (不硬编码: 随回执落库, 写死会外泄基础设施信息且 IP 变更后误导)。
    """
    if not isinstance(body, dict):
        return ""
    code = body.get("errcode")
    if code in (None, 0):
        return ""
    msg = str(body.get("errmsg", ""))[:160]
    if code == 40164:
        egress_ip = os.environ.get("PROMO_WECHAT_MP_EGRESS_IP", "")
        ip_hint = (f"出口IP {egress_ip} " if egress_ip else "服务器出口IP ")
        return (f"errcode=40164 {msg}；请将生产{ip_hint}"
                f"加入公众号IP白名单"
                f"(设置与开发→基本配置→IP白名单)")
    return f"errcode={code} {msg}"


def _wechat_mp_article_html(content: dict) -> str:
    """正文 → 公众号图文 HTML(段落 <p> + 话题尾注; 转义防注入)"""
    body = content.get("body", "") or ""
    paras = [html_lib.escape(p.strip())
             for p in body.splitlines() if p.strip()]
    tags = (content.get("hashtags", "") or "").strip()
    if tags:
        paras.append(html_lib.escape(tags))
    return "".join(f"<p>{p}</p>" for p in paras) or "<p></p>"


class PromoChannelService:
    """发布通道(真实平台 API + mock 回退)与百度 SEO 提交"""

    def __init__(self, repo: PromoRepository = PromoRepository()):
        self.repo = repo

    # ============================================================
    # 通道状态
    # ============================================================

    def channel_status(self) -> list[dict]:
        """各平台通道配置状态(看板/排障)"""
        rows = []
        for platform in PROMO_PLATFORMS:
            key = channel_key(platform)
            effective = (CHANNEL_MODE_REAL if
                         channel_mode() == CHANNEL_MODE_REAL and key
                         else CHANNEL_MODE_MOCK)
            rows.append({
                "platform": platform,
                "mode": channel_mode(),
                "keyConfigured": bool(key),
                "effectiveMode": effective,
                "authStyle": PLATFORM_AUTH_STYLES.get(platform, ""),
                "endpoint": platform_endpoint(platform),
            })
        return rows

    # ============================================================
    # 发布(mock 确定性回执 / real 平台 API / 失败回退)
    # ============================================================

    async def publish_to_platform(self, content: dict,
                                  hotspot: dict = None) -> dict:
        """发布单条内容到平台, 返回统一回执

        Returns:
            {"mode": "real|mock|mock_fallback", "platform",
             "publishId", "exposureEstimate", "error"}
        """
        platform = content.get("platform", "")
        heat = float((hotspot or {}).get("heat", 0))
        mock_receipt = self._mock_receipt(platform, content, heat)
        if channel_mode() != CHANNEL_MODE_REAL:
            return mock_receipt
        key = channel_key(platform)
        if not key:
            if platform in RPA_PLATFORMS:
                # RPA 通道平台无 API 凭证概念(官方无发布 API)——
                # 出队后进入 RPA 待发布清单, 浏览器自动化执行
                mock_receipt["mode"] = CHANNEL_MODE_RPA_PENDING
                mock_receipt["error"] = (
                    "待 RPA 通道执行(创作者中心浏览器发布)")
                return mock_receipt
            # real 模式但该平台未配置凭证 → 可观测回退
            mock_receipt["mode"] = CHANNEL_MODE_MOCK_FALLBACK
            mock_receipt["error"] = (f"通道凭证未配置(PROMO_CHANNEL_"
                                     f"{platform.upper()}_KEY)")
            return mock_receipt
        try:
            if platform == PROMO_PLATFORM_WECHAT_MP:
                # 公众号专用轨: 多步协议(token→素材→草稿→群发),
                # 不走通用单端点 _publish_real 范式
                return await self._publish_wechat_mp(
                    key, content, mock_receipt)
            return await self._publish_real(platform, key, content,
                                             mock_receipt)
        except Exception as exc:
            logger.warning("promo_channel_real_failed platform=%s: %s",
                           platform, exc)
            mock_receipt["mode"] = CHANNEL_MODE_MOCK_FALLBACK
            mock_receipt["error"] = str(exc)[:200]
            return mock_receipt

    async def _publish_real(self, platform: str, key: str,
                            content: dict, mock_receipt: dict) -> dict:
        """真实平台 API 发布(按平台认证风格构造请求, 2026-09-02 校准)

        认证风格(PLATFORM_AUTH_STYLES):
            - form  (微博): 表单 access_token + status
            - query (微信系): access_token 查询参数 + JSON body
            - header(开放平台): access-token 请求头 + JSON body
        响应解析按 PUBLISH_ID_ALIASES 兼容各平台字段; HTTP 错误
        提取响应体真实报错(勿只抛 'HTTP Error 400')。
        """
        endpoint = platform_endpoint(platform)
        if not endpoint:
            raise ValueError(f"平台无 API 端点({platform}, 可配 "
                             f"PROMO_CHANNEL_{platform.upper()}_URL)")
        style = PLATFORM_AUTH_STYLES.get(platform, AUTH_STYLE_HEADER)
        text = " ".join(x for x in (content.get("title", ""),
                                    content.get("body", "")) if x)
        if content.get("hashtags"):
            text = f"{text} {content['hashtags']}".strip()
        if style == AUTH_STYLE_FORM:
            # 微博: 表单字段(share.json 实测校准)
            data = urllib.parse.urlencode(
                {"access_token": key,
                 "status": text[:1000]}).encode("utf-8")
            url = endpoint
            headers = {"Content-Type":
                       "application/x-www-form-urlencoded"}
        elif style == AUTH_STYLE_QUERY:
            # 微信系: access_token 查询参数 + JSON body
            sep = "&" if "?" in endpoint else "?"
            url = (f"{endpoint}{sep}access_token="
                   f"{urllib.parse.quote(key)}")
            data = self._json_payload(content)
            headers = {"Content-Type": "application/json"}
        else:
            # 开放平台惯例: access-token 请求头 + JSON body
            url = endpoint
            data = self._json_payload(content)
            headers = {"Content-Type": "application/json",
                       "access-token": key}
        request = urllib.request.Request(
            url, data=data, headers=headers, method="POST")

        def _read():
            # C2: 同步 urlopen 包进 to_thread, 避免阻塞事件循环
            with urllib.request.urlopen(request,
                                        timeout=_HTTP_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))

        try:
            body = await asyncio.to_thread(_read)
        except urllib.error.HTTPError as exc:
            raise ValueError(
                f"平台API拒绝({_http_error_detail(exc)})") from exc
        publish_id = _extract_publish_id(body)
        if not publish_id:
            raise ValueError(
                f"平台响应缺少 publishId: {str(body)[:120]}")
        return {
            "mode": CHANNEL_MODE_REAL,
            "platform": platform,
            "publishId": publish_id,
            "exposureEstimate": mock_receipt["exposureEstimate"],
            "error": "",
        }

    # ============================================================
    # 微信公众号群发 API 专用轨(wechat_mp, 2026-09-27)
    # ============================================================

    async def _publish_wechat_mp(self, key: str, content: dict,
                                 mock_receipt: dict) -> dict:
        """公众号群发四步协议: token→封面素材→草稿→preview|sendall

        凭证格式 appid|secret; 发送形态 PROMO_WECHAT_MP_SEND_MODE:
            preview(默认) → mass/preview 发给指定 openid 试收,
                不计月度配额(上线前版式验证)
            send → mass/sendall 全体群发, 认证服务号自然月硬限
                4 次(独立月度配额闸门, 与发布队列日上限并行)
        """
        parts = [p.strip() for p in (key or "").split("|") if p.strip()]
        if len(parts) != 2:
            raise ValueError("公众号凭证格式应为 appid|secret"
                            "(PROMO_CHANNEL_WECHAT_MP_KEY)")
        app_id, secret = parts
        send_mode = wechat_mp_send_mode()
        # 前置校验(fail-fast, 不打平台 API): preview 需试收 openid,
        # send 需月度配额余量
        if send_mode == WECHAT_MP_SEND_MODE_PREVIEW:
            openid = (os.environ.get(
                "PROMO_WECHAT_MP_PREVIEW_OPENID", "")
                or "").strip()
            if not openid:
                raise ValueError(
                    "preview 形态需配置试收 openid(PROMO_WECHAT_MP_"
                    "PREVIEW_OPENID, 运营者微信关注公众号后后台可见)")
        else:
            await self._wechat_mp_quota_guard()
        # token 先行获取(协议顺序 token→封面; 同时预热缓存, 后续
        # 各步骤经 _wechat_mp_call 从缓存取, 正常链路不再二次请求)
        await self._wechat_mp_token(app_id, secret)
        # 已创建的微信侧资源 ID(C6: 失败路径回执携带, 供人工清理
        # /复用重发——素材与草稿不随失败自动回收)
        thumb_media_id = ""
        draft_media_id = ""
        try:
            # 1) 封面(品牌卡片, /api/promo-cover 确定性渲染)
            cover_png = await self._wechat_mp_cover_bytes(content)
            thumb_media_id = await self._wechat_mp_call(
                app_id, secret, self._wechat_mp_add_material,
                cover_png,
                f"promo_cover_{content.get('contentId', 0)}.png")
            # 2) 草稿(图文消息)
            draft_media_id = await self._wechat_mp_call(
                app_id, secret, self._wechat_mp_draft_add,
                content, thumb_media_id)
            # 3) 发送(preview 试收不计配额 / sendall 群发计配额)
            if send_mode == WECHAT_MP_SEND_MODE_PREVIEW:
                msg_id = await self._wechat_mp_call(
                    app_id, secret, self._wechat_mp_mass_preview,
                    draft_media_id, openid)
            else:
                try:
                    msg_id = await self._wechat_mp_call(
                        app_id, secret, self._wechat_mp_mass_sendall,
                        draft_media_id)
                except (urllib.error.URLError, TimeoutError) as exc:
                    # C3: 网络类异常(超时/连接中断; HTTPError 已在
                    # _wechat_mp_read 内转业务错不会到此)——微信侧
                    # 可能已群发成功, 结果未知: 不走失败回退(自动
                    # 重试会双发烧配额), 也不标成功, 留证据待人工
                    # 在公众号后台核对; 配额不计数(不重复扣减)
                    logger.warning(
                        "wechat_mp_sendall_unknown_result: %s", exc)
                    return {
                        "mode": CHANNEL_MODE_UNKNOWN,
                        "platform": PROMO_PLATFORM_WECHAT_MP,
                        "publishId": "",
                        "exposureEstimate":
                            mock_receipt["exposureEstimate"],
                        "error": (f"群发结果未知(网络异常: {exc}), "
                                  "需人工在公众号后台核对"),
                        "sendMode": send_mode,
                        "mediaId": draft_media_id,
                        "thumbMediaId": thumb_media_id,
                    }
                await self._wechat_mp_quota_consume()
        except Exception as exc:
            # C6: 失败回执携带已创建的素材/草稿 media_id——微信后台
            # 已残留孤儿资源, 供人工清理或复用重发(成功路径之外
            # 唯一的 ID 留痕)
            logger.warning("promo_channel_real_failed platform=%s: %s",
                           PROMO_PLATFORM_WECHAT_MP, exc)
            mock_receipt["mode"] = CHANNEL_MODE_MOCK_FALLBACK
            mock_receipt["error"] = str(exc)[:200]
            if thumb_media_id:
                mock_receipt["thumbMediaId"] = thumb_media_id
            if draft_media_id:
                mock_receipt["mediaId"] = draft_media_id
            return mock_receipt
        return {
            "mode": CHANNEL_MODE_REAL,
            "platform": PROMO_PLATFORM_WECHAT_MP,
            "publishId": str(msg_id or ""),
            "exposureEstimate": mock_receipt["exposureEstimate"],
            "error": "",
            "sendMode": send_mode,
            "mediaId": draft_media_id,
        }

    async def _wechat_mp_token(self, app_id: str,
                               secret: str) -> str:
        """stable_token 换取 access_token(Redis 缓存 ~2h, 内存回退)

        凭据轮换免人工: stable_token 长命(相对经典 getaccess_token
        可控刷新), 缓存提前 200s 失效防边界 40125/40001。
        """
        if is_redis_mode():
            client = await get_redis_client()
            cached = await client.get(_WECHAT_MP_TOKEN_KEY)
            if cached:
                return str(cached)
        elif (_WECHAT_MP_MEM["token"]
                and _WECHAT_MP_MEM["tokenExpiresAt"] > time.time()):
            return _WECHAT_MP_MEM["token"]
        base = platform_endpoint(PROMO_PLATFORM_WECHAT_MP)
        payload = json.dumps({
            "grant_type": "client_credential",
            "appid": app_id, "secret": secret,
        }, ensure_ascii=False).encode("utf-8")
        body = await self._wechat_mp_post_json(
            f"{base}/cgi-bin/stable_token", payload)
        token = str(body.get("access_token") or "")
        if not token:
            raise ValueError("公众号 stable_token 响应缺少 "
                            f"access_token: {str(body)[:120]}")
        ttl = max(60, int(body.get("expires_in", 7200) or 7200) - 200)
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(_WECHAT_MP_TOKEN_KEY, token, ex=ttl)
        else:
            _WECHAT_MP_MEM["token"] = token
            _WECHAT_MP_MEM["tokenExpiresAt"] = time.time() + ttl
        return token

    async def _wechat_mp_cover_bytes(self, content: dict) -> bytes:
        """品牌卡片封面下载(PromoCoverService 确定性渲染端点)"""
        cid = content.get("contentId", 0)
        request = urllib.request.Request(
            f"{SITE_BASE_URL}/api/promo-cover/{cid}.png")

        def _download():
            # C2: 同步下载包进 to_thread, 避免阻塞事件循环
            with urllib.request.urlopen(request,
                                        timeout=_HTTP_TIMEOUT) as resp:
                return resp.read()

        try:
            data = await asyncio.to_thread(_download)
        except urllib.error.HTTPError as exc:
            raise ValueError(
                f"封面下载失败({_http_error_detail(exc)})") from exc
        if not data.startswith(b"\x89PNG"):
            raise ValueError(f"封面响应非PNG(contentId={cid})")
        return data

    async def _wechat_mp_add_material(self, token: str, png: bytes,
                                      filename: str) -> str:
        """永久图片素材上传(add_material, multipart) → media_id"""
        base = platform_endpoint(PROMO_PLATFORM_WECHAT_MP)
        url = f"{base}/cgi-bin/material/add_material"
        boundary = "zhuxiang-promo-20260927"
        head = (f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="media"; '
                f'filename="{filename}"; filetype="image/png"\r\n'
                "Content-Type: image/png\r\n\r\n").encode("utf-8")
        tail = f"\r\n--{boundary}--\r\n".encode("utf-8")
        request = urllib.request.Request(
            f"{url}?type=IMAGE&access_token="
            f"{urllib.parse.quote(token)}",
            data=head + png + tail,
            headers={"Content-Type":
                     f"multipart/form-data; boundary={boundary}"},
            method="POST")
        body = await self._wechat_mp_read(request)
        media_id = str(body.get("media_id") or "")
        if not media_id:
            raise ValueError(f"封面素材上传响应缺少 media_id: "
                             f"{str(body)[:120]}")
        return media_id

    async def _wechat_mp_draft_add(self, token: str, content: dict,
                                   thumb_media_id: str) -> str:
        """图文草稿新建(draft/add) → media_id"""
        base = platform_endpoint(PROMO_PLATFORM_WECHAT_MP)
        plain = " ".join(x for x in (content.get("body", ""),
                                     content.get("hashtags", "")) if x)
        source_url = (f"{SITE_BASE_URL}/r/{content['shortCode']}"
                      if content.get("shortCode") else "")
        payload = json.dumps({"articles": [{
            "title": (content.get("title", "") or "")[:64],
            "author": "竹香酒业",
            "digest": plain[:120],
            "thumb_media_id": thumb_media_id,
            "content": _wechat_mp_article_html(content),
            "content_source_url": source_url,
            "need_open_comment": 0,
            "only_fans_can_comment": 0,
        }]}, ensure_ascii=False).encode("utf-8")
        body = await self._wechat_mp_post_json(
            f"{base}/cgi-bin/draft/add", payload, token=token)
        media_id = str(body.get("media_id") or "")
        if not media_id:
            raise ValueError(f"草稿创建响应缺少 media_id: "
                             f"{str(body)[:120]}")
        return media_id

    async def _wechat_mp_mass_preview(self, token: str, media_id: str,
                                      openid: str) -> str:
        """预览(mass/preview): 指定 openid 试收, 不计月度配额"""
        base = platform_endpoint(PROMO_PLATFORM_WECHAT_MP)
        payload = json.dumps({
            "touser": openid,
            "mpnews": {"media_id": media_id},
            "msgtype": "mpnews",
        }, ensure_ascii=False).encode("utf-8")
        body = await self._wechat_mp_post_json(
            f"{base}/cgi-bin/message/mass/preview", payload, token=token)
        return str(body.get("msg_id") or "")

    async def _wechat_mp_mass_sendall(self, token: str,
                                      media_id: str) -> str:
        """正式群发(mass/sendall): 全体关注者(认证服务号)"""
        base = platform_endpoint(PROMO_PLATFORM_WECHAT_MP)
        payload = json.dumps({
            "filter": {"is_to_all": True},
            "mpnews": {"media_id": media_id},
            "msgtype": "mpnews",
        }, ensure_ascii=False).encode("utf-8")
        body = await self._wechat_mp_post_json(
            f"{base}/cgi-bin/message/mass/sendall", payload, token=token)
        return str(body.get("msg_id") or "")

    async def _wechat_mp_post_json(self, url: str, data: bytes,
                                   token: str = "") -> dict:
        """公众号 API POST(JSON)——access_token 查询参数 + errcode 检查"""
        if token:
            sep = "&" if "?" in url else "?"
            url = (f"{url}{sep}access_token="
                   f"{urllib.parse.quote(token)}")
        request = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json"}, method="POST")
        return await self._wechat_mp_read(request)

    async def _wechat_mp_read(self, request) -> dict:
        """公众号 API 响应统一读取(HTTP 错 + errcode 业务错)"""

        def _read():
            # C2: 同步 urlopen 包进 to_thread, 避免单次发布最多
            # 5 次串行 HTTP ×10s 超时挂起全站事件循环
            with urllib.request.urlopen(request,
                                        timeout=_HTTP_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))

        try:
            body = await asyncio.to_thread(_read)
        except urllib.error.HTTPError as exc:
            raise ValueError(
                f"公众号API拒绝({_http_error_detail(exc)})") from exc
        err = _wechat_mp_errtext(body)
        if err:
            # C4: errcode 随异常携带, 供 _wechat_mp_call 判定
            # 40001/42001 token 失效自愈
            code = (body.get("errcode")
                    if isinstance(body, dict) else 0) or 0
            raise WechatMpAPIError(f"公众号API拒绝({err})",
                                   errcode=int(code))
        return body

    async def _wechat_mp_invalidate_token(self) -> None:
        """删除 token 缓存(40001/42001 自愈第一步: 强制重取)"""
        if is_redis_mode():
            client = await get_redis_client()
            await client.delete(_WECHAT_MP_TOKEN_KEY)
        else:
            _WECHAT_MP_MEM["token"] = ""
            _WECHAT_MP_MEM["tokenExpiresAt"] = 0.0

    async def _wechat_mp_call(self, app_id: str, secret: str,
                              fn, *args):
        """带 token 的业务调用包装(token 失效自愈, C4)

        token 被微信提前失效(官方强刷/多实例换 token 竞争)时, 缓存
        命中最长 7000s 持续报 40001/42001——捕获后删缓存重取 token
        并重试一次原请求; 仍失败才抛错。
        """
        token = await self._wechat_mp_token(app_id, secret)
        try:
            return await fn(token, *args)
        except WechatMpAPIError as exc:
            if exc.errcode not in _WECHAT_MP_TOKEN_INVALID_ERRCODES:
                raise
            logger.warning("wechat_mp_token_invalid(errcode=%s), 删缓存"
                           "重取后重试一次", exc.errcode)
            await self._wechat_mp_invalidate_token()
            token = await self._wechat_mp_token(app_id, secret)
            return await fn(token, *args)

    async def _wechat_mp_quota_used(self) -> int:
        """本月已群发次数(Redis 计数, 内存回退)"""
        month = _wechat_mp_month_key()
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.get(
                _k("promo", "wechat_mp", "quota", month))
            return int(raw or 0)
        return int(_WECHAT_MP_MEM["quota"].get(month, 0))

    async def _wechat_mp_quota_guard(self) -> None:
        """月度配额闸门(send 前置; 认证服务号自然月 4 次)"""
        used = await self._wechat_mp_quota_used()
        cap = wechat_mp_monthly_cap()
        if used >= cap:
            raise ValueError(
                f"本月公众号群发配额已用尽({used}/{cap}, 认证服务号"
                "自然月硬限)——次月自动恢复")

    async def _wechat_mp_quota_consume(self) -> None:
        """群发成功后配额计数 +1(月键 TTL 40 天防泄漏)"""
        month = _wechat_mp_month_key()
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("promo", "wechat_mp", "quota", month)
            await client.incr(key)
            await client.expire(key, 40 * 24 * 3600)
        else:
            _WECHAT_MP_MEM["quota"][month] = (
                _WECHAT_MP_MEM["quota"].get(month, 0) + 1)

    @staticmethod
    def _json_payload(content: dict) -> bytes:
        """JSON 请求体(微信系/开放平台通用字段)"""
        return json.dumps({
            "title": content.get("title", ""),
            "content": content.get("body", ""),
            "hashtags": content.get("hashtags", ""),
        }, ensure_ascii=False).encode("utf-8")

    @staticmethod
    def _mock_receipt(platform: str, content: dict, heat: float) -> dict:
        """确定性 mock 回执(与 P0 模拟轨口径一致)"""
        return {
            "mode": CHANNEL_MODE_MOCK,
            "platform": platform,
            "publishId": f"PUB-{platform}-{content.get('contentId', 0)}",
            "exposureEstimate": int(heat * 10000 * 0.3),
            "error": "",
        }

    # ============================================================
    # 百度普通收录推送(Urls 主动推送, Mock-first)
    # ============================================================

    async def baidu_push(self, urls: list[str]) -> dict:
        """推送 URL 列表到百度普通收录

        Returns:
            {"mode": "real|mock", "success": N, "remain": N,
             "failed": N, "error": str, "urls": [...]}
        """
        urls = [u for u in (urls or []) if u]
        if not urls:
            return {"mode": CHANNEL_MODE_MOCK, "success": 0, "remain": 0,
                    "failed": 0, "error": "URL 列表为空", "urls": []}
        if not all(baidu_push_config()):
            # mock 轨: 确定性成功回执(全部受理)
            return {"mode": CHANNEL_MODE_MOCK, "success": len(urls),
                    "remain": max(0, 3000 - len(urls)), "failed": 0,
                    "error": "", "urls": urls}
        try:
            return await self._baidu_push_real(urls)
        except Exception as exc:
            logger.warning("promo_baidu_push_failed: %s", exc)
            return {"mode": CHANNEL_MODE_REAL, "success": 0, "remain": 0,
                    "failed": len(urls), "error": str(exc)[:200],
                    "urls": urls}

    async def _baidu_push_real(self, urls: list[str]) -> dict:
        """百度 urls 主动推送(POST data.zz.baidu.com/urls)

        实测校准(2026-09-02): token 无效时返回 HTTP 400 + 响应体
        {"error":400,"message":"token invalid"} —— 须读取响应体
        保留真实报错, 勿只抛 'HTTP Error 400'。
        """
        site, token = baidu_push_config()
        query = urllib.parse.urlencode({"site": site, "token": token})
        data = "\n".join(urls).encode("utf-8")
        request = urllib.request.Request(
            f"{BAIDU_PUSH_ENDPOINT}?{query}", data=data,
            headers={"Content-Type": "text/plain"}, method="POST")

        def _read():
            # C2: 同步 urlopen 包进 to_thread, 避免阻塞事件循环
            with urllib.request.urlopen(request,
                                        timeout=_HTTP_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))

        try:
            body = await asyncio.to_thread(_read)
        except urllib.error.HTTPError as exc:
            raise ValueError(
                f"百度推送被拒({_http_error_detail(exc)})") from exc
        # 响应体携带 error 字段且无成功计数 → 推送被拒(实测口径)
        if (isinstance(body, dict) and body.get("error")
                and not body.get("success")):
            return {
                "mode": CHANNEL_MODE_REAL, "success": 0, "remain": 0,
                "failed": len(urls),
                "error": str(body.get("message")
                             or body.get("error"))[:200],
                "urls": urls,
            }
        # 百度成功响应: {"success": n, "remain": n, "not_same_site": [...]}
        return {
            "mode": CHANNEL_MODE_REAL,
            "success": int(body.get("success", 0)),
            "remain": int(body.get("remain", 0)),
            "failed": len(urls) - int(body.get("success", 0)),
            "error": str(body.get("not_same_site") or "")[:200],
            "urls": urls,
        }

    # ============================================================
    # 已发布内容 URL 收集与推送(幂等: 当日去重)
    # ============================================================

    async def collect_published_urls(self) -> list[str]:
        """收集可推送 URL: sitemap 索引 + 已发布内容短链落地页"""
        urls = [f"{SITE_BASE_URL}/sitemap.xml"]
        contents = await self.repo.list_contents(limit=10000)
        for content in contents:
            code = content.get("shortCode", "")
            if code and content.get("status") == "published":
                urls.append(f"{SITE_BASE_URL}/r/{code}")
        return urls

    async def push_seo(self, force: bool = False) -> dict:
        """SEO 提交入口: 收集 URL → 当日去重 → 百度推送 → 落库

        Args:
            force: True 忽略当日去重(强制重推)

        Returns:
            {"pushId", "mode", "submitted": N, "skipped": N,
             "success": N, "status": "ok|failed"}
        """
        date_key = datetime.now(UTC).strftime("%Y%m%d")
        all_urls = await self.collect_published_urls()
        pushed = set() if force else await self.repo.pushed_urls_today(
            date_key)
        pending_urls = [u for u in all_urls if u not in pushed]
        skipped = len(all_urls) - len(pending_urls)
        if not pending_urls:
            # 当日已全部推送(幂等跳过): 非失败, 落 ok 留痕防运维误报
            push_id = await self.repo.next_id("seo_push")
            record = {
                "pushId": push_id, "dateKey": date_key, "mode": "skipped",
                "urls": [], "submitted": 0, "skipped": skipped,
                "success": 0, "failed": 0, "remain": 0,
                "error": "", "status": SEO_PUSH_STATUS_OK,
                "createdAt": _now_iso(),
            }
            await self.repo.save_seo_push(record)
            logger.info("promo_seo_push skipped(当日已全量推送) "
                        "skipped=%s", skipped)
            return record
        result = await self.baidu_push(pending_urls)
        status = (SEO_PUSH_STATUS_OK
                  if not result["error"] else SEO_PUSH_STATUS_FAILED)
        push_id = await self.repo.next_id("seo_push")
        record = {
            "pushId": push_id,
            "dateKey": date_key,
            "mode": result["mode"],
            "urls": pending_urls,
            "submitted": len(pending_urls),
            "skipped": skipped,
            "success": result["success"],
            "failed": result["failed"],
            "remain": result["remain"],
            "error": result["error"],
            "status": status,
            "createdAt": _now_iso(),
        }
        await self.repo.save_seo_push(record)
        logger.info("promo_seo_push mode=%s submitted=%s success=%s "
                    "skipped=%s", result["mode"], record["submitted"],
                    record["success"], skipped)
        return record

    async def list_pushes(self, limit: int = 50) -> list[dict]:
        return await self.repo.list_seo_pushes(limit=limit)
