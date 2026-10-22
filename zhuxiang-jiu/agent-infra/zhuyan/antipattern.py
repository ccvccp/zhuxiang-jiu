"""zhuyan antipattern: 反模式规则库 Fast Filter（81号·T2.3 / T3.7 规则改进 v2）

前沿五类反模式裁剪为静态快筛(块级近似——Fast Filter 定位=快筛
供人工精审, 非精判; 命中如实标注):
- AP-1 过度缓存: cache_set/redis.set 单参调用(无 TTL/失效参数)
- AP-2 异步误用: async def 同文件内同步阻塞(time.sleep/requests.*)
- AP-3 批量陷阱: 循环体内单条 DB 调用(execute/fetch*)
- AP-4 分布式滥用: 循环体内锁获取(acquire/setnx)——单机规模滥用分布式协调
- AP-5 日志风暴: 循环体内高频日志(logger.debug/info/print)
B4 验收: 五类已知触发例拦截 5/5=100%(检不出即不合格)。

[T3.7 规则改进 v2——74 处人工精审结论落地(3 误报+1 豁免归零)]:
1. def 块缩进感知: 块止于缩进回落至 def 行及以下的非空行
   (v1 按行扫描至下一 def 行——嵌套/后续代码边界过宽, 误报源)
2. 块级规则收紧: 危险调用须位于 for/while **循环体内**(缩进>循环行),
   块内任意位置共现不再触发(except 兜底/循环后聚合日志不再误报)
3. 文件级豁免通道: 源文件含 `# zhuyan-exempt: AP-5` (或 all) 标记时
   跳过对应规则——CLI 报告输出等管理面场景人工裁量豁免
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
# 块级规则: 危险调用须位于循环体内(v2 收紧)
_BLOCK_RULES = [
    ("AP-3 批量陷阱", re.compile(r"\b(?:execute|executemany|fetchone|fetchall)\("),
     "循环体内单条 DB 调用(建议改批量 executemany/IN 查询)"),
    ("AP-4 分布式滥用", re.compile(r"\.acquire\(|\bsetnx\(|\bredis\w*\.lock\("),
     "循环体内锁获取——单机规模滥用分布式协调(建议锁提出循环外)"),
    ("AP-5 日志风暴", re.compile(r"\b(?:logger|logging|print)\s*[.(]\s*(?:debug|info|log)?\(?"),
     "循环体内高频日志(建议聚合一条或降采样)"),
]

# AP-2 文件级门槛: 仅当文件含 async def 才触发
_ASYNC_RE = re.compile(r"\basync\s+def\s")
_DEF_RE = re.compile(r"^\s*(?:async\s+)?def\s+\w+")
_FOR_RE = re.compile(r"^\s*(?:for|while)\s")
# 豁免标记: `# zhuyan-exempt: AP-5` / `# zhuyan-exempt: AP-3,AP-5` / `# zhuyan-exempt: all`
_EXEMPT_RE = re.compile(r"#\s*zhuyan-exempt:\s*([A-Za-z0-9, \-]+)")


def _indent_of(line):
    """行缩进列数(空行→None)。"""
    if not line.strip():
        return None
    return len(line) - len(line.lstrip())


def _def_blocks(text):
    """缩进感知 def 块 → [(start_line, [(line_no, indent, line), ...])]。

    块= def 行起, 至缩进回落至 def 行及以下的非空行(或文件尾)结束——
    v2 改进: v1 按行扫描至下一 def 行, 后续同级代码被并入块(边界过宽)。
    """
    blocks, cur, base = [], None, 0
    for i, ln in enumerate(text.splitlines(), 1):
        ind = _indent_of(ln)
        if ind is None:
            if cur is not None:
                cur[1].append((i, None, ln))
            continue
        if _DEF_RE.match(ln):
            if cur:
                blocks.append(cur)
            base = ind
            cur = (i, [(i, ind, ln)])
        elif cur is not None:
            if ind <= base:
                blocks.append(cur)
                cur = None
            else:
                cur[1].append((i, ind, ln))
    if cur:
        blocks.append(cur)
    return blocks


def _first_hit_inside_loop(block_lines, pattern):
    """块内首处「循环体内命中」→ 循环体行号; 无则 None。

    循环体= for/while 行(缩进 m)之后缩进> m 的连续非空行; 缩进<= m 出体。
    v2 收紧: v1 口径为块内任意位置共现(except 兜底/循环后聚合日志误报源)。
    """
    for idx, (_no, ind, ln) in enumerate(block_lines):
        if ind is None or not _FOR_RE.match(ln):
            continue
        for j in range(idx + 1, len(block_lines)):
            no2, ind2, ln2 = block_lines[j]
            if ind2 is None:
                continue
            if ind2 <= ind:
                break
            if pattern.search(ln2):
                return no2
    return None


def _exempts(text):
    """文件级豁免名单(v2 通道)。"""
    out = set()
    for m in _EXEMPT_RE.finditer(text):
        for tok in re.split(r"[,，、]", m.group(1)):
            tok = tok.strip().upper()
            if tok:
                out.add(tok)
    return out


def _is_exempt(exset, rule_name):
    if "ALL" in exset:
        return True
    return any(rule_name.startswith(t) for t in exset if t)


def scan_text(unit_id, text):
    """单文本快筛 → [(rule, line_no, detail)](去重: 每规则首处取证)。"""
    hits = []
    seen = set()
    exset = _exempts(text)
    for name, pattern, advice in _LINE_RULES:
        if _is_exempt(exset, name):
            continue
        if name == "AP-2 异步误用" and not _ASYNC_RE.search(text):
            continue  # 无 async 上下文不触发
        for m in pattern.finditer(text):
            ln = text.count("\n", 0, m.start()) + 1
            if name not in seen:
                hits.append((name, ln, advice))
                seen.add(name)
            break
    for name, pattern, advice in _BLOCK_RULES:
        if name in seen or _is_exempt(exset, name):
            continue
        for start, lines in _def_blocks(text):
            ln = _first_hit_inside_loop(lines, pattern)
            if ln is not None:
                hits.append((name, ln, advice))
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
    """zhuyan antipattern: 真实代码全量快筛+B4 回放(v2 规则)。"""
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
    print(f"[zhuyan antipattern] 真实代码快筛(v2 缩进感知+循环体内+豁免): "
          f"{len(units)} 单元 → 命中 {len(found)} 处(Fast Filter 快筛口径, 供人工精审)")
    for uid, rule, ln, advice in found[:8]:
        print(f"  - {uid} —— {rule}(行 {ln}): {advice}")
    if found:
        print("[zhuyan antipattern] 命中为快筛疑似, 只出不施——人工精审后再定")
    else:
        print("[zhuyan antipattern] v2 改进后零命中(74 处疑似全数精审归零)")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
