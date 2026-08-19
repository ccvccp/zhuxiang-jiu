#!/bin/sh
# Benchmark: pre-commit hook with vs without logging
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
HOOK="$REPO_ROOT/.git/hooks/pre-commit"
TEST_FILE="$REPO_ROOT/zhuxiang-jiu/js/__perf_test.js"
ITERATIONS=20

# Create a valid test JS file
echo 'function perfTest() { var x = (1+2); if (x) { return [x]; } }' > "$TEST_FILE"
git add "$TEST_FILE" 2>/dev/null

# Create no-log version of the hook
NOLOG_HOOK="/tmp/hook_nolog_$$"
sed 's|>> "$LOG_FILE"|>> /dev/null|g' "$HOOK" > "$NOLOG_HOOK"

echo "=== Benchmark: $ITERATIONS iterations each ==="

# Benchmark WITH logging
START1=$(date +%s%N)
for i in $(seq 1 $ITERATIONS); do
    printf 'Y\n' | sh "$HOOK" > /dev/null 2>&1
done
END1=$(date +%s%N)
MS1=$(( (END1 - START1) / 1000000 ))
AVG1=$(( MS1 / ITERATIONS ))

# Benchmark WITHOUT logging (no-log hook)
START2=$(date +%s%N)
for i in $(seq 1 $ITERATIONS); do
    printf 'Y\n' | sh "$NOLOG_HOOK" > /dev/null 2>&1
done
END2=$(date +%s%N)
MS2=$(( (END2 - START2) / 1000000 ))
AVG2=$(( MS2 / ITERATIONS ))

echo "WITH logging:    ${MS1}ms total, ${AVG1}ms/run"
echo "WITHOUT logging: ${MS2}ms total, ${AVG2}ms/run"

if [ "$MS2" -gt 0 ]; then
    OVERHEAD_MS=$(( MS1 - MS2 ))
    OVERHEAD_PCT=$(( OVERHEAD_MS * 100 / MS2 ))
    OVERHEAD_PER_RUN=$(( OVERHEAD_MS / ITERATIONS ))
    echo "Overhead: ${OVERHEAD_MS}ms total (${OVERHEAD_PCT}%), ${OVERHEAD_PER_RUN}ms/run"
fi

# Log file stats
LOG_LINES=$(wc -l < "$REPO_ROOT/.git/hooks/pre-commit.log" 2>/dev/null || echo 0)
LOG_BYTES=$(wc -c < "$REPO_ROOT/.git/hooks/pre-commit.log" 2>/dev/null || echo 0)
echo "Log file: ${LOG_LINES} lines, ${LOG_BYTES} bytes ($ITERATIONS runs)"

# Cleanup
git reset HEAD "$TEST_FILE" 2>/dev/null
rm -f "$TEST_FILE" "$NOLOG_HOOK" "$REPO_ROOT/.git/hooks/pre-commit.log"
