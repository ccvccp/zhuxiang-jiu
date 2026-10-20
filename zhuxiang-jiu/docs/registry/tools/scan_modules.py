# -*- coding: utf-8 -*-
"""竹盾 T0.3 模块清单生成器（临时工具——P1 起由 zhudun index 替代并移除）

扫描 backend 模块结构(services/core/repositories/scripts)与环境开关全集,
产出 docs/registry/L0-模块清单.md:
  1) 模块明细(按类别: 调度器/灰度门控/评分器/业务服务等)
  2) 环境开关总表(代码默认值/取值域检测/回滚值推导/来源域/读取位置)
  3) 三向对齐(代码读取 ↔ .env.example 模板 ↔ 本地 .env)+ 遗漏报告
  4) 回滚命令模板(ECS nssm 通道 + 11-11 前海外 sed 通道 + 免重启特例)

安全: 凭据类开关(KEY/SECRET/TOKEN/PASSWORD/DB_URL)默认值一律掩码;
      本地 .env 只输出键名, 绝不输出值。
用法: python docs/registry/tools/scan_modules.py
"""
import io
import os
import re
import sys
from collections import OrderedDict, defaultdict

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
BACKEND = os.path.join(REPO, "backend")
OUT = os.path.join(REPO, "docs", "registry", "L0-模块清单.md")

# ---------- 环境开关读取模式(全文匹配, 支持多行调用) ----------
# os.environ.get("X", default) / os.getenv("X", default) / _env("X") / _env_or_file("X") / _load_env_var("X")
ENV_CALL_RE = re.compile(
    r'(?:os\.environ\.get|os\.getenv|_env_or_file|_load_env_var|(?<!\w)_env)\(\s*["\']([A-Z][A-Z0-9_]+)["\']\s*(?:,\s*([^)\n]*))?')
# os.environ["X"] 读取(排除赋值写入)
ENV_INDEX_RE = re.compile(r'os\.environ\[\s*["\']([A-Z][A-Z0-9_]+)["\']\s*\](?!\s*=[^=])')
# f-string 动态键名前缀: f"PREFIX_{var}" (如 AGENT75_ROLE_KILL_{role})
DYN_PREFIX_RE = re.compile(r'f["\']([A-Z][A-Z0-9_]+_)\{')
# REQUIRED/KEYS 类凭据元组(网关族循环 _env(k) 读取的键)
TUPLE_RE = re.compile(r'(?:REQUIRED|_?KEYS|CREDENTIALS?|_?CREDS)\s*=\s*\(([^)]*)\)', re.S)
TUPLE_KEY_RE = re.compile(r'["\']([A-Z][A-Z0-9_]*_[A-Z0-9_]+)["\']')  # 必须含下划线, 排除 GET/POST 等
# 常量间接: KEY_ENV = "KEY_NAME" 后经 os.environ.get(KEY_ENV) 读取
CONST_ASSIGN_RE = re.compile(r'\b([A-Z][A-Z0-9_]{2,})\s*=\s*["\']([A-Z][A-Z0-9_]*_[A-Z0-9_]+)["\']')
ENV_VAR_CALL_RE = re.compile(r'(?:os\.environ\.get|os\.getenv|(?<!\w)_env)\(\s*([A-Z][A-Z0-9_]{2,})\b')


def scan_switches(files):
    """扫描全部环境开关读取(全文匹配+行号定位+间接读取检测)"""
    switches = OrderedDict()  # key -> dict(domains:set, locs:list, defaults:set)
    dyn_prefixes = set()

    def add(key, default, src, pos, rel, domain):
        s = switches.setdefault(key, {"domains": set(), "locs": [], "defaults": set()})
        s["domains"].add(domain)
        s["locs"].append((rel, src.count("\n", 0, pos) + 1))
        if default:
            s["defaults"].add(default)

    for full, rel in files:
        try:
            src = io.open(full, encoding="utf-8-sig", errors="replace").read()
        except OSError:
            continue
        domain = domain_of(rel)
        for m in ENV_CALL_RE.finditer(src):
            add(m.group(1), (m.group(2) or "").strip().rstrip(",").strip(), src, m.start(), rel, domain)
        for m in ENV_INDEX_RE.finditer(src):
            add(m.group(1), "", src, m.start(), rel, domain)
        for m in DYN_PREFIX_RE.finditer(src):
            dyn_prefixes.add(m.group(1))
        # 网关凭据元组: 键经 REQUIRED 循环 _env(k) 读取(文件内须有环境读取语境)
        if "os.environ" in src or "_env(" in src:
            for tm in TUPLE_RE.finditer(src):
                for km in TUPLE_KEY_RE.finditer(tm.group(1)):
                    add(km.group(1), "", src, tm.start(), rel, domain)
            # 常量间接读取: KEY_ENV = "KEY_NAME" -> os.environ.get(KEY_ENV)
            const_keys = {m.group(1): m.group(2) for m in CONST_ASSIGN_RE.finditer(src)}
            if const_keys:
                for m in ENV_VAR_CALL_RE.finditer(src):
                    if m.group(1) in const_keys:
                        add(const_keys[m.group(1)], "", src, m.start(), rel, domain)
    return switches, dyn_prefixes


SECRET_RE = re.compile(r'(KEY|SECRET|TOKEN|PASSWORD|DB_URL|AK|SK)$|(_KEY|_SECRET|_TOKEN|_PASSWORD|DB_URL)')
KEYDEF_RE = re.compile(r'^#?\s*([A-Z][A-Z0-9_]+)\s*=')
TEST_RE = re.compile(r'^(test_|probe_|e2e_|smoke_|debug_|diagnose_|dump_|peek_|check_|fix_|build_|gen_|generate_|finalize_|fetch_|plot_|pick_|migrate_|clean_|embed_|inject_|demo_|f0_|judge_|band_|validate|verify_|monitor_probe)')
RUNTIME_TOP = {"main.py", "models.py", "db.py"}
CURATED = {  # 人工核定特例(来源: 生产记忆与 .env.example 批注)
    "AUTH_MODE": "compat(生产=strict,回滚=恢复兼容模式)",
    "VOICE82_ASSIST_ROLLOUT": "0(免重启:POST /api/voice82/rollout 0,Redis键优先)",
    "ATTRACT72_MODE": "assist(紧急制动走 ATTRACT72_KILL=true,KILL优先)",
    "MCP_SERVER_MODE": "off",
}


def domain_of(rel):
    """来源域分类"""
    d = rel.replace("\\", "/")
    if d.startswith("core/") or d.startswith("routes/") or d.startswith("services/") \
            or d.startswith("repositories/") or os.path.basename(d) in RUNTIME_TOP:
        return "运行时"
    if d.startswith("scripts/"):
        return "scripts"
    if TEST_RE.match(os.path.basename(d)):
        return "测试/探针"
    if d.startswith("deploy/"):
        return "部署脚本"
    return "Worker/工具"


def module_desc(src):
    m = re.search(r'^\s*"""(.+)', src, re.M)
    if m:
        return m.group(1).strip().rstrip('"').strip()
    m = re.search(r'^\s*#\s*(.+)', src, re.M)
    return m.group(1).strip() if m else ""


def service_category(fname):
    if fname.endswith("_scheduler.py"):
        return "调度器"
    if fname.endswith("_mode_service.py"):
        return "灰度门控"
    if fname.endswith("_scorer.py"):
        return "评分器"
    if fname.endswith("_registry.py"):
        return "注册表"
    if fname.endswith("_service.py"):
        return "业务服务"
    return "引擎/工具"


def read_env_keys(path):
    """解析 .env / .env.example 的键名(不含值)"""
    keys = OrderedDict()
    if not os.path.exists(path):
        return keys
    for line in io.open(path, encoding="utf-8-sig", errors="replace"):
        m = KEYDEF_RE.match(line.strip())
        if m:
            keys[m.group(1)] = True
    return keys


def collect_files():
    out = []
    for root, dirs, files in os.walk(BACKEND):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git", "node_modules", "assets", "data", "logs", "guard_baselines")]
        for f in files:
            if f.endswith(".py"):
                full = os.path.join(root, f)
                rel = os.path.relpath(full, BACKEND)
                out.append((full, rel.replace("\\", "/")))
    return sorted(out, key=lambda x: x[1])


def vocab_of(key, files_src):
    """检测开关取值域: 该键出现行附近的小写引号词面量; 词数>4 视为污染返回空集"""
    words = set()
    pat = re.compile(r'["\']([a-z_]{2,12})["\']')
    ok_words = ("off", "mock", "assist", "full", "compat", "strict", "on",
                "observe", "enforce", "mock_fallback", "real", "listen")
    for (full, rel), src in files_src.items():
        if key not in src:
            continue
        lines = src.split("\n")
        for i, line in enumerate(lines):
            if key in line:
                window = "\n".join(lines[i:i + 2])
                for w in pat.findall(window):
                    if w in ok_words:
                        words.add(w)
    if len(words) > 4:  # 命中过多=邻近开关泄漏, 不可信
        return set()
    return words


def fmt_default(dv):
    """默认值清理: 剥引号/截断/嵌套调用标记"""
    s = (dv or "").strip().rstrip(",").strip()
    if not s:
        return "-"
    if "os.environ" in s or "os.getenv" in s or "_env" in s:
        return "嵌套默认"
    s = s.strip('"\'')[:24]
    return s or "-"


def first_runtime_loc(v):
    """首处读取优先运行时域定位"""
    for rel, ln in v["locs"]:
        if domain_of(rel) == "运行时":
            return rel, ln
    return v["locs"][0]


def main():
    files = collect_files()
    files_src = {}
    for full, rel in files:
        try:
            files_src[(full, rel)] = io.open(full, encoding="utf-8-sig", errors="replace").read()
        except OSError:
            files_src[(full, rel)] = ""

    switches, dyn_prefixes = scan_switches(files)
    runtime_keys = OrderedDict((k, v) for k, v in switches.items() if "运行时" in v["domains"])
    other_keys = OrderedDict((k, v) for k, v in switches.items() if "运行时" not in v["domains"])

    # 对齐源
    example_keys = read_env_keys(os.path.join(BACKEND, ".env.example"))
    local_keys = read_env_keys(os.path.join(BACKEND, ".env"))
    all_read = set(switches.keys())

    # ---------- 模块清单 ----------
    svc = []
    for full, rel in files:
        if rel.startswith("services/") and rel != "services/__init__.py":
            svc.append((os.path.basename(rel), module_desc(files_src[(full, rel)])))
    core_mods = [(os.path.basename(rel), module_desc(files_src[(full, rel)]))
                 for full, rel in files if rel.startswith("core/") and rel != "core/__init__.py"]
    repo_mods = [(os.path.basename(rel), module_desc(files_src[(full, rel)]))
                 for full, rel in files if rel.startswith("repositories/") and rel != "repositories/__init__.py"]
    script_mods = [(os.path.basename(rel), module_desc(files_src[(full, rel)]))
                   for full, rel in files if rel.startswith("scripts/")]
    cat_stat = defaultdict(int)
    for name, _ in svc:
        cat_stat[service_category(name)] += 1

    # ---------- 生成 MD ----------
    buf = []
    buf.append("# L0-模块清单（竹盾资产图谱 · 模块与开关层）")
    buf.append("")
    buf.append("> 生成: 2026-10-20 · 任务 T0.3 · 扫描面: backend 全量 py（%d 文件）" % len(files))
    buf.append("> 生成方式: [tools/scan_modules.py](tools/scan_modules.py)；开关读取模式=os.environ.get/os.getenv/_env helper/os.environ[读]，写入口(测试桩)不计入")
    buf.append("> 对齐口径: 代码读取 ↔ [.env.example](../../backend/.env.example) 模板 ↔ 本地 .env（只比键名，凭据类掩码）")
    buf.append("> 维护: 新增/修改模块或开关后重跑扫描器+人工校对（P1 起由 `zhudun index` 自动重建）· 上游: [78号_竹盾增量管理Agent_创新规划方案.md](../78号_竹盾增量管理Agent_创新规划方案.md)")
    buf.append("")
    buf.append("## 总览")
    buf.append("")
    buf.append("| 指标 | 数值 |")
    buf.append("| --- | --- |")
    buf.append("| services/ 业务模块 | %d（调度器 %d / 灰度门控 %d / 评分器 %d / 注册表 %d / 业务服务 %d / 引擎工具 %d） |" % (
        len(svc), cat_stat["调度器"], cat_stat["灰度门控"], cat_stat["评分器"],
        cat_stat["注册表"], cat_stat["业务服务"], cat_stat["引擎/工具"]))
    buf.append("| core/ 中间件与基础件 | %d |" % len(core_mods))
    buf.append("| repositories/ 数据访问 | %d |" % len(repo_mods))
    buf.append("| scripts/ 运维种子脚本 | %d |" % len(script_mods))
    buf.append("| 环境开关总数(代码读取) | %d（运行时 %d / 附属域 %d） |" % (len(switches), len(runtime_keys), len(other_keys)))
    buf.append("| .env.example 模板键 | %d |" % len(example_keys))
    buf.append("| 本地 .env 键 | %d |" % len(local_keys))
    buf.append("")
    buf.append("> 端点层见 [L0-端点清单.md](L0-端点清单.md)（2612 端点/124 路由模块）。")
    buf.append("")

    # 模块明细
    def mod_table(title, rows):
        buf.append("### %s" % title)
        buf.append("")
        buf.append("| 文件 | 类别 | 描述 |")
        buf.append("| --- | --- | --- |")
        for name, desc in rows:
            buf.append("| %s | %s | %s |" % (name, service_category(name) if title.startswith("services") else "-", desc))
        buf.append("")

    buf.append("## 模块明细")
    buf.append("")
    mod_table("services/（业务模块族）", svc)
    mod_table("core/（中间件与基础件）", core_mods)
    mod_table("repositories/（数据访问层）", repo_mods)
    if script_mods:
        mod_table("scripts/（运维种子脚本）", script_mods)

    # 开关总表
    buf.append("## 环境开关总表")
    buf.append("")
    buf.append("回滚值推导: 检测到 off 档→off；否则 mock 档→mock；否则代码默认值。特例见回滚模板节。")
    buf.append("")

    def switch_rows(keys):
        rows = []
        for k, v in keys.items():
            defaults_disp = sorted({fmt_default(d) for d in v["defaults"]})
            default = "/".join(x for x in defaults_disp if x != "-") or "-"
            if SECRET_RE.search(k) and default != "-":
                default = "***"
            vocab = vocab_of(k, files_src)
            vocab_s = "/".join(sorted(vocab)) if vocab else "-"
            if k in CURATED:
                rb = CURATED[k]
            elif "off" in vocab:
                rb = "off"
            elif "mock" in vocab:
                rb = "mock"
            elif defaults_disp and defaults_disp != ["-"]:
                rb = defaults_disp[0]
            else:
                rb = "见代码"
            f0, l0 = first_runtime_loc(v)
            rows.append((k, default, vocab_s, rb, f0, l0, "/".join(sorted(v["domains"]))))
        return rows

    buf.append("### 运行时开关（app 进程读取）")
    buf.append("")
    buf.append("| 开关 | 代码默认 | 取值域(检测) | 回滚值 | 首处读取 | 域 |")
    buf.append("| --- | --- | --- | --- | --- | --- |")
    for k, default, vocab_s, rb, f0, l0, dom in switch_rows(runtime_keys):
        buf.append("| `%s` | %s | %s | %s | %s:%d | %s |" % (k, default, vocab_s, rb, f0, l0, dom))
    buf.append("")
    if other_keys:
        buf.append("### 附属域开关（Worker/脚本/测试读取，进程外生效）")
        buf.append("")
        buf.append("| 开关 | 代码默认 | 回滚值 | 首处读取 | 域 |")
        buf.append("| --- | --- | --- | --- | --- |")
        for k, v in other_keys.items():
            defaults_disp = sorted({fmt_default(d) for d in v["defaults"]})
            default = "/".join(x for x in defaults_disp if x != "-") or "-"
            if SECRET_RE.search(k) and default != "-":
                default = "***"
            if k in CURATED:
                rb = CURATED[k]
            elif defaults_disp and defaults_disp != ["-"]:
                rb = defaults_disp[0]
            else:
                rb = "见代码"
            f0, l0 = v["locs"][0]
            buf.append("| `%s` | %s | %s | %s:%d | %s |" % (k, default, rb, f0, l0, "/".join(sorted(v["domains"]))))
        buf.append("")

    # 三向对齐
    rt = set(runtime_keys.keys())
    miss_example = sorted(k for k in rt if k not in example_keys)
    dyn_read = sorted(k for k in example_keys if k not in all_read
                      and any(k.startswith(p) for p in dyn_prefixes))
    dead_example = sorted(k for k in example_keys
                          if k not in all_read and k not in dyn_read)
    local_only = sorted(k for k in local_keys if k not in all_read
                        and not any(k.startswith(p) for p in dyn_prefixes))
    buf.append("## 三向对齐报告（验收: 开关名对齐无遗漏）")
    buf.append("")
    buf.append("> 口径说明: .env.example 模板的定位是**外部依赖凭证域**; 18 大模型族运行时开关历史上无任何入库记录, 本清单即其首次权威登记。")
    buf.append("")
    buf.append("| 检查 | 结果 |")
    buf.append("| --- | --- |")
    buf.append("| 运行时开关已入 .env.example 模板 | %d / %d |" % (len(rt) - len(miss_example), len(rt)))
    buf.append("| 运行时开关未入模板（首次以本清单登记, 新增/修改开关须同步本清单——竹盾 M2 检查清单映射项） | %d |" % len(miss_example))
    buf.append("| 模板键动态键名读取（f-string 前缀拼接, 静态不可直达） | %d |" % len(dyn_read))
    buf.append("| 模板键无任何代码读取（死模板键, 疑废弃或预留未实现, 人工确认后处置） | %d |" % len(dead_example))
    buf.append("| 本地 .env 键无代码读取（外部进程消费） | %d |" % len(local_only))
    buf.append("")
    if miss_example:
        buf.append("**未入模板的运行时开关**（运行时开关全集权威登记, 每项附首读定位）:")
        buf.append("")
        buf.append("```")
        for k in miss_example:
            f0, l0 = first_runtime_loc(runtime_keys[k])
            buf.append("%s  (首读 %s:%d)" % (k, f0, l0))
        buf.append("```")
        buf.append("")
    if dyn_read:
        buf.append("**动态键名读取**（按 f-string 前缀匹配登记）:")
        buf.append("")
        buf.append("```")
        for k in dyn_read:
            p = next(p for p in dyn_prefixes if k.startswith(p))
            buf.append("%s  (前缀 %s*)" % (k, p))
        buf.append("```")
        buf.append("")
    if dead_example:
        buf.append("**模板死键**（.env.example 有但全代码无读取）:")
        buf.append("")
        buf.append("```")
        for k in dead_example:
            buf.append(k)
        buf.append("```")
        buf.append("")
    if local_only:
        buf.append("**本地 .env 专属键**（后端代码不读, 由 Worker/MCP 等外部进程消费）:")
        buf.append("")
        buf.append("```")
        for k in local_only:
            buf.append(k)
        buf.append("```")
        buf.append("")

    # 回滚模板
    buf.append("## 回滚命令模板（生产两通道）")
    buf.append("")
    buf.append("| 通道 | 操作 | 适用期 |")
    buf.append("| --- | --- | --- |")
    buf.append("| ECS（现生产, nssm 服务） | 修改 backend8147 的 AppEnvironmentExtra（**nssm 多变量须 \\0 分隔**, 参见 [deploy/ecs8147-envfix.ps1](../../deploy/ecs8147-envfix.ps1)）→ `C:\\nginx\\nssm.exe restart backend8147` | 长期 |")
    buf.append("| 海外（legacy, 11-11 退役） | `sed -i 's/^<KEY>=.*/<KEY>=<回滚值>/' /opt/zhuxiang/.env && docker compose up -d backend` | 至 2026-11-11 |")
    buf.append("")
    buf.append("特例（人工核定）:")
    buf.append("")
    for k, note in CURATED.items():
        buf.append("- `%s` → %s" % (k, note))
    buf.append("- 凭据类开关（*_KEY/*_SECRET/*_TOKEN/*_PASSWORD/*_DB_URL）: 只经部署平台密钥注入, 回滚=移除注入值; 严禁入仓库/日志")
    buf.append("")

    io.open(OUT, "w", encoding="utf-8").write("\n".join(buf))
    print("OK switches=%d runtime=%d other=%d example=%d local=%d miss_example=%d dead_example=%d svc=%d core=%d repo=%d scripts=%d" % (
        len(switches), len(runtime_keys), len(other_keys), len(example_keys), len(local_keys),
        len(miss_example), len(dead_example), len(svc), len(core_mods), len(repo_mods), len(script_mods)))


if __name__ == "__main__":
    main()
