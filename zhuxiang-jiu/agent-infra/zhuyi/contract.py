"""zhuyi contract: L2 前后端契约对齐（79号·P2, 当前实现 T2.1 前端调用面提取）

T2.1: 扫主站前端(仓库根 html + js/*.js)的 fetch/axios/WebSocket 调用——
括号跟踪取参数区字面量序列, 重组为 URL 模板(变量拼接处标 {var}), 剥查询串,
method 自参数区 'POST'/'GET' 字面量取(缺省 GET)。产物=前端调用面明细
[{file, line, method, url}], 供 T2.2 断契对齐(前端调用 vs registry 路由)、
T2.2: 调用集合 vs registry 路由——无对应路由=断契(check_contract_break)。
T2.3: 白名单 EXACT 条目 vs 实际路由形态——永不匹配=死条目
  (check_dead_exact)。**M2 基准**: qr 3 死条目(无参形态 vs 带参路由,
  e73f6800 修复前实录)回放检出 3/3。
后续: T2.4 契约报告+链联动。
"""
import os
import re

try:
    from zhuyi import audit as audit_mod
    from zhuyi import chains as chains_mod
except ImportError:
    import audit as audit_mod
    import chains as chains_mod

_CALL_RE = re.compile(r"(?:fetch|WebSocket|axios(?:\.\w+)?)\s*\(")
_LIT_RE = re.compile(r"['\"]([^'\"()]+)['\"]")
_METHOD_RE = re.compile(r"method\s*[:=]\s*['\"](GET|POST|PUT|DELETE|PATCH)['\"]")


def extract_frontend_calls(repo_root=None):
    """前端调用面提取 → [{file, line, method, url}]。

    重组规则: 参数区字面量序列中首个含 /api/ 的为 base; 右侧以 / 开头的
    短字面量(≤16字符, 非静态前缀)按出现顺序以 {var} 连接(变量拼接模板化);
    base 后存在裸变量拼接而无尾段字面量时补 {var}。
    """
    texts = audit_mod._frontend_texts(repo_root)
    calls = []
    for fname, text in texts:
        for m in _CALL_RE.finditer(text):
            depth, i = 1, m.end()
            while i < len(text) and depth:
                depth += (text[i] == "(") - (text[i] == ")")
                i += 1
            seg = text[m.end():i - 1]
            lits = _LIT_RE.findall(seg)
            base = next((s for s in lits if "/api/" in s), None)
            if base is None:
                continue
            mm = _METHOD_RE.search(seg)
            is_ws = text[m.start():m.end()].startswith("WebSocket")
            method = "WS" if is_ws else (mm.group(1) if mm else "GET")
            url = base.split("?", 1)[0]
            rest = lits[lits.index(base) + 1:]
            for s in rest:
                if (s.startswith("/") and len(s) <= 16
                        and not s.startswith(("/api", "/images", "/css", "/js/"))):
                    url += "{var}" + s
            calls.append({
                "file": fname,
                "line": text.count("\n", 0, m.start()) + 1,
                "method": method,
                "url": url,
            })
    return calls


def _url_matches(call_url, reg_path):
    """调用 url × registry path 匹配: 模板段({var}~{param})互认 + 动态前缀截断形态。

    /api/entry/qr/{var}/exchange ~ /api/entry/qr/{qr_id}/exchange → True(段数同,参数段互认)
    /api/product/ ~ /api/product/{id} → True(前缀截断, 拼接变量截于调用侧)
    """
    a = call_url.strip("/").split("/")
    b = reg_path.strip("/").split("/")
    n = len(a) - (1 if a and a[-1] == "" else 0)
    if n > len(b):
        return False
    for x, y in zip(a[:n], b[:n]):
        if x == y:
            continue
        if x == "{var}" and y.startswith("{") and y.endswith("}"):
            continue
        return False
    if n == len(b):
        return True
    # 前缀形态: 调用侧截断, registry 剩余首段须为参数段(动态拼接)
    return b[n].startswith("{") and b[n].endswith("}")


def check_contract_break(calls=None, registry=None):
    """T2.2 断契对齐: 前端调用(去重) × registry 路由——无路由=断契。

    返回 (hits, breaks): hits=对齐命中的调用形态集合(method,url);
    breaks=[{method, url, files, kind='contract_break'}] 断契明细。
    """
    calls = calls if calls is not None else extract_frontend_calls()
    registry = registry if registry is not None else chains_mod.load_registry()
    by_path = {}
    for e in registry["endpoints"]:
        by_path.setdefault(e["path"], []).append(e)
    uniq = {}
    for c in calls:
        uniq.setdefault((c["method"], c["url"]), []).append(f"{c['file']}:{c['line']}")
    hits, breaks = set(), []
    for (method, url), locs in sorted(uniq.items()):
        matched = False
        for path, eps in by_path.items():
            if not _url_matches(url, path):
                continue
            if any(e["method"] == method or (method == "GET" and e["method"] == "WS")
                   for e in eps):  # GET↔WS: ws 提取降级兼容
                matched = True
                break
        if matched:
            hits.add((method, url))
        else:
            # method 不匹配但 path 命中 → 也算断契(方法不符), 标注实际注册方法
            reg_methods = [e["method"] for path2, eps2 in by_path.items()
                           if _url_matches(url, path2) for e in eps2]
            breaks.append({
                "method": method, "url": url, "files": locs,
                "kind": "contract_break",
                "registered": ",".join(sorted(set(reg_methods))) or "无",
            })
    return hits, breaks


def extract_public_exact(auth_path=None):
    """backend/core/auth_middleware.py 的 PUBLIC_EXACT 条目提取。

    块内剥 # 注释行后抓 / 开头的引号串(白名单口径唯一来源=代码, 对齐
    registry README 规则 3)。
    """
    if auth_path is None:
        auth_path = os.path.join(chains_mod.REPO_ROOT, "backend", "core",
                                 "auth_middleware.py")
    with open(auth_path, encoding="utf-8") as fh:
        text = fh.read()
    m = re.search(r"PUBLIC_EXACT\s*=\s*\{", text)
    if not m:
        return []
    depth, i = 1, m.end()
    while i < len(text) and depth:
        depth += (text[i] == "{") - (text[i] == "}")
        i += 1
    block = re.sub(r"#[^\n]*", "", text[m.end():i - 1])
    return re.findall(r"['\"](/[^'\"\s]+)['\"]", block)


def _template_compatible(entry, path):
    """EXACT 条目 vs 路由模板构型: 段数同 + 非参数段全等 + 模板参数段吞任意
    → 条目可能是某真实请求的全等实例(活)。段数不同构=永不匹配。"""
    a = entry.strip("/").split("/")
    b = path.strip("/").split("/")
    if len(a) != len(b):
        return False
    for x, y in zip(a, b):
        if y.startswith("{") and y.endswith("}"):
            continue
        if x != y:
            return False
    return True


def check_dead_exact(entries=None, registry=None):
    """T2.3 死条目检测: EXACT 条目 × registry 路由模板——永不匹配=死条目。

    返回 dead=[{entry, kind='dead_exact'}]。
    """
    if entries is None:
        entries = extract_public_exact()
    registry = registry if registry is not None else chains_mod.load_registry()
    paths = {e["path"] for e in registry["endpoints"]}
    return [{"entry": p, "kind": "dead_exact"}
            for p in entries
            if not any(_template_compatible(p, rp) for rp in paths)]


def run(args=None):
    """contract CLI 入口: T2.1-T2.3 三检, T2.4 逐任务补齐。"""
    calls = extract_frontend_calls()
    print(f"[zhuyi contract] T2.1 前端调用面: {len(calls)} 处调用")
    hits, breaks = check_contract_break(calls)
    print(f"[zhuyi contract] T2.2 断契对齐: 命中 {len(hits)} 形态 / 断契 {len(breaks)} 形态")
    for b in breaks:
        locs = ", ".join(b["files"][:2])
        extra = f"(registry 实际方法: {b['registered']})" if b["registered"] != "无" else ""
        print(f"  [contract_break] {b['method']} {b['url']} ← {locs}{extra}——前端调用后端无对应路由")
    entries = extract_public_exact()
    dead = check_dead_exact(entries)
    print(f"[zhuyi contract] T2.3 死条目检测: EXACT {len(entries)} 条 / 死条目 {len(dead)} 条")
    for d in dead:
        print(f"  [dead_exact] {d['entry']}——白名单条目与任何注册路由形态永不匹配"
              f"(对照 L0-端点清单修正形态或删除)")
    print("[zhuyi contract] T2.4 契约报告+链联动: 后续任务实现")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
