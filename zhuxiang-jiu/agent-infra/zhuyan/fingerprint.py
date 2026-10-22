"""zhuyan fingerprint: 功能指纹提取（81号·T0.3, L2 实体层底座）

功能单元三类枚举 → 指纹 json 落盘(T0.3 基础字段, S_r 特征在 T1.2 扩展):
- 端点: 竹盾 L0 台账 registry.json 权威源(2612 条) → backend 源文件映射;
- 核心模块: backend/core/*.py;
- 页面: 主站非豁免页(竹园 audit 豁免口径复用)。
每指纹: unit_id/unit_type/file/func/line/lines/imports/defs/docstring_rate/
tests(关联测试文件)/contract(auth·SEO 特征) —— 全静态取证, 每字段可追溯。
"""
import json
import os
import re
from collections import Counter

try:
    from zhuyi import chains as chains_mod
    from zhuyuan import audit as zy_audit_mod
except ImportError:
    import sys
    _pkg = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _pkg not in sys.path:
        sys.path.insert(0, _pkg)
    from zhuyi import chains as chains_mod
    from zhuyuan import audit as zy_audit_mod

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_FINGERPRINTS_JSON = os.path.join(
    REPO_ROOT, "docs", "registry", "maturity", "fingerprints.json")
# 落盘路径注入点(测试隔离用; None=默认 docs/registry/maturity/fingerprints.json)
FINGERPRINTS_PATH = None

_DEF_RE = re.compile(r"^\s*def\s+\w+", re.MULTILINE)
_IMPORT_RE = re.compile(r"^\s*(?:import|from)\s+", re.MULTILINE)


def _module_path(repo_root, module):
    """L0 module 字段 → backend 源文件相对路径(routes 优先, 根目录回退)。"""
    for cand in (os.path.join("backend", "routes", module),
                 os.path.join("backend", module)):
        full = os.path.join(repo_root, cand)
        if os.path.exists(full):
            return cand.replace(os.sep, "/"), full
    return None, None


def _py_file_fingerprint(path):
    """python 文件基础指纹: 行数/import 计数(耦合代理)/def 数/docstring 覆盖率。"""
    with open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    defs = len(_DEF_RE.findall(text))
    docstrings = text.count('"""') // 2
    return {"lines": text.count("\n") + 1,
            "imports": len(_IMPORT_RE.findall(text)),
            "defs": defs,
            "docstring_rate": round(min(docstrings / defs, 1.0), 2) if defs else 0.0}


def _load_test_texts(repo_root):
    """backend 测试文件文本预载(关联测试判定用, 一次读入避免重复 IO)。"""
    texts = {}
    bdir = os.path.join(repo_root, "backend")
    if not os.path.isdir(bdir):
        return texts
    for name in sorted(os.listdir(bdir)):
        if name.startswith("test") and name.endswith(".py"):
            try:
                with open(os.path.join(bdir, name), encoding="utf-8",
                          errors="replace") as fh:
                    texts[f"backend/{name}"] = fh.read()
            except OSError:
                continue
    return texts


def _related_tests(test_texts, keywords):
    """关联测试: 关键词(func 名/模块词根)在测试文件文本中命中 → 文件列表。"""
    kws = [k for k in keywords if k]
    if not kws:
        return []
    return [path for path, text in test_texts.items()
            if any(k in text for k in kws)]


def enumerate_units(repo_root=None):
    """三类功能单元枚举 → [unit dict(含基础指纹)]。全静态取证。"""
    repo_root = repo_root or REPO_ROOT
    test_texts = _load_test_texts(repo_root)
    units = []
    # ---- 1) 端点(竹盾 L0 权威源) ----
    registry = chains_mod.load_registry()
    file_cache = {}
    for ep in registry["endpoints"]:
        rel, full = _module_path(repo_root, ep["module"])
        if full and rel not in file_cache:
            file_cache[rel] = _py_file_fingerprint(full)
        units.append({
            "unit_id": f"{ep['method']} {ep['path']}",
            "unit_type": "endpoint",
            "file": rel, "func": ep["func"], "line": ep["line"],
            "tests": _related_tests(test_texts, [ep["func"]]),
            "contract": {"auth": ep["auth"], "gated": ep["gated"]},
            **(file_cache.get(rel) or {"lines": 0, "imports": 0,
                                       "defs": 0, "docstring_rate": 0.0}),
        })
    # ---- 2) 核心模块(backend/core/*.py) ----
    core_dir = os.path.join(repo_root, "backend", "core")
    if os.path.isdir(core_dir):
        for name in sorted(os.listdir(core_dir)):
            if not name.endswith(".py"):
                continue
            rel = f"backend/core/{name}"
            units.append({
                "unit_id": f"core/{name}", "unit_type": "module",
                "file": rel, "func": None, "line": None,
                "tests": _related_tests(test_texts, [name[:-3]]),
                "contract": {},
                **_py_file_fingerprint(os.path.join(core_dir, name)),
            })
    # ---- 3) 页面(主站非豁免, 竹园 audit 豁免口径复用) ----
    for name in sorted(os.listdir(repo_root)):
        if not (name.endswith(".html") and not zy_audit_mod._is_exempt(name)):
            continue
        with open(os.path.join(repo_root, name), encoding="utf-8",
                  errors="replace") as fh:
            html = fh.read()
        head = html[:html.find("</head>")] if "</head>" in html else html
        units.append({
            "unit_id": name, "unit_type": "page",
            "file": name, "func": None, "line": None,
            "lines": html.count("\n") + 1, "imports": 0, "defs": 0,
            "docstring_rate": 0.0,
            "tests": [],
            "contract": {"has_title": "<title>" in head,
                         "has_description": 'name="description"' in head},
        })
    return units


def save_fingerprints(units, out_path=None):
    """指纹台账落盘 json(L2 实体层基线) → 路径。"""
    out_path = out_path or FINGERPRINTS_PATH or _FINGERPRINTS_JSON
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(units, fh, ensure_ascii=False, indent=1)
    return out_path


def run(args=None):
    """zhuyan score CLI: T0.3 功能指纹台账(三维评分在 T1.1-T1.4 逐维实装)。"""
    units = enumerate_units()
    by_type = Counter(u["unit_type"] for u in units)
    print(f"[zhuyan score] 功能指纹台账: 端点 {by_type['endpoint']}"
          f" + 核心模块 {by_type['module']} + 页面 {by_type['page']}"
          f" = {len(units)} 单元")
    for t in ("endpoint", "module", "page"):
        for u in [u for u in units if u["unit_type"] == t][:3]:
            print(f"  [{t}] {u['unit_id']} —— {u['file']}"
                  f" lines={u['lines']} imports={u['imports']}"
                  f" docstring_rate={u['docstring_rate']}"
                  f" tests={len(u['tests'])}")
    out = save_fingerprints(units)
    print(f"[zhuyan score] T0.3 指纹基线已落盘: {out}")
    print("[zhuyan score] S_b/S_r/S_e 三维评分在 T1.1-T1.4 逐维实装, "
          "口径见 docs/registry/maturity/成熟度评分标准.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
