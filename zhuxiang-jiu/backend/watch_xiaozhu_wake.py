"""48号 小竹·唤醒分值观察脚本(watch)——P3-1 判定数据源

用法(10-08 观察期节点与三项联合检查同批跑; 平时随时可跑):
    Get-Content .\\watch_xiaozhu_wake.py -Raw |
        ssh root@47.236.61.117 "docker exec -i zhuxiang-backend-1 python -"

背景(P3 计划 §一; 10-01 联合检查发现):
    turns 键空间混型——每轮一个 hash
    (voice48:voice48_turns:{sessionId}:{seq})与会话级
    string 发号器键(voice48:turns:{sessionId}:seq)并存,
    旧扫描器 keys(turns:*)+lrange 对 string 键报 WRONGTYPE
    (且轮次本身是 hash 不是 list, lrange 双重错型)。
    本脚本即修复后的扫描器:
    - 双族精确 pattern(repo 表族常量构造, 不手拼 key)
    - endswith(":seq") 跳过 + type() 分派双保险——
      非hash键计数跳过, 零 WRONGTYPE 由构造保证
    - hgetall 逐轮 + repo._deserialize 同口径还原

输出四节(W1-W4) + 观察记录行(复制入 P3 计划留痕):
    W1 扫描健康   键空间规模/类型分派画像
    W2 分值覆盖   持久化 wakeScore 字段占比(新轮)/
                  历史轮 wake_score(rawText) 确定性回放回填
    W3 分布画像   not_woken 残分分布(0-0.3/0.3-0.55/
                  0.55-0.74 段)/wakeup 纯唤醒变体画像
    W4 结论建议   P3-1 判定矩阵三分支自动结论

判据(P3 计划 §一判定矩阵, 同口径):
    not_woken 样本 <100            → 继续积累(流量低常态)
    ≥100 且 0.55-0.74 段集中        → 触发优化方案
                  (话术分层/u韵画像回看/阈值微调评估)
    ≥100 但残分全落 0.0-0.3        → 证伪关闭(转 VAD 门槛)
幂等可重复执行; 纯只读监控, 不改任何状态。
"""

import asyncio
from collections import Counter
from datetime import UTC, datetime


def _variant_of(text: str, variants) -> str:
    """唤醒变体画像(表内变体按长度降序先长后短防截胡)"""
    for w in variants:
        if w in text:
            return w
    return "其他/无"


async def main():
    from repositories.backend import (
        is_redis_mode, get_redis_client, _k,
    )
    from repositories.xiaozhu_repository import (
        Xiaozhu48Repository,
    )
    from services.xiaozhu_service import (
        wake_score, WAKE_SCORE_TABLE,
    )

    print("=" * 60)
    print("48号 小竹·唤醒分值观察报告(P3-1 数据源)  ",
          datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"))
    print("=" * 60)

    repo = Xiaozhu48Repository()
    turns = []
    type_counts = Counter()
    n_keys = 0
    n_seq = 0

    if is_redis_mode():
        client = await get_redis_client()
        # 双族精确 pattern(repo 表族常量——同源铁律):
        #   voice48:voice48_turns:{sid}:{seq}  轮次 hash
        #   voice48:turns:{sid}:seq            发号器 string
        key_space = []
        for pat in (_k("voice48", repo.TABLE_TURNS, "*"),
                    _k("voice48", "turns", "*")):
            async for k in client.scan_iter(match=pat,
                                            count=500):
                key_space.append(k)
        n_keys = len(key_space)
        # 双保险①: 发号器键显式跳过(即旧扫描器 lrange 报
        # WRONGTYPE 的混型键——W1 计数留痕)
        seq_keys = [k for k in key_space
                   if k.endswith(":seq")]
        n_seq = len(seq_keys)
        cand = [k for k in key_space
                if not k.endswith(":seq")]
        # 双保险②: type() 分派——仅 hash 进 hgetall,
        # 其余(非轮次键)计数跳过, 不再 lrange 硬读
        pipe = client.pipeline(transaction=False)
        for k in cand:
            pipe.type(k)
        types = await pipe.execute()
        type_counts = Counter(types)
        hash_keys = [k for k, t in zip(cand, types)
                     if t == "hash"]
        # 轮次读取(list_turns 同款: pipeline hgetall)
        for i in range(0, len(hash_keys), 5000):
            pipe = client.pipeline(transaction=False)
            for k in hash_keys[i:i + 5000]:
                pipe.hgetall(k)
            for data in await pipe.execute():
                if data:
                    turns.append(repo._deserialize(data))
    else:
        # 本地 asyncio 模式(内存店直读——同源结构)
        repo._ensure_store()
        turns = [dict(t) for t in
                 (repo.store[repo.TABLE_TURNS] or {}).values()]
        n_keys = len(turns)

    # ---------------- W1 扫描健康 ----------------
    if is_redis_mode():
        n_other = sum(v for k, v in type_counts.items()
                      if k != "hash")
        print(f"\n[W1 扫描健康] 键空间={n_keys} "
              f"(发号器seq键跳过={n_seq}——旧扫描器"
              f"WRONGTYPE 的混型键) 类型分派={dict(type_counts)}")
        if n_other:
            print(f"    (非hash非seq键 {n_other} 个按类型分派"
                  "跳过)")
        print("    全部按类型分派读取——零 WRONGTYPE 由构造保证")
    else:
        print(f"\n[W1 扫描健康] 内存模式 turns={n_keys}")

    # ---------------- W2 分值覆盖 ----------------
    def _score(t) -> float:
        v = t.get("wakeScore")
        if v is None:   # 历史轮: 现算回放(确定性纯函数)
            try:
                v = wake_score(str(t.get("rawText") or ""))
            except Exception:
                v = 0.0
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    n = len(turns)
    persisted = sum(1 for t in turns
                    if t.get("wakeScore") is not None)
    pct = (persisted / n * 100) if n else 0.0
    print(f"\n[W2 分值覆盖] turns={n} "
          f"持久化wakeScore={persisted}({pct:.0f}%) "
          f"历史轮回放回填={n - persisted}")
    if n and pct < 100:
        print("    (历史轮无该字段属预期——本批刚补落库, "
              "回放分值与判定时刻同函数等值)")

    # ---------------- W3 分布画像 ----------------
    variants = sorted(WAKE_SCORE_TABLE,
                      key=len, reverse=True)
    not_woken = [t for t in turns
                 if t.get("intent") == "not_woken"]
    wakeup = [t for t in turns
              if t.get("intent") == "wakeup"]
    voice_nw = [t for t in not_woken
                if t.get("channel") == "voice"]

    def _bucket(s: float) -> str:
        if s < 0.3:
            return "0-0.3"
        if s < 0.55:
            return "0.3-0.55"
        if s < 0.75:
            return "0.55-0.74"
        return "0.75+"

    nw_buckets = Counter(_bucket(_score(t))
                         for t in not_woken)
    band = [t for t in not_woken
            if 0.3 <= _score(t) < 0.75]      # 残分样本
    hi = [t for t in band if _score(t) >= 0.55]
    band_var = Counter(
        _variant_of(str(t.get("rawText") or ""), variants)
        for t in band)
    wk_scores = Counter(round(_score(t), 2) for t in wakeup)
    wk_var = Counter(
        _variant_of(str(t.get("rawText") or ""), variants)
        for t in wakeup)

    print(f"\n[W3 分布画像] not_woken={len(not_woken)} "
          f"(voice={len(voice_nw)}) "
          f"wakeup纯唤醒={len(wakeup)}")
    print(f"    not_woken 残分分布: {dict(nw_buckets)}")
    if band:
        print(f"    残分样本(0.3-0.74)={len(band)} "
              f"其中0.55-0.74段={len(hi)} "
              f"变体画像={dict(band_var)}")
    if wakeup:
        print(f"    wakeup 分值分布: {dict(wk_scores)} "
              f"变体画像={dict(wk_var)}")
        pig = wk_var.get("小猪", 0)
        if pig:
            print(f"    (「小猪」纯唤醒 {pig} 条——memory 在案 "
                  "HITL 信号: 真机'喊了没下文'体验项)")

    # ---------------- W4 结论建议 ----------------
    print("\n[W4 结论建议] P3-1 判定矩阵(P3 计划 §一)")
    n_nw, n_band = len(not_woken), len(band)
    if n_nw == 0 and n == 0:
        verdict = "无轮次数据(语音入口未使用/清库后)——" \
                  "下周同批再核"
        action = "继续积累"
    elif n_band < 100:
        verdict = (f"残分样本 {n_band}<100——继续积累(流量低"
                   "属常态), 10-08 后同批再核; 参考: not_woken "
                   f"总量 {n_nw}(voice {len(voice_nw)})")
        action = "继续积累"
    elif len(hi) / n_band >= 0.4:
        verdict = (f"残分样本 {n_band} 中 0.55-0.74 段 "
                   f"{len(hi)}"
                   f"({len(hi) / n_band:.0%})集中(近似音误触"
                   "画像)——触发优化方案: ①wakeup 兜底话术按"
                   "分值分层 ②u 韵 0.55 轮画像回看决定纳入/排除 "
                   "③阈值微调评估")
        action = "触发优化方案"
    elif len(hi) == 0:
        verdict = (f"残分样本 {n_band}≥100 且 0.55-0.74 段为"
                   "零(全落 0-0.55, 环境残声/弱近似)——证伪关闭:"
                   " 非近似音误触而是噪声, 转采集层 VAD 门槛评估,"
                   " 不动意图层")
        action = "证伪关闭(转 VAD)"
    else:
        verdict = (f"残分样本 {n_band} 中 0.55-0.74 段 "
                   f"{len(hi)}({len(hi) / n_band:.0%})——"
                   "未达集中线, 继续观察细分分布")
        action = "继续观察"
    print(f"    [{action}] {verdict}")

    # ---------------- 观察记录行(复制入 P3 计划留痕) --------
    today = datetime.now(UTC).strftime("%m-%d")
    print(f"\n观察记录: | {today} | turns={n} | "
          f"not_woken={n_nw}(残分{n_band}) | "
          f"wakeup={len(wakeup)} | {dict(nw_buckets)} "
          f"| {action} |")


asyncio.run(main())
