#!/bin/sh
# ============================================================
# bench-pre-commit.sh - 钩子拦截逻辑优化前后性能基准对比
# ------------------------------------------------------------
# 对比方案:
#   OLD: grep -o | wc -l x4 (4 次子进程, 不跳过字符串/注释, 2 类括号)
#   NEW: awk 状态机单次遍历 (1 次子进程, 跳过字符串/注释, 3 类括号)
# 用法: sh scripts/bench-pre-commit.sh [iterations]
# 默认: 20 次迭代
# ============================================================

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || echo "$(dirname "$(dirname "$0")")")"
JS_DIR="$REPO_ROOT/zhuxiang-jiu/js"
ITERATIONS="${1:-20}"
SQ="'"

# ---------- 测试文件选取 ----------
TEST_FILES=""
for f in mutex.js inventory-service.js checkout-service.js \
         agent-upgrade-service.js main.js env-adapter.js \
         profit-sharing.js order-pricing.js data.js modules.js; do
    if [ -f "$JS_DIR/$f" ]; then
        TEST_FILES="$TEST_FILES $JS_DIR/$f"
    fi
done

FILE_COUNT=$(echo "$TEST_FILES" | wc -w)
echo "=========================================="
echo "  钩子拦截逻辑性能基准对比"
echo "=========================================="
echo "  迭代次数: $ITERATIONS"
echo "  测试文件: $FILE_COUNT 个"
echo ""

# 显示文件信息
echo "  文件清单:"
for f in $TEST_FILES; do
    LINES=$(wc -l < "$f" 2>/dev/null || echo 0)
    BYTES=$(wc -c < "$f" 2>/dev/null || echo 0)
    BASENAME=$(basename "$f")
    printf "    %-35s %6s 行  %7s 字节\n" "$BASENAME" "$LINES" "$BYTES"
done
echo ""

# ---------- OLD: grep-based check ----------
check_old() {
    f="$1"
    OPENS=$(grep -o '(' "$f" | wc -l)
    CLOSES=$(grep -o ')' "$f" | wc -l)
    B_OPENS=$(grep -o '{' "$f" | wc -l)
    B_CLOSES=$(grep -o '}' "$f" | wc -l)
    echo "$OPENS $CLOSES $B_OPENS $B_CLOSES"
}

# ---------- NEW: awk-based check (enhanced with regex literal support) ----------
check_new() {
    f="$1"
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
    ' "$f" 2>/dev/null
}

# ---------- 准确性对比 (单次) ----------
echo "=========================================="
echo "  准确性对比 (单次运行)"
echo "=========================================="
printf "  %-35s %-18s %-24s %s\n" "文件" "OLD (grep)" "NEW (awk)" "差异"
echo "  -------------------------------------------------------------------"
for f in $TEST_FILES; do
    OLD_RESULT=$(check_old "$f")
    NEW_RESULT=$(check_new "$f")
    OLD_PO=$(echo "$OLD_RESULT" | cut -d' ' -f1)
    OLD_PC=$(echo "$OLD_RESULT" | cut -d' ' -f2)
    OLD_BO=$(echo "$OLD_RESULT" | cut -d' ' -f3)
    OLD_BC=$(echo "$OLD_RESULT" | cut -d' ' -f4)
    NEW_PO=$(echo "$NEW_RESULT" | cut -d' ' -f1)
    NEW_PC=$(echo "$NEW_RESULT" | cut -d' ' -f2)
    NEW_BO=$(echo "$NEW_RESULT" | cut -d' ' -f3)
    NEW_BC=$(echo "$NEW_RESULT" | cut -d' ' -f4)
    NEW_SO=$(echo "$NEW_RESULT" | cut -d' ' -f5)
    NEW_SC=$(echo "$NEW_RESULT" | cut -d' ' -f6)

    OLD_STR="()$OLD_PO/$OLD_PC {}$OLD_BO/$OLD_BC"
    NEW_STR="()$NEW_PO/$NEW_PC {}$NEW_BO/$NEW_BC []$NEW_SO/$NEW_SC"

    DIFF="—"
    if [ "$OLD_PO" -ne "$NEW_PO" ] || [ "$OLD_PC" -ne "$NEW_PC" ]; then
        DIFF="()差异: ($OLD_PO/$OLD_PC → $NEW_PO/$NEW_PC)"
    elif [ "$OLD_BO" -ne "$NEW_BO" ] || [ "$OLD_BC" -ne "$NEW_BC" ]; then
        DIFF="{}差异: {$OLD_BO/$OLD_BC → $NEW_BO/$NEW_BC)"
    fi

    BASENAME=$(basename "$f")
    printf "  %-35s %-18s %-24s %s\n" "$BASENAME" "$OLD_STR" "$NEW_STR" "$DIFF"
done
echo ""

# ---------- 性能基准 ----------
echo "=========================================="
echo "  性能基准 ($ITERATIONS 次迭代)"
echo "=========================================="

# OLD benchmark
START_OLD=$(date +%s%N)
for i in $(seq 1 $ITERATIONS); do
    for f in $TEST_FILES; do
        check_old "$f" > /dev/null 2>&1
    done
done
END_OLD=$(date +%s%N)
MS_OLD=$(( (END_OLD - START_OLD) / 1000000 ))
TOTAL_CHECKS_OLD=$((ITERATIONS * FILE_COUNT))
PER_FILE_OLD=$(( MS_OLD / TOTAL_CHECKS_OLD ))

# NEW benchmark
START_NEW=$(date +%s%N)
for i in $(seq 1 $ITERATIONS); do
    for f in $TEST_FILES; do
        check_new "$f" > /dev/null 2>&1
    done
done
END_NEW=$(date +%s%N)
MS_NEW=$(( (END_NEW - START_NEW) / 1000000 ))
TOTAL_CHECKS_NEW=$((ITERATIONS * FILE_COUNT))
PER_FILE_NEW=$(( MS_NEW / TOTAL_CHECKS_NEW ))

# 汇总
echo "  OLD (grep 4x 子进程):"
echo "    总耗时: ${MS_OLD}ms ($ITERATIONS 次 x $FILE_COUNT 文件 = $TOTAL_CHECKS_OLD 次检查)"
echo "    平均:   ${PER_FILE_OLD}ms/文件"
echo "    子进程: 4 个/文件 (grep x2 + wc x2, 仅查 () 和 {})"
echo ""
echo "  NEW (awk 1x 状态机):"
echo "    总耗时: ${MS_NEW}ms ($ITERATIONS 次 x $FILE_COUNT 文件 = $TOTAL_CHECKS_NEW 次检查)"
echo "    平均:   ${PER_FILE_NEW}ms/文件"
echo "    子进程: 1 个/文件 (awk 单次遍历, 查 () {} [], 跳过字符串/注释)"
echo ""

# 对比
DIFF_MS=$((MS_OLD - MS_NEW))
DIFF_PCT=0
if [ "$MS_OLD" -gt 0 ]; then
    DIFF_PCT=$(( DIFF_MS * 100 / MS_OLD ))
fi

echo "=========================================="
echo "  性能对比汇总"
echo "=========================================="
if [ "$DIFF_MS" -gt 0 ]; then
    echo "  NEW 比 OLD 快: ${DIFF_MS}ms (-${DIFF_PCT}%)"
else
    ABS_DIFF=$(( -DIFF_MS ))
    echo "  NEW 比 OLD 慢: ${ABS_DIFF}ms (+${DIFF_PCT}%)"
fi
echo "  单文件差异: $(( PER_FILE_OLD - PER_FILE_NEW ))ms"
echo "  准确性提升: +1 类括号([]) + 字符串/注释感知"
echo "  子进程减少: 4 → 1 (-75%)"
echo "=========================================="
