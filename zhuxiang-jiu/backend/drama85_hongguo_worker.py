"""85号·红果漫剧采集 worker(独立脚本, 通道②; 2026-10-13 实测结构)

形态: 红果漫剧分享落地页(kylin.hainanyuyue.com/s/{码}/ → 302 →
  hainanyuyue.com/share/video-animation-share; 红果运营主体海南愉阅,
  og:image 走字节 CDN, robots 无 Disallow 规则)

数据: 页面 SSR 全局变量 window._ROUTER_DATA JSON(样例《牢狱蒙尘》):
  pageData.series_data      主剧: title / serial_count(总集数) /
                             category_list(题材) / series_play_cnt(播放) /
                             like_count(点赞); comment_count 不采(红线)
  pageData.recommend_series 推荐位 ~12 条: series_title / episode_cnt /
                             series_play_cnt / category_name / like_cnt
  —— 通道② 首个带真实播放/点赞指标的源: 一条分享链 = 1 主剧 + ~12 推荐小榜单

env:
  HONGGUO_SHARE_URLS        红果分享链列表(逗号/分号/空白分隔)
  HONGGUO_QPS               限速(默认 1, clamp 0.2~5)
  HONGGUO_CACHE_TTL         页面缓存秒(默认 86400=T+1)
  HONGGUO_PLATFORMS         默认 hongguo
  HONGGUO_INCLUDE_RECOMMEND 推荐位开关(默认 1; 0=仅主剧)

CLI:
  python drama85_hongguo_worker.py fetch --n 50 [--offset 0] [--out f.json]
  python drama85_hongguo_worker.py probe          # 逐链结构摘要
  python drama85_hongguo_worker.py serve --port 8511

85号接入: DRAMA85_SOURCE_MODE=real +
  DRAMA85_SOURCE_URL=http://127.0.0.1:8511/dramas + DRAMA85_SOURCE_API_KEY

合规红线(与悟空 worker 同款, 内置不可绕过):
  只 GET 公开页 / robots Disallow 一律拒采(单链网络失败跳过留痕,
  robots 拒采整批上抛) / QPS 限速 / T+1 缓存 / 评论与收藏数不采
  (commentsData 恒空, comment_count/favorite_cnt 均不映射) / 移动 UA
  (分享页本为浏览器直接打开的公开页)
"""
import argparse
import html
import json
import logging
import os
import re
import sys
import time
from urllib.parse import urlparse

from drama85_wukong_worker import DEFAULT_UA, _robots_allowed, _to_int

logging.basicConfig(
    level=os.environ.get("DRAMA85_LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("drama85.hongguo_worker")

DEFAULT_QPS = 1.0
DEFAULT_TTL = 86400
DEFAULT_PORT = 8511
DEFAULT_PLATFORMS = "hongguo"
FETCH_TIMEOUT = 15.0
MAX_ATTEMPTS = 3
MOBILE_UA = ("Mozilla/5.0 (Linux; Android 13; Pixel 7) "
             "AppleWebKit/537.36 (KHTML, like Gecko) "
             "Chrome/120.0 Mobile Safari/537.36")


def _env(key: str, default: str = "") -> str:
    return (os.environ.get(key) or default).strip()


# ============================================================
# 解析层: _ROUTER_DATA SSR JSON → (主剧 raw, 推荐 raw 列表)
# ============================================================

def _extract_router_data(text: str) -> dict | None:
    """提取 window._ROUTER_DATA = {...} 整对象(字符串感知括号配对);
    无该全局变量 / JSON 非法 → None"""
    m = re.search(r"window\._ROUTER_DATA\s*=\s*", text)
    if not m:
        return None
    i, depth, start = m.end(), 0, m.end()
    in_str, esc = False, False
    while i < len(text):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    try:
        return json.loads(text[start:i + 1])
    except json.JSONDecodeError:
        return None


def _find_page_data(router: dict) -> dict | None:
    """loaderData.*.pageData(含 series_data/recommend_series 的那个)"""
    for v in (router.get("loaderData") or {}).values():
        if isinstance(v, dict) and isinstance(v.get("pageData"), dict):
            pd = v["pageData"]
            if "series_data" in pd or "recommend_series" in pd:
                return pd
    return None


# 新域 novelquickapp.com: Modern.js streaming hydration 形态(2026-10-15 适配)
# 真实数据不在 window._ROUTER_DATA(空壳), 而在
# <script data-fn-name="r" data-fn-args="[loaderKey,&quot;pageData&quot;,{...}]">
# 属性里 —— unescape 后为 [loader_key, prop_key, value] 三元组, value 即 pageData
_FN_ARGS_RE = re.compile(
    r'<script\b[^>]*data-fn-name="r"[^>]*data-fn-args="([^"]*)"[^>]*>')


def _extract_fn_args_page(text: str) -> dict | None:
    """提取 data-fn-args 流注水 payload 中 prop_key=='pageData' 的 value;
    兼容 novelquickapp 漫剧(video-animation-share)与短剧(video-list-share-ssr)"""
    for raw in _FN_ARGS_RE.findall(text):
        try:
            arr = json.loads(html.unescape(raw))
        except (json.JSONDecodeError, TypeError):
            continue
        if (isinstance(arr, list) and len(arr) >= 3
                and arr[1] == "pageData" and isinstance(arr[2], dict)
                and ("series_data" in arr[2] or "recommend_series" in arr[2])):
            return arr[2]
    return None


def _main_raw(sd: dict, page: dict) -> dict:
    """主剧 series_data → 采集 raw(键名与推荐位不同: like_count/serial_count)"""
    tags = sd.get("category_list")
    if not isinstance(tags, list) or not tags:
        tags = [sd["category"]] if sd.get("category") else []
    return {
        "title": sd.get("title"),
        "episodes": sd.get("serial_count"),      # 总集数(准确)
        "tags": [str(t) for t in tags if t],
        "plays": sd.get("series_play_cnt"),
        "likes": sd.get("like_count"),
        "itemId": str(page.get("used_chapter_id") or ""),
    }


def _rec_raw(r: dict) -> dict:
    """推荐位条目 → 采集 raw(series_title/episode_cnt/like_cnt)"""
    return {
        "title": r.get("series_title"),
        "episodes": r.get("episode_cnt"),
        "tags": [str(r["category_name"])] if r.get("category_name") else [],
        "plays": r.get("series_play_cnt"),
        "likes": r.get("like_cnt"),
        "itemId": str(r.get("series_id") or ""),
    }


def _loader_kind(text: str, router: dict | None) -> str:
    """内容类型判定(2026-10-15): 落地页 loader_key 含 video-animation=漫剧(manju),
    video-list=短剧(duanju); 旧域 _ROUTER_DATA 与新域 data-fn-args 双形态通用。
    (SceneTrace 佐证: 海外站将红果漫剧归类 AI 生成内容, contentType 为
    漫剧/短剧分轨观测基础)"""
    keys: list[str] = []
    if router:
        keys = list((router.get("loaderData") or {}).keys())
    if not keys:
        import html as _h
        for raw in _FN_ARGS_RE.findall(text):
            try:
                arr = json.loads(_h.unescape(raw))
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(arr, list) and arr:
                keys.append(str(arr[0]))
    joined = " ".join(keys)
    if "video-animation" in joined:
        return "manju"
    if "video-list" in joined:
        return "duanju"
    return ""


# ============================================================
# 采集器(robots/限速/T+1 缓存红线复用悟空 worker 同款)
# ============================================================

class HongguoCollector:
    """红果分享落地页采集器(同步; http_get/robots_get 可注入 fake 零网络)"""

    def __init__(self, http_get=None, robots_get=None):
        # http_get(url, headers) -> (status, text)
        self._http_get = http_get or self._default_http_get
        # robots_get(url) -> str|None(None=获取失败, 按允许)
        self._robots_get = robots_get or self._default_robots_get
        self._last_call = 0.0
        self._cache: dict[str, tuple[float, str]] = {}
        self.request_count = 0

    # ---------- 传输层 ----------
    @staticmethod
    def _default_http_get(url: str, headers: dict = None):
        import httpx
        with httpx.Client(timeout=FETCH_TIMEOUT, follow_redirects=True) as c:
            r = c.get(url, headers=headers or {})
            return r.status_code, r.text

    @staticmethod
    def _default_robots_get(url: str):
        try:
            import httpx
            p = urlparse(url)
            with httpx.Client(timeout=5.0, follow_redirects=True) as c:
                r = c.get(f"{p.scheme}://{p.netloc}/robots.txt",
                          headers={"User-Agent": DEFAULT_UA})
                return r.text if r.status_code == 200 else None
        except Exception:
            return None

    def _throttle(self) -> None:
        try:
            qps = float(_env("HONGGUO_QPS") or DEFAULT_QPS)
        except ValueError:
            qps = DEFAULT_QPS
        qps = max(0.2, min(5.0, qps))
        wait = (1.0 / qps) - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def _ttl(self) -> int:
        try:
            return max(60, int(_env("HONGGUO_CACHE_TTL") or DEFAULT_TTL))
        except ValueError:
            return DEFAULT_TTL

    def fetch_page(self, url: str, force: bool = False) -> str:
        """GET 公开页(TTL 缓存 + 限速 + robots 红线; 非 200 → ValueError);
        移动 UA——分享链本为浏览器直接打开的公开页"""
        now = time.monotonic()
        cached = self._cache.get(url)
        if cached and not force and (now - cached[0]) < self._ttl():
            logger.info("hongguo_cache_hit ttl_left=%ss",
                        int(self._ttl() - (now - cached[0])))
            return cached[1]
        robots = self._robots_get(url)
        if not _robots_allowed(url, robots):
            raise ValueError(
                f"robots.txt 禁止采集该路径({url})——合规红线拒采, 不可覆盖")
        headers = {"User-Agent": MOBILE_UA}
        last_err = None
        for attempt in range(MAX_ATTEMPTS):
            self._throttle()
            self.request_count += 1
            try:
                status, text = self._http_get(url, headers)
                logger.info("hongguo_page_fetch attempt=%s status=%s "
                            "bytes=%s", attempt + 1, status, len(text or ""))
                if status == 200 and text:
                    self._cache[url] = (time.monotonic(), text)
                    return text
                last_err = f"status={status}"
            except Exception as e:      # 网络/超时
                last_err = str(e)
            time.sleep(0.5 * (attempt + 1))
        raise ValueError(f"红果公开页获取失败({last_err}): {url}")

    # ---------- 配置 ----------
    @staticmethod
    def _share_urls() -> list[str]:
        raw = _env("HONGGUO_SHARE_URLS")
        return [u for u in re.split(r"[,;\s]+", raw) if u]

    @staticmethod
    def _include_recommend() -> bool:
        return _env("HONGGUO_INCLUDE_RECOMMEND", "1").lower() \
            not in ("0", "false", "no", "off")

    @staticmethod
    def _platforms() -> list[str]:
        raw = _env("HONGGUO_PLATFORMS") or DEFAULT_PLATFORMS
        return [p for p in re.split(r"[,;\s]+", raw) if p]

    # ---------- 解析层 ----------
    def parse_page(self, text: str) -> tuple[dict | None, list[dict]]:
        """页面 → (主剧 raw, 推荐 raw 列表); 非红果落地页 → ValueError
        双入口: 旧域 window._ROUTER_DATA / 新域 novelquickapp data-fn-args"""
        page = None
        router = _extract_router_data(text)
        if router:
            page = _find_page_data(router)
        if page is None:
            page = _extract_fn_args_page(text)  # novelquickapp streaming 形态
        if page is None:
            raise ValueError("页面无 _ROUTER_DATA/data-fn-args pageData(非红果分享落地页)")
        sd = page.get("series_data") or {}
        main = _main_raw(sd, page) if str(sd.get("title") or "").strip() \
            else None
        recs = [_rec_raw(r)
                for r in (page.get("recommend_series") or [])
                if isinstance(r, dict)]
        ct = _loader_kind(text, router)      # 漫剧/短剧分轨(2026-10-15)
        if main is not None:
            main["contentType"] = ct
        for r in recs:
            r["contentType"] = ct
        return main, recs

    # ---------- 归一层(→ 85号契约条目) ----------
    @staticmethod
    def _contract_item(raw: dict, platforms: list[str]) -> dict:
        return {
            "title": str(raw.get("title") or "").strip(),
            "itemId": str(raw.get("itemId") or "") or None,  # 归一根键(2026-10-15 入契约: 持久化层 dedup 用)
            "platforms": list(platforms),
            "contentType": str(raw.get("contentType") or ""),  # manju=漫剧/duanju=短剧(2026-10-15 分轨观测)
            "episodes": _to_int(raw.get("episodes")),
            "durationSec": 0,               # 页面无时长(留通道① 补齐)
            "tags": [str(t) for t in (raw.get("tags") or [])][:8],
            "metrics": {
                "plays": _to_int(raw.get("plays")),
                "likes": _to_int(raw.get("likes")),
                "comments": 0,              # 红线: 评论不采
                "shares": 0,
                "completionRate": 0.0,      # 页面无完播率
            },
            "commentsData": [],             # 红线: 评论不采
            "subtitleText": "",
        }

    # ---------- 对外 ----------
    def collect(self, n: int = 50, offset: int = 0) -> dict:
        """采一次 → 85号标准契约 {"dramas": [...], "hasMore": bool}
        (逐链: 主剧 + 推荐位小榜单; itemId/title 去重; 指标缺 0 留通道①)"""
        urls = self._share_urls()
        if not urls:
            raise ValueError("HONGGUO_SHARE_URLS 未配置(红果分享链接列表)")
        platforms = self._platforms()
        include_rec = self._include_recommend()
        raws, seen = [], set()
        for u in urls:
            try:
                text = self.fetch_page(u)
            except ValueError as e:
                if "robots" in str(e):
                    raise            # 合规红线不可跳过(整批拒采)
                logger.warning("hongguo_share_skip url=%s err=%s", u, e)
                continue
            try:
                main, recs = self.parse_page(text)
            except ValueError as e:
                # novelquickapp SSR streaming 偶发只出骨架(实测重抓即恢复):
                # force 重抓一次再试, 仍败才计入 skip
                try:
                    main, recs = self.parse_page(self.fetch_page(u, force=True))
                except ValueError:
                    logger.warning("hongguo_share_parse url=%s err=%s", u, e)
                    continue
            for raw in ([main] if main else []) + \
                    (recs if include_rec else []):
                if not str(raw.get("title") or "").strip():
                    continue
                iid = str(raw.get("itemId") or "")  # 保留入契约(2026-10-15): 不再 pop
                tkey = re.sub(r"\s+", "", str(raw["title"]))
                key = iid or tkey
                if key in seen or tkey in seen:
                    # 同名双入防线: 推荐位 series_id 与主剧 used_chapter_id
                    # 是两套 ID(实测链3推荐位复推链1主剧), itemId 去重失效
                    # → title 归一化第二道, 保留先入(主剧位字段更全)
                    logger.info("hongguo_dedup_drop title=%s iid=%s",
                                raw["title"], iid)
                    continue
                seen.add(key)
                seen.add(tkey)
                raws.append(raw)
        dramas = [self._contract_item(r, platforms) for r in raws]
        page = dramas[offset:offset + max(1, int(n))]
        logger.info("hongguo_collect links=%s dramas=%s page=%s",
                    len(urls), len(dramas), len(page))
        return {"dramas": page,
                "hasMore": offset + len(page) < len(dramas)}

    def probe(self) -> dict:
        """逐链结构摘要(填配置/验证用; 不落契约)"""
        urls = self._share_urls()
        if not urls:
            raise ValueError("probe 需配置 HONGGUO_SHARE_URLS(红果分享链接)")
        preview, ok = [], 0
        for u in urls:
            entry = {"url": u}
            try:
                main, recs = self.parse_page(self.fetch_page(u))
            except ValueError as e:
                entry["error"] = str(e)
            else:
                ok += 1
                entry["main"] = ({k: main[k] for k in
                                  ("title", "episodes", "tags", "itemId")}
                                 if main else None)
                entry["recommendCount"] = len(recs)
                entry["sample"] = [
                    {"title": r["title"], "episodes": r["episodes"],
                     "plays": r["plays"], "tags": r["tags"]}
                    for r in recs[:3]]
            preview.append(entry)
        return {"kind": "hongguo", "urls": urls, "preview": preview,
                "hint": f"{ok}/{len(urls)} 链接解析成功——"
                        f"HONGGUO_SHARE_URLS 配置即用"}


# ============================================================
# serve 模式(本地契约端点; 85号 DRAMA85_SOURCE_URL 指向即可)
# ============================================================

def build_contract_response(collector: HongguoCollector,
                            query: str) -> tuple[int, dict]:
    """serve handler 纯逻辑(可测): ?offset=&n= → (status, 契约 dict)"""
    from urllib.parse import parse_qs
    qs = parse_qs(query or "")
    try:
        offset = int((qs.get("offset") or ["0"])[0])
        n = int((qs.get("n") or ["50"])[0])
    except ValueError:
        return 400, {"error": "offset/n 须为整数"}
    n = max(1, min(200, n))
    offset = max(0, offset)
    try:
        return 200, collector.collect(n=n, offset=offset)
    except ValueError as e:
        return 502, {"error": str(e)}


def serve(port: int = None) -> None:
    """本地契约 HTTP 端点(标准库 ThreadingHTTPServer)"""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    port = port or int(_env("HONGGUO_PORT") or DEFAULT_PORT)
    collector = HongguoCollector()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):           # noqa: N802(标准库命名)
            path, _, query = self.path.partition("?")
            if path.rstrip("/") != "/dramas":
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b'{"error":"not found, use /dramas"}')
                return
            status, body = build_contract_response(collector, query)
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; "
                                             "charset=utf-8")
            self.send_header("X-Source", "hongguo-worker")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, fmt, *args):
            logger.info("hongguo_endpoint " + fmt, *args)

    addr = ("127.0.0.1", port)
    with ThreadingHTTPServer(addr, Handler) as httpd:
        logger.info("hongguo_worker_serving %s:%s/dramas "
                    "(85号侧: DRAMA85_SOURCE_URL=http://127.0.0.1:%s/dramas)",
                    *addr, port)
        httpd.serve_forever()


# ============================================================
# CLI
# ============================================================

def main(argv=None) -> int:
    # best-effort 加载 backend/.env(HONGGUO_*/DRAMA85_* 可落 .env)
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    parser = argparse.ArgumentParser(
        description="85号·红果漫剧合规采集 worker(通道②, 分享落地页"
                    "+推荐位小榜单)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_fetch = sub.add_parser("fetch", help="采一次 → 契约 JSON")
    p_fetch.add_argument("--n", type=int, default=50)
    p_fetch.add_argument("--offset", type=int, default=0)
    p_fetch.add_argument("--out", help="输出文件(缺省 stdout)")
    p_probe = sub.add_parser("probe", help="逐链结构摘要(验证用)")
    p_serve = sub.add_parser("serve", help="本地契约 HTTP 端点")
    p_serve.add_argument("--port", type=int, default=None)
    args = parser.parse_args(argv)

    collector = HongguoCollector()
    try:
        if args.cmd == "fetch":
            contract = collector.collect(n=args.n, offset=args.offset)
            text = json.dumps(contract, ensure_ascii=False, indent=2)
            if args.out:
                with open(args.out, "w", encoding="utf-8") as f:
                    f.write(text)
                logger.info("hongguo_fetch_written file=%s", args.out)
            else:
                print(text)
        elif args.cmd == "probe":
            print(json.dumps(collector.probe(), ensure_ascii=False, indent=2))
        else:
            serve(args.port)
    except ValueError as e:
        logger.error("hongguo_worker_error %s", e)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
