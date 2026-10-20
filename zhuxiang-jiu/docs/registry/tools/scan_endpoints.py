"""竹盾 T0.2 端点清单生成器（临时工具——P1 起由 zhudun index 替代并移除）

扫描 backend/main.py + backend/routes/*.py 的路由装饰器,
产出 docs/registry/L0-端点清单.md(方法/路径/函数/行号/鉴权面/@_decision 门控)。

覆盖形态(2026-10-20 实测修正):
    1. 任意命名路由器变量(var = APIRouter(prefix=...), 如 res_router/unlock_router)
    2. f-string 常量插值路径(f"{API_BASE}/health" -> 解析文件级常量)
    3. 参数化路由模板(/api/auth/oauth/{platform}/url -> 与白名单具体实例模板匹配)
    4. websocket 装饰器(方法列 WS)
鉴权面标注直接 import core/auth_middleware 权威白名单, 零转录。
自动发现: EXACT 死条目(白名单路径无路由可匹配)、GET 前缀死条目。
用法: python docs/registry/tools/scan_endpoints.py
"""
import io
import os
import re
import sys
from collections import OrderedDict

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
BACKEND = os.path.join(REPO, "backend")
ROUTES_DIR = os.path.join(BACKEND, "routes")
OUT = os.path.join(REPO, "docs", "registry", "L0-端点清单.md")

sys.path.insert(0, BACKEND)
from core.auth_middleware import (  # ruff 作用域外, sys.path 注入后显式导入
    PUBLIC_EXACT, PUBLIC_GET_PREFIXES, PUBLIC_GET_PATTERNS,
    PUBLIC_POST_PATTERNS,
)

DECO_RE = re.compile(r'@(\w+)\.(get|post|put|delete|patch|websocket)\((.*)')
PATH_RE = re.compile(r'''["']([^"']+)["']''')
CONST_RE = re.compile(r'^(\w+)\s*=\s*["\']([^"\']+)["\']', re.M)


def extract_router_vars(src):
    """收集本文件全部 var = APIRouter(...) 及其 prefix(括号配平, 防 Depends() 截断)"""
    res = {}
    for m in re.finditer(r'(\w+)\s*=\s*APIRouter\(', src):
        var = m.group(1)
        i = m.end() - 1
        depth = 0
        j = i
        while j < len(src):
            c = src[j]
            if c == '(':
                depth += 1
            elif c == ')':
                depth -= 1
                if depth == 0:
                    break
            j += 1
        seg = src[i:j + 1]
        pm = re.search(r'prefix\s*=\s*["\']([^"\']+)["\']', seg)
        res[var] = pm.group(1) if pm else ''
    return res


def extract_constants(src):
    """文件级字符串常量(用于 f-string 路径解析)"""
    return {m.group(1): m.group(2) for m in CONST_RE.finditer(src)}


def resolve_path(raw, consts):
    """解析 f-string 中的大写常量插值; 小写 {param} 为路径参数, 原样保留"""
    path = raw
    for _ in range(3):
        new = re.sub(r'\{(\w+)\}',
                     lambda m: consts.get(m.group(1), m.group(0)), path)
        if new == path:
            break
        path = new
    return path


def path_template_regex(path):
    """参数化路径模板转正则: {param} -> [^/]+"""
    segs = []
    for seg in path.split('/'):
        segs.append('[^/]+' if re.fullmatch(r'\{\w+\}', seg) else re.escape(seg))
    return re.compile('^' + '/'.join(segs) + '$')


_TPL_CACHE = {}


def classify(path, method):
    """鉴权面标注——与 core/auth_middleware.is_public_path 口径对齐"""
    if path in PUBLIC_EXACT:
        return "公开·EXACT"
    # 参数化路由: 白名单具体实例(如 /api/auth/oauth/wechat/url)命中模板
    rx = _TPL_CACHE.get(path)
    if rx is None:
        rx = path_template_regex(path)
        _TPL_CACHE[path] = rx
    if any(rx.match(e) for e in PUBLIC_EXACT):
        return "公开·EXACT·模板"
    if method == "GET":
        for p in PUBLIC_GET_PREFIXES:
            if path.startswith(p):
                return "公开·GET前缀"
        for pat in PUBLIC_GET_PATTERNS:
            if pat.match(path):
                return "公开·GET正则"
    # 39号扫码带 qr_id 段 POST 公开(2026-10-21 竹盾 R2 修复对齐)
    if method == "POST":
        for pat in PUBLIC_POST_PATTERNS:
            if pat.match(path):
                return "公开·POST正则"
    return "登录·JWT"


def scan_file(src, router_vars, global_consts):
    """逐行扫描路由装饰器, 返回 [(行号, 方法, 完整路径, 函数名, 是否@_decision门控)]"""
    consts = dict(global_consts)
    consts.update(extract_constants(src))  # 文件本地常量优先
    out = []
    lines = src.split("\n")
    n = len(lines)
    for i in range(n):
        m = DECO_RE.match(lines[i].strip())
        if not m:
            continue
        var = m.group(1)
        if var != 'app' and var not in router_vars:
            continue  # 非路由器装饰器
        prefix = router_vars.get(var, '')
        rest = m.group(3)
        pm = PATH_RE.search(rest)
        j = i
        while not pm and j < min(i + 6, n):
            j += 1
            rest += " " + lines[j].strip()
            pm = PATH_RE.search(rest)
        if not pm:
            continue
        raw_path = resolve_path(pm.group(1), consts)
        full_path = (prefix + raw_path) if (prefix and not raw_path.startswith("/api")) else raw_path
        method = "WS" if m.group(2) == "websocket" else m.group(2).upper()
        # 向下找 def 与 @_decision(门控装饰器通常紧贴 def, 位于路由装饰器之下)
        gated = False
        func = "?"
        for k in range(i + 1, min(i + 9, n)):
            l2 = lines[k]
            if "@_decision" in l2:
                gated = True
            dm = re.search(r'\bdef\s+(\w+)', l2)
            if dm:
                func = dm.group(1)
                break
        # 兜底: 向上看同装饰器栈(最多3行, 遇非@行停)
        if not gated:
            for k in range(i - 1, max(i - 4, -1), -1):
                s = lines[k].strip()
                if not s.startswith("@"):
                    break
                if "@_decision" in s:
                    gated = True
                    break
        out.append((i + 1, method, full_path, func, gated))
    return out


def module_desc(src):
    """取文件首行 docstring 或注释作模块描述"""
    m = re.search(r'^\s*"""(.+)', src, re.M)
    if m:
        return m.group(1).strip().rstrip('"').strip()
    m = re.search(r'^\s*#\s*(.+)', src, re.M)
    if m:
        return m.group(1).strip()
    return ""


def main():
    # 全局常量池: core/*.py(API_BASE 等跨文件 import 的路径常量)
    global_consts = {}
    core_dir = os.path.join(BACKEND, "core")
    for f in sorted(os.listdir(core_dir)):
        if f.endswith(".py"):
            src_c = io.open(os.path.join(core_dir, f), encoding="utf-8-sig", errors="replace").read()
            global_consts.update(extract_constants(src_c))

    modules = OrderedDict()
    # 1) main.py 入口路由(@app)
    src_main = io.open(os.path.join(BACKEND, "main.py"), encoding="utf-8-sig", errors="replace").read()
    rs_main = scan_file(src_main, {}, global_consts)
    if rs_main:
        modules["main.py"] = (module_desc(src_main), rs_main)
    # 2) routes/*.py(全部路由器变量)
    route_files = sorted(f for f in os.listdir(ROUTES_DIR) if f.endswith(".py") and f != "__init__.py")
    for f in route_files:
        src = io.open(os.path.join(ROUTES_DIR, f), encoding="utf-8-sig", errors="replace").read()
        rv = extract_router_vars(src)
        rs = scan_file(src, rv, global_consts)
        if rs:
            modules[f] = (module_desc(src), rs)

    # ---- 汇总 ----
    total = 0
    by_method = {}
    auth_stat = OrderedDict([("公开·EXACT", 0), ("公开·EXACT·模板", 0),
                             ("公开·GET前缀", 0), ("公开·GET正则", 0),
                             ("公开·POST正则", 0), ("登录·JWT", 0)])
    gated_cnt = 0
    all_paths = set()
    all_templates = set()
    for _desc, rs in modules.values():
        for (line, method, path, func, gated) in rs:
            total += 1
            by_method[method] = by_method.get(method, 0) + 1
            auth_stat[classify(path, method)] += 1
            if gated:
                gated_cnt += 1
            if re.search(r'\{', path):
                all_templates.add(path)
            else:
                all_paths.add(path)

    # ---- 白名单漂移检测 ----
    tpl_rxs = [path_template_regex(p) for p in all_templates]
    dead_exact = [e for e in sorted(PUBLIC_EXACT)
                  if e not in all_paths and not any(rx.match(e) for rx in tpl_rxs)]
    dead_prefix = [p for p in PUBLIC_GET_PREFIXES
                   if not any(x.startswith(p) for x in all_paths | all_templates)]

    # ---- 写 MD ----
    buf = []
    buf.append("# L0-端点清单（竹盾资产图谱 · 端点层）")
    buf.append("")
    buf.append("> 生成: 2026-10-20 · 任务 T0.2 · 扫描面: backend/main.py + backend/routes/*.py（%d 个路由文件）" % len(route_files))
    buf.append("> 生成方式: [tools/scan_endpoints.py](tools/scan_endpoints.py) 提取路由装饰器（覆盖子路由器变量/f-string 常量/参数模板/WS）；鉴权面标注 import [core/auth_middleware.py](../../backend/core/auth_middleware.py) 权威白名单，零转录")
    buf.append("> 维护: 新增/修改路由后重跑扫描器+人工校对（P1 起由 `zhudun index` 自动重建）· 上游: [78号_竹盾增量管理Agent_创新规划方案.md](../78号_竹盾增量管理Agent_创新规划方案.md)")
    buf.append("")
    buf.append("## 总览")
    buf.append("")
    buf.append("| 指标 | 数值 |")
    buf.append("| --- | --- |")
    buf.append("| 端点总数 | %d |" % total)
    buf.append("| 方法分布 | %s |" % " / ".join("%s %d" % kv for kv in sorted(by_method.items())))
    for k, v in auth_stat.items():
        label = k
        if k == "公开·EXACT":
            label = "公开·EXACT（免登录，含支付回调等资金链路）"
        elif k == "公开·EXACT·模板":
            label = "公开·EXACT·模板（参数化路由，白名单具体实例命中）"
        elif k == "公开·GET前缀":
            label = "公开·GET前缀（游客浏览面）"
        elif k == "公开·GET正则":
            label = "公开·GET正则（动态路径）"
        elif k == "公开·POST正则":
            label = "公开·POST正则（39号扫码带 qr_id 段，2026-10-21 R2 修复）"
        buf.append("| %s | %d |" % (label, v))
    buf.append("| @_decision 门控端点 | %d |" % gated_cnt)
    buf.append("")
    buf.append("> 注: 公开≠无防护——EXACT 多数端点路由层自带校验（admin 拦截/频控/验签），中间件仅免登录，详见 auth_middleware 白名单注释。")
    buf.append("")
    if dead_exact or dead_prefix:
        buf.append("## ⚠ 白名单与路由的漂移（扫描器自动发现，待治理）")
        buf.append("")
        buf.append("| 类型 | 条目 | 说明 |")
        buf.append("| --- | --- | --- |")
        for e in dead_exact:
            buf.append("| EXACT 死条目 | `%s` | 白名单路径无任何路由可匹配（含参数模板对照），中间件层永不放行，需人工确认是路由漂移还是残留 |" % e)
        for p in dead_prefix:
            buf.append("| GET 前缀死条目 | `%s` | 前缀下无任何已扫描路由命中 |" % p)
        buf.append("")
    buf.append("## 模块明细")
    buf.append("")
    for f, (desc, rs) in modules.items():
        title = f + (" — " + desc if desc else "")
        buf.append("### " + title)
        buf.append("")
        buf.append("| 方法 | 路径 | 函数 | 行号 | 鉴权面 | 门控 |")
        buf.append("| --- | --- | --- | --- | --- | --- |")
        for (line, method, path, func, gated) in rs:
            auth = classify(path, method)
            buf.append("| %s | `%s` | %s | %d | %s | %s |" % (
                method, path, func, line, auth, "@_decision" if gated else "-"))
        buf.append("")

    io.open(OUT, "w", encoding="utf-8").write("\n".join(buf))
    print("OK total=%d modules=%d exact=%d exact_tpl=%d getpre=%d getre=%d postre=%d jwt=%d gated=%d dead_exact=%d dead_prefix=%d" % (
        total, len(modules), auth_stat["公开·EXACT"], auth_stat["公开·EXACT·模板"],
        auth_stat["公开·GET前缀"], auth_stat["公开·GET正则"], auth_stat["公开·POST正则"],
        auth_stat["登录·JWT"],
        gated_cnt, len(dead_exact), len(dead_prefix)))


if __name__ == "__main__":
    main()
