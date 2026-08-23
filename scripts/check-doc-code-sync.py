# -*- coding: utf-8 -*-
"""文档-代码同步核对脚本(v8.0 阶段1·任务1)

盘点「根目录模块设计文档 ↔ backend 路由/服务/仓库代码」的客观对应关系:
    - 自动机检: 路由文件 docstring 声明端点数 vs @router 装饰器实际端点数
    - 自动盘点: 文档 DB 表名 vs 仓库层存储实体(启发式前缀匹配)
    - 双向缺口: 无代码对应的设计文档 / 无文档对应的路由文件

产出(写入 docs/):
    1. 文档代码同步核对报告.md  → 客观盘点结果(自动生成, 勿手改)
    2. 文档代码同步核对清单.md  → 人工核对工作清单(状态列待人工填写)

用法(仓库根目录):
    python scripts/check-doc-code-sync.py

约定:
    - 纯标准库/零依赖/只读扫描, 不修改任何代码与文档
    - 设计文档为中文叙述型(无结构化 API 清单), 业务规则/字段细节
      的一致性需人工核对, 脚本只负责把客观事实摆齐
"""

import re
import sys
from datetime import datetime
from pathlib import Path

# Windows GBK 控制台兼容
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = REPO_ROOT / "docs"
ROUTES_DIR = REPO_ROOT / "zhuxiang-jiu" / "backend" / "routes"
SERVICES_DIR = REPO_ROOT / "zhuxiang-jiu" / "backend" / "services"
REPOS_DIR = REPO_ROOT / "zhuxiang-jiu" / "backend" / "repositories"

REPORT_FILE = DOCS_DIR / "文档代码同步核对报告.md"
CHECKLIST_FILE = DOCS_DIR / "文档代码同步核对清单.md"

# 不参与模块映射的根目录文档
EXCLUDED_DOCS = {
    "README.md",
    "更新日志.md",
    "竹香酒网站总体架构设计文档.md",
    "生产环境Redis配置检查清单.md",
    "事务工具包-预检与失败阶段策略.md",
}

# 文档后缀 → 类别(design=设计/接口文档, db=表结构文档)
DOC_SUFFIXES = [
    ("数据库表结构设计", "db"),
    ("设计文档", "design"),
    ("接口文档", "design"),
]
# 这些后缀的文档不参与模块映射(测试/清单类)
DOC_IGNORE_SUFFIXES = ["测试文档", "检查清单", "测试报告"]

# 路由文件 → 模块名 兜底映射(tag 自动匹配失败时使用)
# 特殊值: __infra__=基础设施(架构文档覆盖, 无需独立文档)
#         __nodoc__=架构文档章节覆盖(无独立设计文档, 预期缺口)
# 说明: 以下多为「路由 tag 命名 ≠ 文档标题命名」的已确认对应关系
ROUTE_MODULE_HINTS = {
    "decision_routes.py": "AI决策筹划模块",
    "system_routes.py": "__infra__",
    "business_routes.py": "__infra__",
    "auth_routes.py": "__nodoc__",
    "ai_scoring_routes.py": "__nodoc__",
    "ai_scoring_ext_routes.py": "__nodoc__",
    "ai_scoring_auth_routes.py": "__nodoc__",
    "ai_learning_routes.py": "__nodoc__",
    "agent_routes.py": "代理商管理模块",
    "agreement_routes.py": "网站条款及角色协议管理模块",
    "chat_routes.py": "AI智能聊天及人工聊天模块",
    "member_routes.py": "会员管理模块",
    "order_routes.py": "订单管理模块",
    "points_routes.py": "会员积分管理模块",
    "product_routes.py": "产品展示模块",
    "promotion_routes.py": "推广码矩阵获利模块",
    "recycle_routes.py": "老酒兑换新酒及回收模块",
    "trace_routes.py": "竹奕酒生命码管理模块",
    "monitor_routes.py": "AI智能监控+维护模块",
    "maintenance_routes.py": "AI智能监控+维护模块",
}

# 模块名合并(子模块文档并入父模块, 避免同一业务拆成多个缺口行)
MODULE_MERGE = {
    "AI智能监控模块": "AI智能监控+维护模块",
    "AI智能维护模块": "AI智能监控+维护模块",
}

# 已知部分实现备注(人工确认过的事实, 避免误报为纯缺口)
KNOWN_PARTIAL_NOTES = [
    "评价管理模块: order_routes.py 含 POST /{order_id}/review(评价订单)单端点, 其余表结构未见独立实现",
    "代理商箱码管理模块: trace_routes.py 含箱码生成/绑定/查询 3 端点, 其余未见独立实现",
    "代理商发货路由: shipping_service.py 存在但无独立路由文件(由其他路由内部调用), 场景测试文档见根目录",
]

# 路由装饰器解析
CALL_RE = re.compile(r"@router\.(get|post|put|delete|patch)\((.*?)\)", re.S)
QUOTED_RE = re.compile(r"[\'\"]([^\'\"]+)[\'\"]")
TAGS_RE = re.compile(r"tags\s*=\s*\[\s*[\'\"]([^\'\"]+)[\'\"]")
# docstring 端点数声明(两种书写风格)
DECL_PATTERNS = [
    re.compile(r"(\d+)\s*个?端点"),
    re.compile(r"端点分布[（(](\d+)个?[)）]"),
]
# SQL 代码块内表名(表头行 / CREATE TABLE 语句)
TABLE_HEAD_RE = re.compile(r"^\s*([a-z_][a-z0-9_]*)\s*\(")
CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\'\"`]?([a-z_][a-z0-9_]*)", re.I
)
# 仓库层存储实体名
ENTITY_PATTERNS = [
    re.compile(r"_k\(\s*[\'\"]([a-z_][a-z0-9_]*)[\'\"]"),
    re.compile(r"_mock_store(?:\.setdefault)?\[\s*[\'\"]([a-z_][a-z0-9_]*)[\'\"]"),
    re.compile(r"store(?:\.setdefault)?\[\s*[\'\"]([a-z_][a-z0-9_]*)[\'\"]"),
]


def norm_module(name: str) -> str:
    """模块名归一化: 去编号/去-P1表尾巴/去首尾连接符"""
    name = name.strip()
    name = re.sub(r"模块\d+", "模块", name)
    name = re.sub(r"-P\d+表?$", "", name)
    return name.strip("-_ ")


def classify_doc(filename: str):
    """根目录文档 → (模块名, 类别); 不参与映射返回 None"""
    if filename in EXCLUDED_DOCS:
        return None
    stem = Path(filename).stem
    for suffix in DOC_IGNORE_SUFFIXES:
        if stem.endswith(suffix):
            return None
    for suffix, cat in DOC_SUFFIXES:
        if stem.endswith(suffix):
            return norm_module(stem[: -len(suffix)]), cat
    return None


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def scan_doc_tables(text: str) -> list:
    """提取文档 SQL 代码块中的表名(去重保序)"""
    tables, in_sql = [], False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_sql:
                in_sql = False
            elif stripped.lower().startswith("```sql"):
                in_sql = True
            continue
        if not in_sql:
            continue
        m = CREATE_TABLE_RE.search(line)
        if m:
            name = m.group(1)
            if name not in tables:
                tables.append(name)
            continue
        m = TABLE_HEAD_RE.match(line)
        if m:
            name = m.group(1)
            # 排除列定义行(行内通常有逗号结尾的约束)与关键字
            if name not in ("index", "key", "primary", "unique", "foreign", "check", "constraint") and name not in tables:
                tables.append(name)
    return tables


def scan_route_file(path: Path) -> dict:
    """解析路由文件: 声明端点数/实际端点/tags"""
    src = read_text(path)
    declared = None
    for pat in DECL_PATTERNS:
        m = pat.search(src)
        if m:
            declared = int(m.group(1))
            break
    endpoints, tags = [], set()
    for m in CALL_RE.finditer(src):
        method, body = m.group(1).upper(), m.group(2)
        pm = QUOTED_RE.search(body)
        if not pm:
            continue
        path_str = pm.group(1)
        if not path_str.startswith("/"):
            continue  # 跳过非路径首参(如 response_model)
        endpoints.append({"method": method, "path": path_str})
        tm = TAGS_RE.search(body)
        if tm:
            tags.add(tm.group(1).strip())
    return {"declared": declared, "endpoints": endpoints, "tags": tags}


def scan_repo_entities(path: Path) -> set:
    """解析仓库文件中的存储实体名"""
    src = read_text(path)
    entities = set()
    for pat in ENTITY_PATTERNS:
        entities.update(pat.findall(src))
    return entities


def table_has_entity(table: str, entities: set) -> bool:
    """启发式: 表名是否能在仓库实体中找到对应(前缀匹配)"""
    return any(table == e or table.startswith(e) for e in entities)


def md_esc(s) -> str:
    return str(s).replace("|", "\\|")


def main() -> int:
    if not ROUTES_DIR.exists():
        print("[ERROR] 未找到路由目录: %s" % ROUTES_DIR)
        return 1

    # ---------- 1. 收集根目录文档 ----------
    module_docs = {}   # 模块名 → {"design": [文件], "db": [文件]}
    unclassified = []
    for md in sorted(REPO_ROOT.glob("*.md")):
        result = classify_doc(md.name)
        if result is None:
            if md.name not in EXCLUDED_DOCS:
                unclassified.append(md.name)
            continue
        module, cat = result
        module = MODULE_MERGE.get(module, module)
        module_docs.setdefault(module, {"design": [], "db": []})[cat].append(md.name)

    # ---------- 2. 扫描路由/服务/仓库 ----------
    routes = {}        # 路由文件名 → scan 结果 + 模块归类
    route_modules = {}  # 模块名 → [路由文件名]
    infra_routes, nodoc_routes = [], []
    unmatched_routes = []
    for rf in sorted(ROUTES_DIR.glob("*_routes.py")):
        if rf.name == "__init__.py":
            continue
        info = scan_route_file(rf)
        stem = rf.name[: -len("_routes.py")]
        mods = set()
        for t in info["tags"]:
            for m in module_docs:
                if t and (t in m or m in t):
                    mods.add(m)
        hint = ROUTE_MODULE_HINTS.get(rf.name)
        if hint == "__infra__":
            infra_routes.append(rf.name)
        elif hint == "__nodoc__":
            nodoc_routes.append(rf.name)
        else:
            if not mods and hint and hint in module_docs:
                mods = {hint}
            elif not mods and hint:
                mods = {hint}
            if mods:
                for m in mods:
                    route_modules.setdefault(m, []).append(rf.name)
            else:
                unmatched_routes.append(rf.name)
        service = SERVICES_DIR / ("%s_service.py" % stem)
        repo = REPOS_DIR / ("%s_repository.py" % stem)
        info.update({
            "file": rf.name, "stem": stem,
            "service": service.name if service.exists() else None,
            "repo": repo.name if repo.exists() else None,
            "repo_entities": scan_repo_entities(repo) if repo.exists() else set(),
        })
        routes[rf.name] = info

    # 服务层无对应路由文件(内部服务或缺口, 待人工确认)
    services_without_routes = []
    if SERVICES_DIR.exists():
        route_names = {rf.name for rf in ROUTES_DIR.glob("*_routes.py")}
        for sf in sorted(SERVICES_DIR.glob("*_service.py")):
            stem = sf.stem[: -len("_service")]
            if "%s_routes.py" % stem not in route_names:
                services_without_routes.append(sf.name)

    # ---------- 3. 汇总模块行 ----------
    rows = []
    endpoint_mismatch = []
    for module in sorted(module_docs):
        docs = module_docs[module]
        rfs = route_modules.get(module, [])
        endpoints, declared_sum = [], 0
        has_declared = False
        services, repos, entities = set(), set(), set()
        for name in rfs:
            info = routes[name]
            endpoints.extend(info["endpoints"])
            if info["declared"] is not None:
                declared_sum += info["declared"]
                has_declared = True
            if info["service"]:
                services.add(info["service"])
            if info["repo"]:
                repos.add(info["repo"])
                entities |= info["repo_entities"]
        tables = []
        for doc in docs["design"] + docs["db"]:
            for t in scan_doc_tables(read_text(REPO_ROOT / doc)):
                if t not in tables:
                    tables.append(t)
        unmatched_tables = [t for t in tables if not table_has_entity(t, entities)]

        if has_declared:
            ok = declared_sum == len(endpoints)
            check = "OK" if ok else "MISMATCH"
            if not ok:
                endpoint_mismatch.append((module, declared_sum, len(endpoints), rfs))
        else:
            check = "-"
        rows.append({
            "module": module, "design": docs["design"], "db": docs["db"],
            "routes": rfs, "endpoints": endpoints, "declared": declared_sum if has_declared else None,
            "services": sorted(services), "repos": sorted(repos),
            "entities": entities, "tables": tables, "unmatched_tables": unmatched_tables,
            "check": check,
        })

    # ---------- 4. 生成报告 ----------
    total_endpoints = sum(len(i["endpoints"]) for i in routes.values())
    doc_modules_no_routes = [r["module"] for r in rows if not r["routes"]]
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = []
    lines.append("# 文档-代码同步核对报告(v8.0 阶段1·任务1)")
    lines.append("")
    lines.append("> 生成时间: %s | 生成命令: `python scripts/check-doc-code-sync.py`" % now)
    lines.append("> 本报告为自动机检结果(客观事实), 业务规则一致性见核对清单人工列。")
    lines.append("")
    lines.append("## 一、汇总")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("|------|------|")
    lines.append("| 模块设计文档(含接口文档) | %d 篇 |" % sum(len(d["design"]) for d in module_docs.values()))
    lines.append("| 数据库表结构文档 | %d 篇 |" % sum(len(d["db"]) for d in module_docs.values()))
    lines.append("| 路由文件 | %d 个 |" % len(routes))
    lines.append("| HTTP 端点总数 | %d 个 |" % total_endpoints)
    lines.append("| 端点声明数不一致 | %d 个模块 |" % len(endpoint_mismatch))
    lines.append("| 设计文档无对应路由 | %d 个模块 |" % len(doc_modules_no_routes))
    lines.append("| 路由无文档(tag+兜底均未匹配) | %d 个 |" % len(unmatched_routes))
    if unclassified:
        lines.append("| 未分类根目录文档 | %d 个 |" % len(unclassified))
    lines.append("")

    lines.append("## 二、机检:端点声明数 vs 实际数")
    lines.append("")
    if endpoint_mismatch:
        lines.append("| 模块 | 声明 | 实际 | 路由文件 |")
        lines.append("|------|------|------|----------|")
        for module, dec, act, rfs in endpoint_mismatch:
            lines.append("| %s | %d | %d | %s |" % (md_esc(module), dec, act, ", ".join(rfs)))
    else:
        lines.append("全部一致(或路由文件无端点数声明)。")
    lines.append("")

    lines.append("## 三、模块映射全景")
    lines.append("")
    lines.append("| 模块 | 设计文档 | DB文档 | 路由文件 | 端点(声明/实际) | 服务 | 仓库 | 文档表数 | 仓库实体数 |")
    lines.append("|------|----------|--------|----------|----------------|------|------|----------|------------|")
    for r in rows:
        dec = str(r["declared"]) if r["declared"] is not None else "-"
        lines.append("| %s | %s | %s | %s | %s / %d | %s | %s | %d | %d |" % (
            md_esc(r["module"]),
            md_esc(", ".join(r["design"]) or "-"),
            md_esc(", ".join(r["db"]) or "-"),
            md_esc(", ".join(r["routes"]) or "**缺**"),
            dec, len(r["endpoints"]),
            md_esc(", ".join(r["services"]) or "-"),
            md_esc(", ".join(r["repos"]) or "-"),
            len(r["tables"]), len(r["entities"]),
        ))
    lines.append("")

    lines.append("## 四、表-实体未对应清单(启发式, 供人工复核)")
    lines.append("")
    has_unmatched = False
    for r in rows:
        if r["unmatched_tables"]:
            has_unmatched = True
            lines.append("- **%s**: %s" % (md_esc(r["module"]), ", ".join("`%s`" % t for t in r["unmatched_tables"])))
    if not has_unmatched:
        lines.append("(无)")
    lines.append("")

    lines.append("## 五、缺口清单")
    lines.append("")
    lines.append("### 5.1 设计文档无对应路由(需决策: 补实现 / 文档标注分期)")
    lines.append("")
    if doc_modules_no_routes:
        for r in rows:
            if not r["routes"]:
                lines.append("- **%s**(文档: %s)" % (md_esc(r["module"]), md_esc(", ".join(r["design"] + r["db"]))))
    else:
        lines.append("(无)")
    lines.append("")
    lines.append("### 5.2 路由无文档(需补设计文档或并入既有文档)")
    lines.append("")
    if unmatched_routes:
        for name in unmatched_routes:
            info = routes[name]
            lines.append("- **%s**(端点 %d 个, tags: %s)" % (name, len(info["endpoints"]), md_esc(", ".join(sorted(info["tags"])) or "无")))
    else:
        lines.append("(无)")
    lines.append("")
    lines.append("### 5.3 预期缺口(架构文档覆盖, 无需独立文档)")
    lines.append("")
    lines.append("- 基础设施路由: %s" % (", ".join(infra_routes) or "无"))
    lines.append("- 架构文档章节覆盖: %s" % (", ".join(nodoc_routes) or "无"))
    if unclassified:
        lines.append("- 未分类文档(人工确认归类): %s" % ", ".join(unclassified))
    lines.append("")
    lines.append("### 5.4 服务层无对应路由文件(内部服务或缺口, 待人工确认)")
    lines.append("")
    if services_without_routes:
        for name in services_without_routes:
            lines.append("- %s" % name)
    else:
        lines.append("(无)")
    lines.append("")
    lines.append("### 5.5 已知部分实现(人工确认过的事实)")
    lines.append("")
    for note in KNOWN_PARTIAL_NOTES:
        lines.append("- %s" % note)
    lines.append("")
    REPORT_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ---------- 5. 生成核对清单 ----------
    cl = []
    cl.append("# 文档-代码同步核对清单(v8.0 阶段1·任务1)")
    cl.append("")
    cl.append("> 生成时间: %s | 重新生成: `python scripts/check-doc-code-sync.py`" % now)
    cl.append("")
    cl.append("## 使用说明")
    cl.append("")
    cl.append("1. 机检列(端点核对/表对应)已由脚本自动填写, 勿手改; 重新运行脚本会覆盖本文件。")
    cl.append("2. 逐模块打开「设计文档 ↔ 服务层代码」, 人工核对业务规则/阈值/字段/角色权益是否一致。")
    cl.append("3. 填写「人工核对」列与「状态」列; 状态取值: `一致` / `文档滞后` / `代码滞后` / `缺口` / `分期`。")
    cl.append("4. 结论处置: 文档滞后→修文档; 代码滞后→补代码(另开任务); 缺口→阶段4统一排期。")
    cl.append("")
    cl.append("## 模块核对表")
    cl.append("")
    cl.append("| # | 模块 | 设计文档 | 路由(端点声明/实际) | 机检:端点 | 机检:表对应 | 人工核对(规则/字段/阈值) | 状态 |")
    cl.append("|---|------|----------|---------------------|-----------|--------------|--------------------------|------|")
    for i, r in enumerate(rows, 1):
        dec = str(r["declared"]) if r["declared"] is not None else "-"
        table_check = "%d/%d" % (len(r["tables"]) - len(r["unmatched_tables"]), len(r["tables"])) if r["tables"] else "-"
        cl.append("| %d | %s | %s | %s (%s/%d) | %s | %s | 待核对 | 待核对 |" % (
            i, md_esc(r["module"]),
            md_esc(", ".join(r["design"]) or "(仅DB文档)"),
            md_esc(", ".join(r["routes"]) or "**缺路由**"),
            dec, len(r["endpoints"]),
            r["check"], table_check,
        ))
    cl.append("")
    cl.append("## 缺口决策表(设计文档无对应路由)")
    cl.append("")
    cl.append("| 模块 | 文档 | 决策(补实现/分期/归档) | 备注 |")
    cl.append("|------|------|------------------------|------|")
    for r in rows:
        if not r["routes"]:
            cl.append("| %s | %s | 待决策 | |" % (md_esc(r["module"]), md_esc(", ".join(r["design"] + r["db"]))))
    if not doc_modules_no_routes:
        cl.append("| (无) | | | |")
    cl.append("")
    cl.append("## 核对结论汇总(人工填写)")
    cl.append("")
    cl.append("| 日期 | 核对人 | 已核对模块数 | 发现问题数 | 备注 |")
    cl.append("|------|--------|--------------|------------|------|")
    cl.append("|      |        |              |            |      |")
    cl.append("")
    CHECKLIST_FILE.write_text("\n".join(cl) + "\n", encoding="utf-8")

    # ---------- 6. 控制台摘要 ----------
    print("=" * 60)
    print("[OK] 文档-代码同步核对完成")
    print("     路由文件 %d 个 / 端点 %d 个 / 模块文档 %d 组" % (
        len(routes), total_endpoints, len(module_docs)))
    print("     端点数不一致: %d | 文档无路由: %d | 路由无文档: %d" % (
        len(endpoint_mismatch), len(doc_modules_no_routes), len(unmatched_routes)))
    print("[1] 报告:   %s" % REPORT_FILE)
    print("[2] 清单:   %s" % CHECKLIST_FILE)
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
