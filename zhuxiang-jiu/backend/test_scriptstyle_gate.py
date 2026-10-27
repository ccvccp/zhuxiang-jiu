"""IMP-12c 闸门：脚本式测试台账（文件头 banner + 机器可读清单）一致性校验。

# [进化 2026-10-27] 复盘报告 §10 改进项 IMP-12 批次 0 落地件（12c）。
# 背景：backend/ 下 539+ 个 test_*.py 中有 453 个是"脚本式测试"——用
#   record()/模块级计数函数组织，或 Test* 类内方法未命名为 test_*，pytest
#   对其收集 0 条并以退出码 5「no tests collected」结束。历史上多次被误判为
#   "测试损坏"，浪费大量排查工时。
# 本闸门把该认知固化为可执行断言（三件事）：
#   1. 台账完整：AST 判定的非可收集 test_*.py 集合 == 清单 entries 集合（双向）。
#   2. banner 存在且位置正确：磁盘上确有 marker，且紧贴 module docstring 之后。
#   3. 计数口径可信：summary 自洽、measured 基线只含已实测项（棘轮不回退）。
# 判据必须与 deploy/tmp/build_scriptstyle_manifest.py 保持同步（同一套
#   collectable_count / has_main_guard），否则清单重生成后本闸门会失败。
# 本文件自身是普通可收集 pytest 用例（有 test_* 函数），不在台账内、不带 banner。
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent
MANIFEST = BACKEND / "scriptstyle_tests_manifest.json"
GATE_FILE = Path(__file__).resolve().name

# 拼接而非直写完整字面量：避免任何"按 marker 全文检索"的审计把本闸门文件
# 误计为已插 banner 的脚本式测试。
BANNER_MARK = "[SCRIPT-STYLE TEST | " + "脚本式测试]"

# 本轮（2026-10-27）已实测 8 套语音链路脚本式测试，作为棘轮下限：
# 后续只允许增加 measured 项，不允许把已实测基线退回 pending。
MEASURED_FLOOR = 8

SCRIPT_CATEGORIES = {"SCRIPT_STYLE", "HELPER_MODULE", "ORPHAN_UNVERIFIED"}

REQUIRED_TOP_KEYS = {
    "schema_version", "generated_at", "generated_by", "banner_marker",
    "pytest_ini_rules", "source_of_truth", "summary", "entries",
}
REQUIRED_SUMMARY_KEYS = {
    "scanned_test_files", "collectable_by_pytest", "pytest_collected_nodes",
    "script_style", "helper_module", "orphan_unverified", "entries_total",
    "baseline_measured", "baseline_pending", "rollback",
}
REQUIRED_ENTRY_KEYS = {
    "file", "category", "lines", "run_cmd", "cwd", "collectable_by_pytest",
    "pytest_exit_code", "has_banner", "banner_marker", "asyncio_run",
    "baseline_status", "pass_count", "fail_count", "measured_at",
    "evidence", "note",
}


# --- AST 判据（与 build_scriptstyle_manifest.py 同源） ----------------------


def has_main_guard(tree: ast.Module) -> bool:
    for node in tree.body:
        if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
            continue
        left = node.test.left
        if not isinstance(left, ast.Name) or left.id != "__name__":
            continue
        for comp in node.test.comparators:
            if isinstance(comp, ast.Constant) and comp.value == "__main__":
                return True
    return False


def collectable_count(tree: ast.Module) -> int:
    n = 0
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test"):
                n += 1
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            if any(isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and m.name == "__init__" for m in node.body):
                continue
            n += sum(1 for m in node.body
                     if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and m.name.startswith("test"))
    return n


# --- 扫描结果载体（只留断言所需字段，避免把 500+ 文件正文常驻内存） ---------


@dataclass(frozen=True)
class Scanned:
    name: str
    line_count: int
    collectable: int
    main_guard: bool
    uses_asyncio_run: bool
    marker_lines: tuple[int, ...]
    sep_line: str | None
    prev_line: str | None


@dataclass(frozen=True)
class ScanResult:
    files: dict[str, Scanned]
    errors: list[str]


@pytest.fixture(scope="module")
def scan() -> ScanResult:
    files: dict[str, Scanned] = {}
    errors: list[str] = []
    for path in sorted(BACKEND.glob("test_*.py")):
        try:
            text = path.read_bytes().decode("utf-8-sig")
            tree = ast.parse(text)
        except (OSError, SyntaxError, UnicodeDecodeError) as exc:
            errors.append(f"{path.name}: {exc}")
            continue
        lines = text.splitlines()
        marks = tuple(i + 1 for i, ln in enumerate(lines) if BANNER_MARK in ln)
        idx = marks[0] - 1 if marks else None
        files[path.name] = Scanned(
            name=path.name,
            line_count=len(lines),
            collectable=collectable_count(tree),
            main_guard=has_main_guard(tree),
            uses_asyncio_run="asyncio.run" in text,
            marker_lines=marks,
            sep_line=lines[idx - 1] if idx is not None and idx >= 1 else None,
            prev_line=lines[idx - 2] if idx is not None and idx >= 2 else None,
        )
    return ScanResult(files=files, errors=errors)


@pytest.fixture(scope="module")
def manifest() -> dict:
    assert MANIFEST.is_file(), f"缺少脚本式测试清单：{MANIFEST}"
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def entries_of(manifest: dict, category: str) -> list[dict]:
    return [e for e in manifest["entries"] if e["category"] == category]


# --- 闸门用例 --------------------------------------------------------------


def test_all_test_files_parse(scan: ScanResult) -> None:
    assert not scan.errors, "以下 test_*.py 无法解析（E9 级问题，先修语法）：\n" + "\n".join(scan.errors)
    assert scan.files, f"{BACKEND} 下未扫到任何 test_*.py"


def test_manifest_schema(manifest: dict) -> None:
    assert manifest["schema_version"] == 1
    assert manifest["banner_marker"] == BANNER_MARK, "清单声明的 banner marker 与闸门不一致"
    missing = REQUIRED_TOP_KEYS - set(manifest)
    assert not missing, f"清单缺少顶层字段：{sorted(missing)}"
    missing_s = REQUIRED_SUMMARY_KEYS - set(manifest["summary"])
    assert not missing_s, f"清单 summary 缺少字段：{sorted(missing_s)}"
    assert manifest["entries"], "清单 entries 为空"


def test_entries_wellformed(manifest: dict) -> None:
    seen: set[str] = set()
    for e in manifest["entries"]:
        name = e.get("file", "<无 file 字段>")
        missing = REQUIRED_ENTRY_KEYS - set(e)
        assert not missing, f"{name}: 缺少字段 {sorted(missing)}"
        assert name not in seen, f"{name}: 清单中重复登记"
        seen.add(name)
        assert e["category"] in SCRIPT_CATEGORIES, f"{name}: 未知 category {e['category']!r}"
        assert e["baseline_status"] in {"measured", "pending"}, f"{name}: 未知 baseline_status"
        assert (BACKEND / name).is_file(), f"{name}: 清单指向的文件不存在（台账过期）"
        assert e["collectable_by_pytest"] == 0, f"{name}: 台账只登记 pytest 收集为 0 的文件"
        assert e["pytest_exit_code"] == 5, f"{name}: 退出码应为 5(no tests collected)"
        assert e["cwd"] == "zhuxiang-jiu/backend", f"{name}: cwd 口径错误 {e['cwd']!r}"


def test_summary_self_consistent(manifest: dict) -> None:
    s = manifest["summary"]
    entries = manifest["entries"]
    assert s["rollback"] == 0, "存在 banner 插入回滚，须重跑生成器修复"
    assert s["orphan_unverified"] == 0, "存在未经人工确认的孤儿文件，待 IMP-13 处置后清零"
    assert s["entries_total"] == len(entries)
    for key in ("script_style", "helper_module", "orphan_unverified"):
        actual = sum(1 for e in entries if e["category"] == key.upper())
        assert s[key] == actual, f"summary.{key}={s[key]} 与 entries 实数 {actual} 不符"
    assert s["scanned_test_files"] == s["collectable_by_pytest"] + s["entries_total"], (
        "scanned_test_files 应等于 可收集数 + 台账条目数"
    )
    measured = sum(1 for e in entries if e["baseline_status"] == "measured")
    assert s["baseline_measured"] == measured
    assert s["baseline_pending"] == len(entries) - measured


def test_ast_population_matches_manifest(manifest: dict, scan: ScanResult) -> None:
    """核心断言：AST 扫出的非可收集 test_*.py 100% 在清单内，且清单无过期条目。"""
    non_collectable = {n for n, f in scan.files.items() if f.collectable == 0}
    ledger = {e["file"] for e in manifest["entries"]}
    unregistered = non_collectable - ledger
    assert not unregistered, (
        "以下脚本式测试未登记入清单，请重跑 deploy/tmp/build_scriptstyle_manifest.py：\n"
        + "\n".join(sorted(unregistered))
    )
    stale = ledger - non_collectable
    assert not stale, (
        "以下清单条目现已可被 pytest 收集（台账过期），请重跑生成器：\n" + "\n".join(sorted(stale))
    )


def test_category_matches_ast_evidence(manifest: dict, scan: ScanResult) -> None:
    bad: list[str] = []
    for e in manifest["entries"]:
        f = scan.files[e["file"]]
        name = e["file"]
        if f.main_guard and e["category"] != "SCRIPT_STYLE":
            bad.append(f"{name}: 有 __main__ 入口却登记为 {e['category']}")
        elif not f.main_guard and e["category"] == "SCRIPT_STYLE":
            bad.append(f"{name}: 无 __main__ 入口却登记为 SCRIPT_STYLE")
        if e["lines"] != f.line_count:
            bad.append(f"{name}: 清单 lines={e['lines']} 与磁盘 {f.line_count} 不符")
        if e["asyncio_run"] != f.uses_asyncio_run:
            bad.append(f"{name}: 清单 asyncio_run={e['asyncio_run']} 与磁盘不符")
        if e["category"] == "SCRIPT_STYLE":
            if e["run_cmd"] != f"python {name}":
                bad.append(f"{name}: run_cmd 口径错误 {e['run_cmd']!r}")
            if e["banner_marker"] != BANNER_MARK:
                bad.append(f"{name}: banner_marker 错误")
        else:
            if e["banner_marker"] is not None or e["run_cmd"] is not None:
                bad.append(f"{name}: 非脚本式条目不应有 banner_marker/run_cmd")
    assert not bad, "\n".join(bad)


def test_banner_present_on_disk(manifest: dict, scan: ScanResult) -> None:
    missing, dup, flag_mismatch, stray = [], [], [], []
    for e in manifest["entries"]:
        f = scan.files[e["file"]]
        name = e["file"]
        if e["category"] == "SCRIPT_STYLE":
            if not f.marker_lines:
                missing.append(name)
            elif len(f.marker_lines) > 1:
                dup.append(f"{name}: 第 {list(f.marker_lines)} 行重复 banner")
            if not e["has_banner"]:
                flag_mismatch.append(f"{name}: 清单 has_banner=false 但应为 true")
        elif f.marker_lines:
            stray.append(f"{name}: {e['category']} 文件被误插 banner（第 {list(f.marker_lines)} 行）")
        elif e["has_banner"]:
            flag_mismatch.append(f"{name}: 清单 has_banner=true 但磁盘无 banner")
    assert not missing, "以下脚本式测试缺少文件头 banner：\n" + "\n".join(missing)
    assert not dup, "以下文件存在重复 banner：\n" + "\n".join(dup)
    assert not stray, "以下非脚本式文件被误插 banner：\n" + "\n".join(stray)
    assert not flag_mismatch, "清单 has_banner 与磁盘不一致：\n" + "\n".join(flag_mismatch)


def test_banner_position_is_valid(manifest: dict, scan: ScanResult) -> None:
    """banner 必须紧贴 module docstring 之后（无 docstring 则在文件首）。

    marker 位于 banner 第 2 行、第 1 行是分隔线，故取 sep_line/prev_line 判定。
    历史上因用 AST 的 col_offset（UTF-8 字节偏移，与 str 字符偏移不等）算插入点，
    把 banner 静默放到了错误位置，ast.parse 验不出来——此断言即为此而设。
    """
    quotes = ('"""', "'''")
    bad: list[str] = []
    for e in entries_of(manifest, "SCRIPT_STYLE"):
        f = scan.files[e["file"]]
        if not f.marker_lines:
            continue  # 由 test_banner_present_on_disk 报缺
        if f.sep_line is None or f.sep_line.strip("#- ") != "":
            bad.append(f"{e['file']}: banner 首行不是分隔线 {f.sep_line!r}")
            continue
        if f.marker_lines[0] == 2:
            continue  # 分隔线在第 1 行 → banner 位于文件首，合法
        prev = (f.prev_line or "").strip()
        ok = prev.startswith(quotes) or prev.endswith(quotes) \
            or prev.startswith("#!") or "coding" in prev
        if not ok:
            bad.append(f"{e['file']}: banner 前一行既非 docstring 结尾也非 shebang/coding：{prev!r}")
    assert not bad, "banner 位置异常：\n" + "\n".join(bad)


def test_baseline_only_measured(manifest: dict) -> None:
    """期望计数基线只允许来自实测（用户裁定 2026-10-27），绝不写未验证数字。"""
    measured = entries_of_measured(manifest)
    assert len(measured) >= MEASURED_FLOOR, (
        f"已实测基线不得回退：当前 {len(measured)} 套 < 下限 {MEASURED_FLOOR} 套"
    )
    for e in measured:
        name = e["file"]
        assert isinstance(e["pass_count"], int) and not isinstance(e["pass_count"], bool), \
            f"{name}: measured 项 pass_count 必须是整数"
        assert isinstance(e["fail_count"], int) and not isinstance(e["fail_count"], bool), \
            f"{name}: measured 项 fail_count 必须是整数"
        assert e["pass_count"] >= 0 and e["fail_count"] >= 0, f"{name}: 计数为负"
        assert e["measured_at"], f"{name}: measured 项缺 measured_at"
        assert e["evidence"], f"{name}: measured 项缺 evidence（实测出处）"
    for e in manifest["entries"]:
        if e["baseline_status"] != "pending":
            continue
        assert e["pass_count"] is None and e["fail_count"] is None, \
            f"{e['file']}: pending 项不得写入未验证的计数"
        assert e["measured_at"] is None and e["evidence"] is None, \
            f"{e['file']}: pending 项不得有 measured_at/evidence"
        assert e["note"], f"{e['file']}: pending 项必须写明未实测原因"


def entries_of_measured(manifest: dict) -> list[dict]:
    return [e for e in manifest["entries"] if e["baseline_status"] == "measured"]


def test_gate_file_itself_is_collectable(manifest: dict, scan: ScanResult) -> None:
    """自检：闸门文件必须可被 pytest 收集，否则自己也会被当成脚本式测试。"""
    assert GATE_FILE in scan.files, f"扫描未覆盖 {GATE_FILE}"
    f = scan.files[GATE_FILE]
    assert f.collectable > 0, "闸门文件自身收集为 0，命名或结构有误"
    assert f.marker_lines == (), "闸门文件不应含 banner marker 字面量"
    assert GATE_FILE not in {e["file"] for e in manifest["entries"]}, "闸门文件不应登记入台账"
