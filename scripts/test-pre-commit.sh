#!/bin/sh
# ============================================================
# test-pre-commit.sh - pre-commit 钩子 CI 确认流程单元测试
# ------------------------------------------------------------
# 用法 (Git Bash):
#   sh scripts/test-pre-commit.sh
# 或 (PowerShell 调用):
#   & "C:\Program Files\Git\bin\sh.exe" scripts/test-pre-commit.sh
# 覆盖用例:
#   TC1: 自动输入 Y  → 钩子通过 (exit 0)
#   TC2: 自动输入 n  → 钩子阻止 (exit 1)
#   TC3: 自动输入 N  → 钩子阻止 (exit 1)
#   TC4: 空输入/EOF  → 钩子通过 (exit 0)
#   TC5: 无 JS 暂存  → 钩子跳过 (exit 0)
# ============================================================

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || echo "$(cd "$(dirname "$0")/.." && pwd)")"
HOOK="$REPO_ROOT/.git/hooks/pre-commit"
TEST_FILE="$REPO_ROOT/zhuxiang-jiu/js/__precommit_unit_test.js"

PASS=0
FAIL=0

# ---------- 辅助函数 ----------
run_case() {
    desc="$1"
    input="$2"
    expected_exit="$3"
    setup_cmd="$4"

    echo ""
    echo "--- $desc ---"

    # 执行 setup (如暂存测试文件)
    if [ -n "$setup_cmd" ]; then
        eval "$setup_cmd"
    fi

    # 运行钩子并捕获输出+退出码
    output=$(printf '%s\n' "$input" | sh "$HOOK" 2>&1)
    actual_exit=$?

    # 显示钩子输出(缩进)
    echo "$output" | sed 's/^/  /'

    if [ "$actual_exit" -eq "$expected_exit" ]; then
        echo "  RESULT: PASS (exit $actual_exit == expected $expected_exit)"
        PASS=$((PASS + 1))
    else
        echo "  RESULT: FAIL (exit $actual_exit != expected $expected_exit)"
        FAIL=$((FAIL + 1))
    fi
}

# ---------- 创建测试 JS 文件 (括号/花括号匹配) ----------
cat > "$TEST_FILE" << 'JSEOF'
function __precommitTest() {
    var x = (1 + 2);
    if (x === 3) {
        return { ok: true };
    }
    return { ok: false };
}
JSEOF

# 清理之前残留的暂存状态 (避免干扰)
git reset HEAD zhuxiang-jiu/js/*.js >/dev/null 2>&1 || true

# ---------- 测试用例 ----------

# TC1: 输入 Y → 通过
run_case "TC1: auto-input Y -> PASS" "Y" 0 \
    "git add '$TEST_FILE' >/dev/null 2>&1"

# TC2: 输入 n → 阻止
run_case "TC2: auto-input n -> BLOCK" "n" 1 \
    "git add '$TEST_FILE' >/dev/null 2>&1"

# TC3: 输入 N → 阻止
run_case "TC3: auto-input N -> BLOCK" "N" 1 \
    "git add '$TEST_FILE' >/dev/null 2>&1"

# TC4: 空输入/EOF → 通过 (response="" 非 n/N)
run_case "TC4: empty input/EOF -> PASS" "" 0 \
    "git add '$TEST_FILE' >/dev/null 2>&1"

# TC5: 无 JS 暂存 → 跳过
run_case "TC5: no staged JS -> SKIP" "" 0 \
    "git reset HEAD '$TEST_FILE' >/dev/null 2>&1"

# ---------- 清理 ----------
git reset HEAD "$TEST_FILE" >/dev/null 2>&1
rm -f "$TEST_FILE"

# ---------- 汇总 ----------
echo ""
echo "============================================"
echo "  pre-commit CI 确认流程单元测试结果"
echo "============================================"
echo "  PASS: $PASS"
echo "  FAIL: $FAIL"
echo "  TOTAL: $((PASS + FAIL))"
echo "============================================"

if [ "$FAIL" -eq 0 ]; then
    echo "  ALL PASS"
    exit 0
else
    echo "  HAS FAILURES"
    exit 1
fi
