# -*- coding: utf-8 -*-
"""批4投喂 23 链探针: 剧名对照 + 形态 + 池内/聚合榜联动预判"""
import html as _h
import io
import json
import re
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
import httpx
from sqlalchemy import select
from db import get_session
from models import Drama, DramaRankSnapshot

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")
LINKS = [
    ("Lc8f3QmOLck", "偏要偷尝半口甜"),
    ("F3wuXSXhkAI", "替白月光撕开那张脸"),
    ("KHYyS33jHyk", "老婆出国旅游不带我，我笑了"),
    ("7WYGNsrX5iU", "奔头儿"),
    ("MASlQMJgr1s", "妹妹博士毕业宴上，我成了外人"),
    ("XALDsqfdaG0", "保姆真千金，心声漏了只想跑"),
    ("vOHSO5avv34", "离婚当天，董事长母亲找到我"),
    ("b_1QWGYwF3k", "全家都盼弟弟成龙，他偏托我成凤"),
    ("uLhqqIuiSMU", "嫁进沈家第一天，我把规矩掀了"),
    ("dX0H-7dNZis", "给流浪汉带了份盒饭，他让我明天别上班"),
    ("KqoL4YLXtnE", "天降俏弟媳，大姑子们追着宠"),
    ("WnjuSEzkno4", "年薪八百万，我妈以为我月薪三千八"),
    ("Cn7w-aSVjTk", "盛律师的离婚前规则"),
    ("7SjT_fA9vqE", "准儿媳她不对劲"),
    ("JlsCfwlVDdw", "继承者游戏：双鱼对局"),
    ("7ogmb2hXwSU", "东北老太嫁台北，两手叉腰爱谁谁"),
    ("-pxiTNEQTCk", "合租凶宅"),
    ("XBdSrczJGCc", "假千金的优秀全面曝光了"),
    ("EoqmVfEL73Y", "开局百年内力，我带师妹横扫武林"),
    ("5Hlc9KYdl6U", "半生托举，换来七分答卷"),
    ("odm0ruLOnus", "落魄书生：开局迎娶俏佳人"),
    ("TcqWZmb0OhM", "退休老爸不好惹"),
    ("b-zVBsGWtK8", "一卦显圣1：得到道家传承后我终于发达了"),
]
FN = re.compile(r'data-fn-args="([^"]+)"')

with get_session() as s:
    pool = set(s.execute(select(Drama.drama_name)).scalars())
    # 快照层(聚合榜观测): 剧名 -> 源
    snap = {}
    for nm, src in s.execute(select(DramaRankSnapshot.drama_name,
                                    DramaRankSnapshot.source)).all():
        snap.setdefault(nm, set()).add(src)
print(f"主表 {len(pool)} 部 / 快照剧名 {len(snap)} 个")

env = open(".env.example", encoding="utf-8").read()
existing = re.search(r"HONGGUO_SHARE_URLS=(.*)", env).group(1)

ok, new_codes = 0, []
with httpx.Client(timeout=20.0, follow_redirects=True,
                  headers={"User-Agent": UA}) as c:
    for code, claim in LINKS:
        if f"/s/{code}/" in existing or f"/s/{code}" in existing:
            print(f"[{code:>14}] 跳过: 码已在池")
            continue
        url = f"https://novelquickapp.com/s/{code}/"
        for attempt in range(2):
            time.sleep(1.0)
            try:
                r = c.get(url)
                text = r.text
                break
            except Exception:  # noqa: BLE001
                text = ""
        if not text:
            print(f"[{code:>14}] 抓取失败(重试后)")
            continue
        ttl = re.search(r"<title>(.*?)</title>", text, re.S)
        title = ttl.group(1).strip() if ttl else ""
        m_name = re.search(r"《([^》]+)》", title)
        name = m_name.group(1) if m_name else title[:30]
        kind = ""
        for raw in FN.findall(text):
            try:
                arr = json.loads(_h.unescape(raw))
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(arr, list) and arr:
                kind = ("manju" if "animation" in str(arr[0])
                        else "duanju" if "video-list" in str(arr[0])
                        else str(arr[0])[:18])
                break
        in_pool = "主表✓" if name in pool else "主表-"
        in_snap = "榜✓" + ",".join(sorted(snap.get(name, []))) if name in snap else "榜-"
        claim_ok = "✓" if claim in name or name in claim else "✗"
        fuzzy = [n for n in snap if n != name and (claim in n or n in claim)
                 and abs(len(n) - len(claim)) <= 6 and n not in (name,)]
        fz = f" 模糊:{fuzzy[:2]}" if fuzzy else ""
        new_codes.append(code)
        ok += 1
        print(f"[{code:>14}] {kind:<7} {name} | 文案{claim_ok} {in_pool} {in_snap}{fz}")

print(f"\n探针 {ok}/{len(LINKS)} | 全新码 {len(new_codes)} 个")
