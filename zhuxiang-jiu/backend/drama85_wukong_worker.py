"""85号·悟空浏览器短剧合规采集 worker(通道②, P1 先行入口, 2026-10-13)

定位(《短剧平台数据采集字段表》2.5 节):
    悟空浏览器短剧频道公开页(字节系同源) → 85号标准 JSON 契约。
    免采购/免凭证冷启动: 跑通采集池 + 验证契约与 FIELD_MAP;
    第三方采购到位后本 worker 保留为字节系交叉验证源。

合规红线(内置, 不可配置绕过):
    1. 只 GET 公开页——不登录/不带 Cookie/不伪造签名/不绕任何访问控制;
    2. UA 诚实标识(WUKONG_UA, 默认含站点 worker 名称);
    3. robots.txt Disallow 一律拒采(无开关后门);
    4. 限速 WUKONG_QPS(默认 1, 0.2~5 clamp);
    5. 只采公开元数据(剧名/题材/集数/播放量级)——不采用户身份/不采评论
       (commentsData 留通道① 第三方脱敏供给);
    6. 缓存 T+1(WUKONG_CACHE_TTL 默认 86400s), 同窗口内不重复请求。

用法:
    python drama85_wukong_worker.py probe --gen-map
        [推荐第一步] 拉一次目标页 → 自动推导 WUKONG_FIELD_MAP 草稿
        (FIELD_ALIASES 别名匹配, JSON 形态全自动) + 试采验证;
        stderr 直接输出可粘贴的 WUKONG_FIELD_MAP=... 配置行。
        HTML 形态输出 WUKONG_XPATH 骨架草稿(需人工按标签/class 微调)。
    python drama85_wukong_worker.py fetch [--n 50] [--offset 0] [--out f.json]
        采一次 → 契约 JSON(stdout 或 --out 文件)
    python drama85_wukong_worker.py probe
        仅结构摘要(JSON 键路径 / HTML 标签频次)
    python drama85_wukong_worker.py serve [--port 8510]
        本地契约 HTTP 端点(GET /dramas?offset=&n=);
        85号侧配置 DRAMA85_SOURCE_URL=http://127.0.0.1:8510/dramas 即零改动消费。
    注: 入口 best-effort 加载 backend/.env(WUKONG_*/DRAMA85_* 可落 .env)。

环境变量(WUKONG_*):
    WUKONG_LIST_URL     目标公开页 URL(json/html 形态必填)
    WUKONG_LIST_KIND    json|html|share|auto(默认 auto: 首字符 { / [ →
                        json, 否则 html; 配了 WUKONG_SHARE_URLS 且未配
                        LIST_URL → share)
    WUKONG_SHARE_URLS   分享链接列表(share 形态数据源, 逗号/分号/空白
                        分隔; 分享页 = 单剧元数据页, 指标留通道① 补齐)
    WUKONG_FIELD_MAP    JSON 形态字段映射(语义同 DRAMA85_SOURCE_FIELD_MAP:
                        list=点路径必需 + 契约字段→源字段; 未配置时自动探测
                        $.data.list / $.data.rows / $.list / $.dramas 常见路径)
    WUKONG_XPATH        HTML 形态 XPath 映射(JSON):
                        {"item":"//div[@class='item']","title":".//h3/text()",...}
                        (item 为条目绝对 XPath, 其余为条目内相对 XPath;
                         数值字段支持中文单位换算 1.2万→12000 / 3.4亿→34亿)
    WUKONG_PLATFORMS    契约 platforms 默认值(逗号分隔,
                        缺省 "wukong,douyin,hongguo" 字节系并集)
    WUKONG_QPS=1        WUKONG_UA(默认诚实标识)  WUKONG_CACHE_TTL=86400
"""
import argparse
import json
import logging
import os
import re
import sys
import time
from urllib import robotparser
from urllib.parse import urlparse, parse_qs, unquote

# 复用 85号网关点路径取数(契约语义一致; 顶层导入供静态方法共用)
from services.drama85_source_gateway import _dig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("drama85.wukong_worker")

DEFAULT_QPS = 1.0
FETCH_TIMEOUT = 10.0
MAX_ATTEMPTS = 2
DEFAULT_TTL = 86400
DEFAULT_PORT = 8510
DEFAULT_PLATFORMS = "wukong,douyin,hongguo"
DEFAULT_UA = ("zhuxiang-jiu-drama85-wukong-worker/1.0 "
              "(compliant public-page collector; contact: site-admin)")

# JSON 形态未配置映射时的常见数组路径自动探测(逐个尝试)
_AUTO_LIST_PATHS = ("$.data.list", "$.data.rows", "$.list", "$.dramas")

# 中文数字单位换算(HTML 文本量级 "1.2万"/"3.4亿")
_CN_UNITS = {"万": 10_000, "亿": 100_000_000}

# 契约字段 ← 常见源字段名别名表(probe --gen-map 自动推导用;
# 覆盖驼峰/下划线/短别名三种主流命名风格)
FIELD_ALIASES = {
    "title": ("title", "name", "dramaName", "drama_name",
              "videoName", "video_name", "dramaTitle"),
    "tags": ("tags", "tag", "category", "cate", "categoryName",
             "category_name", "labels", "label", "theme"),
    "episodes": ("episodes", "episodeCount", "episode_count",
                 "total_episodes", "totalEpisodes", "ep",
                 "episodeNum", "episode_num", "episodeTotal"),
    "durationSec": ("durationSec", "duration_sec", "duration",
                    "total_sec", "durationSeconds", "videoDuration"),
    "plays": ("plays", "playCount", "play_count", "playCnt",
              "pv", "viewCount", "view_count", "views",
              "playNum", "play_num"),
    "likes": ("likes", "likeCount", "like_count", "likeCnt",
              "like_num", "diggCount", "digg_count", "thumbUp"),
    "comments": ("comments", "commentCount", "comment_count",
                 "commentCnt", "cmt_num", "commentNum"),
    "shares": ("shares", "shareCount", "share_count", "shareCnt",
               "share_num", "forwardCount", "forward_count"),
    "completionRate": ("completionRate", "completion_rate",
                       "finishRate", "finish_rate", "finish",
                       "completion", "completeRate"),
    "subtitleText": ("subtitleText", "subtitle_text", "synopsis",
                     "description", "desc", "intro", "subtitle",
                     "summary", "brief"),
}


def _env(key: str, default: str = "") -> str:
    return (os.environ.get(key) or default).strip()


def _to_int(value) -> int:
    """数值容错: int/float/数字串/'1.2万'/'3,400'/'72集' → int(失败 0)
    (剥离'集/次/%'等展示后缀, 保留 万/亿 单位换算)"""
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    text = re.sub(r"[^\d.万亿,]", "", str(value).strip())
    text = text.replace(",", "")
    if not text:
        return 0
    m = re.match(r"^([\d.]+)([万亿]?)$", text)
    if not m:
        try:
            return int(float(text))
        except ValueError:
            return 0
    num, unit = m.group(1), m.group(2)
    try:
        base = float(num)
    except ValueError:
        return 0
    return int(base * _CN_UNITS.get(unit, 1))


def _robots_allowed(url: str, robots_text: str | None) -> bool:
    """robots 判定: Disallow → 拒; robots 拉取失败(robots_text=None)→允许(业界惯例)。"""
    if robots_text is None:
        return True
    parser = robotparser.RobotFileParser()
    parser.parse(robots_text.splitlines())
    return parser.can_fetch(DEFAULT_UA, url)


class WukongCollector:
    """悟空公开页采集器(同步; http_get/status 可注入 fake 供测试零网络)"""

    def __init__(self, http_get=None, robots_get=None):
        # http_get(url, headers) -> (status, text); 注入则零网络
        self._http_get = http_get or self._default_http_get
        # robots_get(url) -> str|None(robots.txt 文本; None=获取失败)
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
            origin = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
            with httpx.Client(timeout=5.0, follow_redirects=True) as c:
                r = c.get(f"{origin}/robots.txt",
                          headers={"User-Agent": DEFAULT_UA})
                return r.text if r.status_code == 200 else None
        except Exception:
            return None

    def _throttle(self) -> None:
        try:
            qps = float(_env("WUKONG_QPS") or DEFAULT_QPS)
        except ValueError:
            qps = DEFAULT_QPS
        qps = max(0.2, min(5.0, qps))
        wait = (1.0 / qps) - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def _ttl(self) -> int:
        try:
            return max(60, int(_env("WUKONG_CACHE_TTL") or DEFAULT_TTL))
        except ValueError:
            return DEFAULT_TTL

    def fetch_page(self, url: str, force: bool = False,
                   ua: str = None) -> str:
        """GET 公开页(TTL 缓存 + 限速 + robots 红线; 非 200 → ValueError)
        ua: 可选覆盖(share 形态用移动 UA——分享链本为浏览器直接打开
        的公开页, 移动端渲染需要移动 UA; 其余形态用诚实标识 UA)"""
        now = time.monotonic()
        cached = self._cache.get(url)
        if cached and not force and (now - cached[0]) < self._ttl():
            logger.info("wukong_cache_hit ttl_left=%ss",
                        int(self._ttl() - (now - cached[0])))
            return cached[1]
        robots = self._robots_get(url)
        if not _robots_allowed(url, robots):
            raise ValueError(
                f"robots.txt 禁止采集该路径({url})——合规红线拒采, 不可覆盖")
        headers = {"User-Agent": ua or _env("WUKONG_UA") or DEFAULT_UA}
        last_err = None
        for attempt in range(MAX_ATTEMPTS):
            self._throttle()
            self.request_count += 1
            try:
                status, text = self._http_get(url, headers)
                logger.info("wukong_page_fetch attempt=%s status=%s "
                            "bytes=%s", attempt + 1, status, len(text or ""))
                if status == 200 and text:
                    self._cache[url] = (time.monotonic(), text)
                    return text
                last_err = f"status={status}"
            except Exception as e:      # 网络/超时
                last_err = str(e)
            time.sleep(0.5 * (attempt + 1))
        raise ValueError(f"悟空公开页获取失败({last_err}): {url}")

    # ---------- 解析层 ----------
    @staticmethod
    def _detect_kind(text: str) -> str:
        kind = (_env("WUKONG_LIST_KIND") or "auto").lower()
        if kind in ("json", "html"):
            return kind
        return "json" if text.lstrip()[:1] in ("{", "[") else "html"

    # ---------- share 形态(分享链接 → 单剧元数据, 2026-10-13 实测结构) ----------
    SHARE_MOBILE_UA = ("Mozilla/5.0 (Linux; Android 13; Pixel 7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0 Mobile Safari/537.36")

    @staticmethod
    def _share_urls() -> list[str]:
        """WUKONG_SHARE_URLS(逗号/分号/空白分隔的分享链接列表)"""
        raw = _env("WUKONG_SHARE_URLS")
        return [u for u in re.split(r"[,;\s]+", raw) if u]

    def _is_share_mode(self) -> bool:
        kind = (_env("WUKONG_LIST_KIND") or "").lower()
        if kind == "share":
            return True
        # auto: 配了分享链接且未配列表页 → share
        return bool(self._share_urls()) and not _env("WUKONG_LIST_URL")

    @staticmethod
    def _extract_render_data(text: str) -> dict | None:
        """悟空分享页 SSR RENDER_DATA(URL-encoded JSON) → dict;
        无该节点/解码失败 → None(2026-10-13 深挖: articleInfo 含
        serialCount 总集数/videoDuration 单集时长/content 剧情简介/
        videoPlayCount·diggCount 当前集指标; relatedInfo 恒 null——
        推荐位由运行时签名 API 拉取, 不可采结论维持)"""
        m = re.search(r"RENDER_DATA[^>]*>([^<]+)<", text)
        if not m:
            return None
        try:
            return json.loads(unquote(m.group(1).strip()))
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _parse_share_page(text: str) -> dict | None:
        """分享页 HTML → 契约条目(五级结构, 2026-10-14 升级):
        ①RENDER_DATA SSR 优先: seoTDK 剧名(剥「- 悟空浏览器」后缀)/
          tomatoPlayetInfo.serialCount 总集数/articleInfo.videoDuration
          单集时长/content 剧情简介(→subtitleText 激活研判层结构化)/
          seriesId 去重; 当前集指标(videoPlayCount/diggCount)与出品方
          仅入预览键(episodeMetrics/source/creator)——集级口径与红果
          全剧级不可比, 不入契约 metrics 防污染爆款指数;
        ②正文正则回退: <title>/og:title + 「第N集 共X集 已完结」+ og:url;
        ③西瓜短剧页(iesdouyin /xg/video/): _parse_xigua_page——
          剧名/出品方/#话题/发布日期/itemId, episodes=0(无总集数);
        ④红果落地页形态: _parse_hongguo_page;
        ⑤百度 lite 视频分享页(mbdlite): _parse_baidu_page——
          剧名(剥集号)/单集时长/集号下界, 指标零 SSR 留通道①。
        五级均无剧名 → None(非剧页)。"""
        plain = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
        plain = re.sub(r"<script.*?</script>|<style.*?</style>", " ",
                       plain, flags=re.S)
        plain = re.sub(r"<[^>]+>", " ", plain)
        plain = re.sub(r"\s+", " ", plain)
        # ①RENDER_DATA 优先(结构化, 字段更全)
        rd = WukongCollector._extract_render_data(text)
        if rd:
            seo = str((rd.get("seoTDK") or {}).get("title") or "").strip()
            title = re.sub(r"\s*-\s*悟空浏览器\s*$", "", seo)
            ai = rd.get("articleInfo") or {}
            tp = ai.get("tomatoPlayetInfo") or {}
            try:
                eps = int(str(tp.get("serialCount") or "0"))
            except ValueError:
                eps = 0
            if title and eps > 0:
                item = {"title": title, "episodes": eps}
                if ai.get("videoDuration"):
                    item["durationSec"] = ai["videoDuration"]
                content = str(ai.get("content") or "").strip()
                if content:
                    item["subtitleText"] = content[:500]
                m_st = re.search(r"(已完结|连载中|更新中)", plain)
                if m_st:
                    item["tags"] = [m_st.group(1)]
                sid = str(tp.get("seriesId") or "").strip()
                if sid:
                    item["itemId"] = sid
                if ai.get("videoPlayCount") is not None \
                        or ai.get("diggCount") is not None:
                    item["episodeMetrics"] = {
                        "plays": _to_int(ai.get("videoPlayCount")),
                        "digg": _to_int(ai.get("diggCount"))}
                if str(ai.get("source") or "").strip():
                    item["source"] = str(ai["source"]).strip()
                mu = ai.get("mediaUser") or {}
                if mu.get("screenName"):
                    item["creator"] = str(mu["screenName"]).strip()
                return item
        # ②正文正则回退(原实测结构)
        m = re.search(r"<title[^>]*>([^<]{1,60})</title>", text, re.S)
        title = m.group(1).strip() if m else ""
        if not title:
            m = re.search(r'property="og:title"\s+content="([^"]+)"', text)
            title = m.group(1).strip() if m else ""
        if not title:
            return None
        m_ep = re.search(r"共\s*(\d+)\s*集", plain)
        if not m_ep:
            # 百度 lite 视频分享页(mr.baidu.com 短链 302 → mbdlite):
            # 强特征("external_share_video" 独有 source_name)必须前置——
            # 百度页「第N集」+任意 JSON title(如画质标签"蓝光")会误中
            # 红果形态弱特征被截胡(实测《全族流放》-第1集形态暴露)
            item = WukongCollector._parse_baidu_page(text)
            if item:
                return item
            # 西瓜短剧页(iesdouyin /xg/video/, v.douyin 短链 302 落地):
            # 第4级形态, 特征强(og:url 含 /xg/video/)——西瓜页内嵌推荐
            # 视频 JSON "title" 会误中红果的 JSON 匹配
            item = WukongCollector._parse_xigua_page(text, plain)
            if item:
                return item
            # 红果漫剧分享落地页(hainanyuyue 域, 无总集数): 弱特征兜底
            return WukongCollector._parse_hongguo_page(text, plain)
        item = {"title": title, "episodes": int(m_ep.group(1))}
        m_st = re.search(r"共\s*\d+\s*集\s*(已完结|连载中|更新中)", plain)
        if m_st:
            item["tags"] = [m_st.group(1)]
        m = re.search(r'property="og:url"[^>]+content="[^"]*?/(\d+)/?"',
                      text)
        if m:
            item["itemId"] = m.group(1)             # 内部去重键(不入契约)
        return item

    @staticmethod
    def _parse_xigua_page(text: str, plain: str) -> dict | None:
        """西瓜视频短剧页 → 契约条目(实测《长风独赴前程》2026-10-14,
        v.douyin.com/{码} 短链 302 → iesdouyin.com/xg/video/{videoId};
        share/video 同域路径零 SSR 不可采, xg 形态标题层有 SSR):
        <title>/og:title「{剧名} - 短剧高清在线观看 | 西瓜视频」;
        正文「{出品方} N万粉丝 M视频 关注」/「YYYY-MM-DD发布」/
        #话题标签(≤5); og:url videoId 作 itemId 去重。
        无总集数/主体播放量/时长(全运行时签名 API) → episodes=0
        如实, 指标留通道①; platforms 标 xigua(抖音集团西瓜视频)。
        非 xg 短剧页 → None。"""
        if not re.search(r"/xg/video/\d+", text):
            return None
        m_t = re.search(
            r"<title[^>]*>([^<]{2,60}?)\s*-\s*短剧高清在线观看", text)
        if not m_t:
            return None
        title = m_t.group(1).strip()
        item = {"title": title, "episodes": 0, "platforms": ["xigua"]}
        m_cr = re.search(r"([\u4e00-\u9fa5A-Za-z0-9]{2,16})\s+\d[\d.]*万?粉丝",
                         plain)
        if m_cr:
            item["creator"] = m_cr.group(1)
        m_pub = re.search(r"(\d{4}-\d{2}-\d{2})发布", plain)
        if m_pub:
            item["pubDate"] = m_pub.group(1)
        tags = re.findall(r"#([^\s#<]{2,20})", plain)
        if tags:
            item["tags"] = list(dict.fromkeys(tags))[:5]
        m_id = re.search(r'property="og:url"[^>]*content="'
                         r'[^"]*?/xg/video/(\d+)', text)
        if m_id:
            item["itemId"] = m_id.group(1)           # 内部去重键(不入契约)
        return item

    @staticmethod
    def _parse_baidu_page(text: str) -> dict | None:
        """百度 lite 视频分享页 → 契约条目(实测《复仇厨娘》2026-10-14,
        mr.baidu.com/r/{码} 短链 302 → mbdlite.baidu.com/newspage/data/
        videoshare?nid=sv_{id}; 页面 19KB 壳, robots 返回 HTML 无有效
        规则=默认允许):
        <title>「{剧名} {N}」——尾部集号剥离(当前集下界, 同红果第N集
        逻辑); JSON "duration" 单集时长(→durationSec); nid(sv_) 作
        itemId 去重。
        无播放量/点赞/题材/总集数(全运行时) → metrics 0 留通道①;
        platforms 标 baidu(百度 App/好看视频分发)。
        特征判定(双变体, 10-14 实测): `"nid":"sv_{10+位}"` 页面独有
        ——mbdlite 分享页两种来源变体(collection_video_landing 带
        source_name="external_share_video / 其他 rawFrom 不带),
        sv_ nid 两者共有; duration 数值/字符串双形态("162"/162)兼容。
        不在 → None(非百度 lite 分享页)。"""
        if not re.search(r'"nid"\s*:\s*"?sv_\d{10,}', text):
            return None
        m_t = re.search(r"<title[^>]*>([^<]{2,60})</title>", text, re.S)
        if not m_t:
            return None
        raw = m_t.group(1).strip()
        # 尾部集号双形态剥离: 「剧名 01」(空格数字)与「剧名-第1集」
        title = re.sub(r"[\s\-]*第\d{1,3}集\s*$", "", raw)
        title = re.sub(r"[\s\-]+\d{1,3}\s*$", "", title).strip()
        item = {"title": title, "episodes": 0, "platforms": ["baidu"]}
        m_ep = (re.search(r"第(\d{1,3})集\s*$", raw)
                or re.search(r"(\d{1,3})\s*$", raw))
        if m_ep:
            item["episodes"] = int(m_ep.group(1))   # 集号=当前集下界
        m_dur = re.search(r'"duration"\s*:\s*"?(\d{2,6})', text)
        if m_dur:
            item["durationSec"] = int(m_dur.group(1))
        m_id = re.search(r'"(?:videoId|nid)"\s*:\s*"?sv_([A-Za-z0-9]+)',
                         text)
        if m_id:
            item["itemId"] = "sv_" + m_id.group(1)  # 内部去重键(不入契约)
        return item

    @staticmethod
    def _parse_hongguo_page(text: str, plain: str) -> dict | None:
        """红果漫剧分享落地页 → 契约条目(实测《牢狱蒙尘》2026-10-13,
        kylin.hainanyuyue.com/s/ 分享链 302 落地页):
        JSON "title"=剧名(<title> 带「红果漫剧免费看《》」前后缀);
        正文「剧名 题材标签… 第N集」——页面无总集数, 第N集=分享时
        当前集(episodes 取其为 ≥N 下界); "vid" 作 itemId 去重;
        platforms 标 hongguo(红果运营主体海南愉阅, og:image 走字节 CDN)。
        无 JSON title + 第N集 双要素 → None(非红果剧页)。"""
        m_t = re.search(r'"title"\s*:\s*"([^"]{2,60})"', text)
        m_ep = re.search(r"第\s*(\d+)\s*集", plain)
        if not (m_t and m_ep):
            return None
        title = m_t.group(1).strip()
        item = {"title": title, "episodes": int(m_ep.group(1)),
                "platforms": ["hongguo"]}
        # 题材标签: 剧名 与 第N集 之间的短词序列(战神/都市/豪门)
        for m_tags in re.finditer(
                re.escape(title) + r"\s+((?:\S{1,6}\s+){1,5})第\s*\d+\s*集",
                plain):
            words = [w for w in m_tags.group(1).split()
                     if re.fullmatch(r"[\u4e00-\u9fa5A-Za-z]{1,6}", w)]
            if words:
                item["tags"] = words
                break
        m_id = re.search(r'"vid"\s*:\s*"?(\d{9,})', text)
        if m_id:
            item["itemId"] = m_id.group(1)   # 归一根键(2026-10-15 起入契约: 持久化层 dedup 用)
        return item

    def _collect_share(self, n: int, offset: int) -> dict:
        """分享链接列表 → 契约(逐链接单剧页; 指标留通道① 补齐)"""
        urls = self._share_urls()
        if not urls:
            raise ValueError("WUKONG_LIST_KIND=share 需配置 "
                             "WUKONG_SHARE_URLS(分享链接列表)")
        defaults_platforms = [p.strip() for p in
                              (_env("WUKONG_PLATFORMS")
                               or DEFAULT_PLATFORMS).split(",") if p.strip()]
        dramas, seen = [], set()
        for u in urls:
            try:
                text = self.fetch_page(u, ua=self.SHARE_MOBILE_UA)
            except ValueError as e:
                if "robots" in str(e):
                    raise            # 合规红线不可跳过(整批拒采)
                logger.warning("wukong_share_skip url=%s err=%s", u, e)
                continue
            parsed = self._parse_share_page(text)
            if not parsed:
                logger.warning("wukong_share_no_title url=%s", u)
                continue
            key = parsed.get("itemId") or parsed["title"]  # 2026-10-15: 不再 pop, itemId 入契约
            if key in seen:
                continue
            seen.add(key)
            # 来源域标记(纯增量字段: 持久化层快照按真实来源分桶)
            if "mr.baidu.com" in u:
                parsed["sourcePlatform"] = "baidu"
            elif "v.douyin.com" in u or "iesdouyin.com" in u:
                parsed["sourcePlatform"] = "xigua"
            else:
                parsed["sourcePlatform"] = "wukong"
            dramas.append(self._contract_item(parsed, defaults_platforms))
        page = dramas[offset:offset + max(1, int(n))]
        logger.info("wukong_collect_share links=%s dramas=%s page=%s",
                    len(urls), len(dramas), len(page))
        return {"dramas": page,
                "hasMore": offset + len(page) < len(dramas)}

    def _parse_json(self, text: str) -> list[dict]:
        """JSON 形态: WUKONG_FIELD_MAP 键名映射(语义同 85号 FIELD_MAP)。
        注: 数值归一不走 apply_field_map——网页文本含 '1.2万'/'3,400'/
        '71%' 展示口径, 由 _contract_item._to_int 中文单位换算承担。"""
        try:
            resp = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"页面非合法 JSON(WUKONG_LIST_KIND=json 请核对): "
                             f"{e}") from e
        if isinstance(resp, list):
            resp = {"dramas": resp}
        if isinstance(resp.get("dramas"), list):
            return [r for r in resp["dramas"] if isinstance(r, dict)]
        fmap = self._load_wukong_field_map(resp)
        raw_rows = _dig(resp, fmap["list"]) or []
        mapped = []
        for row in raw_rows:
            if not isinstance(row, dict):
                continue
            item = {}
            for ckey, skey in fmap.items():
                if ckey == "list":
                    continue
                sk = str(skey)
                val = (_dig(row, sk) if "." in sk.lstrip("$")
                       else row.get(sk))
                if val is not None:
                    item[ckey] = val
            mapped.append(item)
        if set(fmap.keys()) == {"list"} and not any(
                i.get("title") for i in mapped):
            raise ValueError(
                "已定位剧集数组但条目无 title——auto 模式无法映射字段名, "
                "请配置 WUKONG_FIELD_MAP(先跑 probe 查看字段名)")
        return mapped

    @staticmethod
    def _load_wukong_field_map(resp: dict) -> dict:
        """WUKONG_FIELD_MAP(语义同 85号); 未配置→常见路径自动探测"""
        raw = _env("WUKONG_FIELD_MAP")
        if raw:
            try:
                fmap = json.loads(raw)
            except json.JSONDecodeError as e:
                raise ValueError(f"WUKONG_FIELD_MAP JSON 非法: {e}") from e
            if not (isinstance(fmap, dict) and fmap.get("list")
                    and fmap.get("title")):
                raise ValueError("WUKONG_FIELD_MAP 需含 list(点路径)+title")
            return fmap
        for path in _AUTO_LIST_PATHS:
            if isinstance(_dig(resp, path), list):
                logger.info("wukong_auto_list_path=%s", path)
                return {"list": path}
        raise ValueError(
            "未能定位剧集数组: 请配置 WUKONG_FIELD_MAP(list 点路径)"
            "或先跑 probe 查看页面结构")

    def _parse_html(self, text: str) -> list[dict]:
        """HTML 形态: WUKONG_XPATH(item 绝对 + 字段相对)"""
        raw = _env("WUKONG_XPATH")
        if not raw:
            raise ValueError("HTML 形态需配置 WUKONG_XPATH"
                             "(先跑 probe 查看结构)")
        try:
            xmap = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"WUKONG_XPATH JSON 非法: {e}") from e
        item_xpath = xmap.get("item")
        if not item_xpath:
            raise ValueError("WUKONG_XPATH 需含 item(条目绝对 XPath)")
        from lxml import html as _lh
        doc = _lh.fromstring(text)
        rows = []
        for node in doc.xpath(item_xpath):
            item = {}
            for field, xp in xmap.items():
                if field == "item":
                    continue
                vals = node.xpath(xp) if xp.startswith(".") else \
                    node.xpath("." + xp)
                text_val = ""
                if vals:
                    v0 = vals[0]
                    text_val = v0.strip() if isinstance(v0, str) else \
                        (v0.text_content() or "").strip()
                item[field] = text_val
            rows.append(item)
        return rows

    # ---------- 归一层(→ 契约条目) ----------
    def _contract_item(self, raw: dict, defaults_platforms: list[str]) -> dict:
        platforms = raw.get("platforms")
        if isinstance(platforms, str) and platforms.strip():
            platforms = [p.strip() for p in platforms.split(",") if p.strip()]
        if not isinstance(platforms, list) or not platforms:
            platforms = list(defaults_platforms)
        m = raw.get("metrics")
        if not isinstance(m, dict):
            m = {k: raw.get(k) for k in
                 ("plays", "likes", "comments", "shares", "completionRate")}
        cr = m.get("completionRate")
        try:
            crv = float(cr)
            # 网页展示口径: 纯数值 >1 视为百分数(71 → 0.71), ≤1 直用
            cr = min(1.0, max(0.0, crv / 100.0 if crv > 1 else crv))
        except (TypeError, ValueError):
            # "62%" 等带符号串 → 剥离后按百分数
            cr = min(1.0, _to_int(re.sub(r"[^\d.]", "", str(cr))) / 100.0)
        tags = raw.get("tags")
        if isinstance(tags, str) and tags.strip():
            tags = [t.strip() for t in re.split(r"[,，/ ]", tags) if t.strip()]
        return {
            "title": str(raw.get("title") or "").strip(),
            "itemId": str(raw.get("itemId") or "") or None,          # 归一根键(2026-10-15 入契约)
            "sourcePlatform": str(raw.get("sourcePlatform") or "") or None,  # 分享链来源域: baidu/xigua/wukong
            "platforms": platforms,
            "episodes": _to_int(raw.get("episodes")),
            "durationSec": _to_int(raw.get("durationSec")),
            "tags": [str(t) for t in tags][:8] if isinstance(tags, list) else [],
            "metrics": {
                "plays": _to_int(m.get("plays")),
                "likes": _to_int(m.get("likes")),
                "comments": _to_int(m.get("comments")),
                "shares": _to_int(m.get("shares")),
                "completionRate": cr,
            },
            "commentsData": [],      # 红线: 评论不采, 留通道① 脱敏供给
            "subtitleText": str(raw.get("subtitleText") or ""),
        }

    # ---------- 对外 ----------
    def collect(self, n: int = 50, offset: int = 0) -> dict:
        """采一次 → 85号标准契约 {"dramas": [...], "hasMore": bool}"""
        if self._is_share_mode():
            return self._collect_share(n, offset)
        url = _env("WUKONG_LIST_URL")
        if not url:
            raise ValueError("WUKONG_LIST_URL 未配置(悟空短剧频道公开页 URL)")
        text = self.fetch_page(url)
        kind = self._detect_kind(text)
        raws = (self._parse_json(text) if kind == "json"
                else self._parse_html(text))
        defaults_platforms = [p.strip() for p in
                              (_env("WUKONG_PLATFORMS")
                               or DEFAULT_PLATFORMS).split(",") if p.strip()]
        dramas = [self._contract_item(r, defaults_platforms)
                  for r in raws]
        dramas = [d for d in dramas if d["title"]]   # 缺 title 丢弃(契约同款)
        page = dramas[offset:offset + max(1, int(n))]
        logger.info("wukong_collect kind=%s total=%s page=%s",
                    kind, len(dramas), len(page))
        return {"dramas": page,
                "hasMore": offset + len(page) < len(dramas)}

    def probe(self, gen_map: bool = False) -> dict:
        """拉一次页面 → 结构摘要(填配置用; 不解析不落契约)
        gen_map=True: JSON 形态额外自动推导 WUKONG_FIELD_MAP 草稿
        (按 FIELD_ALIASES 别名匹配 listSamples 键名)并试采验证。
        share 形态: 逐分享链接解析预览(title/episodes/状态)。"""
        if self._is_share_mode():
            urls = self._share_urls()
            if not urls:
                raise ValueError("share 形态需配置 WUKONG_SHARE_URLS")
            preview = []
            for u in urls[:10]:
                try:
                    text = self.fetch_page(u, force=True,
                                           ua=self.SHARE_MOBILE_UA)
                    parsed = self._parse_share_page(text) or {}
                    preview.append({"url": u,
                                    "title": parsed.get("title"),
                                    "episodes": parsed.get("episodes"),
                                    "tags": parsed.get("tags"),
                                    "itemId": parsed.get("itemId"),
                                    "durationSec": parsed.get("durationSec"),
                                    "episodeMetrics":
                                        parsed.get("episodeMetrics"),
                                    "source": parsed.get("source")})
                except ValueError as e:
                    preview.append({"url": u, "error": str(e)})
            ok = sum(1 for p in preview if p.get("title"))
            return {"url": urls[0], "kind": "share", "links": len(urls),
                    "sharePreview": preview,
                    "hint": (f"{ok}/{len(preview)} 链接解析成功——配置即用"
                             "(WUKONG_SHARE_URLS 已生效)")}
        url = _env("WUKONG_LIST_URL")
        if not url:
            raise ValueError("WUKONG_LIST_URL 未配置")
        text = self.fetch_page(url, force=True)
        kind = self._detect_kind(text)
        out = {"url": url, "kind": kind}
        if kind == "json":
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                return {**out, "hint": "JSON 解析失败, 请核对 WUKONG_LIST_KIND"}
            out["topKeys"] = sorted(data.keys()) if isinstance(data, dict) \
                else ["<list>"]
            list_samples: dict[str, list[str]] = {}

            def walk(node, prefix, depth):
                if depth >= 3 or not isinstance(node, dict):
                    return
                for k, v in node.items():
                    path = f"{prefix}.{k}"
                    if isinstance(v, list):
                        out.setdefault("listPaths", []).append(path)
                        if v and isinstance(v[0], dict):
                            list_samples[path] = sorted(v[0].keys())
                            walk(v[0], f"{path}[0]", depth + 1)
                    elif isinstance(v, dict):
                        walk(v, path, depth + 1)
                    else:
                        out.setdefault("leafKeys", {})[path] = v
            walk(data, "$", 0)
            out["hint"] = ("数组路径见 listPaths; 取其一作 WUKONG_FIELD_MAP "
                           "的 list, 叶子键作字段映射值")
            if gen_map:
                out["fieldMapDraft"] = self._derive_field_map(list_samples)
                if out["fieldMapDraft"]:
                    out["tryFetch"] = self._try_draft(out["fieldMapDraft"])
        else:
            from lxml import html as _lh
            doc = _lh.fromstring(text)
            tags: dict[str, int] = {}
            for el in doc.iter():
                # lxml 注释/PI 节点的 tag 是函数对象, 跳过只统计元素
                if not isinstance(el.tag, str):
                    continue
                tags[el.tag] = tags.get(el.tag, 0) + 1
            out["tagFreq"] = dict(sorted(tags.items(),
                                         key=lambda kv: -kv[1])[:15])
            out["hint"] = ("按 tagFreq 选条目容器写 WUKONG_XPATH 的 item, "
                           "字段用相对 XPath; 数值自动换算 万/亿")
            if gen_map:
                out["xpathDraft"] = {
                    "item": "//div[contains(@class,'item')]",
                    "title": ".//h3/text()",
                    "tags": ".//span[contains(@class,'tag')]/text()",
                    "episodes": ".//span[contains(@class,'ep')]/text()",
                    "plays": ".//span[contains(@class,'play')]/text()",
                    "completionRate":
                        ".//span[contains(@class,'fin')]/text()",
                }
                out["xpathDraftHint"] = (
                    "HTML 形态无法全自动——此为骨架草稿, 按页面实际 "
                    "标签/class 改写后写入 WUKONG_XPATH")
        return out

    @staticmethod
    def _derive_field_map(list_samples: dict[str, list[str]]) -> dict | None:
        """数组路径+键名 → WUKONG_FIELD_MAP 草稿(别名命中数最多的数组)"""
        if not list_samples:
            return None
        best_path, best_hits, best_keys = None, -1, None
        for path, keys in list_samples.items():
            hits = sum(
                1 for ckey in FIELD_ALIASES
                if any(k in FIELD_ALIASES[ckey] for k in keys))
            # title 命中的数组优先(同分时)
            has_title = any(k in FIELD_ALIASES["title"] for k in keys)
            score = hits + (5 if has_title else 0)
            if score > best_hits:
                best_path, best_hits, best_keys = path, score, keys
        if best_path is None or best_hits <= 0:
            return None
        draft = {"list": best_path}
        for ckey, aliases in FIELD_ALIASES.items():
            for k in best_keys:
                if k in aliases:
                    draft[ckey] = k
                    break
        if "title" not in draft:
            draft["__warning"] = "未识别 title 字段——请按 leafKeys 手工补"
        return draft

    def _try_draft(self, draft: dict) -> dict:
        """用草稿映射立即试采(T+1 缓存不重复请求)→ 验证草稿可用性"""
        saved = os.environ.get("WUKONG_FIELD_MAP")
        try:
            os.environ["WUKONG_FIELD_MAP"] = json.dumps(
                draft, ensure_ascii=False)
            contract = self.collect(n=5)
            return {"rows": len(contract.get("dramas") or []),
                    "hasMore": contract.get("hasMore"),
                    "ok": len(contract.get("dramas") or []) > 0}
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        finally:
            if saved is None:
                os.environ.pop("WUKONG_FIELD_MAP", None)
            else:
                os.environ["WUKONG_FIELD_MAP"] = saved


# ============================================================
# serve 模式(本地契约端点; 85号 DRAMA85_SOURCE_URL 指向即可)
# ============================================================

def build_contract_response(collector: WukongCollector,
                            query: str) -> tuple[int, dict]:
    """serve handler 纯逻辑(可测): ?offset=&n= → (status, 契约 dict)"""
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
    port = port or int(_env("WUKONG_PORT") or DEFAULT_PORT)
    collector = WukongCollector()

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
            self.send_header("X-Source", "wukong-worker")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, fmt, *args):
            logger.info("wukong_endpoint " + fmt, *args)

    addr = ("127.0.0.1", port)
    with ThreadingHTTPServer(addr, Handler) as httpd:
        logger.info("wukong_worker_serving %s:%s/dramas "
                    "(85号侧: DRAMA85_SOURCE_URL=http://127.0.0.1:%s/dramas)",
                    *addr, port)
        httpd.serve_forever()


# ============================================================
# CLI
# ============================================================

def main(argv=None) -> int:
    # best-effort 加载 backend/.env(WUKONG_*/DRAMA85_* 可落 .env; 无则纯 env)
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    parser = argparse.ArgumentParser(
        description="85号·悟空浏览器短剧合规采集 worker(通道②)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_fetch = sub.add_parser("fetch", help="采一次 → 契约 JSON")
    p_fetch.add_argument("--n", type=int, default=50)
    p_fetch.add_argument("--offset", type=int, default=0)
    p_fetch.add_argument("--out", help="输出文件(缺省 stdout)")
    p_probe = sub.add_parser("probe", help="页面结构摘要(填配置用)")
    p_probe.add_argument("--gen-map", action="store_true",
                         help="自动推导 WUKONG_FIELD_MAP 草稿并试采验证"
                              "(JSON 形态全自动; HTML 出 XPATH 骨架)")
    p_serve = sub.add_parser("serve", help="本地契约 HTTP 端点")
    p_serve.add_argument("--port", type=int, default=None)
    args = parser.parse_args(argv)

    collector = WukongCollector()
    try:
        if args.cmd == "fetch":
            contract = collector.collect(n=args.n, offset=args.offset)
            text = json.dumps(contract, ensure_ascii=False, indent=2)
            if args.out:
                with open(args.out, "w", encoding="utf-8") as f:
                    f.write(text)
                logger.info("wukong_fetch_written file=%s", args.out)
            else:
                print(text)
        elif args.cmd == "probe":
            summary = collector.probe(gen_map=getattr(args, "gen_map", False))
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            fmap = summary.get("fieldMapDraft")
            if fmap:
                print("\n# 写入配置(.env 或环境变量):", file=sys.stderr)
                print(f"WUKONG_FIELD_MAP="
                      f"{json.dumps(fmap, ensure_ascii=False)}",
                      file=sys.stderr)
        else:
            serve(args.port)
    except ValueError as e:
        logger.error("wukong_worker_error %s", e)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
