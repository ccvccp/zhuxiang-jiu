# -*- coding: utf-8 -*-
"""批5投喂 30 链探针: 漫剧剧名对照 + 形态 + 池内/聚合榜联动预判"""
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
    ("VNqwTNR5Br0", "谁让这个悍匪修仙的！"),
    ("sM6wBaxyDuQ", "山海藏墟，无眼窥天"),
    ("VCWlJ90zwtY", "满院亲戚全是上古大妖"),
    ("07PvIPkedrI", "全球死考：刚下刑场你让我去考试第一季"),
    ("o4Lxjo0-E8I", "全员恶仙：我有一座种仙观第一季"),
    ("czpF_TlBi1U", "带少爷回家后他慌了"),
    ("F4HVoC3rD_0", "丈母娘百万招婿，我的冰山老婆"),
    ("RXlMAb_hfZg", "那村那人那傻瓜之好事花生"),
    ("A5O5b-173Rc", "师尊明明无敌为何怂如蝼蚁"),
    ("4hJw6bKtPWY", "菊花满山开，将错就错"),
    ("-6HNExWpC3w", "末日漂流：从小木筏开始无限升级"),
    ("ai-HSo9XgGc", "八元猪脚饭还送饮料"),
    ("JPKwJ8U37MU", "老祖宗的情债，压不住了"),
    ("kpvyEDUOA8A", "我住帐篷那天，整个豪宅区慌了"),
    ("TgPu06HZ2vw", "大力末世降临，小尸尸我呀吸吸吸"),
    ("Nh89IJY5DM8", "汪氏小厨开餐馆还债，结果全网抢着吃"),
    ("KdkJISXJXFc", "曲曲爆火，百万粉丝却求我别写了"),
    ("2UyzkmTHCgo", "诡异们快跑，那个男人出村了"),
    ("Ly-mATadcG4", "神豪外挂第一季"),
    ("ZbayjhthPTM", "垫底漫画家：靠考古一战成名"),
    ("0nLusoYNM3o", "兽语饲养员：我家不养闲宠"),
    ("1vAo_UGHKPI", "29次返岗失败"),
    ("6L5mBkl4GZE", "从陪衬到主场"),
    ("u8eSTK5opFg", "大师未满"),
    ("K8WiUB57eF8", "爸妈偏心，我选择入赘"),
    ("5MY4hYrZ_7k", "重生归来我直接封死地下室！"),
    ("Vmb7LViyYSs", "带着闺女进深山"),
    ("HGF66zS9OYw", "青梅退场竹马慌了"),
    ("g5d0McpSZfQ", "老公破产，花瓶夫人的身份瞒不住了"),
    ("0WqJ__ai1zc", "珩珩漫漫寻恩路"),
]
FN = re.compile(r'data-fn-args="([^"]+)"')

with get_session() as s:
    pool = set(s.execute(select(Drama.drama_name)).scalars())
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
        text = ""
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
