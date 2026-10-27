"""IMP-12b：为脚本式测试批量插入文件头 banner，并出具机器可读清单。

# [进化 2026-10-27] IMP-12 落地工具（批次 0）。
# 甄别依据（权威口径 = pytest 实测，AST 仅作结构解释）：
#   · deploy/tmp/collect_all.txt      pytest --collect-only 全量：2703 tests / 85 文件
#   · deploy/tmp/scriptstyle_tests.py v2 AST 判据：COLLECTABLE 85 / SCRIPT_STYLE 453 / ORPHAN 1
#   · deploy/tmp/xcheck_collect.py    交叉校验：false_pos=0 false_neg=0（判据可信）
# v1 判据（顶层出现 class Test* 即视为可收集）已被实测证伪：test_xiaozhu_p49_1.py
#   含 6 个 Test* 类，但类内方法均不叫 test_*，pytest collect 0 / exit 5。
# 本脚本安全性约束：
#   1. 纯注释插入，零逻辑改动；不规范化行尾、不改 BOM 状态。
#   2. 每个文件插入后立即 ast.parse 自检 + verify_banner_position 位置断言，
#      失败即回滚原字节并中止（首轮曾靠此安全网救回 2 个文件）。
#   3. 幂等且自愈：每次先 strip_banner 摘除旧 banner 再重插，故重复执行既能
#      跳过正确文件（字节不变），也能修正历史上插错位置的文件。
#   4. banner 文案内禁止出现 noqa 指令字面量（IMP-18 教训：ruff 会当真指令解析）。
#   5. ★★★ 插入点只按「行边界」计算，严禁使用 AST 的 col_offset/end_col_offset：
#      CPython 的列偏移是 UTF-8 字节偏移，而 str 索引是字符偏移，docstring 含
#      中文时二者不等，会算出越界插入点。首轮据此损坏过 test_xiaozhu_smart.py /
#      test_xiaozhu_tap_fix.py（均单行中文 docstring），并可能把已插入的 banner
#      静默放到错误位置——ast.parse 只验语法、不验位置，故必须另加位置断言。
# 期望计数基线只填本轮已实测的 8 套（用户裁定 2026-10-27），其余标 pending，
#   绝不写入未验证的数字。
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

TMP = Path(__file__).parent
BACKEND = TMP.parent.parent / "backend"
MANIFEST = BACKEND / "scriptstyle_tests_manifest.json"
EVIDENCE = "语音交互修复与多模型接入优化总结报告-20261027.md §5.1 语音全链路 8 套实测表"
MEASURED_AT = "2026-10-27"

BANNER_MARK = "[SCRIPT-STYLE TEST | 脚本式测试]"
BANNER_LINES = [
    "# ---------------------------------------------------------------------------",
    f"# {BANNER_MARK}  pytest 收集为 0 属预期（IMP-12 台账文件）。",
    "#   运行方式 : python {name}        （cwd 必须是 zhuxiang-jiu/backend）",
    "#   结果判定 : 看脚本尾部打印的 pass/fail 计数，不要看 pytest 输出。",
    "#   勿误判   : pytest --collect-only 对本文件收集 0 条并以退出码 5",
    "#              「no tests collected」结束——这是本文件的正常形态。",
    "#              禁止据此判定测试损坏，禁止把退出码 5 当失败上报。",
    "#   成因     : 用例以 record()/模块级计数函数组织，或 Test* 类内方法未命名",
    "#              为 test_*，不满足 pytest.ini 的 python_functions=test_* 规则。",
    "#   台账     : backend/scriptstyle_tests_manifest.json（机器可读）",
    "#   闸门     : backend/test_scriptstyle_gate.py 校验清单一致性与 banner 存在性",
    "# ---------------------------------------------------------------------------",
]

# 本轮已实测的 8 套语音测试（pass/fail），来源见 EVIDENCE。
MEASURED: dict[str, tuple[int, int]] = {
    "test_xiaozhu_voice_p1.py": (15, 0),
    "test_xiaozhu_p49_1.py": (34, 0),
    "test_xiaozhu_p49_3.py": (29, 0),
    "test_xiaozhu_p49_4.py": (41, 0),
    "test_xiaozhu_smart.py": (53, 0),
    "test_xiaozhu_affirm.py": (14, 0),
    "test_xiaozhu_joyvoice.py": (81, 0),
    "test_xiaozhu_confirm_freeze.py": (5, 0),
}

# 人工确认为共享 helper（被脚本式测试 import，本就不该被收集、也不该直跑）。
CONFIRMED_HELPER = {
    "test_support.py": "直跑测试自举模块(AUTH-TEST-01 P0)：设 LOCK_MODE/STORE_MODE/"
                       "AUTH_COMPAT_TRUST_HEADERS 并提供 mint_token；被 453 个脚本式"
                       "测试 import。处置建议见 IMP-13（改名 support_*.py 或加入 "
                       "conftest collect_ignore），不得插入脚本式 banner。",
}

PENDING_NOTE = ("本轮未实测：依赖 Redis/真实服务或属历史批次。按用户裁定(2026-10-27)"
                "基线只填已实测项，不写入未验证的数字。")


def line_offsets(text: str) -> list[int]:
    offs = [0]
    i = text.find("\n")
    while i >= 0:
        offs.append(i + 1)
        i = text.find("\n", i + 1)
    return offs


def is_docstring(node: ast.stmt) -> bool:
    return (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str))


def insert_offset(text: str, tree: ast.Module, offs: list[int]) -> int:
    """banner 插入点（行边界）：module docstring 之后 > shebang/coding 之后 > 文件首。

    只用 lineno、绝不用 col_offset：CPython 的 col_offset/end_col_offset 是
    UTF-8 字节偏移而非字符偏移，docstring 含中文时会算出越界的插入点
    （实测 test_xiaozhu_smart.py / test_xiaozhu_tap_fix.py 因此被安全网回滚）。
    """
    def after_line(idx0: int) -> int:
        return offs[idx0 + 1] if idx0 + 1 < len(offs) else len(text)

    if tree.body and is_docstring(tree.body[0]):
        return after_line(tree.body[0].end_lineno - 1)
    for idx, line in enumerate(text.splitlines()[:2]):
        if line.startswith("#!") or "coding" in line:
            return after_line(idx)
    return 0


def build_banner(name: str, eol: str, rest: str) -> str:
    body = [ln.replace("{name}", name) for ln in BANNER_LINES]
    # 插入点已是行首；若紧随其后本来就是空行则不再补空行，避免多余 diff。
    tail = eol if rest.startswith(("\n", "\r\n")) else eol + eol
    return eol.join(body) + tail


def strip_banner(text: str, name: str) -> str:
    """摘除已有 banner，使重跑能修正错误位置（对已插入文件同样幂等）。

    逐行保留原行尾，绝不规范化混合行尾。
    """
    tpl = {ln.replace("{name}", name) for ln in BANNER_LINES}
    kept = [ln for ln in text.splitlines(keepends=True)
            if ln.rstrip("\r\n") not in tpl]
    return "".join(kept)


def process(path: Path) -> tuple[str, dict | None]:
    raw = path.read_bytes()
    has_bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    if BANNER_MARK in text:
        text = strip_banner(text, path.name)
    tree = ast.parse(text)

    n = collectable_count(tree)
    if n > 0:
        return "COLLECTABLE", None

    if has_main_guard(tree):
        category = "SCRIPT_STYLE"
        note = "" if path.name in MEASURED else PENDING_NOTE
    elif path.name in CONFIRMED_HELPER:
        category = "HELPER_MODULE"
        note = CONFIRMED_HELPER[path.name]
    else:
        category = "ORPHAN_UNVERIFIED"
        note = "收集为 0 且无 __main__ 入口，未经人工确认，待 IMP-13 处置。"

    if category != "SCRIPT_STYLE":
        return category, entry(path, category, text, note, False)

    eol = "\r\n" if "\r\n" in text[:4096] else "\n"
    off = insert_offset(text, tree, line_offsets(text))
    new_text = text[:off] + build_banner(path.name, eol, text[off:]) + text[off:]
    try:
        ast.parse(new_text)
    except SyntaxError as exc:
        print(f"  ROLLBACK {path.name}: banner 插入后语法错误 {exc.lineno} {exc.msg}")
        return "ROLLBACK", None
    verify_banner_position(new_text, path.name)
    out = new_text.encode("utf-8")
    if has_bom:
        out = b"\xef\xbb\xbf" + out
    path.write_bytes(out)
    return category, entry(path, category, new_text, note, True)


def verify_banner_position(text: str, name: str) -> None:
    """banner 必须紧贴 module docstring 之后（无 docstring 则在文件首两行内）。

    注意：marker 位于 banner 的第 2 行，第 1 行是分隔线，故须回退两行才是
    banner 之前的原始内容。
    """
    lines = [ln.rstrip("\r\n") for ln in text.splitlines()]
    mark_idx = next(i for i, ln in enumerate(lines) if BANNER_MARK in ln)
    banner_first = mark_idx - 1
    if lines[banner_first].strip("#- ") != "":
        raise AssertionError(f"{name}: banner 首行不是分隔线 {lines[banner_first]!r}")
    if banner_first == 0:
        return
    head = lines[banner_first - 1].strip()
    quotes = ('"""', "'''")
    ok = head.startswith(quotes) or head.endswith(quotes) \
        or head.startswith("#!") or "coding" in head
    if not ok:
        raise AssertionError(f"{name}: banner 位置异常，前一行={head!r}")


def entry(path: Path, category: str, text: str, note: str, has_banner: bool) -> dict:
    m = MEASURED.get(path.name)
    return {
        "file": path.name,
        "category": category,
        "lines": len(text.splitlines()),
        "run_cmd": f"python {path.name}" if category == "SCRIPT_STYLE" else None,
        "cwd": "zhuxiang-jiu/backend",
        "collectable_by_pytest": 0,
        "pytest_exit_code": 5,
        "has_banner": has_banner,
        "banner_marker": BANNER_MARK if category == "SCRIPT_STYLE" else None,
        "asyncio_run": "asyncio.run" in text,
        "baseline_status": "measured" if m else "pending",
        "pass_count": m[0] if m else None,
        "fail_count": m[1] if m else None,
        "measured_at": MEASURED_AT if m else None,
        "evidence": EVIDENCE if m else None,
        "note": note,
    }


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


def main() -> int:
    counts = {"COLLECTABLE": 0, "SCRIPT_STYLE": 0, "HELPER_MODULE": 0,
              "ORPHAN_UNVERIFIED": 0, "ROLLBACK": 0}
    changed = unchanged = 0
    entries: list[dict] = []

    for path in sorted(BACKEND.glob("test_*.py")):
        before = path.read_bytes()
        category, ent = process(path)
        counts[category] = counts.get(category, 0) + 1
        if category == "SCRIPT_STYLE":
            # 先 strip 再 insert，故 changed 同时涵盖「新插入」与「位置被修正」。
            if path.read_bytes() != before:
                changed += 1
            else:
                unchanged += 1
        if ent:
            entries.append(ent)

    measured = [e for e in entries if e["baseline_status"] == "measured"]
    manifest = {
        "schema_version": 1,
        "generated_at": MEASURED_AT,
        "generated_by": "deploy/tmp/build_scriptstyle_manifest.py (IMP-12b)",
        "banner_marker": BANNER_MARK,
        "pytest_ini_rules": {"python_files": "test_*.py", "python_classes": "Test*",
                             "python_functions": "test_*"},
        "source_of_truth": {
            "pytest_collect_only": "deploy/tmp/collect_all.txt (2713 tests / 86 files)",
            "ast_scan_v2": "deploy/tmp/scriptstyle_tests.json",
            "cross_check": "deploy/tmp/xcheck_collect.py (false_pos=0 false_neg=0)",
        },
        "summary": {
            "scanned_test_files": sum(counts.values()),
            "collectable_by_pytest": counts["COLLECTABLE"],
            # 2026-10-27 实测：pytest --collect-only -q → 2713 tests collected / 68.73s。
            # 原值 2703 为 IMP-12c 闸门(test_scriptstyle_gate.py, 10 条用例)落地前所测。
            "pytest_collected_nodes": 2713,
            "script_style": counts["SCRIPT_STYLE"],
            "helper_module": counts["HELPER_MODULE"],
            "orphan_unverified": counts["ORPHAN_UNVERIFIED"],
            "entries_total": len(entries),
            "baseline_measured": len(measured),
            "baseline_pending": len(entries) - len(measured),
            "rollback": counts["ROLLBACK"],
        },
        "entries": entries,
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")

    print(f"counts={counts}")
    print(f"banner_changed={changed} unchanged={unchanged} "
          f"rollback={counts['ROLLBACK']}")
    print(f"entries={len(entries)} measured={len(measured)} "
          f"pending={len(entries) - len(measured)}")
    print(f"manifest={MANIFEST}")
    return 1 if counts["ROLLBACK"] else 0


if __name__ == "__main__":
    sys.exit(main())
