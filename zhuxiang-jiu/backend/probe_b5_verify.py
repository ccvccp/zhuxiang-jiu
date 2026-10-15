# -*- coding: utf-8 -*-
"""批5 verify: 30 链入池状态 + contentType + 主表总量"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
from sqlalchemy import select, func
from db import get_session
from models import Drama, DramaRankSnapshot

B5 = ["谁让这个悍匪修仙的！", "山海藏墟，无眼窥天", "满院亲戚全是上古大妖",
      "全球死考：刚下刑场你让我去考试第一季", "全员恶仙：我有一座种仙观第一季",
      "带少爷回家后他慌了", "丈母娘百万招婿，我的冰山老婆", "那村那人那傻瓜之好事花生",
      "师尊明明无敌为何怂如蝼蚁", "菊花满山开，将错就错", "末日漂流：从小木筏开始无限升级",
      "八元猪脚饭还送饮料", "老祖宗的情债，压不住了", "我住帐篷那天，整个豪宅区慌了",
      "大力末世降临，小尸尸我呀吸吸吸", "汪氏小厨开餐馆还债，结果全网抢着吃",
      "曲曲爆火，百万粉丝却求我别写了", "诡异们快跑，那个男人出村了", "神豪外挂第一季",
      "垫底漫画家：靠考古一战成名", "兽语饲养员：我家不养闲宠", "29次返岗失败",
      "从陪衬到主场", "大师未满", "爸妈偏心，我选择入赘", "重生归来我直接封死地下室！",
      "带着闺女进深山", "青梅退场竹马慌了", "老公破产，花瓶夫人的身份瞒不住了",
      "珩珩漫漫寻恩路"]

with get_session() as s:
    total = s.execute(select(func.count(Drama.drama_id))).scalar()
    print(f"主表总量: {total}")
    rows = s.execute(select(Drama).where(
        Drama.drama_name.in_(B5))).scalars().all()
    got = {r.drama_name: r for r in rows}
    print(f"批5 30 链主表命中: {len(got)}")
    for name in B5:
        r = got.get(name)
        if r:
            print(f"  #{r.drama_id} {name} ep={r.episodes} ai={r.is_ai_drama}")
        else:
            fz = [n for n in got if name[:4] in n]
            print(f"  -- 不在: {name}" + (f" (近似: {fz})" if fz else ""))
    # 今日快照 contentType 分布
    snaps = s.execute(select(DramaRankSnapshot).where(
        DramaRankSnapshot.source == "share_pool",
        DramaRankSnapshot.snapshot_time >= "2026-10-15")).scalars().all()
    from collections import Counter
    ct = Counter()
    for sn in snaps:
        rj = sn.raw_json if isinstance(sn.raw_json, dict) else {}
        ct[(rj.get("contract") or {}).get("contentType", "?")] += 1
    print(f"\nshare_pool 近两日快照 {len(snaps)} 条 contentType: {dict(ct)}")
