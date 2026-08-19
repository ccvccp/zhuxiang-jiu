#!/bin/sh
# ============================================================
# test-bracket-check.sh - JS 括号匹配静态检查单元测试 (v2)
# ------------------------------------------------------------
# 用法: sh scripts/test-bracket-check.sh
# 覆盖用例 (15 cases):
#   TC1:  括号/花括号均匹配           -> PASS
#   TC2:  缺少闭括号 )                -> FAIL (parens)
#   TC3:  多余闭括号 )                -> FAIL (parens)
#   TC4:  缺少闭花括号 }              -> FAIL (braces)
#   TC5:  多余闭花括号 }              -> FAIL (braces)
#   TC6:  均不匹配                    -> FAIL (both)
#   TC7:  空文件                       -> PASS
#   TC8:  无括号代码                   -> PASS
#   TC9:  嵌套括号(平衡)              -> PASS
#   TC10: 字符串内含括号(平衡,不计数)  -> PASS
#   TC11: 字符串内不平衡括号(不计数)   -> PASS (不误报)
#   TC12: 行注释内不平衡括号(不计数)   -> PASS (不误报)
#   TC13: 块注释内不平衡括号(不计数)   -> PASS (不误报)
#   TC14: 方括号匹配                   -> PASS
#   TC15: 方括号不匹配                 -> FAIL (brackets)
# ============================================================

. "$(dirname "$0")/test-lib.sh"
tl_init

echo "=========================================="
echo "  JS 括号匹配静态检查单元测试 v2"
echo "  (awk 单次遍历, 字符串/注释感知)"
echo "=========================================="

# TC1: 均匹配
tl_run_bracket_case "TC1" "balanced parens+braces" \
    'function f() { return (1 + 2); }' \
    "PASS"

# TC2: 缺少闭括号
tl_run_bracket_case "TC2" "missing close paren" \
    'function f( { return 1; }' \
    "FAIL"

# TC3: 多余闭括号
tl_run_bracket_case "TC3" "extra close paren" \
    'function f() { return 1); }' \
    "FAIL"

# TC4: 缺少闭花括号
tl_run_bracket_case "TC4" "missing close brace" \
    'function f() { return 1;' \
    "FAIL"

# TC5: 多余闭花括号
tl_run_bracket_case "TC5" "extra close brace" \
    'function f() { return 1; }}' \
    "FAIL"

# TC6: 均不匹配
tl_run_bracket_case "TC6" "both unbalanced" \
    'function f( { return 1;' \
    "FAIL"

# TC7: 空文件
tl_run_bracket_case "TC7" "empty file" \
    '' \
    "PASS"

# TC8: 无括号
tl_run_bracket_case "TC8" "no brackets at all" \
    'var x = 1;' \
    "PASS"

# TC9: 嵌套(平衡)
tl_run_bracket_case "TC9" "nested balanced" \
    'function f() { if (true) { return (1 + (2 * 3)); } }' \
    "PASS"

# TC10: 字符串内含平衡括号(不计数)
tl_run_bracket_case "TC10" "balanced brackets in string" \
    'var s = "test (value) {key} [idx]";' \
    "PASS"

# TC11: 字符串内含不平衡括号(不计数, 不误报)
tl_run_bracket_case "TC11" "unbalanced in string (no false positive)" \
    'var s = "unmatched ( bracket"; var t = "also { unmatched";' \
    "PASS"

# TC12: 行注释内不平衡括号(不计数)
tl_run_bracket_case "TC12" "unbalanced in line comment" \
    'var x = 1; // function old( { return;' \
    "PASS"

# TC13: 块注释内不平衡括号(不计数)
tl_run_bracket_case "TC13" "unbalanced in block comment" \
    'var x = 1; /* function old( { return; */ var y = 2;' \
    "PASS"

# TC14: 方括号匹配
tl_run_bracket_case "TC14" "balanced square brackets" \
    'var arr = [1, 2, 3]; var [a, b] = arr;' \
    "PASS"

# TC15: 方括号不匹配
tl_run_bracket_case "TC15" "unbalanced square brackets" \
    'var arr = [1, 2, 3; var [a, b = arr;' \
    "FAIL"

# ---------- 清理 + 汇总 ----------
tl_cleanup
tl_print_summary "JS 括号匹配静态检查 (15 cases)"
exit $?
