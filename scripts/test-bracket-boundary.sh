#!/bin/sh
# ============================================================
# test-bracket-boundary.sh - P0 钩子边界条件单元测试
# ------------------------------------------------------------
# 覆盖用例 (20 cases): BC1-BC20
#   BC1-BC2:   正则字面量含括号
#   BC3:       模板插值含括号
#   BC4-BC6:   转义引号边界
#   BC7-BC8:   多行注释/字符串
#   BC9:       嵌套模板字符串
#   BC10:      BOM 头文件
#   BC11:      CRLF 行尾
#   BC12-BC14: 仅注释/字符串/全角
#   BC15-BC16: 混合引号/连续转义
#   BC17-BC20: 空行/超长/深层嵌套/注释字符串混合
# 用法: sh scripts/test-bracket-boundary.sh
# ============================================================

. "$(dirname "$0")/test-lib.sh"
tl_init

echo "=========================================="
echo "  P0 钩子边界条件测试 (BC1-BC20)"
echo "  正则字面量 / 模板插值 / 转义引号 / BOM"
echo "=========================================="
echo ""

# ===== BC1-BC2: 正则字面量含括号 =====
echo "--- BC1-BC2: 正则字面量 ---"

tl_run_bracket_case "BC1" "regex literal with paren" \
    'var re = /\(/g; function f() { return 1; }' \
    "PASS" \
    "正则 /\(/ 中的 ( 不应计数"

tl_run_bracket_case "BC2" "regex literal with brace" \
    'var re = /\{n\}/g; function f() { return 1; }' \
    "PASS" \
    "正则 /\{n\}/ 中的 { 不应计数"

# ===== BC3: 模板插值含括号 =====
echo ""
echo "--- BC3: 模板插值 ---"

tl_run_bracket_case "BC3" "template interpolation with parens" \
    'var s = `${obj.method()}`; function f() { return 1; }' \
    "PASS" \
    "插值 \${obj.method()} 中的 () 不应计数"

# ===== BC4-BC6: 转义引号 =====
echo ""
echo "--- BC4-BC6: 转义引号 ---"

tl_run_bracket_case "BC4" "escaped quote in double string" \
    'var s = "text \" more"; function f() { return 1; }' \
    "PASS" \
    "转义引号 \" 不应终止字符串"

tl_run_bracket_case "BC5" "single quote containing double quote" \
    "var s = 'say \"hello (world)\"'; function f() { return 1; }" \
    "PASS" \
    "单引号字符串内的双引号不终止字符串"

tl_run_bracket_case "BC6" "double quote containing single quote" \
    'var s = "it'"'"'s (test)"; function f() { return 1; }' \
    "PASS" \
    "双引号字符串内的单引号不终止字符串"

# ===== BC7-BC8: 多行注释/字符串 =====
echo ""
echo "--- BC7-BC8: 多行注释/字符串 ---"

tl_run_bracket_case "BC7" "multi-line block comment with brackets" \
    '/* line1
 * line2 (unmatched
 * line3 {also
 */ function f() { return 1; }' \
    "PASS" \
    "多行块注释内的括号不应计数"

tl_run_bracket_case "BC8" "multi-line template string" \
    'var s = `line1
${expr()}
line3`; function f() { return 1; }' \
    "PASS" \
    "多行模板字符串内的括号不应计数"

# ===== BC9: 嵌套模板字符串 =====
echo ""
echo "--- BC9: 嵌套模板字符串 ---"

tl_run_bracket_case "BC9" "nested template literals" \
    'var s = `outer ${`inner ()`}`; function f() { return 1; }' \
    "PASS" \
    "嵌套模板字符串内的括号不应计数"

# ===== BC10: BOM 头文件 =====
echo ""
echo "--- BC10: BOM ---"

BOM=$(printf '\xef\xbb\xbf')
tl_run_bracket_case "BC10" "UTF-8 BOM file" \
    "${BOM}function f() { return (1 + 2); }" \
    "PASS" \
    "BOM 头不影响括号计数"

# ===== BC11: CRLF 行尾 =====
echo ""
echo "--- BC11: CRLF ---"

tl_run_bracket_case "BC11" "CRLF line endings" \
    "$(printf 'function f() {\r\n return (1 + 2);\r\n}')" \
    "PASS" \
    "CRLF 行尾不影响状态机"

# ===== BC12-BC14: 仅注释/字符串/全角 =====
echo ""
echo "--- BC12-BC14: 纯内容文件 ---"

tl_run_bracket_case "BC12" "comment-only file" \
    '// just a comment with ( and { brackets' \
    "PASS" \
    "纯注释文件, 括号不计数"

tl_run_bracket_case "BC13" "string-only file" \
    'var s = "only (string {with [brackets]";' \
    "PASS" \
    "纯字符串文件, 括号不计数"

tl_run_bracket_case "BC14" "Unicode fullwidth brackets" \
    'var s = "中文（测试）更多【内容】"; function f() { return 1; }' \
    "PASS" \
    "全角括号（）【】不计数"

# ===== BC15-BC16: 混合引号/连续转义 =====
echo ""
echo "--- BC15-BC16: 混合转义 ---"

tl_run_bracket_case "BC15" "mixed nested quotes" \
    'var s = "it'"'"'s \"nested\" (test)"; function f() { return 1; }' \
    "PASS" \
    "混合嵌套引号, 字符串内括号不计数"

tl_run_bracket_case "BC16" "consecutive escapes" \
    'var s = "\\\\\\\\("; function f() { return 1; }' \
    "PASS" \
    "连续反斜杠+括号, 转义正确处理"

# ===== BC17-BC20: 空行/超长/深层嵌套/混合 =====
echo ""
echo "--- BC17-BC20: 结构边界 ---"

tl_run_bracket_case "BC17" "interspersed empty lines" \
    'function f() {

 return (1 + 2);

}' \
    "PASS" \
    "空行穿插不影响计数"

# BC18: 单行超长 (重复 var x=1; 200次)
LONG_LINE=""
for _ in $(seq 1 200); do
    LONG_LINE="${LONG_LINE}var x=1; "
done
tl_run_bracket_case "BC18" "very long single line (200 vars)" \
    "$LONG_LINE" \
    "PASS" \
    "超长单行不影响计数"

tl_run_bracket_case "BC19" "deeply nested brackets" \
    'var x = (((((a))))) + (((b)));' \
    "PASS" \
    "深层嵌套括号正确匹配"

tl_run_bracket_case "BC20" "comment and string mix" \
    '/* "fake (string" */ var s = "// not (comment"; function f() { return 1; }' \
    "PASS" \
    "注释内假字符串 + 字符串内假注释, 互不干扰"

# ---------- 清理 + 汇总 ----------
tl_cleanup
tl_print_summary "P0 钩子边界条件测试 (20 cases)"
exit $?
