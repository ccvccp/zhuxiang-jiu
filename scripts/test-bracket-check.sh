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

PASS=0
FAIL=0
TMP_DIR="/tmp/bracket_test_$$"
mkdir -p "$TMP_DIR"
SQ="'"

# ---------- 被测逻辑 (从 pre-commit 钩子提取, awk 单次遍历) ----------
check_brackets() {
    f="$1"
    if [ ! -f "$f" ]; then echo "0 0 0 0 0 0"; return; fi

    awk -v sq="$SQ" '
    BEGIN { in_str=0; in_block=0; str_ch=""; po=pc=bo=bc=so=sc=0 }
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
            if (in_str) {
                if (c == "\\") { i+=2; continue }
                if (c == str_ch) { in_str=0 }
                i++; continue
            }
            if (c == "/" && nc == "/") { break }
            if (c == "/" && nc == "*") { in_block=1; i+=2; continue }
            if (c == sq || c == "\"" || c == "`") { in_str=1; str_ch=c; i++; continue }
            if (c == "(") po++
            else if (c == ")") pc++
            else if (c == "{") bo++
            else if (c == "}") bc++
            else if (c == "[") so++
            else if (c == "]") sc++
            i++
        }
        if (in_str) in_str=0
    }
    END { printf "%d %d %d %d %d %d\n", po, pc, bo, bc, so, sc }
    ' "$f" 2>/dev/null
}

# ---------- 测试框架 ----------
run_case() {
    id="$1"
    desc="$2"
    content="$3"
    expect="$4"

    f="$TMP_DIR/test_${id}.js"
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
    [ "$po" -ne "$pc" ] && { reason="paren ($po vs ) $pc"; errors=$((errors+1)); }
    [ "$bo" -ne "$bc" ] && { reason="${reason:+$reason; }brace {$bo vs } $bc"; errors=$((errors+1)); }
    [ "$so" -ne "$sc" ] && { reason="${reason:+$reason; }bracket [$so vs ] $sc"; errors=$((errors+1)); }

    if [ "$expect" = "PASS" ]; then
        if [ $errors -eq 0 ]; then
            echo "$id PASS | $desc | -> PASS [()$po/$pc {}$bo/$bc []$so/$sc]"
            PASS=$((PASS + 1))
        else
            echo "$id FAIL | $desc | expected PASS, got: FAIL ($reason)"
            FAIL=$((FAIL + 1))
        fi
    else
        if [ $errors -gt 0 ]; then
            echo "$id PASS | $desc | -> FAIL: $reason"
            PASS=$((PASS + 1))
        else
            echo "$id FAIL | $desc | expected FAIL, got: PASS [()$po/$pc {}$bo/$bc []$so/$sc]"
            FAIL=$((FAIL + 1))
        fi
    fi
}

# ---------- 测试用例 ----------

echo "=========================================="
echo "  JS 括号匹配静态检查单元测试 v2"
echo "  (awk 单次遍历, 字符串/注释感知)"
echo "=========================================="

# TC1: 均匹配
run_case "TC1" "balanced parens+braces" \
    'function f() { return (1 + 2); }' \
    "PASS"

# TC2: 缺少闭括号
run_case "TC2" "missing close paren" \
    'function f( { return 1; }' \
    "FAIL"

# TC3: 多余闭括号
run_case "TC3" "extra close paren" \
    'function f() { return 1); }' \
    "FAIL"

# TC4: 缺少闭花括号
run_case "TC4" "missing close brace" \
    'function f() { return 1;' \
    "FAIL"

# TC5: 多余闭花括号
run_case "TC5" "extra close brace" \
    'function f() { return 1; }}' \
    "FAIL"

# TC6: 均不匹配
run_case "TC6" "both unbalanced" \
    'function f( { return 1;' \
    "FAIL"

# TC7: 空文件
run_case "TC7" "empty file" \
    '' \
    "PASS"

# TC8: 无括号
run_case "TC8" "no brackets at all" \
    'var x = 1;' \
    "PASS"

# TC9: 嵌套(平衡)
run_case "TC9" "nested balanced" \
    'function f() { if (true) { return (1 + (2 * 3)); } }' \
    "PASS"

# TC10: 字符串内含平衡括号(不计数)
run_case "TC10" "balanced brackets in string" \
    'var s = "test (value) {key} [idx]";' \
    "PASS"

# TC11: 字符串内含不平衡括号(不计数, 不误报)
run_case "TC11" "unbalanced in string (no false positive)" \
    'var s = "unmatched ( bracket"; var t = "also { unmatched";' \
    "PASS"

# TC12: 行注释内不平衡括号(不计数)
run_case "TC12" "unbalanced in line comment" \
    'var x = 1; // function old( { return;' \
    "PASS"

# TC13: 块注释内不平衡括号(不计数)
run_case "TC13" "unbalanced in block comment" \
    'var x = 1; /* function old( { return; */ var y = 2;' \
    "PASS"

# TC14: 方括号匹配
run_case "TC14" "balanced square brackets" \
    'var arr = [1, 2, 3]; var [a, b] = arr;' \
    "PASS"

# TC15: 方括号不匹配
run_case "TC15" "unbalanced square brackets" \
    'var arr = [1, 2, 3; var [a, b = arr;' \
    "FAIL"

# ---------- 清理 ----------
rm -rf "$TMP_DIR"

# ---------- 汇总 ----------
echo "=========================================="
echo "  PASS: $PASS    FAIL: $FAIL    TOTAL: $((PASS + FAIL))"
echo "=========================================="

if [ "$FAIL" -eq 0 ]; then
    echo "  ALL PASS"
    exit 0
else
    echo "  HAS FAILURES"
    exit 1
fi
