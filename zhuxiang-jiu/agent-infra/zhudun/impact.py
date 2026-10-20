"""zhudun impact 输入层+反向引用（T1.3 diff 采集 / T1.4 引用方反查）

改动面 = git diff --name-only <base>（已改未提交 + 已提交未推送）
        ∪ git status --porcelain -uall 未跟踪新文件
反向引用 = 改动文件符号(文件名/py 模块名/顶层 def·class/registry 路由 path)
        单遍 union 正则扫描全仓(子串语义, 对齐人工 grep) → {引用文件: [行号]}
默认基线 = origin/master；生产仓库根 = 本包上三级（d:/网站架构设计）。
后续: T1.5 红线命中 / T1.6 检查清单映射 / T1.7 报告生成。
"""
import json
import os
import re
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
DEFAULT_BASE = "origin/master"
SCAN_EXCLUDE_DIRS = {".git", "__pycache__", "node_modules", "docs", ".ruff_cache",
                     ".pytest_cache", ".tools", ".docxgen", "商品设计素材",
                     "支付宝申诉材料", "注册认证库", "images", "videos", "dist"}
SCAN_EXTS = (".py", ".js", ".html")
TOP_DEF_RE = re.compile(r"^(?:async\s+)?(?:def|class)\s+(\w+)", re.MULTILINE)


def changed_files(base=DEFAULT_BASE, repo=DEFAULT_REPO):
    """返回改动文件清单（相对仓库根的路径列表, 已排序）; git 不可用/无差异返回空表"""
    diff = subprocess.run(
        ["git", "diff", "--name-only", base],
        capture_output=True, text=True, encoding="utf-8", check=False, cwd=repo)
    files = {ln.strip().strip('"') for ln in diff.stdout.splitlines() if ln.strip()}
    status = subprocess.run(
        ["git", "status", "--porcelain", "-uall"],
        capture_output=True, text=True, encoding="utf-8", check=False, cwd=repo)
    for ln in status.stdout.splitlines():
        if ln.startswith("??"):
            name = ln[3:].strip().strip('"')
            if name and not name.endswith("/"):
                files.add(name)
    return sorted(files)


def _load_registry(repo):
    """载入 registry.json（不存在返回空 dict——引用层尽力而为）"""
    p = os.path.join(repo, "zhuxiang-jiu", "docs", "registry", "registry.json")
    try:
        return json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def symbols_of(changed, repo=DEFAULT_REPO, registry=None):
    """从改动文件提取可引用符号: 文件名 / py 模块名+顶层 def·class / registry 路由 path"""
    syms = set()
    for f in changed:
        base = os.path.basename(f)
        syms.add(base)
        if f.endswith(".py"):
            syms.add(os.path.splitext(base)[0])
            try:
                with open(os.path.join(repo, f), encoding="utf-8-sig", errors="replace") as fh:
                    src = fh.read()
            except OSError:
                src = ""
            syms.update(TOP_DEF_RE.findall(src))
    if registry is None:
        registry = _load_registry(repo)
    changed_basenames = {os.path.basename(f) for f in changed}
    for e in registry.get("endpoints", []):
        if e.get("module") in changed_basenames:
            syms.add(e["path"])
    return {s for s in syms if len(s) >= 4 and "{" not in s}


def referencing_files(changed, repo=DEFAULT_REPO, registry=None):
    """反向引用: 单遍 union 正则扫描全仓 → (符号集, {引用文件: [行号]})

    子串语义对齐人工 grep; 排除改动文件自身/生成物目录/媒体目录。
    """
    syms = symbols_of(changed, repo=repo, registry=registry)
    if not syms:
        return syms, {}
    pattern = re.compile("|".join(re.escape(s) for s in sorted(syms)))
    changed_set = set(changed)
    refs = {}
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in SCAN_EXCLUDE_DIRS]
        for fn in sorted(files):
            if not fn.endswith(SCAN_EXTS):
                continue
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, repo).replace("\\", "/")
            if rel in changed_set:
                continue
            try:
                with open(full, encoding="utf-8-sig", errors="replace") as fh:
                    src = fh.read()
            except OSError:
                continue
            hits = [i for i, line in enumerate(src.split("\n"), 1) if pattern.search(line)]
            if hits:
                refs[rel] = hits[:20]
    return syms, refs


def _path_suffix(changed_path, marker_path):
    """marker 路径(backend 相对或 zhuxiang-jiu 相对)是否为改动路径的路径段后缀"""
    cparts = changed_path.replace("\\", "/").split("/")
    rparts = marker_path.replace("\\", "/").split("/")
    return len(rparts) <= len(cparts) and cparts[-len(rparts):] == rparts


def redline_assess(changed, registry):
    """T1.5 红线命中判定: 改动文件 → {"risk": 红/高/中/低, "hits": [命中条目]}

    判定序(从严到宽): R1-R6 红线文件面→红; B1-B5 行为资产/运行时开关 loc→高;
    backend 核心 py 未直接命中→中; 其余→低。
    """
    reds = registry.get("red_lines", [])
    behs = registry.get("behavior_assets", [])
    switches = registry.get("switches", [])
    out = {}
    for f in changed:
        norm = f.replace("\\", "/")
        hits = []
        risk = "低"
        for r in reds:
            if any(_path_suffix(norm, p) for p in r.get("files", [])):
                hits.append(f"{r['id']} {r['name']}")
                risk = "红"
        if risk != "红":
            for b in behs:
                if any(_path_suffix(norm, p) for p in b.get("files", [])):
                    hits.append(f"{b['id']} {b['item']}")
                    risk = "高"
            for s in switches:
                if _path_suffix(norm, s.get("loc", ":").rsplit(":", 1)[0]):
                    hits.append(f"开关:{s['key']}(回滚={s.get('rollback', '?')})")
                    if risk != "高":
                        risk = "高"
        if risk == "低" and "backend" in norm.split("/") and norm.endswith(".py"):
            risk = "中"
        out[f] = {"risk": risk, "hits": hits[:10]}
    return out


def run(args=None):
    base = getattr(args, "base", None) or DEFAULT_BASE
    files = changed_files(base=base)
    if not files:
        print(f"[zhudun impact] 改动面 0 文件（对照基线 {base}）——T1.6+ 报告层待接入")
        return 0
    registry = _load_registry(DEFAULT_REPO)
    syms, refs = referencing_files(files, registry=registry)
    assessment = redline_assess(files, registry)
    counts = {"红": 0, "高": 0, "中": 0, "低": 0}
    for a in assessment.values():
        counts[a["risk"]] += 1
    print(f"[zhudun impact] 改动面 {len(files)} 文件（对照基线 {base}）"
          f"——风险分级: 🔴红 {counts['红']} / 🟠高 {counts['高']} / 🟡中 {counts['中']} / ⚪低 {counts['低']}")
    for f in files:
        a = assessment[f]
        mark = {"红": "🔴", "高": "🟠", "中": "🟡", "低": "⚪"}[a["risk"]]
        print(f"  {mark} {f}")
        for h in a["hits"][:4]:
            print(f"       ↳ {h}")
    print(f"[zhudun impact] 反向引用: 符号 {len(syms)} 个, 引用方 {len(refs)} 文件")
    for rel, lines in sorted(refs.items())[:10]:
        print(f"  {rel}: 行 {lines}")
    if len(refs) > 10:
        print(f"  ...（共 {len(refs)} 文件, 截断显示）")
    return 0
