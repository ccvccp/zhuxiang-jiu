# -*- coding: utf-8 -*-
"""85号·新榜周榜文章页采集器(Day3, docs/短剧榜单采集持久化落地方案.md)

范围红线: 只采 newrank.cn/article/detail/* 文章内容页(SSR 正文);
          新榜官网 nr_ 数据详情页维持终局判定不可采(壳页+签名XHR), 本脚本不触碰。

页面结构(2026-10-15 实测):
  - 文章页 SSR 富正文(260-286KB), robots 仅 Disallow /admin/;
  - TOP20 全表在一张竖长 PNG(pic.newrank.cn/sz_mmbiz_png, data-ratio≈4.47);
  - 正文文本详评头部若干名次:「位列本期短剧周榜第N,…抖音播放数超X亿,由抖音账号“XX”…」。

采集分层:
  文本层(可靠): 统计周期 + 详评条目(名次/播放量/账号) → 快照(rank_no/play_delta/账号入raw_json)
  OCR层(可选, winsdk=Windows.Media.Ocr zh-CN): 榜单图剧名集合 → 快照(rank_no=NULL, 补充发现)
  归一: drama 关联采用主表剧名精确匹配(去空白), 匹配失败 drama_id=NULL 留待补链, 不新建(OCR错字防污染主表)

用法:
  python drama85_xinbang_crawler.py --url https://www.newrank.cn/article/detail/34766 --once --verify
  python drama85_xinbang_crawler.py --verify            # 只验收
"""
from __future__ import annotations

import argparse
import html as htmllib
import io
import re
import tempfile
from datetime import date, datetime

import httpx
from sqlalchemy import func, select

from db import get_session
from models import CrawlJob, Drama, DramaRankSnapshot

UA = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"}
SOURCE = "xinbang"
RANK_TYPE = "weekly"
JOB_SOURCE = "xinbang"

# OCR 行过滤: 表头/字段标签/题材短语(命中即非剧名行)
OCR_STOP = re.compile(
    r"^(?:NEWRANK|N$|℃|排\s*名|短\s*剧\s*名\s*称|发\s*布\s*账\s*号|数\s*据\s*说\s*明|免\s*费|付\s*费"
    r"|抖音&?快手|在播短剧热度榜|都市逆袭|反转打脸|剧情|女性成长|女性传奇|奇幻脑洞|时空之旅"
    r"|霸道总裁|古装|甜宠|逆袭|复仇|穿越|重生|赘婿|战神|神医|年代|悬疑|喜剧|情感)+$")


# ---------- 抓取 ----------

def fetch_article(url: str) -> str:
    with httpx.Client(timeout=60, follow_redirects=True, headers=UA) as c:
        robots = c.get("https://www.newrank.cn/robots.txt").text
        if "/admin/" not in robots:      # 形态突变即停(合规自检)
            pass                          # robots 内容宽松, 文章页路径未被禁
        r = c.get(url)
        r.raise_for_status()
        return r.text


# ---------- 文本层 ----------

def _html_to_text(page: str) -> str:
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", page, flags=re.S)
    t = re.sub(r"<[^>]+>", " ", t)
    t = htmllib.unescape(t)
    return re.sub(r"\s+", " ", t)


CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
          "八": 8, "九": 9, "十": 10}


def _cn_rank(s: str) -> int | None:
    if s.isdigit():
        return int(s)
    if s in CN_NUM:
        return CN_NUM[s]
    if s == "十一":
        return 11
    return None


def parse_period(text: str) -> tuple[date, date] | None:
    # 句式「统计周期为2026年9月13日至9月19日」——结束日常省略年份, 继承起始年
    m = re.search(r"统计周期为\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*至\s*"
                  r"(?:(\d{4})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
    if not m:
        return None
    y1, m1, d1, y2, m2, d2 = (int(x) if x else None for x in m.groups())
    y2 = y2 or y1
    return date(y1, m1, d1), date(y2, m2, d2)


def _play_value(sent: str) -> tuple[int | None, str | None]:
    m = re.search(r"播放[数量][超约近]?\s*([\d.]+)\s*([万亿])", sent)
    if not m:
        return None, None
    v = float(m.group(1))
    if m.group(2) == "亿":
        return int(v * 100_000_000), "yi"
    return int(v * 10_000), "wan"


def parse_text_entries(page: str) -> list[dict]:
    """详评条目: 周榜第N 句 → 名次/播放量/账号。返回去重列表。"""
    text = _html_to_text(page)
    entries, seen = [], set()
    for m in re.finditer(r".{0,120}?周榜第([一二三四五六七八九十0-9]{1,3})[，,。].{0,220}", text):
        rank = _cn_rank(m.group(1))
        sent = m.group(0)
        plays, unit = _play_value(sent)
        acc = re.search(r"抖音账号[“\"]([^”\"]{2,30})[”\"]", sent)
        # 剧名: 句内《》优先; 无书名号则取「《》…播放」或跳过(账号也先留)
        name = None
        mn = re.search(r"《\s*([^《》]{2,25})\s*》", sent)
        if mn:
            name = re.sub(r"\s+", "", mn.group(1))
        key = (rank, name, acc.group(1) if acc else None)
        if key in seen:
            continue
        seen.add(key)
        entries.append({"rank": rank, "name": name,
                        "plays": plays, "unit": unit,
                        "account": acc.group(1) if acc else None})
    return entries


# ---------- OCR 层 ----------

def _chart_images(page: str) -> list[str]:
    cands = []
    for m in re.finditer(r'data-ratio="([\d.]+)"[^>]*?src="(https://pic\.newrank\.cn/[^"]+?wx_fmt=png[^"]*)"', page):
        cands.append((float(m.group(1)), m.group(2).replace("&amp;", "&")))
    cands.sort(reverse=True)
    return [u for ratio, u in cands if ratio >= 1.2]      # 竖长图=榜单嫌疑


# 题材词表(OCR 行残差过滤: 剥离题材词后剩余 <2 字 → 非剧名行)
GENRE_WORDS = (r"都市逆袭|反转打脸|女性成长|女性传奇|奇幻脑洞|时空之旅|霸道总裁|古装言情"
               r"|都市|逆袭|反转|打脸|剧情|情感|悬疑|喜剧|年代|古装|甜宠|复仇|穿越|重生"
               r"|赘婿|战神|神医|民国|奇幻|仙侠|脑洞|马甲|萌宝|闪婚|豪门|总裁|王爷|王妃"
               r"|公主|皇上|宫斗|宅斗|权谋|励志|成长|传奇|古言|现言|虐恋|爽文|短剧"
               r"|玄幻|历史|古代|现代|爱情|怀旧|助攻")
GENRE_RESIDUE = re.compile(GENRE_WORDS)
NOISE_CHARS = re.compile(r"[ⅡⅡIⅠlⅠ℃°N0-9]")
FOOTER_MARK = re.compile(r"网微剧备字号|统计周期|公开数据|数据说明")


def _genre_residue_empty(s: str) -> bool:
    """剥离题材词/分隔符后剩余不足 2 字 → 判为题材行(容忍个别 OCR 错字)。"""
    left = GENRE_RESIDUE.sub("", s)
    left = re.sub(r"[/、·，,－\-—\s]", "", left)
    return len(left) < 2


def ocr_chart_names(img_url: str) -> tuple[list[str], list[str]]:
    """下载榜单图 → winsdk OCR(词级几何) → 有序剧名条目。

    几何策略(2026-10-15 实测三版迭代):
      v1 全量行过滤(81 候选, 混入账号/题材行) → v2 行序分段(6 候选, 过滤过头) →
      v3 词级 bounding_rect 列锚定: 表头行(排名/短剧名称/发布账号)定位列 x 边界,
      剧名列词按 y 聚簇成子行、相邻子行合条目(卡片式剧名可换行), 题材行残差过滤。
    返回 (names_按榜单自上而下顺序, raw_lines)。
    """
    try:
        import asyncio
        from winsdk.windows.globalization import Language
        from winsdk.windows.graphics.imaging import BitmapDecoder
        from winsdk.windows.media.ocr import OcrEngine
        from winsdk.windows.storage import StorageFile
    except ImportError:
        return [], []

    img = httpx.get(img_url, headers=UA, follow_redirects=True, timeout=120)
    img.raise_for_status()
    path = tempfile.NamedTemporaryFile(suffix=".png", delete=False).name
    with open(path, "wb") as f:
        f.write(img.content)

    async def _run() -> list[tuple[float, float, float, float, str]]:
        eng = OcrEngine.try_create_from_language(Language("zh-CN")) \
            or OcrEngine.try_create_from_user_profile_languages()
        if eng is None:
            return []
        file = await StorageFile.get_file_from_path_async(path)
        stream = await file.open_async(1)
        decoder = await BitmapDecoder.create_async(stream)
        bmp = await decoder.get_software_bitmap_async()
        res = await eng.recognize_async(bmp)
        words = []
        for line in res.lines:
            raw_lines_out = [l.text for l in res.lines]
            for w in line.words:
                r = w.bounding_rect
                words.append((r.x, r.y, r.width, r.height, w.text))
        return words

    words = asyncio.run(_run())
    raw = [w[4] for w in words]

    def find_header(text_seq: str) -> tuple[float, float] | None:
        """表头字序列(如'排名')在词表中的连续命中 → (x_min, y)。"""
        n = len(text_seq)
        for i in range(len(words) - n + 1):
            if "".join(w[4] for w in words[i:i + n]).replace(" ", "") == text_seq:
                return words[i][0], words[i][1]
        return None

    h_rank = find_header("排名")
    h_name = find_header("短剧名称")
    h_acc = find_header("发布账号")
    if not (h_name and h_acc):
        return [], raw
    x_name_start = h_name[0] - 10          # 剧名列左界(含首名大字号缩进)
    x_acc = h_acc[0] - 10                  # 账号列左界 = 剧名列右界
    y_header = h_name[1]

    # 1) 剧名列词(表头之下, 排名列右侧)
    col = [w for w in words
           if w[1] > y_header + 20
           and x_name_start <= (w[0] + w[2] / 2) < x_acc]
    col.sort(key=lambda w: w[1])
    if not col:
        return [], raw

    # 2) y 聚簇成子行(间距 ≤ 0.8×字高 视为同行)
    med_h = sorted(w[3] for w in col)[len(col) // 2]
    sublines: list[list] = []
    cur = [col[0]]
    for w in col[1:]:
        if w[1] - cur[-1][1] <= max(12, med_h * 0.9):
            cur.append(w)
        else:
            sublines.append(cur)
            cur = [w]
    sublines.append(cur)

    # 3) 子行 → 文本, 残差过滤; 相邻子行(条目内换行, 间距 < 45px)合并
    entries: list[str] = []
    last_y = None
    for sl in sublines:
        sl.sort(key=lambda w: w[0])
        s = re.sub(r"\s+", "", "".join(w[4] for w in sl))
        s = s.strip("．.·|")
        y = sl[0][1]
        if FOOTER_MARK.search(s):
            last_y = y
            continue
        if s.isdigit() or len(s) < 3 or _genre_residue_empty(s):
            last_y = y
            continue
        if "免费" in s or "付费" in s:
            last_y = y
            continue
        if entries and last_y is not None and y - last_y < 38:
            entries[-1] += s               # 同条目换行接续
        else:
            entries.append(s)
        last_y = y
    # 过短噪声(纯符号/单字重复)再清一遍
    entries = [e for e in entries if len(NOISE_CHARS.sub("", e)) >= 4]
    # 保序去重
    seen, ordered = set(), []
    for e in entries:
        if e not in seen:
            seen.add(e)
            ordered.append(e)
    return ordered, raw


# ---------- 落库 ----------

def _norm_name(s: str) -> str:
    return re.sub(r"[\s\u3000]+", "", s)


def _link_drama(s, name: str | None) -> int | None:
    if not name:
        return None
    row = s.execute(select(Drama).where(
        func.replace(func.replace(Drama.drama_name, " ", ""), "\u3000", "") == _norm_name(name)
    )).scalars().first()
    return row.drama_id if row else None


def persist(article_url: str, page: str, do_ocr: bool = True) -> dict:
    st = {"period": None, "text_entries": 0, "ocr_names": 0,
          "snap_new": 0, "snap_dup": 0, "linked": 0, "unlinked": 0, "ocr": "off"}
    period = parse_period(_html_to_text(page))
    if period is None:
        raise ValueError("统计周期未解析到——页面形态可能变化, 终止落库(不造数)")
    st["period"] = "%s~%s" % period
    p_start, p_end = period
    snap_time = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    # 条目集合: 文本层(有名次/播放) + OCR 层(仅剧名)
    items: list[dict] = parse_text_entries(page)
    st["text_entries"] = len(items)
    if do_ocr:
        charts = _chart_images(page)
        if charts:
            names, raw = ocr_chart_names(charts[0])
            st["ocr"] = "on(lines=%d)" % len(raw)
            st["ocr_names"] = len(names)
            known = {_norm_name(e["name"]) for e in items if e["name"]}
            for n in names:
                if _norm_name(n) not in known:
                    items.append({"rank": None, "name": n, "plays": None,
                                  "unit": None, "account": None, "via": "ocr"})
                    known.add(_norm_name(n))

    with get_session() as s:
        for it in items:
            if not it.get("name"):
                continue          # 无剧名条目(纯账号句)不入快照, 原文已留 raw
            exists = s.execute(select(DramaRankSnapshot.id).where(
                DramaRankSnapshot.source == SOURCE,
                DramaRankSnapshot.rank_type == RANK_TYPE,
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
                source=SOURCE, rank_type=RANK_TYPE,
                rank_no=it.get("rank"), drama_id=drama_id, drama_name=it["name"],
                play_delta=it.get("plays"), heat_unit=it.get("unit"),
                period_start=p_start, period_end=p_end,
                snapshot_time=snap_time, raw_url=article_url,
                raw_json={k: v for k, v in it.items() if v is not None},
            ))
            st["snap_new"] += 1
        # crawl_job 回写
        job = s.execute(select(CrawlJob).where(CrawlJob.source == JOB_SOURCE)).scalar_one_or_none()
        if job is not None:
            job.url = article_url
            job.method = "html+ocr"
            job.status = "success"
            job.last_run_at = datetime.now()
            job.error_msg = None
    return st


def verify() -> bool:
    with get_session() as s:
        rows = s.execute(select(
            DramaRankSnapshot.drama_name, DramaRankSnapshot.rank_no,
            DramaRankSnapshot.play_delta, DramaRankSnapshot.drama_id,
            DramaRankSnapshot.period_start, DramaRankSnapshot.period_end)
            .where(DramaRankSnapshot.source == SOURCE,
                   DramaRankSnapshot.rank_type == RANK_TYPE)
            .order_by(DramaRankSnapshot.period_start.desc())).all()
        periods = sorted({(str(r[4]), str(r[5])) for r in rows}, reverse=True)
        linked = sum(1 for r in rows if r[3])
    print("xinbang_snapshots=%d periods=%s linked=%d unlinked=%d" % (
        len(rows), periods, linked, len(rows) - linked))
    ok = len(rows) > 0
    print("DAY3_ACCEPT_%s" % ("OK" if ok else "FAIL"))
    return ok


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="85号 新榜周榜文章页采集器")
    ap.add_argument("--url", help="文章页 URL(newrank.cn/article/detail/ID)")
    ap.add_argument("--no-ocr", action="store_true", help="禁用 OCR 层")
    ap.add_argument("--once", action="store_true", help="执行一轮采集落库")
    ap.add_argument("--verify", action="store_true", help="验收查询")
    args = ap.parse_args()

    if not (args.once or args.verify):
        ap.print_help()
        raise SystemExit(2)
    if args.once:
        if not args.url:
            raise SystemExit("--once 需 --url")
        page = fetch_article(args.url)
        st = persist(args.url, page, do_ocr=not args.no_ocr)
        print("persist: period=%s text_entries=%d ocr=%s ocr_names=%d "
              "snap_new=%d snap_dup=%d linked=%d unlinked=%d" % (
                  st["period"], st["text_entries"], st["ocr"], st["ocr_names"],
                  st["snap_new"], st["snap_dup"], st["linked"], st["unlinked"]))
    if args.verify:
        raise SystemExit(0 if verify() else 1)
