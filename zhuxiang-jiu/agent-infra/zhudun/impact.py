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


def run(args=None):
    base = getattr(args, "base", None) or DEFAULT_BASE
    files = changed_files(base=base)
    if not files:
        print(f"[zhudun impact] 改动面 0 文件（对照基线 {base}）——T1.5+ 判定层待接入")
        return 0
    syms, refs = referencing_files(files)
    print(f"[zhudun impact] 改动面 {len(files)} 文件（对照基线 {base}）:")
    for f in files:
        print("  " + f)
    print(f"[zhudun impact] 反向引用: 符号 {len(syms)} 个, 引用方 {len(refs)} 文件")
    for rel, lines in sorted(refs.items())[:20]:
        print(f"  {rel}: 行 {lines}")
    if len(refs) > 20:
        print(f"  ...（共 {len(refs)} 文件, 截断显示）")
    return 0
