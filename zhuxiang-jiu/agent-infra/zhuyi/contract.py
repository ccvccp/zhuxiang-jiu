"""zhuyi contract: L2 前后端契约对齐（79号·P2, 当前实现 T2.1 前端调用面提取）

T2.1: 扫主站前端(仓库根 html + js/*.js)的 fetch/axios/WebSocket 调用——
括号跟踪取参数区字面量序列, 重组为 URL 模板(变量拼接处标 {var}), 剥查询串,
method 自参数区 'POST'/'GET' 字面量取(缺省 GET)。产物=前端调用面明细
[{file, line, method, url}], 供 T2.2 断契对齐(前端调用 vs registry 路由)、
T2.3 死条目检测消费。后续: T2.2 断契 / T2.3 死条目 / T2.4 契约报告+链联动。
"""
import re

try:
    from zhuyi import audit as audit_mod
except ImportError:
    import audit as audit_mod

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
            method = mm.group(1) if mm else "GET"
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


def run(args=None):
    """contract CLI 入口: 当前输出 T2.1 前端调用面清单, T2.2-T2.4 逐任务补齐。"""
    calls = extract_frontend_calls()
    print(f"[zhuyi contract] T2.1 前端调用面: {len(calls)} 处调用")
    seen = set()
    for c in calls:
        key = (c["method"], c["url"])
        mark = "*" if key in seen else " "
        seen.add(key)
        print(f"  {mark} {c['method']:4} {c['url']}  ← {c['file']}:{c['line']}")
    uniq = len(seen)
    print(f"  去重端点形态: {uniq}")
    print("[zhuyi contract] T2.2 断契对齐 / T2.3 死条目检测 / "
          "T2.4 契约报告+链联动: 后续任务实现")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
