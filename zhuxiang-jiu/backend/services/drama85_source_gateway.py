"""85号·短剧数据源网关(Provider 三态, 2026-10-12 真实数据源接入)

模式(DRAMA85_SOURCE_MODE, 对齐 PAY60/批次②③ 范式):
    mock(默认)      MockDramaSource 确定性数据源, 零凭证本地/CI 可测
    mock_fallback   先走真实数据源, 失败/超时自动降级 mock 留痕
                    (fallback/fallbackReason, 采集不中断)
    real            全走真实渠道; 凭证缺失 fail-hard(ValueError)

真实数据源契约(标准 JSON——两类合规供给方任选其一):
    A. 企业开通的第三方短剧数据服务 API(商务采购, 如短剧数据平台)
    B. 自建合规采集 worker(另行部署: 只采公开元数据/评论/字幕,
       不绕登录、不破接口、不采隐私——红线在 worker 侧执行)
       输出本契约即可被 85号直接消费。

契约定义:
    GET {DRAMA85_SOURCE_URL}?offset={o}&n={n}
    Header: X-Api-Key: {DRAMA85_SOURCE_API_KEY}
            X-Timestamp: {unix秒}
            X-Source-Signature: HMAC-SHA256(apikey+timestamp+path,
                                            DRAMA85_SOURCE_SECRET)
            (DRAMA85_SOURCE_SECRET 未配置时仅 Api-Key 鉴权)
    响应: {"dramas": [{
              "title": str,
              "platforms": [str],
              "episodes": int, "durationSec": int,
              "tags": [str],
              "metrics": {"plays": int, "likes": int,
                          "comments": int, "shares": int,
                          "completionRate": float(0-1)},
              "commentsData": [{"platform": str, "text": str}],
              "subtitleText": str(含[开场钩子][核心冲突][中段反转]
                                  [结尾悬念]标注),
              "structure": {"hook":..., "conflict":...,
                            "twist":..., "suspense":...}(可选直传,
                            优先于 subtitleText 标注解析)
            }],
           "hasMore": bool}

观测: real 调用记 gatewayCall 留痕(耗时/结果/降级原因);
      QPS 频控(DRAMA85_SOURCE_QPS, 默认 1)防数据服务限流。
"""
import hashlib
import hmac
import logging
import os
import time
from urllib.parse import urlparse

logger = logging.getLogger("services.drama85_source_gateway")

DEFAULT_QPS = 1.0
FETCH_TIMEOUT = 10.0
MAX_ATTEMPTS = 3


def _env(key: str) -> str:
    return (os.environ.get(key) or "").strip()


def source_mode() -> str:
    m = (_env("DRAMA85_SOURCE_MODE") or "mock").strip().lower()
    return m if m in ("mock", "mock_fallback", "real") else "mock"


def _seed_int(text: str, salt: str = "") -> int:
    """确定性种子(md5——跨进程稳定)"""
    return int(hashlib.md5(
        f"{salt}:{text}".encode("utf-8")).hexdigest()[:12], 16)


# ============================================================
# Mock 数据源(确定性, 原 service 内实现迁入)
# ============================================================

_TITLE_A = ("隐婚", "闪婚", "替嫁", "逆袭", "归来", "重生之",
            "破产", "顶替", "双面", "神秘")
_TITLE_B = ("千金", "赘婿", "总裁", "保镖", "弃少", "厨神",
            "医妃", "主播", "股东", "掌柜")
_HOOKS = ("婚礼上被当众退婚", "入职第一天被顶替身份",
          "家产被亲叔叔霸占", "参赛作品被人窃取",
          "救命恩人竟是仇人之子", "体检报告拿错了")
_CONFLICTS = ("全家逼迫她放弃继承权", "合伙人卷款跑路留下巨债",
              "对手用卑劣手段抢走订单", "兄弟反目争夺掌门之位")
_TWISTS = ("原来他才是真正的继承人", "账本里藏着父亲留的证据",
           "对手的把柄意外落入手中", "失踪多年的母亲突然现身")
_SUSPENSES = ("门后传来陌生的脚步声", "电话那头只有一段录音",
              "合同最后一页的名字让人愣住", "信封里掉出一把钥匙")
_COMMENTS_POS = ("看得停不下来, 追更了", "反转太绝了封神",
                 "女主终于硬气了爽", "演技在线剧情紧凑",
                 "每天蹲更新根本不够看")
_COMMENTS_NEG = ("节奏太拖了弃剧", "广告植入有点硬",
                 "配角戏份太多", "结局仓促了")
_PLATFORMS = ("douyin", "kuaishou", "shipinhao", "bili")


class MockDramaSource:
    """模拟通道(确定性短剧数据: 元数据+指标+评论+四段标注字幕)"""

    def fetch(self, n: int, offset: int = 0) -> list[dict]:
        rows = []
        for i in range(offset, offset + n):
            h = _seed_int(f"drama-src-{i}", "fetch")
            platforms = sorted({
                _PLATFORMS[(h >> s) % len(_PLATFORMS)]
                for s in (0, 7, 13)})
            plays = 50_000 + (h % 9_500_000)
            like_rate = 0.02 + (h % 70) / 1000.0
            comment_rate = 0.002 + (h % 20) / 10_000
            completion = 0.25 + (h % 60) / 100.0
            fraud = ((h >> 20) % 17 == 0)
            if fraud:
                plays *= 12
                like_rate = 0.003
                completion = 0.12
            comments = []
            for c in range(4):
                ch = _seed_int(f"cm-{i}-{c}", "c")
                text = (_COMMENTS_POS[ch % len(_COMMENTS_POS)]
                        if (ch >> 3) % 5 else
                        _COMMENTS_NEG[ch % len(_COMMENTS_NEG)])
                comments.append({"platform": platforms[
                    ch % len(platforms)], "text": text})
            rows.append({
                "sourceSeq": i,
                "title": (f"{_TITLE_A[(h >> 4) % len(_TITLE_A)]}"
                          f"{_TITLE_B[(h >> 9) % len(_TITLE_B)]}"
                          f"第{(h >> 14) % 3 + 1}季"),
                "platforms": platforms,
                "episodes": 40 + (h % 60),
                "durationSec": 60 + (h % 120),
                "tags": ["都市", "情感"],
                "metrics": {
                    "plays": int(plays),
                    "likes": int(plays * like_rate),
                    "comments": int(plays * comment_rate),
                    "shares": int(plays * like_rate * 0.3),
                    "completionRate": round(completion, 3),
                },
                "commentsData": comments,
                "subtitleText": (
                    f"[开场钩子]{_HOOKS[(h >> 5) % len(_HOOKS)]}。"
                    f"[核心冲突]{_CONFLICTS[(h >> 8) % len(_CONFLICTS)]}。"
                    f"[中段反转]{_TWISTS[(h >> 11) % len(_TWISTS)]}。"
                    f"[结尾悬念]{_SUSPENSES[(h >> 15) % len(_SUSPENSES)]}。"),
            })
        return rows


# ============================================================
# Real 数据源(契约 HTTP 采集: 鉴权+频控+重试+字段容错)
# ============================================================

def _dig(obj, path: str):
    """点路径取数('$.data.rows'/'data.rows' → 嵌套取值; 失败 None)"""
    node = obj
    for part in str(path or "").lstrip("$").strip(".").split("."):
        if not part:
            continue
        if isinstance(node, dict):
            node = node.get(part)
        else:
            return None
    return node


def load_field_map() -> dict | None:
    """第三方字段映射(DRAMA85_SOURCE_FIELD_MAP, JSON env)

    结构(全部条目内字段名或点路径; list 为剧集数组点路径, 必需):
        {"list": "$.data.rows", "title": "dramaName",
         "platforms": "platform", "episodes": "episodeCount",
         "durationSec": "duration", "tags": "tags",
         "plays": "playCnt", "likes": "likeCnt",
         "comments": "commentCnt", "shares": "shareCnt",
         "completionRate": "finishRate",
         "commentsData": "hotComments",
         "subtitleText": "synopsis",
         "structure": "storyStructure"}

    Raises:
        ValueError: JSON 非法 / 缺 list/title
    """
    raw = _env("DRAMA85_SOURCE_FIELD_MAP")
    if not raw:
        return None
    import json as _json
    try:
        fm = _json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise ValueError(
            f"DRAMA85_SOURCE_FIELD_MAP JSON 非法: {exc}") from exc
    if not isinstance(fm, dict) or not fm.get("list") \
            or not fm.get("title"):
        raise ValueError(
            "DRAMA85_SOURCE_FIELD_MAP 须含 list(剧集数组路径)与"
            " title(标题字段)")
    return fm


def apply_field_map(resp: dict, field_map: dict) -> dict:
    """第三方原始响应 → 标准契约响应(字段映射+类型容错)

    commentsData 兼容三种形态: 字符串列表 / [{content|text}] /
    标准 [{platform, text}]。
    """
    raw_list = _dig(resp, field_map["list"])
    dramas = []
    for raw in (raw_list if isinstance(raw_list, list) else []):
        if not isinstance(raw, dict):
            continue

        def f(key, default=None):
            v = _dig(raw, field_map.get(key, ""))
            return default if v is None else v
        # 评论兼容形态归一
        comments = []
        raw_comments = f("commentsData")
        if isinstance(raw_comments, list):
            for c in raw_comments:
                if isinstance(c, str):
                    comments.append({"platform": "unknown",
                                     "text": c})
                elif isinstance(c, dict):
                    comments.append({
                        "platform": str(c.get("platform")
                                        or "unknown"),
                        "text": str(c.get("text")
                                    or c.get("content") or "")[:200],
                    })
        platforms = f("platforms")
        tags = f("tags")
        dramas.append({
            "title": str(f("title") or "").strip()[:60],
            "platforms": ([str(p) for p in platforms][:5]
                          if isinstance(platforms, list)
                          else ([str(platforms)] if platforms
                                else ["unknown"])),
            "episodes": int(f("episodes") or 0),
            "durationSec": int(float(f("durationSec") or 0)),
            "tags": ([str(t) for t in tags][:8]
                     if isinstance(tags, list) else []),
            "metrics": {
                "plays": int(float(f("plays") or 0)),
                "likes": int(float(f("likes") or 0)),
                "comments": int(float(f("comments") or 0)),
                "shares": int(float(f("shares") or 0)),
                "completionRate": float(f("completionRate") or 0),
            },
            "commentsData": comments[:20],
            "subtitleText": str(f("subtitleText") or "")[:2000],
            "structure": (f("structure")
                          if isinstance(f("structure"), dict)
                          else None),
        })
    return {"dramas": dramas,
            "hasMore": bool(len(dramas))}


class RealDramaSource:
    """真实数据源采集器(契约见模块 docstring; fail-hard 凭证)"""

    REQUIRED = ("DRAMA85_SOURCE_URL", "DRAMA85_SOURCE_API_KEY")

    def __init__(self, http_get=None):
        self._http_get = http_get
        self._last_call = 0.0

    def _credentials(self) -> dict:
        missing = [k for k in self.REQUIRED if not _env(k)]
        if missing:
            raise ValueError(
                f"真实数据源凭证未配置({','.join(missing)}), "
                f"经 .env 注入——real 渠道 fail-hard 不静默降级"
                f"(契约见 drama85_source_gateway 模块注释)")
        return {k: _env(k) for k in self.REQUIRED}

    def _throttle(self) -> None:
        """QPS 频控(防数据服务限流; DRAMA85_SOURCE_QPS 默认 1)"""
        try:
            qps = float(_env("DRAMA85_SOURCE_QPS") or DEFAULT_QPS)
        except ValueError:
            qps = DEFAULT_QPS
        qps = max(0.1, min(10.0, qps))
        wait = (1.0 / qps) - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def _headers(self, path_qs: str) -> dict:
        creds = self._credentials()
        timestamp = str(int(time.time()))
        headers = {
            "X-Api-Key": creds["DRAMA85_SOURCE_API_KEY"],
            "X-Timestamp": timestamp,
        }
        secret = _env("DRAMA85_SOURCE_SECRET")
        if secret:
            headers["X-Source-Signature"] = hmac.new(
                secret.encode("utf-8"),
                f"{creds['DRAMA85_SOURCE_API_KEY']}"
                f"{timestamp}{path_qs}".encode("utf-8"),
                hashlib.sha256).hexdigest()
        return headers

    @staticmethod
    def _normalize(raw: dict, seq: int) -> dict | None:
        """契约条目 → 内部行(字段容错: 缺关键项丢弃)"""
        if not isinstance(raw, dict) or not str(
                raw.get("title") or "").strip():
            return None
        m = raw.get("metrics") or {}
        return {
            "sourceSeq": seq,
            "title": str(raw["title"]).strip()[:60],
            "platforms": [str(p) for p in (raw.get("platforms")
                                           or ["unknown"])][:5],
            "episodes": int(raw.get("episodes") or 0),
            "durationSec": int(raw.get("durationSec") or 0),
            "tags": [str(t) for t in (raw.get("tags") or [])][:8],
            "metrics": {
                "plays": int(m.get("plays") or 0),
                "likes": int(m.get("likes") or 0),
                "comments": int(m.get("comments") or 0),
                "shares": int(m.get("shares") or 0),
                "completionRate": min(
                    1.0, max(0.0, float(m.get("completionRate")
                                        or 0.0))),
            },
            "commentsData": [
                {"platform": str(c.get("platform") or "unknown"),
                 "text": str(c.get("text") or "")[:200]}
                for c in (raw.get("commentsData") or [])[:20]],
            "subtitleText": str(raw.get("subtitleText") or "")[:2000],
            "structure": raw.get("structure") or None,
        }

    async def fetch(self, n: int, offset: int = 0) -> list[dict]:
        import json as _json
        creds = self._credentials()
        parsed = urlparse(creds["DRAMA85_SOURCE_URL"])
        path_qs = parsed.path or "/"
        last_err = None
        for attempt in range(MAX_ATTEMPTS):
            self._throttle()
            url = (f"{creds['DRAMA85_SOURCE_URL']}"
                   f"{'&' if '?' in creds['DRAMA85_SOURCE_URL'] else '?'}"
                   f"offset={int(offset)}&n={int(n)}")
            started = time.time()
            try:
                status, resp = await self._http_get(
                    url, headers=self._headers(path_qs))
                elapsed = round((time.time() - started) * 1000, 1)
                logger.info("drama85_source_call attempt=%s "
                            "status=%s elapsed_ms=%s",
                            attempt + 1, status, elapsed)
                resp = resp if isinstance(resp, dict) else {}
                # 适配(auto): 契约直用优先; 无 dramas 键且配置了
                # 第三方字段映射(DRAMA85_SOURCE_FIELD_MAP)→转换
                if status == 200 and not isinstance(
                        resp.get("dramas"), list):
                    field_map = load_field_map()
                    if field_map:
                        resp = apply_field_map(resp, field_map)
                        logger.info("drama85_field_map_applied "
                                    "rows=%s",
                                    len(resp.get("dramas") or []))
                if status == 200 and isinstance(
                        resp.get("dramas"), list):
                    rows = []
                    for i, raw in enumerate(resp["dramas"]):
                        norm = self._normalize(raw, offset + i)
                        if norm:
                            rows.append(norm)
                    return rows
                last_err = ValueError(
                    f"数据源响应异常(HTTP {status}, "
                    f"dramas 条目 {len(resp.get('dramas') or [])}"
                    f"——若第三方格式请配置 DRAMA85_SOURCE_FIELD_MAP)"
                )
            except ValueError:
                raise   # 凭证/配置类错误不重试
            except Exception as exc:   # 网络/超时
                last_err = exc
                logger.warning("drama85_source_failed attempt=%s "
                               "err=%s", attempt + 1, exc)
            time.sleep(0.3 * (attempt + 1))
        raise last_err or ValueError("真实数据源采集失败")


async def _default_http_get(url: str, headers: dict = None):
    """默认传输层(httpx); 测试注入 stub 替代"""
    import httpx
    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT) as client:
        resp = await client.get(url, headers=headers or {})
        try:
            return resp.status_code, resp.json()
        except (ValueError, Exception):
            return resp.status_code, {}


class DramaSourceGateway:
    """数据源分发器(DRAMA85_SOURCE_MODE 三态)"""

    def __init__(self, http_get=None):
        self._mock = MockDramaSource()
        self._real = RealDramaSource(
            http_get or _default_http_get)

    async def fetch(self, n: int, offset: int = 0) -> dict:
        """三态分发: real 失败且 mock_fallback → 降级 mock 留痕

        Returns:
            {rows, sourceMode, source, fallback?, fallbackReason?}
        """
        mode = source_mode()
        if mode == "mock":
            return {"rows": self._mock.fetch(n, offset),
                    "sourceMode": "mock", "source": "mock"}
        try:
            rows = await self._real.fetch(n, offset)
            return {"rows": rows, "sourceMode": mode,
                    "source": "real"}
        except Exception as exc:
            if mode == "mock_fallback":
                logger.warning("drama85_source_fallback reason=%s",
                               exc)
                return {
                    "rows": self._mock.fetch(n, offset),
                    "sourceMode": mode, "source": "mock",
                    "fallback": True,
                    "fallbackReason": str(exc)[:200]}
            raise   # real: fail-hard
