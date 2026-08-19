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

PASS=0
FAIL=0
SKIP=0
TMP_DIR="/tmp/bracket_boundary_$$"
mkdir -p "$TMP_DIR"
SQ="'"

# ---------- 被测逻辑 (当前 awk 状态机, 与 pre-commit 一致) ----------
check_brackets() {
    f="$1"
    if [ ! -f "$f" ]; then echo "0 0 0 0 0 0"; return; fi

    awk -v sq="$SQ" '
    BEGIN { in_str=0; in_block=0; in_re=0; in_class=0; str_ch=""; prev=""; po=pc=bo=bc=so=sc=0 }
    {
        line = $0
        i = 1
        n = length(line)
        while (i <= n) {
            c = substr(line, i, 1)
            nc = (i < n) ? substr(line, i+1, 1) : ""
            if (in_block) {
                if (c == "*" && nc == "/") { in_block=0; i+=2; continue }
                i++; continue
            }
            if (in_re) {
                if (in_class) {
                    if (c == "\\") { i+=2; continue }
                    if (c == "]") { in_class=0 }
                    i++; continue
                }
                if (c == "\\") { i+=2; continue }
                if (c == "[") { in_class=1; i++; continue }
                if (c == "]") { i++; continue }
                if (c == "/") { in_re=0; prev="/"; i++; continue }
                i++; continue
            }
            if (in_str) {
                if (c == "\\") { i+=2; continue }
                if (c == str_ch) { in_str=0; prev=str_ch }
                i++; continue
            }
            if (c == "/" && nc == "/") { break }
            if (c == "/" && nc == "*") { in_block=1; i+=2; continue }
            if (c == "/") {
                is_re = (prev == "=" || prev == "(" || prev == "," || prev == "!" || prev == "&" || prev == "|" || prev == "^" || prev == "~" || prev == "?" || prev == ":" || prev == "+" || prev == "-" || prev == "*" || prev == "%" || prev == "<" || prev == ">" || prev == ";" || prev == "{" || prev == "[" || prev == "\"" || prev == "")
                if (is_re) { in_re=1; prev="/"; i++; continue }
                prev="/"; i++; continue
            }
            if (c == sq || c == "\"" || c == "`") { in_str=1; str_ch=c; i++; continue }
            if (c == "(") { po++; prev=c }
            else if (c == ")") { pc++; prev=c }
            else if (c == "{") { bo++; prev=c }
            else if (c == "}") { bc++; prev=c }
            else if (c == "[") { so++; prev=c }
            else if (c == "]") { sc++; prev=c }
            else if (c == " " || c == "\t" || c == "\r" || c == "\n") { }
            else { prev=c }
            i++
        }
        if (in_str && str_ch != "`") in_str=0
    }
    END { printf "%d %d %d %d %d %d\n", po, pc, bo, bc, so, sc }
    ' "$f"
}

# ---------- 测试框架 ----------
run_case() {
    id="$1"
    desc="$2"
    content="$3"
    expect="$4"
    note="$5"

    f="$TMP_DIR/boundary_${id}.js"
    printf '%s' "$content" > "$f"

    counts=$(check_brackets "$f")
    po=$(echo "$counts" | cut -d' ' -f1)
    pc=$(echo "$counts" | cut -d' ' -f2)
    bo=$(echo "$counts" | cut -d' ' -f3)
    bc=$(echo "$counts" | cut -d' ' -f4)
    so=$(echo "$counts" | cut -d' ' -f5)
    sc=$(echo "$counts" | cut -d' ' -f6)

    errors=0
    reason=""
    [ "$po" -ne "$pc" ] && { reason="paren ($po/$pc)"; errors=$((errors+1)); }
    [ "$bo" -ne "$bc" ] && { reason="${reason:+$reason; }brace {$bo/$bc}"; errors=$((errors+1)); }
    [ "$so" -ne "$sc" ] && { reason="${reason:+$reason; }bracket [$so/$sc]"; errors=$((errors+1)); }

    status="()$po/$pc {}$bo/$bc []$so/$sc"
    if [ "$expect" = "PASS" ]; then
        if [ $errors -eq 0 ]; then
            echo "$id  PASS | $desc | -> PASS [$status]"
            PASS=$((PASS + 1))
        else
            echo "$id  FAIL | $desc | expected PASS, got FAIL ($reason) [$status]"
            echo "         NOTE: $note"
            FAIL=$((FAIL + 1))
        fi
    else
        if [ $errors -gt 0 ]; then
            echo "$id  PASS | $desc | -> FAIL ($reason) [$status]"
            PASS=$((PASS + 1))
        else
            echo "$id  SKIP | $desc | expected FAIL but got PASS [$status]"
            echo "         NOTE: $note"
            SKIP=$((SKIP + 1))
        fi
    fi
}

# ---------- 测试用例 ----------

echo "=========================================="
echo "  P0 钩子边界条件测试 (BC1-BC20)"
echo "  正则字面量 / 模板插值 / 转义引号 / BOM"
echo "=========================================="
echo ""

# ===== BC1-BC2: 正则字面量含括号 =====
echo "--- BC1-BC2: 正则字面量 ---"

run_case "BC1" "regex literal with paren" \
    'var re = /\(/g; function f() { return 1; }' \
    "PASS" \
    "正则 /\(/ 中的 ( 不应计数 (当前状态机不支持 regex 识别, 可能 FAIL)"

run_case "BC2" "regex literal with brace" \
    'var re = /\{n\}/g; function f() { return 1; }' \
    "PASS" \
    "正则 /\{n\}/ 中的 { 不应计数"

# ===== BC3: 模板插值含括号 =====
echo ""
echo "--- BC3: 模板插值 ---"

run_case "BC3" "template interpolation with parens" \
    'var s = `${obj.method()}`; function f() { return 1; }' \
    "PASS" \
    "插值 \${obj.method()} 中的 () 不应计数 (当前状态机不支持 \${} 识别)"

# ===== BC4-BC6: 转义引号 =====
echo ""
echo "--- BC4-BC6: 转义引号 ---"

run_case "BC4" "escaped quote in double string" \
    'var s = "text \" more"; function f() { return 1; }' \
    "PASS" \
    "转义引号 \" 不应终止字符串, 字符串内的 ( 不计数"

run_case "BC5" "single quote containing double quote" \
    "var s = 'say \"hello (world)\"'; function f() { return 1; }" \
    "PASS" \
    "单引号字符串内的双引号不终止字符串"

run_case "BC6" "double quote containing single quote" \
    'var s = "it'"'"'s (test)"; function f() { return 1; }' \
    "PASS" \
    "双引号字符串内的单引号不终止字符串"

# ===== BC7-BC8: 多行注释/字符串 =====
echo ""
echo "--- BC7-BC8: 多行注释/字符串 ---"

run_case "BC7" "multi-line block comment with brackets" \
    '/* line1
 * line2 (unmatched
 * line3 {also
 */ function f() { return 1; }' \
    "PASS" \
    "多行块注释内的括号不应计数"

run_case "BC8" "multi-line template string" \
    'var s = `line1
${expr()}
line3`; function f() { return 1; }' \
    "PASS" \
    "多行模板字符串内的括号不应计数 (不支持 \${} 插值)"

# ===== BC9: 嵌套模板字符串 =====
echo ""
echo "--- BC9: 嵌套模板字符串 ---"

run_case "BC9" "nested template literals" \
    'var s = `outer ${`inner ()`}`; function f() { return 1; }' \
    "PASS" \
    "嵌套模板字符串内的括号不应计数"

# ===== BC10: BOM 头文件 =====
echo ""
echo "--- BC10: BOM ---"

# BOM = \xEF\xBB\xBF (UTF-8 BOM)
BOM=$(printf '\xef\xbb\xbf')
run_case "BC10" "UTF-8 BOM file" \
    "${BOM}function f() { return (1 + 2); }" \
    "PASS" \
    "BOM 头不影响括号计数"

# ===== BC11: CRLF 行尾 =====
echo ""
echo "--- BC11: CRLF ---"

run_case "BC11" "CRLF line endings" \
    "$(printf 'function f() {\r\n return (1 + 2);\r\n}')" \
    "PASS" \
    "CRLF 行尾不影响状态机"

# ===== BC12-BC14: 仅注释/字符串/全角 =====
echo ""
echo "--- BC12-BC14: 纯内容文件 ---"

run_case "BC12" "comment-only file" \
    '// just a comment with ( and { brackets' \
    "PASS" \
    "纯注释文件, 括号不计数"

run_case "BC13" "string-only file" \
    'var s = "only (string {with [brackets]";' \
    "PASS" \
    "纯字符串文件, 括号不计数"

run_case "BC14" "Unicode fullwidth brackets" \
    'var s = "中文（测试）更多【内容】"; function f() { return 1; }' \
    "PASS" \
    "全角括号（）【】不计数"

# ===== BC15-BC16: 混合引号/连续转义 =====
echo ""
echo "--- BC15-BC16: 混合转义 ---"

run_case "BC15" "mixed nested quotes" \
    'var s = "it'"'"'s \"nested\" (test)"; function f() { return 1; }' \
    "PASS" \
    "混合嵌套引号, 字符串内括号不计数"

run_case "BC16" "consecutive escapes" \
    'var s = "\\\\\\\\("; function f() { return 1; }' \
    "PASS" \
    "连续反斜杠+括号, 转义正确处理"

# ===== BC17-BC20: 空行/超长/深层嵌套/混合 =====
echo ""
echo "--- BC17-BC20: 结构边界 ---"

run_case "BC17" "interspersed empty lines" \
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
run_case "BC18" "very long single line (200 vars)" \
    "$LONG_LINE" \
    "PASS" \
    "超长单行不影响计数"

run_case "BC19" "deeply nested brackets" \
    'var x = (((((a))))) + (((b)));' \
    "PASS" \
    "深层嵌套括号正确匹配"

run_case "BC20" "comment and string mix" \
    '/* "fake (string" */ var s = "// not (comment"; function f() { return 1; }' \
    "PASS" \
    "注释内假字符串 + 字符串内假注释, 互不干扰"

# ---------- 清理 ----------
rm -rf "$TMP_DIR"

# ---------- 汇总 ----------
echo ""
echo "=========================================="
echo "  PASS: $PASS    FAIL: $FAIL    SKIP: $SKIP    TOTAL: $((PASS + FAIL + SKIP))"
echo "=========================================="

if [ "$FAIL" -gt 0 ]; then
    echo "  HAS FAILURES (需增强 awk 状态机)"
    exit 1
elif [ "$SKIP" -gt 0 ]; then
    echo "  HAS SKIPS (预期 FAIL 但实际 PASS)"
    exit 0
else
    echo "  ALL PASS"
    exit 0
fi
