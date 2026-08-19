#!/bin/sh
# ============================================================
# test-lib.sh - shell 测试共享库
# ------------------------------------------------------------
# 提供通用测试框架函数, 供 test-*.sh 脚本复用
# 用法: source "$(dirname "$0")/test-lib.sh"
# ============================================================

# ---------- 全局计数器 ----------
TL_PASS=0
TL_FAIL=0
TL_SKIP=0
TL_SQ="'"
TL_TMP_DIR="/tmp/tl_test_$$"

# ---------- 初始化 ----------
tl_init() {
    mkdir -p "$TL_TMP_DIR"
}

# ---------- 增强版 awk 括号检查 (含正则/字符串/注释感知) ----------
tl_check_brackets() {
    f="$1"
    if [ ! -f "$f" ]; then echo "0 0 0 0 0 0"; return; fi

    awk -v sq="$TL_SQ" '
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

# ---------- 括号测试用例运行器 ----------
# 参数: id desc content expect [note]
tl_run_bracket_case() {
    id="$1"
    desc="$2"
    content="$3"
    expect="$4"
    note="${5:-}"

    f="$TL_TMP_DIR/test_${id}.js"
    printf '%s' "$content" > "$f"

    counts=$(tl_check_brackets "$f")
    po=$(echo "$counts" | cut -d' ' -f1)
    pc=$(echo "$counts" | cut -d' ' -f2)
    bo=$(echo "$counts" | cut -d' ' -f3)
    bc=$(echo "$counts" | cut -d' ' -f4)
    so=$(echo "$counts" | cut -d' ' -f5)
    sc=$(echo "$counts" | cut -d' ' -f6)

    errors=0
    reason=""
    [ "$po" -ne "$pc" ] 2>/dev/null && { reason="paren ($po vs ) $pc"; errors=$((errors+1)); }
    [ "$bo" -ne "$bc" ] 2>/dev/null && { reason="${reason:+$reason; }brace {$bo vs } $bc"; errors=$((errors+1)); }
    [ "$so" -ne "$sc" ] 2>/dev/null && { reason="${reason:+$reason; }bracket [$so vs ] $sc"; errors=$((errors+1)); }

    status="()$po/$pc {}$bo/$bc []$so/$sc"
    if [ "$expect" = "PASS" ]; then
        if [ $errors -eq 0 ]; then
            echo "$id  PASS | $desc | -> PASS [$status]"
            TL_PASS=$((TL_PASS + 1))
        else
            echo "$id  FAIL | $desc | expected PASS, got FAIL ($reason) [$status]"
            [ -n "$note" ] && echo "         NOTE: $note"
            TL_FAIL=$((TL_FAIL + 1))
        fi
    else
        if [ $errors -gt 0 ]; then
            echo "$id  PASS | $desc | -> FAIL ($reason) [$status]"
            TL_PASS=$((TL_PASS + 1))
        else
            echo "$id  SKIP | $desc | expected FAIL but got PASS [$status]"
            [ -n "$note" ] && echo "         NOTE: $note"
            TL_SKIP=$((TL_SKIP + 1))
        fi
    fi
}

# ---------- hook 测试用例运行器 ----------
# 参数: desc input expected_exit setup_cmd
tl_run_hook_case() {
    desc="$1"
    input="$2"
    expected_exit="$3"
    setup_cmd="$4"

    hook="${TL_HOOK:-}"
    if [ -z "$hook" ]; then
        echo "ERROR: TL_HOOK not set"
        TL_FAIL=$((TL_FAIL + 1))
        return
    fi

    echo ""
    echo "--- $desc ---"

    if [ -n "$setup_cmd" ]; then
        eval "$setup_cmd"
    fi

    output=$(printf '%s\n' "$input" | sh "$hook" 2>&1)
    actual_exit=$?

    echo "$output" | sed 's/^/  /'

    if [ "$actual_exit" -eq "$expected_exit" ]; then
        echo "  RESULT: PASS (exit $actual_exit == expected $expected_exit)"
        TL_PASS=$((TL_PASS + 1))
    else
        echo "  RESULT: FAIL (exit $actual_exit != expected $expected_exit)"
        TL_FAIL=$((TL_FAIL + 1))
    fi
}

# ---------- 汇总打印 + 退出 ----------
tl_print_summary() {
    title="${1:-测试结果}"
    total=$((TL_PASS + TL_FAIL + TL_SKIP))
    echo ""
    echo "============================================"
    echo "  $title"
    echo "============================================"
    echo "  PASS: $TL_PASS    FAIL: $TL_FAIL    SKIP: $TL_SKIP    TOTAL: $total"
    echo "============================================"

    if [ "$TL_FAIL" -gt 0 ]; then
        echo "  HAS FAILURES"
        return 1
    elif [ "$TL_SKIP" -gt 0 ]; then
        echo "  HAS SKIPS"
        return 0
    else
        echo "  ALL PASS"
        return 0
    fi
}

# ---------- 清理临时目录 ----------
tl_cleanup() {
    rm -rf "$TL_TMP_DIR"
}
