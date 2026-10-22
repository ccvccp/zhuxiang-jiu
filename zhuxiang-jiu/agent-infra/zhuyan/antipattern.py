"""zhuyan antipattern: 反模式规则库 Fast Filter（81号·T2.3）

前沿五类反模式裁剪为静态正则快筛(块级近似——Fast Filter 定位=快筛
供人工精审, 非精判; 命中如实标注):
- AP-1 过度缓存: cache_set/redis.set 单参调用(无 TTL/失效参数)
- AP-2 异步误用: async def 同文件内同步阻塞(time.sleep/requests.*)
- AP-3 批量陷阱: def 块内 for 循环+单条 DB 调用(execute/fetch*)
- AP-4 分布式滥用: def 块内 for 循环+锁获取(acquire/setnx)——循环内频繁
  获取释放锁(单机规模滥用分布式协调)
- AP-5 日志风暴: def 块内 for 循环+高频日志(logger.debug/info/print)
B4 验收: 五类已知触发例拦截 5/5=100%(检不出即不合格)。
"""
import os
import re

try:
    from zhuyan import fingerprint
except ImportError:
    import sys
    _pkg = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _pkg not in sys.path:
        sys.path.insert(0, _pkg)
    from zhuyan import fingerprint

# 行级规则
_LINE_RULES = [
    ("AP-1 过度缓存",
     re.compile(r"\b(?:cache_set|cache\.set|redis\w*\.set)\(\s*[\"'][^\"']*[\"']\s*,[^,)]+\)\s*$"),
     "缓存写入未携带 TTL/失效参数——缓存可能永不过期(建议补 TTL 与主动失效)"),
    ("AP-2 异步误用",
     re.compile(r"\btime\.sleep\(|\brequests\.(?:get|post|put)\("),
     "疑似 async 上下文同步阻塞调用(建议 asyncio.sleep/aiohttp——快筛口径: 同文件命中即报)"),
]
# 块级规则: def 块内同时含 for 循环与危险调用
_BLOCK_RULES = [
    ("AP-3 批量陷阱", re.compile(r"\b(?:execute|executemany|fetchone|fetchall)\("),
     "循环内单条 DB 调用(建议改批量 executemany/IN 查询)"),
    ("AP-4 分布式滥用", re.compile(r"\.acquire\(|\bsetnx\(|\bredis\w*\.lock\("),
     "循环内锁获取——单机规模滥用分布式协调(建议锁提出循环外)"),
    ("AP-5 日志风暴", re.compile(r"\b(?:logger|logging|print)\s*[.(]\s*(?:debug|info|log)?\(?"),
     "循环内高频日志(建议聚合一条或降采样)"),
]

# AP-2 文件级门槛: 仅当文件含 async def 才触发
_ASYNC_RE = re.compile(r"\basync\s+def\s")
_DEF_RE = re.compile(r"^\s*(?:async\s+)?def\s+\w+", re.MULTILINE)
_FOR_RE = re.compile(r"^\s*(?:for|while)\s", re.MULTILINE)


def _def_blocks(text):
    blocks, cur = [], None
    for i, ln in enumerate(text.splitlines(), 1):
        if _DEF_RE.match(ln):
            if cur:
                blocks.append(cur)
            cur = (i, [ln])
        elif cur is not None:
            cur[1].append(ln)
    if cur:
        blocks.append(cur)
    return [(b[0], "\n".join(b[1])) for b in blocks]


def scan_text(unit_id, text):
    """单文本快筛 → [(rule, line_no, detail)](去重: 每规则首处取证)。"""
    hits = []
    seen = set()
    for name, pattern, advice in _LINE_RULES:
        if name == "AP-2 异步误用" and not _ASYNC_RE.search(text):
            continue  # 无 async 上下文不触发
        for m in pattern.finditer(text):
            ln = text.count("\n", 0, m.start()) + 1
            if name not in seen:
                hits.append((name, ln, advice))
                seen.add(name)
            break
    for name, pattern, advice in _BLOCK_RULES:
        if name in seen:
            continue
        for start, block in _def_blocks(text):
            if _FOR_RE.search(block) and pattern.search(block):
                hits.append((name, start, advice))
                seen.add(name)
                break
    return hits


def scan_antipatterns(units=None, src_ctx=None):
    """全量评分单元源文件快筛 → [(unit_id, rule, line_no, detail)]。"""
    units = units if units is not None else fingerprint.enumerate_units()
    src_ctx = src_ctx if src_ctx is not None else {}
    out = []
    for u in units:
        if u["unit_type"] == "page":
            continue
        text = src_ctx.get(u.get("file"))
        if not text:
            continue
        for name, ln, advice in scan_text(u["unit_id"], text):
            out.append((u["unit_id"], name, ln, advice))
    return out


# B4 已知触发例(五类各一+干净对照)
B4_TRIGGERS = {
    "AP-1 过度缓存": "def f():\n    cache_set('k', data)\n",
    "AP-2 异步误用": "async def g():\n    time.sleep(2)\n",
    "AP-3 批量陷阱": "def h(ids):\n    for i in ids:\n        db.execute(sql % i)\n",
    "AP-4 分布式滥用": "def w(items):\n    for i in items:\n        lock.acquire()\n",
    "AP-5 日志风暴": "def v(xs):\n    for x in xs:\n        logger.info(x)\n",
}
B4_CLEAN = "def ok(ids):\n    rows = db.executemany(sql, ids)\n    logger.info('done %d', len(ids))\n"


def b4_replay():
    """B4 基准: 五类已知触发例拦截 → (命中数, 明细)。5/5=100% PASS。"""
    hits = []
    for rule, text in B4_TRIGGERS.items():
        got = [h[0] for h in scan_text("b4-sample", text)]
        (hits.append((rule, True, got)) if rule in got
         else hits.append((rule, False, got)))
    clean_hits = scan_text("b4-clean", B4_CLEAN)
    return hits, clean_hits


def run(args=None):
    """zhuyan antipattern: 真实代码全量快筛+B4 回放。"""
    hits, clean_hits = b4_replay()
    ok_n = sum(1 for _r, ok, _g in hits if ok)
    print(f"[zhuyan antipattern] B4 拦截回放: {ok_n}/{len(hits)}"
          f"{'=100% PASS' if ok_n == len(hits) else ' FAIL(检不出即不合格)'}")
    for rule, ok, got in hits:
        print(f"  {'✓' if ok else '✗'} {rule} → 实际命中 {got}")
    print(f"  对照: 干净样本命中 {len(clean_hits)} 处(误报 {'0=合格' if not clean_hits else clean_hits})")
    units = fingerprint.enumerate_units()
    src_ctx = {}
    for u in units:
        rel = u.get("file")
        if u["unit_type"] != "page" and rel and rel not in src_ctx:
            full = os.path.join(fingerprint.REPO_ROOT, rel.replace("/", os.sep))
            if os.path.exists(full):
                with open(full, encoding="utf-8", errors="replace") as fh:
                    src_ctx[rel] = fh.read()
            else:
                src_ctx[rel] = ""
    found = scan_antipatterns(units, src_ctx)
    print(f"[zhuyan antipattern] 真实代码快筛: {len(units)} 单元"
          f" → 命中 {len(found)} 处(Fast Filter 快筛口径, 供人工精审)")
    for uid, rule, ln, advice in found[:8]:
        print(f"  - {uid} —— {rule}(行 {ln}): {advice}")
    if found:
        print("[zhuyan antipattern] 命中为快筛疑似, 只出不施——人工精审后再定")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
