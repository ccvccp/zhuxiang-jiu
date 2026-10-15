"""85号·悟空浏览器短剧合规采集 worker 专项测试(fake 页面零网络)

运行:
    python test_drama85_wukong.py

覆盖:
    1. JSON 形态: fake 字节系风格页面 + WUKONG_FIELD_MAP → 契约
       (点路径取数/中文单位换算/percent 完播率/platforms 默认并集)
    2. JSON auto 路径探测(未配置 FIELD_MAP 时 $.data.list 等常见路径)
    3. HTML 形态: fake 榜单页 + WUKONG_XPATH → 契约(万/亿换算/字符串 tags)
    4. auto kind 判定(JSON/HTML)
    5. 合规红线: robots Disallow 拒采/缺 WUKONG_LIST_URL 明确报错/
       commentsData 恒为空(评论不采)
    6. 限速(QPS 间隔)与 T+1 缓存(同窗口不重复请求)
    7. serve handler 纯逻辑(offset/n 分页/参数容错/上游错误 502)
    8. probe 结构摘要(JSON 键路径/HTML 标签频次)
    9. 全链闭环: worker 契约 → RealDramaSource 直接消费(85号零改动)
"""
import asyncio
import json
import os
import sys
import time

os.environ.setdefault("LOCK_MODE", "asyncio")
os.environ.setdefault("STORE_MODE", "asyncio")
os.environ.setdefault("AUTH_MODE", "compat")
os.environ.setdefault("AUTH_COMPAT_TRUST_HEADERS", "1")

PASS = 0
FAIL = 0
RESULTS = []

WUKONG_KEYS = ("WUKONG_LIST_URL", "WUKONG_LIST_KIND", "WUKONG_FIELD_MAP",
               "WUKONG_XPATH", "WUKONG_PLATFORMS", "WUKONG_QPS",
               "WUKONG_UA", "WUKONG_CACHE_TTL", "WUKONG_SHARE_URLS")

# fake 分享页(复刻实测结构《父亲的礼物》2026-10-13)
SHARE_PAGE_1 = """<html><head>
<title>父亲的礼物</title>
<meta property="og:title" content="父亲的礼物">
<meta property="og:url" content="https://www.toutiao.com/short_play/7456636418283015193/">
</head><body>短剧 · 父亲的礼物 第2集 共99集 已完结 去看全集</body></html>"""
SHARE_PAGE_2 = """<html><head><title>隐婚千金</title></head>
<body>短剧 · 隐婚千金 第5集 共66集 更新中</body></html>"""
SHARE_PAGE_BAD = "<html><head><title>悟空浏览器</title></head><body>下载</body></html>"

# fake 悟空分享页·RENDER_DATA SSR 形态(复刻深挖结构《父亲的礼物》2026-10-13:
# seoTDK 剧名/tomatoPlayetInfo.serialCount 总集数/videoDuration 单集时长/
# content 剧情简介/集级指标 videoPlayCount·diggCount; 正文「共101集」与
# serialCount=99 不一致 → 验证 SSR 优先; relatedInfo 恒 null 无推荐位)
RD_PAGE = """<html><head><title>父亲的礼物</title></head><body>
短剧 · 父亲的礼物 第2集 共101集 已完结 去看全集
<textarea style="display:none" name="RENDER_DATA">{"seoTDK":{"title":"父亲的礼物 - 悟空浏览器"},
"articleInfo":{"videoDuration":88,"videoPlayCount":519,"diggCount":49,
"content":"陆建设隐瞒首富身份，归隐后做了保安，儿子陆子轩对父亲一直有意见。",
"source":"心动短剧","mediaUser":{"screenName":"天桥短剧"},
"tomatoPlayetInfo":{"serialCount":"99","seriesId":"7456609977034099737"}},
"relatedInfo":null,"commentList":null}</textarea>
</body></html>"""

# fake 红果分享落地页(复刻实测《牢狱蒙尘，都市起风云第一季》2026-10-13,
# kylin.hainanyuyue.com/s/ 分享链 302 落地页: 无「共X集」, 第N集=当前集下界)
HONGGUO_PAGE = """<html><head>
<title>红果漫剧免费看《牢狱蒙尘，都市起风云第一季》</title>
<meta property="og:type" content="video">
</head><body>
<div>红果漫剧 海量漫剧、短剧、小说免费看</div><div>打开APP</div>
<h1>牢狱蒙尘，都市起风云第一季</h1>
<span>战神</span> <span>都市</span> <span>豪门</span>
<p>第61集｜出狱当天被未婚妻当众羞辱，却被冷艳女总裁闪婚领证。</p>
<script>var data = {"title":"牢狱蒙尘，都市起风云第一季",
"vid":"7689429703953746969","video_id":"7689391734379269144"};</script>
</body></html>"""

# fake 西瓜短剧页(复刻实测《长风独赴前程》2026-10-14, v.douyin.com 短链
# 302 → iesdouyin.com/xg/video/{id}: 剧名/出品方/话题 SSR, 无总集数/无主体
# 指标(运行时签名 API); 内嵌推荐视频 JSON title 干扰项验证 xigua 优先)
XG_PAGE = """<html><head>
<title>长风独赴前程 - 短剧高清在线观看 | 西瓜视频</title>
<meta property="og:title" content="长风独赴前程 - 短剧高清在线观看 | 西瓜视频">
<meta property="og:url" content="https://www.iesdouyin.com/xg/video/7696147289777212712/?app=video_article">
</head><body>
<div>长风独赴前程 - 短剧高清在线观看 | 西瓜视频 海量影视免费看 打开 打开西瓜视频，看完整高清内容 星光短剧 12万粉丝 3715视频 关注 长风独赴前程 2026-10-13发布 #热门短剧推荐 #AIGC #我的观影报告 #因为一个片段看了整部剧 #AI短剧 相关推荐 评论 6 打开App观看 03:41 植物大战僵尸：开局全是罐 160万次播放 2024年05月01日</div>
<script>{"title":"老公的屁股闯祸了","play_count":1605545}</script>
</body></html>"""

# fake 抖音 share/video 壳(实测零内容形态: _ROUTER_DATA 仅页面配置,
# title 通用壳无剧名 → 四级全 None, 不可采如实跳过)
DY_SHELL_PAGE = """<html><head><title>在抖音记录美好生活20261014 - 抖音火山版</title></head><body>
<script>window._ROUTER_DATA = {"loaderData":{"video_(id)/page":{"itemId":"7672372741114301731","renderInSSR":1}}}</script>
</body></html>"""

# fake 百度 lite 视频分享页(复刻实测《复仇厨娘》2026-10-14, mr.baidu.com
# 短链 302 → mbdlite.baidu.com/newspage/data/videoshare: 壳页 19KB,
# title 带集号后缀「 01」, duration 单集时长, 指标零 SSR)
BAIDU_PAGE = """<html><head><title>复仇厨娘：被弃养的她成了餐饮女王 01</title></head><body>
<div>百度lite</div>
<script>window.data = {"source_name":"external_share_video",
"videoId":"sv_4504100537651537416","nid":"sv_4504100537651537416",
"title":"\\u590d\\u4ec7\\u53a8\\u5a18","duration":96,"like":0};</script>
</body></html>"""

# fake 百度 lite 分享页·「-第N集」连字符形态(复刻《全族流放》2026-10-14:
# title 尾缀「-第1集」满足红果弱特征(JSON title=画质标签"蓝光"+第N集),
# 曾被红果形态截胡 → 百度强特征前置修复)
BAIDU_PAGE_2 = """<html><head><title>全族流放，我开荒造镇权倾荒岛-第1集</title></head><body>
<script>window.data = {"source_name":"external_share_video",
"nid":"sv_12228690175691920413","vid":"12228690175691920413",
"title":"\\u84dd\\u5149","duration":88};</script>
</body></html>"""

# fake 百度 lite 分享页·无 source_name 变体(复刻《卧底刑场》2026-10-14:
# rawFrom 非 collection_video_landing → 无 external_share_video 标记,
# duration 字符串形态 "162"; sv_ nid 双变体共有 → 特征判定改用 sv_)
BAIDU_PAGE_3 = """<html><head><title>卧底刑场截停揭七年黑幕正邪对决 第1集</title></head><body>
<script>window.data = {"nid":"sv_4908672657096981382",
"vid":"4908672657096981382","title":"\\u84dd\\u5149",
"duration":"162"};</script>
</body></html>"""

# fake 榜单页(复刻短剧工程 duanjugongcheng.com/cn/bangdan 真实 DOM, 2026-10-13)
BANGDAN_PAGE = """<html><body><ul>
<li class="flex items-center gap-3 py-2.5"><span class="tabular-nums text-rose-600">1</span>
<span class="min-w-0 flex-1 truncate"><a href="/cn/bangdan/ju/fan-xiang-na-nie">反向拿捏：和闺蜜穿七零带着婆婆一起离</a></span>
<a href="/cn/bangdan/genre/nian-dai-ai-qing">年代爱情</a>
<span class="text-2xs text-slate-400">AI短剧</span>
<span class="w-20 text-right text-xs font-semibold tabular-nums text-slate-900">7,783</span></li>
<li class="flex items-center gap-3 py-2.5"><span class="tabular-nums text-rose-600">2</span>
<span class="min-w-0 flex-1 truncate"><a href="/cn/bangdan/ju/zai-nian-ni-xi">灾年逆袭：我靠穿越养全家</a></span>
<a href="/cn/bangdan/genre/zhong-tian-jing-ying">种田经营</a>
<span class="text-2xs text-slate-400">AI短剧</span>
<span class="w-20 text-right text-xs font-semibold tabular-nums text-slate-900">6,852</span></li>
<li class="flex items-center gap-3"><span>导航项(无剧链, 应被 item 过滤)</span></li>
</ul></body></html>"""

BANGDAN_XPATH = {"item": "//li[.//a[contains(@href,'/bangdan/ju/')]]",
                 "title": ".//a[contains(@href,'/bangdan/ju/')]/text()",
                 "tags": ".//a[contains(@href,'/bangdan/genre/')]/text()",
                 "plays": ".//span[contains(@class,'tabular-nums') and "
                          "contains(@class,'text-slate-900')]/text()"}

# fake 新剧列表页(复刻 shortdramawiki.com/en/new/ 真实 DOM, 2026-10-13)
SDW_PAGE = """<html><body><div class="card-grid">
<a class="poster-card" href="/en/dramas/sacrificial-pharaoh-s-queen/">
<div class="poster"><img alt="Sacrificial Pharaoh's Queen"><span class="badge">63 eps</span></div>
<div class="title">Sacrificial Pharaoh's Queen</div><div class="meta">DramaWave</div></a>
<a class="poster-card" href="/en/dramas/game-of-goals/">
<div class="poster"><span class="badge">80 eps</span></div>
<div class="title">Game Of Goals</div><div class="meta">MoboReels</div></a>
<a class="nav-link" href="/en/genres/">Genres</a>
</div></body></html>"""

SDW_XPATH = {"item": "//a[contains(@class,'poster-card')]",
             "title": ".//div[@class='title']/text()",
             "episodes": ".//span[@class='badge']/text()",
             "platforms": ".//div[@class='meta']/text()"}

# fake JSON 页面(字节系风格: data.list + 驼峰 + 中文单位)
PAGE_JSON = json.dumps({
    "code": 0, "msg": "ok",
    "data": {"list": [
        {"dramaName": "龙王归来第一季", "tag": "战神归来,逆袭",
         "episodeCount": 80, "duration": 95,
         "playCnt": "1.2万", "likeCnt": 156000,
         "finishRate": 0.62},
        {"dramaName": "隐婚千金第二季", "tag": "婚恋",
         "episodeCount": "66", "playCnt": "3,400", "finishRate": "71"},
        {"tag": "无题"},          # 缺 title → 丢弃
    ]},
}, ensure_ascii=False)

FIELD_MAP = {"list": "$.data.list", "title": "dramaName",
             "tags": "tag", "episodes": "episodeCount",
             "durationSec": "duration", "plays": "playCnt",
             "likes": "likeCnt", "completionRate": "finishRate"}

# fake HTML 榜单页
PAGE_HTML = """<html><body>
<div class="rank-list">
  <div class="item"><h3>下山虎都市录</h3>
    <span class="tag">都市 打斗</span><span class="ep">72集</span>
    <span class="play">152万</span><span class="fin">58%</span></div>
  <div class="item"><h3>重生九零小厨娘</h3>
    <span class="tag">年代 美食</span><span class="ep">58集</span>
    <span class="play">66万</span><span class="fin">0.66</span></div>
</div></body></html>"""

XPATH_MAP = {"item": "//div[@class='item']", "title": ".//h3/text()",
             "tags": ".//span[@class='tag']/text()",
             "episodes": ".//span[@class='ep']/text()",
             "plays": ".//span[@class='play']/text()",
             "completionRate": ".//span[@class='fin']/text()"}


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} {detail}")


class EnvGuard:
    def __enter__(self):
        self._backup = {k: os.environ.get(k) for k in WUKONG_KEYS}
        for k in WUKONG_KEYS:
            os.environ.pop(k, None)
        return self

    def __exit__(self, *exc):
        for k, v in self._backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return False


def fake_page(status=200, text=PAGE_JSON, robots="User-agent: *\nAllow: /"):
    calls = {"page": 0, "robots": 0}

    def http_get(url, headers=None):
        calls["page"] += 1
        return status, text

    def robots_get(url):
        calls["robots"] += 1
        return robots

    return http_get, robots_get, calls


def main():
    from drama85_wukong_worker import (
        WukongCollector, build_contract_response, DEFAULT_PLATFORMS)
    from services.drama85_source_gateway import RealDramaSource

    URL = "https://wukong.test/short-channel"

    print("[01 JSON 形态: FIELD_MAP → 契约(容错+单位换算)]")
    http_get, robots_get, calls = fake_page()
    with EnvGuard():
        os.environ["WUKONG_LIST_URL"] = URL
        os.environ["WUKONG_QPS"] = "100"
        os.environ["WUKONG_FIELD_MAP"] = json.dumps(
            FIELD_MAP, ensure_ascii=False)
        c = WukongCollector(http_get, robots_get)
        contract = c.collect(n=50, offset=0)
        check("契约结构(dramas/hasMore)",
              isinstance(contract.get("dramas"), list)
              and "hasMore" in contract, str(contract)[:80])
        check("缺 title 条目丢弃(3 条→2 条)",
              len(contract["dramas"]) == 2, str(len(contract["dramas"])))
        d0 = contract["dramas"][0]
        check("中文单位换算('1.2万'→12000)",
              d0["metrics"]["plays"] == 12000, str(d0["metrics"]))
        check("逗号数字('3,400'→3400)+数字串集数",
              contract["dramas"][1]["metrics"]["plays"] == 3400
              and contract["dramas"][1]["episodes"] == 66, "")
        check("字符串 tags 分隔归一('战神归来,逆袭')",
              d0["tags"] == ["战神归来", "逆袭"], str(d0["tags"]))
        check("platforms 默认字节系并集",
              d0["platforms"] == DEFAULT_PLATFORMS.split(","),
              str(d0["platforms"]))
        check("percent 完播率('71'→0.71)",
              abs(contract["dramas"][1]["metrics"]["completionRate"]
                  - 0.71) < 1e-9,
              str(contract["dramas"][1]["metrics"]))
        check("合规红线: commentsData 恒空(评论不采)",
              all(d["commentsData"] == [] for d in contract["dramas"]), "")

    print("[02 JSON auto 路径探测(未配 FIELD_MAP)]")
    http_get, robots_get, _ = fake_page()
    with EnvGuard():
        os.environ["WUKONG_LIST_URL"] = URL
        os.environ["WUKONG_QPS"] = "100"
        c = WukongCollector(http_get, robots_get)
        # auto 只定位数组; 字段名不同→title 缺→条目丢弃→提示配 FIELD_MAP
        try:
            c.collect()
            check("auto 无法映射字段名 → ValueError 提示",
                  False, "未抛错")
        except ValueError as e:
            check("auto 无法映射字段名 → ValueError 提示",
                  "WUKONG_FIELD_MAP" in str(e), str(e))
        os.environ["WUKONG_FIELD_MAP"] = json.dumps(
            {"list": "$.data.list", "title": "dramaName"})
        contract = WukongCollector(http_get, robots_get).collect()
        check("auto $.data.list 定位 + 最小映射(title)",
              len(contract["dramas"]) == 2
              and contract["dramas"][0]["title"] == "龙王归来第一季", "")

    print("[03 HTML 形态: XPATH → 契约]")
    http_get, robots_get, _ = fake_page(text=PAGE_HTML)
    with EnvGuard():
        os.environ["WUKONG_LIST_URL"] = URL
        os.environ["WUKONG_QPS"] = "100"
        os.environ["WUKONG_XPATH"] = json.dumps(
            XPATH_MAP, ensure_ascii=False)
        c = WukongCollector(http_get, robots_get)
        contract = c.collect()
        check("HTML 条目数(2)", len(contract["dramas"]) == 2, "")
        h0 = contract["dramas"][0]
        check("HTML '152万'→1520000/'72集'→72",
              h0["metrics"]["plays"] == 1520000
              and h0["episodes"] == 72, str(h0))
        check("HTML tags 空格分隔('都市 打斗')",
              h0["tags"] == ["都市", "打斗"], str(h0["tags"]))
        check("HTML percent '58%'→0.58 / 小数 '0.66' 直用",
              abs(h0["metrics"]["completionRate"] - 0.58) < 1e-9
              and abs(contract["dramas"][1]
                      ["metrics"]["completionRate"] - 0.66) < 1e-9, "")

    print("[04 auto kind 判定]")
    http_get, robots_get, _ = fake_page(text=PAGE_JSON)
    with EnvGuard():
        os.environ["WUKONG_LIST_URL"] = URL
        os.environ["WUKONG_QPS"] = "100"
        os.environ["WUKONG_FIELD_MAP"] = json.dumps(FIELD_MAP)
        c = WukongCollector(http_get, robots_get)
        check("首字符 { → json", c._detect_kind(PAGE_JSON) == "json", "")
        check("尖括号开头 → html", c._detect_kind(PAGE_HTML) == "html", "")
        os.environ["WUKONG_LIST_KIND"] = "html"
        check("显式配置优先(WUKONG_LIST_KIND=html)",
              c._detect_kind(PAGE_JSON) == "html", "")

    print("[05 合规红线]")
    http_get, robots_get, _ = fake_page(
        robots="User-agent: *\nDisallow: /short-channel")
    with EnvGuard():
        os.environ["WUKONG_LIST_URL"] = URL
        os.environ["WUKONG_QPS"] = "100"
        c = WukongCollector(http_get, robots_get)
        try:
            c.collect()
            check("robots Disallow → 拒采 ValueError", False, "未抛错")
        except ValueError as e:
            check("robots Disallow → 拒采 ValueError(不可覆盖)",
                  "robots" in str(e), str(e))
    http_get, robots_get, calls2 = fake_page(
        robots="User-agent: *\nDisallow: /short-channel")
    with EnvGuard():
        os.environ["WUKONG_LIST_URL"] = URL
        try:
            WukongCollector(http_get, robots_get).collect()
        except ValueError:
            pass
        check("拒采时页面请求计数=0", calls2["page"] == 0,
              str(calls2))
    with EnvGuard():
        try:
            WukongCollector(*fake_page()[:2]).collect()
            check("缺 WUKONG_LIST_URL → ValueError", False, "未抛错")
        except ValueError as e:
            check("缺 WUKONG_LIST_URL → ValueError(提示配置)",
                  "WUKONG_LIST_URL" in str(e), str(e))

    print("[06 限速与 T+1 缓存]")
    http_get, robots_get, calls3 = fake_page()
    with EnvGuard():
        os.environ["WUKONG_LIST_URL"] = URL
        os.environ["WUKONG_QPS"] = "100"
        os.environ["WUKONG_CACHE_TTL"] = "3600"
        os.environ["WUKONG_FIELD_MAP"] = json.dumps(FIELD_MAP)
        c = WukongCollector(http_get, robots_get)
        c.collect()
        c.collect()
        check("TTL 内二次 collect 复用缓存(页面仅请求 1 次)",
              calls3["page"] == 1, str(calls3))
        os.environ["WUKONG_CACHE_TTL"] = "60"      # 最小 60s
        os.environ["WUKONG_QPS"] = "5"             # 200ms 间隔
        c2 = WukongCollector(http_get, robots_get)
        t0 = time.monotonic()
        c2.collect()
        c2._cache.clear()          # 清缓存强制真实二次请求(命中缓存不走限速)
        c2.collect()
        elapsed = time.monotonic() - t0
        check("限速间隔 ≥ 1/qps(0.2s)", elapsed >= 0.18,
              f"{elapsed:.2f}s")

    print("[07 serve handler 纯逻辑]")
    http_get, robots_get, _ = fake_page()
    with EnvGuard():
        os.environ["WUKONG_LIST_URL"] = URL
        os.environ["WUKONG_QPS"] = "100"
        os.environ["WUKONG_FIELD_MAP"] = json.dumps(FIELD_MAP)
        c = WukongCollector(http_get, robots_get)
        st, body = build_contract_response(c, "offset=0&n=1")
        check("分页 n=1 → 1 条 + hasMore=true",
              st == 200 and len(body["dramas"]) == 1
              and body["hasMore"] is True, str(body)[:80])
        st, body = build_contract_response(c, "offset=5&n=10")
        check("offset 越界 → 空页 hasMore=false",
              st == 200 and body["dramas"] == []
              and body["hasMore"] is False, str(body))
        st, body = build_contract_response(c, "offset=abc")
        check("非法参数 → 400", st == 400, str(st))
    with EnvGuard():
        os.environ["WUKONG_QPS"] = "100"
        c_err = WukongCollector(*fake_page()[:2])   # 无 LIST_URL
        st, body = build_contract_response(c_err, "")
        check("上游配置错误 → 502+error 文案",
              st == 502 and "WUKONG_LIST_URL" in body.get("error", ""),
              str(body))

    print("[08 probe 结构摘要]")
    http_get, robots_get, _ = fake_page()
    with EnvGuard():
        os.environ["WUKONG_LIST_URL"] = URL
        os.environ["WUKONG_QPS"] = "100"
        summary = WukongCollector(http_get, robots_get).probe()
        check("JSON probe: listPaths 含 $.data.list",
              "$.data.list" in summary.get("listPaths", []),
              str(summary.get("listPaths")))
    http_get, robots_get, _ = fake_page(text=PAGE_HTML)
    with EnvGuard():
        os.environ["WUKONG_LIST_URL"] = URL
        os.environ["WUKONG_QPS"] = "100"
        summary = WukongCollector(http_get, robots_get).probe()
        check("HTML probe: tagFreq 含 div",
              "div" in summary.get("tagFreq", {}), "")
        check("HTML probe: kind=html + 提示 XPATH",
              summary.get("kind") == "html"
              and "WUKONG_XPATH" in summary.get("hint", ""), "")

    print("[09 全链闭环: worker 契约 → 85号 RealDramaSource 零改动消费]")
    http_get, robots_get, _ = fake_page()
    contract = None
    with EnvGuard():
        os.environ["WUKONG_LIST_URL"] = URL
        os.environ["WUKONG_QPS"] = "100"
        os.environ["WUKONG_FIELD_MAP"] = json.dumps(FIELD_MAP)
        contract = WukongCollector(http_get, robots_get).collect(n=50)

    async def worker_endpoint(url, headers=None):
        # 模拟 serve 端点: 直接返回 worker 契约
        return 200, contract

    _saved = {k: os.environ.get(k) for k in
              ("DRAMA85_SOURCE_URL", "DRAMA85_SOURCE_API_KEY",
               "DRAMA85_SOURCE_QPS")}
    os.environ["DRAMA85_SOURCE_URL"] = "http://127.0.0.1:8510/dramas"
    os.environ["DRAMA85_SOURCE_API_KEY"] = "local-worker"
    os.environ["DRAMA85_SOURCE_QPS"] = "100"
    try:
        rows = asyncio.run(RealDramaSource(worker_endpoint).fetch(50, 0))
    finally:
        for k, v in _saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    check("85号消费 worker 契约: 条目 2", len(rows) == 2, str(len(rows)))
    r0 = rows[0]
    check("契约字段全通过 normalize(title/metrics/plays)",
          r0["title"] == "龙王归来第一季"
          and r0["metrics"]["plays"] == 12000
          and abs(r0["metrics"]["completionRate"] - 0.62) < 1e-9,
          str(r0)[:100])
    check("platforms 字节系并集入池", r0["platforms"] ==
          ["wukong", "douyin", "hongguo"], str(r0["platforms"]))
    check("commentsData 空列表入池(待通道① 补齐)",
          r0["commentsData"] == [], "")

    print("[10 probe --gen-map 自动推导映射+试采验证]")
    http_get, robots_get, calls4 = fake_page()
    with EnvGuard():
        os.environ["WUKONG_LIST_URL"] = URL
        os.environ["WUKONG_QPS"] = "100"
        c = WukongCollector(http_get, robots_get)
        summary = c.probe(gen_map=True)
        draft = summary.get("fieldMapDraft")
        check("草稿推导(list+title+全字段别名命中)",
              isinstance(draft, dict)
              and draft.get("list") == "$.data.list"
              and draft.get("title") == "dramaName"
              and draft.get("plays") == "playCnt"
              and draft.get("completionRate") == "finishRate",
              str(draft))
        check("草稿试采验证 ok(tryFetch rows=2)",
              summary.get("tryFetch", {}).get("ok") is True
              and summary.get("tryFetch", {}).get("rows") == 2,
              str(summary.get("tryFetch")))
        check("probe+试采共用 T+1 缓存(页面仅 1 次请求)",
              calls4["page"] == 1, str(calls4))
        # 草稿直接作为正式配置 → 全链可用
        os.environ["WUKONG_FIELD_MAP"] = json.dumps(
            draft, ensure_ascii=False)
        contract = WukongCollector(http_get, robots_get).collect()
        check("草稿即配置: 正式 collect 成功(2 条+换算)",
              len(contract["dramas"]) == 2
              and contract["dramas"][0]["metrics"]["plays"] == 12000, "")
    # 多数组评分: 混淆数组(键名不命中)不抢最优路径
    mix_page = json.dumps({"data": {"list": PAGE_JSON and json.loads(
        PAGE_JSON)["data"]["list"], "ads": [{"adId": 1}]}},
        ensure_ascii=False)
    http_get, robots_get, _ = fake_page(text=mix_page)
    with EnvGuard():
        os.environ["WUKONG_LIST_URL"] = URL
        os.environ["WUKONG_QPS"] = "100"
        summary = WukongCollector(http_get, robots_get).probe(gen_map=True)
        check("多数组评分选优($.data.list 而非 $.data.ads)",
              summary.get("fieldMapDraft", {}).get("list")
              == "$.data.list", str(summary.get("fieldMapDraft")))
    # 无可识别数组 → 草稿 None
    http_get, robots_get, _ = fake_page(text=json.dumps(
        {"code": 0, "msg": "ok"}, ensure_ascii=False))
    with EnvGuard():
        os.environ["WUKONG_LIST_URL"] = URL
        os.environ["WUKONG_QPS"] = "100"
        summary = WukongCollector(http_get, robots_get).probe(gen_map=True)
        check("无可识别数组 → fieldMapDraft None",
              summary.get("fieldMapDraft") is None, "")
    # HTML gen_map → XPATH 骨架
    http_get, robots_get, _ = fake_page(text=PAGE_HTML)
    with EnvGuard():
        os.environ["WUKONG_LIST_URL"] = URL
        os.environ["WUKONG_QPS"] = "100"
        summary = WukongCollector(http_get, robots_get).probe(gen_map=True)
        check("HTML gen_map → xpathDraft 骨架+人工提示",
              "item" in summary.get("xpathDraft", {})
              and "WUKONG_XPATH" in summary.get("xpathDraftHint", ""),
              str(summary.get("xpathDraft")))

    print("[11 share 形态: 分享链接列表 → 单剧契约]")
    pages = {"https://m.wkbrowser.com/is/AAA/": SHARE_PAGE_1,
             "https://m.wkbrowser.com/is/BBB/": SHARE_PAGE_2,
             "https://m.wkbrowser.com/is/BAD/": SHARE_PAGE_BAD}
    ua_seen = []

    def share_http(url, headers=None):
        ua_seen.append((headers or {}).get("User-Agent", ""))
        return 200, pages.get(url, SHARE_PAGE_BAD)

    http_get, robots_get = share_http, (
        lambda u: "User-agent: *\nAllow: /")
    with EnvGuard():
        os.environ["WUKONG_SHARE_URLS"] = (
            "https://m.wkbrowser.com/is/AAA/, "
            "https://m.wkbrowser.com/is/BBB/; "
            "https://m.wkbrowser.com/is/BAD/")
        os.environ["WUKONG_QPS"] = "100"
        c = WukongCollector(share_http, robots_get)
        check("auto 判定: 配 SHARE_URLS 未配 LIST_URL → share 模式",
              c._is_share_mode() is True, "")
        contract = c.collect()
        check("share 条目(好页 2 条, 引导页丢弃)",
              len(contract["dramas"]) == 2, str(len(contract["dramas"])))
        s0 = contract["dramas"][0]
        check("share 解析(title/共99集/已完结入 tags)",
              s0["title"] == "父亲的礼物"
              and s0["episodes"] == 99
              and s0["tags"] == ["已完结"], str(s0))
        check("share platforms 字节系并集+指标 0(留通道①)",
              s0["platforms"] == ["wukong", "douyin", "hongguo"]
              and s0["metrics"]["plays"] == 0, str(s0["metrics"]))
        check("share 移动 UA(分享页移动渲染)",
              all("Mobile" in u for u in ua_seen), str(ua_seen[:1]))
        # 去重: 同 itemId/同名剧只留一条
        os.environ["WUKONG_SHARE_URLS"] = (
            "https://m.wkbrowser.com/is/AAA/ "
            "https://m.wkbrowser.com/is/AAA/")
        contract = WukongCollector(share_http, robots_get).collect()
        check("share 去重(同剧两链接 → 1 条)",
              len(contract["dramas"]) == 1, str(len(contract["dramas"])))
        # 红果分享落地页(hainanyuyue 域): 无共X集 → 次级形态解析
        pages["https://kylin.hainanyuyue.com/s/HG1/"] = HONGGUO_PAGE
        os.environ["WUKONG_SHARE_URLS"] = (
            "https://kylin.hainanyuyue.com/s/HG1/")
        contract = WukongCollector(share_http, robots_get).collect()
        hg = contract["dramas"][0] if contract["dramas"] else {}
        check("红果页解析(JSON title/第61集下界/题材 tags)",
              len(contract["dramas"]) == 1
              and hg.get("title") == "牢狱蒙尘，都市起风云第一季"
              and hg.get("episodes") == 61
              and hg.get("tags") == ["战神", "都市", "豪门"], str(hg))
        check("红果页 platforms=hongguo(条目级覆盖)",
              hg.get("platforms") == ["hongguo"], str(hg.get("platforms")))
        # RENDER_DATA SSR 形态(10-13 深挖升级): 结构化字段优先于正文正则
        pages["https://m.wkbrowser.com/is/RD1/"] = RD_PAGE
        os.environ["WUKONG_SHARE_URLS"] = (
            "https://m.wkbrowser.com/is/RD1/")
        contract = WukongCollector(share_http, robots_get).collect()
        d0 = contract["dramas"][0] if contract["dramas"] else {}
        check("RENDER_DATA 优先(seoTDK 剥后缀/serialCount=99 盖过正文 101)",
              d0.get("title") == "父亲的礼物"
              and d0.get("episodes") == 99, str(d0)[:80])
        check("SSR 增强(durationSec=88/subtitleText 剧情简介入契约)",
              d0.get("durationSec") == 88
              and "陆建设" in d0.get("subtitleText", ""), str(d0)[:100])
        check("集级指标不入契约 metrics(口径保护, 留通道①)",
              d0.get("metrics", {}).get("plays") == 0
              and d0.get("metrics", {}).get("likes") == 0,
              str(d0.get("metrics")))
        summary = WukongCollector(share_http, robots_get).probe()
        p0 = summary["sharePreview"][0]
        check("probe 预览含 SSR 增强字段(durationSec/集级指标/出品方)",
              p0.get("durationSec") == 88
              and (p0.get("episodeMetrics") or {}).get("plays") == 519
              and p0.get("source") == "心动短剧", str(p0)[:110])
        # 西瓜短剧页(v.douyin 短链 302 → iesdouyin /xg/video/): 第4级形态
        pages["https://v.douyin.com/XG1/"] = XG_PAGE
        os.environ["WUKONG_SHARE_URLS"] = "https://v.douyin.com/XG1/"
        contract = WukongCollector(share_http, robots_get).collect()
        xg = contract["dramas"][0] if contract["dramas"] else {}
        check("西瓜页解析(title 剥尾缀/episodes=0/platforms=xigua)",
              len(contract["dramas"]) == 1
              and xg.get("title") == "长风独赴前程"
              and xg.get("episodes") == 0
              and xg.get("platforms") == ["xigua"], str(xg)[:90])
        raw_xg = WukongCollector._parse_share_page(XG_PAGE)
        check("西瓜页元数据(creator/发布日期/#话题/itemId 去重键)",
              raw_xg.get("creator") == "星光短剧"
              and raw_xg.get("pubDate") == "2026-10-13"
              and "AI短剧" in raw_xg.get("tags", [])
              and raw_xg.get("itemId") == "7696147289777212712",
              str(raw_xg)[:110])
        check("西瓜页契约 tags 白名单保留(AIGC 入契约)",
              "AIGC" in xg.get("tags", []), str(xg.get("tags")))
        check("西瓜页指标 0(主体播放量运行时, 推荐位 160 万不入契约)",
              xg.get("metrics", {}).get("plays") == 0
              and xg.get("metrics", {}).get("likes") == 0,
              str(xg.get("metrics")))
        # 抖音 share/video 壳(零内容形态): 四级全 None → 如实跳过
        pages["https://v.douyin.com/DY1/"] = DY_SHELL_PAGE
        os.environ["WUKONG_SHARE_URLS"] = "https://v.douyin.com/DY1/"
        contract = WukongCollector(share_http, robots_get).collect()
        check("抖音 share/video 壳 → 0 条(不可采如实, 非编造)",
              len(contract["dramas"]) == 0, str(len(contract["dramas"])))
        # 百度 lite 视频分享页(mr.baidu 短链 302 → mbdlite): 第5级形态
        pages["https://mr.baidu.com/r/BD1"] = BAIDU_PAGE
        os.environ["WUKONG_SHARE_URLS"] = "https://mr.baidu.com/r/BD1"
        contract = WukongCollector(share_http, robots_get).collect()
        bd = contract["dramas"][0] if contract["dramas"] else {}
        check("百度页解析(title 剥集号/episodes 下界/durationSec=96)",
              len(contract["dramas"]) == 1
              and bd.get("title") == "复仇厨娘：被弃养的她成了餐饮女王"
              and bd.get("episodes") == 1
              and bd.get("durationSec") == 96, str(bd)[:90])
        check("百度页 platforms=baidu + 指标 0(零 SSR 留通道①)",
              bd.get("platforms") == ["baidu"]
              and bd.get("metrics", {}).get("plays") == 0,
              str(bd.get("platforms")))
        raw_bd = WukongCollector._parse_share_page(BAIDU_PAGE)
        check("百度页 itemId(sv_ nid 去重键)",
              raw_bd.get("itemId") == "sv_4504100537651537416",
              str(raw_bd.get("itemId")))
        # 百度「-第N集」形态: 防红果截胡(强特征前置)
        parsed2 = WukongCollector._parse_share_page(BAIDU_PAGE_2)
        check("百度「-第1集」形态(剥连字符后缀/防红果截胡)",
              parsed2.get("title") == "全族流放，我开荒造镇权倾荒岛"
              and parsed2.get("episodes") == 1
              and parsed2.get("platforms") == ["baidu"]
              and parsed2.get("durationSec") == 88, str(parsed2)[:100])
        check("百度页不被红果截胡(platforms≠hongguo/JSON 蓝光不入 title)",
              parsed2.get("platforms") != ["hongguo"]
              and "\\u84dd" not in parsed2.get("title", ""),
              str(parsed2.get("platforms")))
        # 百度无 source_name 变体(sv_ nid 特征兜底/「 第N集」空格形态/
        # duration 字符串 "162")
        parsed3 = WukongCollector._parse_share_page(BAIDU_PAGE_3)
        check("百度无标记变体(sv_ 特征/duration 字符串 162s)",
              parsed3.get("title") == "卧底刑场截停揭七年黑幕正邪对决"
              and parsed3.get("episodes") == 1
              and parsed3.get("platforms") == ["baidu"]
              and parsed3.get("durationSec") == 162, str(parsed3)[:100])
        # probe share 预览
        os.environ["WUKONG_SHARE_URLS"] = (
            "https://m.wkbrowser.com/is/AAA/ "
            "https://m.wkbrowser.com/is/BAD/")
        summary = WukongCollector(share_http, robots_get).probe()
        check("probe share 预览(1/2 成功+title/episodes)",
              summary.get("kind") == "share"
              and "1/2" in summary.get("hint", "")
              and summary["sharePreview"][0].get("episodes") == 99,
              str(summary.get("hint")))
        # 显式 kind=share 缺 SHARE_URLS → 报错
        os.environ.pop("WUKONG_SHARE_URLS")
        os.environ["WUKONG_LIST_KIND"] = "share"
        try:
            WukongCollector(share_http, robots_get).collect()
            check("share 缺 SHARE_URLS → ValueError", False, "未抛错")
        except ValueError as e:
            check("share 缺 SHARE_URLS → ValueError(提示配置)",
                  "WUKONG_SHARE_URLS" in str(e), str(e))
        # share 链接 robots 红线复用
        os.environ["WUKONG_SHARE_URLS"] = "https://m.wkbrowser.com/is/AAA/"
        bad_robots = lambda u: "User-agent: *\nDisallow: /is/"   # noqa: E731
        try:
            WukongCollector(share_http, bad_robots).collect()
            check("share robots Disallow → 拒采", False, "未抛错")
        except ValueError as e:
            check("share robots Disallow → 拒采(红线复用)",
                  "robots" in str(e), str(e))
        check("robots 拒采 share 时零页面请求", len(ua_seen) == 12,
              str(len(ua_seen)))

    print("[12 列表页实测配置: 短剧工程榜单(抖音官方日榜) XPATH 全链]")
    http_get, robots_get = (lambda u, h=None: (200, BANGDAN_PAGE)), (
        lambda u: "User-agent: *\nAllow: /")
    with EnvGuard():
        os.environ["WUKONG_LIST_URL"] = (
            "https://www.duanjugongcheng.com/cn/bangdan")
        os.environ["WUKONG_LIST_KIND"] = "html"
        os.environ["WUKONG_PLATFORMS"] = "douyin"
        os.environ["WUKONG_QPS"] = "100"
        os.environ["WUKONG_XPATH"] = json.dumps(
            BANGDAN_XPATH, ensure_ascii=False)
        contract = WukongCollector(http_get, robots_get).collect()
        check("榜单条目(剧链 li 2 条, 导航 li 过滤)",
              len(contract["dramas"]) == 2, str(len(contract["dramas"])))
        b0 = contract["dramas"][0]
        check("榜单解析(title/体裁 tags/热度逗号数 7783)",
              b0["title"] == "反向拿捏：和闺蜜穿七零带着婆婆一起离"
              and b0["tags"] == ["年代爱情"]
              and b0["metrics"]["plays"] == 7783, str(b0))
        check("榜单 platforms=douyin(数据源为抖音官方榜)",
              b0["platforms"] == ["douyin"], str(b0["platforms"]))

    print("[13 出海新剧列表: shortdramawiki XPATH 全链]")
    http_get, robots_get = (lambda u, h=None: (200, SDW_PAGE)), (
        lambda u: "User-agent: *\nAllow: /")
    with EnvGuard():
        os.environ["WUKONG_LIST_URL"] = "https://shortdramawiki.com/en/new/"
        os.environ["WUKONG_LIST_KIND"] = "html"
        os.environ["WUKONG_QPS"] = "100"
        os.environ["WUKONG_XPATH"] = json.dumps(
            SDW_XPATH, ensure_ascii=False)
        contract = WukongCollector(http_get, robots_get).collect()
        check("出海条目(poster-card 2 张, nav 过滤)",
              len(contract["dramas"]) == 2, str(len(contract["dramas"])))
        w0 = contract["dramas"][0]
        check("出海解析(title/eps 后缀剥离 63)",
              w0["title"] == "Sacrificial Pharaoh's Queen"
              and w0["episodes"] == 63, str(w0))
        check("platforms 取自条目 meta(DramaWave/MoboReels)",
              w0["platforms"] == ["DramaWave"]
              and contract["dramas"][1]["platforms"] == ["MoboReels"],
              str((w0["platforms"],
                   contract["dramas"][1]["platforms"])))

    # 汇总
    print()
    print(f"85号悟空 worker 专项: PASS={PASS} FAIL={FAIL}")
    for line in RESULTS:
        print(line)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
