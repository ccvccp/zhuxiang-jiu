# -*- coding: utf-8 -*-
"""85号·媒体图文榜采集器(Day4, docs/短剧榜单采集持久化落地方案.md)

覆盖源(均为澎湃 m.thepaper.cn 公开转载图文, SSR 纯文本可解析, 无需 OCR):
  dataeye  : DataEye 研究院月榜文章——一文三榜(热力榜百强/抖音端原生百强/红果百强),
             rank_type 细分 monthly / monthly_douyin / monthly_hongguo;
             解析「《剧名》登顶/位列第二/…热力值达799.6万/日均热度…/播放增量…」句式。
  kuaishou: 快手星芒片单/内容矩阵文章——《剧名》密集提及, 无名次无周期,
             rank_type=new, 全量收快照(raw_json 留上下文, 含历年爆款引用供题材研究)。

红线: 只 GET 公开媒体页(robots 放行 newsDetail); 不碰 DataEye 官网付费区/快手 App。

用法:
  python drama85_media_crawler.py --source dataeye  --url https://m.thepaper.cn/newsDetail_forward_34234356 --once --verify
  python drama85_media_crawler.py --source kuaishou --url https://m.thepaper.cn/newsDetail_forward_32534610 --once --verify
"""
from __future__ import annotations

import argparse
import html as htmllib
import re
from datetime import datetime

import httpx
from sqlalchemy import func, select

from db import get_session
from models import CrawlJob, Drama, DramaRankSnapshot

UA = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"}

# DataEye 三榜分段标题(正则, 按出现顺序切分)
DE_BOARDS = [
    (re.compile(r"\d{1,2}月热力榜百强榜"), "monthly", "热力榜百强"),
    (re.compile(r"\d{1,2}月抖音端原生百强榜"), "monthly_douyin", "抖音端原生百强"),
    (re.compile(r"\d{1,2}月红果百强榜"), "monthly_hongguo", "红果百强"),
]
RANK_CN = {"登顶": 1, "第一": 1, "第二": 2, "第三": 3, "第四": 4, "第五": 5,
           "第六": 6, "第七": 7, "第八": 8, "第九": 9, "第十": 10}


def fetch_text(url: str) -> str:
    with httpx.Client(timeout=60, follow_redirects=True, headers=UA) as c:
        r = c.get(url)
        r.raise_for_status()
        t = r.text
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", t, flags=re.S)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", htmllib.unescape(t))


def fetch_html(url: str) -> str:
    """原始 HTML(结构化列表页解析用; fetch_text 是纯文本化, 会剥掉标签)"""
    with httpx.Client(timeout=60, follow_redirects=True, headers=UA) as c:
        r = c.get(url)
        r.raise_for_status()
        return r.text


# ---------- DataEye 月榜解析 ----------

def _cn_num(s: str) -> int | None:
    try:
        return int(s)
    except ValueError:
        return None


def de_period(text: str) -> tuple[str, str] | None:
    """「正式发布2026年9月热力榜」/ 标题「9月…榜」→ 月周期。返回 (YYYY-MM-01, 月末)。"""
    m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月[热力榜、抖]", text) or \
        re.search(r"发布\s*(\d{4})\s*年\s*(\d{1,2})\s*月", text)
    if not m:
        return None
    y, mo = int(m.group(1)), int(m.group(2))
    from calendar import monthrange
    d_end = monthrange(y, mo)[1]
    return "%04d-%02d-01" % (y, mo), "%04d-%02d-%02d" % (y, mo, d_end)


_CN_DIGIT = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _cn2int(s: str) -> int | None:
    """中文数字→整数(一~九十九/阿拉伯直转); 不识别返回 None。"""
    if s.isdigit():
        return int(s)
    if s == "十":
        return 10
    if "十" in s:
        a, _, b = s.partition("十")
        head = _CN_DIGIT.get(a, 1) if a else 1
        tail = _CN_DIGIT.get(b, 0) if b else 0
        return head * 10 + tail
    return _CN_DIGIT.get(s)


# 后置直接式(剧名之后同子句): 登顶/位列第二/排名第三/闯入TOP8/《X》第一,
# 全部走命名捕获组——规避中文字面量 replace 手术(实测传输链路存在损坏风险)
RANK_AFTER = re.compile(
    r"(?P<top>登顶)"
    r"|位列第(?P<w1>[一二三四五六七八九十\d]{1,3})"
    r"|排名第(?P<w2>[一二三四五六七八九十\d]{1,3})"
    r"|[闯空]入\s*TOP\s*(?P<w3>\d{1,2})"
    r"|(?<![位列排名])第(?P<w4>[一二三四五六七八九十\d]{1,3})\s*[，,。]")


def _rank_from_after(after: str) -> int | None:
    m = RANK_AFTER.search(after)
    if not m:
        return None
    if m.group("top"):
        # 「登顶红果真人剧榜/抖音热播榜」=分榜登顶, 非本榜名次(2026-10-15
        # 复核修正: 二嫁有喜 no=1 误判案例); 「登顶」后紧跟句读/介词才认本榜
        tail = after[m.end():m.end() + 10]
        if re.match(r"^[一二三四五六七八九十百一-龥]{2,8}榜", tail):
            return None
        return 1
    for g in ("w1", "w2", "w3", "w4"):
        v = m.group(g)
        if v:
            return _cn2int(v)
    return None


def de_parse(text: str) -> list[dict]:
    """一文三榜 → 条目[{board, rank_type, rank, name, heat/plays, unit, ctx}]。

    归属纪律(2026-10-15 抽检修正): 名次/数值只从**同子句**取——
    剧名之后到下一个句读(。；)为止 + 前置 12 字(「榜首《X》」倒装), 杜绝跨句串档。
    """
    marks = []
    for pat, rtype, label in DE_BOARDS:
        m = pat.search(text)
        if m:
            marks.append((m.start(), rtype, label))
    marks.sort()
    if not marks:
        return []
    items = []
    for i, (pos, rtype, label) in enumerate(marks):
        seg_end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        seg = text[pos:seg_end]
        for m in re.finditer(r"《\s*([^《》]{2,25})\s*》", seg):
            name = re.sub(r"\s+", "", m.group(1))
            # 同子句: 剧名后至句读; 前置 12 字(倒装「榜首《X》登顶」)
            tail_m = re.search(r"[。；]", seg[m.end():])
            after = seg[m.end():m.end() + (tail_m.start() if tail_m else 120)]
            before = seg[max(0, m.start() - 12):m.start()]
            clause = (before + "《》" + after)[:150]
            rank = _rank_from_after(after)
            if rank is None:
                # 前置倒装式: 榜首/第N名《X》(以《结尾的前置词)
                mb = re.search(r"(?:榜首|第([一二三四五六七八九十\d]{1,3})名?)\s*$", before)
                if mb:
                    v = mb.group(1)
                    rank = 1 if not v else _cn2int(v)
            # 数值: 只认剧名后同子句内
            heat = plays = None
            unit = None
            mv = re.search(r"(?:热力值|日均热度|热度)[达近约超]*\s*([\d.]+)\s*(万|亿)", after)
            if mv:
                heat = int(float(mv.group(1)) * (100_000_000 if mv.group(2) == "亿" else 10_000))
                unit = "wan" if mv.group(2) == "万" else "yi"
            else:
                mp = re.search(r"播放[增量增额]*[达近约超]*\s*([\d.]+)\s*(万|亿)", after)
                if mp:
                    plays = int(float(mp.group(1)) * (100_000_000 if mp.group(2) == "亿" else 10_000))
                    unit = "wan" if mp.group(2) == "万" else "yi"
            items.append({"board": label, "rank_type": rtype, "rank": rank,
                          "name": name, "heat": heat, "plays": plays,
                          "unit": unit, "ctx": clause})
    best: dict[tuple, dict] = {}
    for it in items:
        k = (it["rank_type"], it["name"])
        # 注意: 布尔加分必须用 is not None —— 旧写法 (heat or plays is not None)
        # 把 heat 数值本身当分数(热度大者胜), 东北话事人 no=10/峰值6715万
        # 压过 no=2/日均5968.8万 即此 bug(2026-10-15 复核修正)
        score = ((it["rank"] is not None) * 2
                 + (it["heat"] is not None or it["plays"] is not None))
        if k not in best or score > best[k][0]:
            best[k] = (score, it)
    return [v[1] for v in best.values()]


# ---------- 快手星芒片单解析 ----------

def ks_parse(text: str) -> list[dict]:
    items, seen = [], set()
    for m in re.finditer(r"《\s*([^《》]{2,25})\s*》", text):
        name = re.sub(r"\s+", "", m.group(1))
        if name in seen:
            continue
        seen.add(name)
        ctx = text[max(0, m.start() - 50):m.end() + 70]
        items.append({"board": "星芒片单", "rank_type": "new", "rank": None,
                      "name": name, "heat": None, "plays": None,
                      "unit": None, "ctx": ctx[:140]})
    return items


# ---------- 第三方聚合榜解析(2026-10-15 三链投喂接入) ----------

def djb_parse(text: str) -> list[dict]:
    """短剧百科 duanjubaike.net/paihang/rebo.html 多平台聚合热播榜(单页~100名, SSR)
    结构: <a href="/duanju/info-{series_id}.html" title="短剧《名》"> 卡片
          + "{n}万热度" + "全 N 集"; 块序=名次。
    ★ info-{sid} 的 sid 与红果 video_series_id 同键(2026-10-15 实证: 京夜诱温
    info-7693823855940357144 == 分享链 video_series_id) — ctx 留档备归一。"""
    items = []
    # 卡片迭代: 完整开标签(含 href/title)到下一卡片——不可用 split,
    # delimiter 会吞掉 href/title(2026-10-15 调试实锤: split 法 0 条)
    tags = list(re.finditer(r'<a\s[^>]*class="card"[^>]*>', text))
    for i, m in enumerate(tags):
        head = m.group(0)
        end = tags[i + 1].start() if i + 1 < len(tags) else len(text)
        blk = text[m.start():end]
        mh = re.search(
            r'href="/duanju/info-(\d+)\.html"[^>]*title="短剧《([^》]{2,30})》"',
            head)
        if not mh:
            continue
        sid, name = mh.group(1), mh.group(2)
        mv = re.search(r"([\d.]+)万热度", blk)
        me = re.search(r"全\s*(\d+)\s*集", blk)
        items.append({
            "board": "聚合热播榜", "rank_type": "hot", "rank": i + 1,
            "name": name,
            "heat": int(float(mv.group(1)) * 10_000) if mv else None,
            "plays": None, "unit": "wan" if mv else None,
            "ctx": f"sid={sid} ep={me.group(1) if me else '?'} "
                   f"heat={mv.group(1) + '万' if mv else '?'}",
        })
    return items


def rebang_parse(text: str) -> list[dict]:
    """rebang.vip/baidu/drama 百度热门短剧榜(今日热榜聚合, SSR 60条)
    结构: <span class="rank">N</span> ... <div class="title">名</div>
          <span class="hot-value">热度：热度:{v}<br>题材｜共N集<br>主演：..."""
    items = []
    pat = re.compile(
        r'class="rank">(\d+)</span>.*?class="title">([^<]{2,30})</div>.*?'
        r'class="hot-value">热度：热度：([\d.]+万?)<br>([^<｜]*)｜?[^<]*',
        re.S)
    for m in pat.finditer(text):
        no, name, hv, genre = m.group(1), m.group(2), m.group(3), m.group(4)
        hv_s = hv.rstrip("万")
        items.append({
            "board": "百度短剧榜", "rank_type": "hot", "rank": int(no),
            "name": name,
            "heat": int(float(hv_s) * (10_000 if hv.endswith("万") else 1)),
            "plays": None,
            "unit": "wan" if hv.endswith("万") else "raw",
            "ctx": f"genre={genre.strip()} {m.group(0)[m.start(4) - 40:m.start(4)][:40]}",
        })
    return items


def djtj7_parse(text: str) -> list[dict]:
    """djtj7.com.cn 首页六平台混合热搜榜(SSR li 结构):
    <li><span class="rank-no">N</span><a>剧名</a><span class="rank-meta">平台 · 热度 V</span></li>"""
    items = []
    pat = re.compile(
        r'class="rank-no">(\d+)</span>\s*<a[^>]*>([^<]{2,30})</a>'
        r'\s*<span class="rank-meta">([^<·]+)·\s*热度\s*([\d.]+[万亿]?)</span>')
    for m in pat.finditer(text):
        no, name, plat, hv = m.groups()
        items.append({
            "board": f"混合热搜榜({plat.strip()})", "rank_type": "hot",
            "rank": int(no), "name": name.strip(),
            "heat": int(float(hv.rstrip("万亿")) *
                        (10_000 if "万" in hv else 100_000_000 if "亿" in hv else 1)),
            "plays": None,
            "unit": "wan" if "万" in hv else ("yi" if "亿" in hv else "raw"),
            "ctx": f"plat={plat.strip()} heat={hv}",
        })
    return items


def djgc_parse(text: str) -> list[dict]:
    """duanjugongcheng.com 短剧风向标(SSR 表格 101 tr):
    列=排名/封面/短剧名称/体裁/题材/标签/今日热播指数/趋势;
    剧名 <a title="查看《名》榜单数据">; 指数=td 文本最大数值(列7),
    趋势=末列(↑↓N/热度±N%)。"""
    items = []
    for tr in re.findall(r"<tr\b[^>]*>.*?</tr>", text, re.S):
        mh = re.search(r'title="查看《([^》]{2,30})》榜单数据"', tr)
        if not mh:
            continue
        name = mh.group(1)
        tds = [re.sub(r"<[^>]+>", "", td).strip()
               for td in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        # 名次取第 1 列 td 文本(enumerate 会因表头/异表 tr 错位)
        rank = int(tds[0]) if tds and re.fullmatch(r"\d+", tds[0]) else None
        # 数值型 td(排除名次列): 取最大纯数值为指数
        nums = []
        for td in tds[1:]:
            if re.fullmatch(r"[\d.,]+", td):
                nums.append(float(td.replace(",", "")))
        idx_v = max(nums) if nums else None
        trend = next((td for td in tds if "↑" in td or "↓" in td or "%" in td), "")
        items.append({
            "board": "短剧风向标", "rank_type": "hot",
            "rank": rank, "name": name,
            "heat": int(idx_v) if idx_v else None,
            "plays": None, "unit": "raw" if idx_v else None,
            "ctx": f"trend={trend[:20]} cols={len(tds)}",
        })
    return items


def sdc_parse(text: str) -> list[dict]:
    """shortdramacast.com(SceneTrace) 中文短剧热门 Top100(海外视角, SSR article):
    <article ... data-analytics-item-name="名" data-analytics-rank="N"
    aria-label="第N名：名, 热度 H"> — 热度为 0-100 相对分。"""
    items = []
    # article 块内属性顺序: aria-label 在 data-* 之前 —— 分属性提取
    for blk in re.split(r"<article\b", text)[1:]:
        mn = re.search(r'data-analytics-item-name="([^"]{2,40})"', blk)
        mr = re.search(r'data-analytics-rank="(\d+)"', blk)
        mh = re.search(r'aria-label="第\d+名：[^,]+, 热度 ([\d.]+)"', blk)
        if not (mn and mr):
            continue
        name, no = mn.group(1), mr.group(1)
        hv = mh.group(1) if mh else None
        items.append({
            "board": "出海中文短剧榜", "rank_type": "hot",
            "rank": int(no), "name": name,
            "heat": int(float(hv)) if hv else None,
            "plays": None, "unit": "raw" if hv else None,
            "ctx": f"rel_score={hv}/100" if hv else "no_score",
        })
    return items


def sdc_actors_parse(text: str) -> list[dict]:
    """sdc 演员热度榜(全量 SSR 185 卡, person 维度):
    <a ... data-analytics-content-type="person" data-analytics-item-name="演员名"
    data-analytics-rank="N"> —— drama_name 列存演员名(rank_type=actor 留档,
    matcher 按剧名匹配天然不会误链)。"""
    items = []
    for blk in re.split(r"<a\s[^>]*actor-discovery-profile", text)[1:]:
        mc = re.search(r'data-analytics-content-type="person"', blk)
        mn = re.search(r'data-analytics-item-name="([^"]{2,40})"', blk)
        mr = re.search(r'data-analytics-rank="(\d+)"', blk)
        if not (mc and mn and mr):
            continue
        items.append({
            "board": "短剧演员热度榜", "rank_type": "actor",
            "rank": int(mr.group(1)), "name": mn.group(1),
            "heat": None, "plays": None, "unit": None,
            "ctx": "person(演员维度, 非剧)",
        })
    return items


# ---------- 落库 ----------

def _link_drama(s, name: str) -> int | None:
    row = s.execute(select(Drama).where(
        func.replace(func.replace(Drama.drama_name, " ", ""), "\u3000", "")
        == re.sub(r"[\s\u3000]+", "", name))).scalars().first()
    return row.drama_id if row else None


def persist(source: str, url: str, text: str) -> dict:
    st = {"items": 0, "snap_new": 0, "snap_dup": 0, "linked": 0, "unlinked": 0, "period": None}
    snap_time = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    p_start = p_end = None
    if source == "dataeye":
        period = de_period(text)
        if period is None:
            raise ValueError("月榜周期未解析到(需「YYYY年M月…榜」句式)——终止, 不造数")
        st["period"] = "~".join(period)
        p_start, p_end = period
        items = de_parse(text)
    elif source == "duanjubaike":
        items = djb_parse(text)
    elif source == "rebang_baidu":
        items = rebang_parse(text)
    elif source == "djtj7":
        items = djtj7_parse(text)
    elif source == "djgc":
        items = djgc_parse(text)
    elif source == "sdc":
        items = sdc_parse(text)
    elif source in ("sdc_global", "sdc_ai"):
        items = sdc_parse(text)
    elif source == "sdc_actors":
        items = sdc_actors_parse(text)
    else:
        items = ks_parse(text)
    st["items"] = len(items)

    with get_session() as s:
        for it in items:
            exists = s.execute(select(DramaRankSnapshot.id).where(
                DramaRankSnapshot.source == source,
                DramaRankSnapshot.rank_type == it["rank_type"],
                DramaRankSnapshot.drama_name == it["name"],
                DramaRankSnapshot.period_start == p_start,
                DramaRankSnapshot.period_end == p_end,
                DramaRankSnapshot.snapshot_time == snap_time,
            )).first()
            if exists:
                st["snap_dup"] += 1
                continue
            drama_id = _link_drama(s, it["name"])
            st["linked" if drama_id else "unlinked"] += 1
            s.add(DramaRankSnapshot(
                source=source, rank_type=it["rank_type"],
                rank_no=it["rank"], drama_id=drama_id, drama_name=it["name"],
                heat_value=it["heat"], heat_unit=it["unit"], play_delta=it["plays"],
                period_start=p_start, period_end=p_end,
                snapshot_time=snap_time, raw_url=url,
                raw_json={"board": it["board"], "ctx": it["ctx"]},
            ))
            st["snap_new"] += 1
        job = s.execute(select(CrawlJob).where(CrawlJob.source == source)).scalar_one_or_none()
        if job is not None:
            job.url = url
            job.method = "html"
            job.status = "success"
            job.last_run_at = datetime.now()
            job.error_msg = None
    return st


def verify() -> bool:
    ok = True
    with get_session() as s:
        for src in ("dataeye", "kuaishou"):
            rows = s.execute(select(
                DramaRankSnapshot.rank_type, func.count()).where(
                DramaRankSnapshot.source == src).group_by(DramaRankSnapshot.rank_type)).all()
            total = sum(n for _, n in rows)
            linked = s.execute(select(func.count()).where(
                DramaRankSnapshot.source == src,
                DramaRankSnapshot.drama_id.isnot(None))).scalar()
            print("%s: total=%d by_type=%s linked=%d" % (src, total, dict(rows), linked))
            ok &= total > 0
    print("DAY4_ACCEPT_%s" % ("OK" if ok else "FAIL"))
    return ok


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="85号 榜单采集器(dataeye/kuaishou/duanjubaike/rebang_baidu/djtj7/djgc/sdc)")
    ap.add_argument("--source",
                    choices=["dataeye", "kuaishou", "duanjubaike", "rebang_baidu",
                             "djtj7", "djgc", "sdc", "sdc_global", "sdc_ai",
                             "sdc_actors"],
                    required=True)
    ap.add_argument("--url", help="文章页 URL")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args()

    if not (args.once or args.verify):
        ap.print_help()
        raise SystemExit(2)
    if args.once:
        if not args.url:
            raise SystemExit("--once 需 --url")
        # 叙事文章页用纯文本; 结构化列表页(聚合榜)用原始 HTML
        if args.source in ("duanjubaike", "rebang_baidu", "djtj7", "djgc",
                           "sdc", "sdc_global", "sdc_ai", "sdc_actors"):
            text = fetch_html(args.url)
        else:
            text = fetch_text(args.url)
        st = persist(args.source, args.url, text)
        print("persist[%s]: items=%d snap_new=%d snap_dup=%d linked=%d unlinked=%d period=%s" % (
            args.source, st["items"], st["snap_new"], st["snap_dup"],
            st["linked"], st["unlinked"], st["period"]))
    if args.verify:
        raise SystemExit(0 if verify() else 1)
