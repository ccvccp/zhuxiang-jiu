#!/usr/bin/env bash
# ============================================================
# Redis 集群 + Prometheus 监控一键验证脚本 (Bash 版)
# 适用: WSL2 / Linux 服务器
# 用法: bash ha-monitor-check.sh
# ============================================================

set -euo pipefail

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# 计数器
PASS=0
FAIL=0
WARN=0

# ============================================================
# 辅助函数
# ============================================================

log_pass() { PASS=$((PASS + 1)); echo -e "  ${GREEN}[PASS]${NC} $1"; }
log_fail() { FAIL=$((FAIL + 1)); echo -e "  ${RED}[FAIL]${NC} $1"; }
log_warn() { WARN=$((WARN + 1)); echo -e "  ${YELLOW}[WARN]${NC} $1"; }
log_info() { echo -e "  ${BLUE}[INFO]${NC} $1"; }
log_section() {
    echo ""
    echo -e "${CYAN}============================================================${NC}"
    echo -e "${CYAN} $1${NC}"
    echo -e "${CYAN}============================================================${NC}"
}

# ============================================================
# 主流程
# ============================================================

echo ""
echo "=========================================="
echo " Redis 集群 + Prometheus 监控验证脚本"
echo " 生成时间: 2026-08-22"
echo "=========================================="

# ------------------------------------------------------------
log_section "1. Redis 主从复制状态"
# ------------------------------------------------------------

REPLICATION=$(docker exec redis redis-cli INFO replication 2>/dev/null || echo "ERROR")
if [ "$REPLICATION" != "ERROR" ]; then
    ROLE=$(echo "$REPLICATION" | grep "^role:" | cut -d: -f2 | tr -d '\r')
    if [ "$ROLE" = "master" ]; then
        log_pass "Redis 角色: master"
        SLAVE_COUNT=$(echo "$REPLICATION" | grep "^connected_slaves:" | cut -d: -f2 | tr -d '\r')
        if [ "$SLAVE_COUNT" -gt 0 ] 2>/dev/null; then
            log_pass "从节点数量: $SLAVE_COUNT"
        else
            log_warn "无从节点 (单机模式)"
        fi
    elif [ "$ROLE" = "slave" ]; then
        log_pass "Redis 角色: slave"
        MASTER_LINK=$(echo "$REPLICATION" | grep "^master_link_status:" | cut -d: -f2 | tr -d '\r')
        if [ "$MASTER_LINK" = "up" ]; then
            log_pass "主从连接状态: up"
        else
            log_fail "主从连接状态: $MASTER_LINK"
        fi
    else
        log_warn "无法确定 Redis 角色"
    fi
else
    log_fail "无法获取 Redis 复制信息"
fi

# ------------------------------------------------------------
log_section "2. Redis Sentinel 哨兵状态"
# ------------------------------------------------------------

SENTINEL_INFO=$(docker exec sentinel1 redis-cli -p 26379 SENTINEL masters 2>/dev/null || echo "ERROR")
if [ "$SENTINEL_INFO" != "ERROR" ] && [ -n "$SENTINEL_INFO" ]; then
    log_pass "Sentinel1 可访问"
    
    # 查询 master 地址
    MASTER_ADDR=$(docker exec sentinel1 redis-cli -p 26379 SENTINEL get-master-addr-by-name mymaster 2>/dev/null || echo "")
    if [ -n "$MASTER_ADDR" ]; then
        log_pass "Sentinel master 地址: $(echo $MASTER_ADDR | head -1):$(echo $MASTER_ADDR | tail -1)"
    fi
    
    # 检查 Sentinel 数量
    SENTINEL_COUNT=$(docker exec sentinel1 redis-cli -p 26379 SENTINEL sentinels mymaster 2>/dev/null | grep -c "ip" || echo "0")
    TOTAL_SENTINELS=$((SENTINEL_COUNT + 1))
    if [ "$TOTAL_SENTINELS" -ge 3 ]; then
        log_pass "Sentinel 节点数: $TOTAL_SENTINELS (≥3)"
    else
        log_warn "Sentinel 节点数: $TOTAL_SENTINELS (<3, 建议至少3个)"
    fi
else
    log_warn "Sentinel 未部署 (单机模式可跳过)"
fi

# ------------------------------------------------------------
log_section "3. Redis Cluster 集群状态"
# ------------------------------------------------------------

CLUSTER_INFO=$(docker exec redis redis-cli CLUSTER INFO 2>/dev/null || echo "ERROR")
if [ "$CLUSTER_INFO" != "ERROR" ] && [ "$CLUSTER_INFO" != "ERR This instance has cluster support disabled" ]; then
    CLUSTER_STATE=$(echo "$CLUSTER_INFO" | grep "^cluster_state:" | cut -d: -f2 | tr -d '\r')
    if [ "$CLUSTER_STATE" = "ok" ]; then
        log_pass "Cluster 状态: ok"
    else
        log_fail "Cluster 状态: $CLUSTER_STATE"
    fi
    
    SLOTS_ASSIGNED=$(echo "$CLUSTER_INFO" | grep "^cluster_slots_assigned:" | cut -d: -f2 | tr -d '\r')
    if [ "$SLOTS_ASSIGNED" = "16384" ]; then
        log_pass "Slot 分配: 16384/16384 (100%)"
    else
        log_fail "Slot 分配: $SLOTS_ASSIGNED/16384 (未完全分配)"
    fi
    
    SLOTS_OK=$(echo "$CLUSTER_INFO" | grep "^cluster_slots_ok:" | cut -d: -f2 | tr -d '\r')
    log_info "Slot OK: $SLOTS_OK"
    
    CLUSTER_SIZE=$(echo "$CLUSTER_INFO" | grep "^cluster_known_nodes:" | cut -d: -f2 | tr -d '\r')
    log_info "集群节点数: $CLUSTER_SIZE"
else
    log_warn "Cluster 未启用 (单机或哨兵模式可跳过)"
fi

# ------------------------------------------------------------
log_section "4. Redis 高可用指标"
# ------------------------------------------------------------

# 主从延迟
REPL_OFFSET=$(docker exec redis redis-cli INFO replication 2>/dev/null | grep "^master_repl_offset:" | cut -d: -f2 | tr -d '\r' || echo "0")
SLAVE_OFFSET=$(docker exec redis redis-cli INFO replication 2>/dev/null | grep "^slave0:" | grep -o "offset=[0-9]*" | cut -d= -f2 || echo "0")
if [ "$REPL_OFFSET" != "0" ] && [ "$SLAVE_OFFSET" != "0" ]; then
    OFFSET_DIFF=$((REPL_OFFSET - SLAVE_OFFSET))
    if [ "$OFFSET_DIFF" -lt 1000000 ]; then
        log_pass "主从延迟: ${OFFSET_DIFF} bytes (<1MB)"
    else
        log_warn "主从延迟: ${OFFSET_DIFF} bytes (>1MB)"
    fi
fi

# 内存碎片率
MEM_INFO=$(docker exec redis redis-cli INFO memory 2>/dev/null || echo "")
FRAG_RATIO=$(echo "$MEM_INFO" | grep "^mem_fragmentation_ratio:" | cut -d: -f2 | tr -d '\r' || echo "0")
if [ "$FRAG_RATIO" != "0" ] && [ -n "$FRAG_RATIO" ]; then
    if (( $(echo "$FRAG_RATIO < 1.5" | bc -l 2>/dev/null || echo 1) )); then
        log_pass "内存碎片率: $FRAG_RATIO (<1.5)"
    else
        log_warn "内存碎片率: $FRAG_RATIO (>1.5, 建议重启 Redis)"
    fi
fi

# 键空间命中率
STATS_INFO=$(docker exec redis redis-cli INFO stats 2>/dev/null || echo "")
HITS=$(echo "$STATS_INFO" | grep "^keyspace_hits:" | cut -d: -f2 | tr -d '\r' || echo "0")
MISSES=$(echo "$STATS_INFO" | grep "^keyspace_misses:" | cut -d: -f2 | tr -d '\r' || echo "0")
if [ "$HITS" != "0" ] && [ "$MISSES" != "0" ]; then
    TOTAL=$((HITS + MISSES))
    if [ "$TOTAL" -gt 0 ]; then
        HIT_RATE=$((HITS * 100 / TOTAL))
        if [ "$HIT_RATE" -ge 90 ]; then
            log_pass "键空间命中率: ${HIT_RATE}% (≥90%)"
        else
            log_warn "键空间命中率: ${HIT_RATE}% (<90%)"
        fi
    fi
fi

# ------------------------------------------------------------
log_section "5. 监控组件容器状态"
# ------------------------------------------------------------

check_container() {
    local name=$1
    local status=$(docker inspect $name --format='{{.State.Status}}' 2>/dev/null || echo "not_found")
    if [ "$status" = "running" ]; then
        log_pass "$name 容器: running"
    else
        log_fail "$name 容器: $status"
    fi
}

check_container "redis-exporter"
check_container "node-exporter"
check_container "cadvisor"
check_container "prometheus"
check_container "alertmanager"
check_container "grafana"

# ------------------------------------------------------------
log_section "6. Prometheus 采集 Targets"
# ------------------------------------------------------------

PROM_API="http://localhost:9090/api/v1/targets"
TARGETS=$(curl -s "$PROM_API" 2>/dev/null || echo "ERROR")
if [ "$TARGETS" != "ERROR" ] && [ -n "$TARGETS" ]; then
    # 统计 UP 的 targets
    UP_COUNT=$(echo "$TARGETS" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    targets = data.get('data', {}).get('activeTargets', [])
    up = [t for t in targets if t.get('health') == 'up']
    print(len(up))
except:
    print(0)
" 2>/dev/null || echo "0")
    
    TOTAL_COUNT=$(echo "$TARGETS" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    targets = data.get('data', {}).get('activeTargets', [])
    print(len(targets))
except:
    print(0)
" 2>/dev/null || echo "0")
    
    if [ "$UP_COUNT" = "$TOTAL_COUNT" ] && [ "$TOTAL_COUNT" -gt 0 ]; then
        log_pass "Targets: $UP_COUNT/$TOTAL_COUNT 全部 UP"
    else
        log_warn "Targets: $UP_COUNT/$TOTAL_COUNT (有 DOWN 的 target)"
    fi
    
    # 检查各 job 状态
    for job in redis node docker backend; do
        JOB_UP=$(echo "$TARGETS" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    targets = data.get('data', {}).get('activeTargets', [])
    up = [t for t in targets if t.get('labels', {}).get('job') == '$job' and t.get('health') == 'up']
    print(len(up))
except:
    print(0)
" 2>/dev/null || echo "0")
        if [ "$JOB_UP" -gt 0 ]; then
            log_pass "Job '$job': UP"
        else
            log_fail "Job '$job': DOWN 或不存在"
        fi
    done
else
    log_fail "无法访问 Prometheus API ($PROM_API)"
fi

# ------------------------------------------------------------
log_section "7. Prometheus 指标验证"
# ------------------------------------------------------------

check_metric() {
    local metric=$1
    local query=$2
    local result=$(curl -s "http://localhost:9090/api/v1/query?query=$query" 2>/dev/null | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    result = data.get('data', {}).get('result', [])
    if result:
        print(result[0]['value'][1])
    else:
        print('N/A')
except:
    print('ERROR')
" 2>/dev/null || echo "ERROR")
    
    if [ "$result" != "N/A" ] && [ "$result" != "ERROR" ]; then
        log_pass "$metric = $result"
    elif [ "$result" = "N/A" ]; then
        log_warn "$metric: 无数据"
    else
        log_fail "$metric: 查询失败"
    fi
}

check_metric "redis_up" "redis_up"
check_metric "redis_memory_used" "redis_memory_used_bytes"
check_metric "redis_connected_clients" "redis_connected_clients"
check_metric "redis_memory_fragmentation_ratio" "redis_memory_fragmentation_ratio"
check_metric "redis_commands_processed" "rate(redis_commands_processed_total[5m])"
check_metric "redis_keyspace_hits_rate" "rate(redis_keyspace_hits_total[5m])"

# ------------------------------------------------------------
log_section "8. Prometheus 告警规则"
# ------------------------------------------------------------

RULES_API="http://localhost:9090/api/v1/rules"
RULES=$(curl -s "$RULES_API" 2>/dev/null || echo "ERROR")
if [ "$RULES" != "ERROR" ] && [ -n "$RULES" ]; then
    RULE_COUNT=$(echo "$RULES" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    groups = data.get('data', {}).get('groups', [])
    count = sum(len(g.get('rules', [])) for g in groups)
    print(count)
except:
    print(0)
" 2>/dev/null || echo "0")
    
    if [ "$RULE_COUNT" -gt 0 ]; then
        log_pass "告警规则数: $RULE_COUNT"
    else
        log_fail "告警规则数: 0 (规则未加载)"
    fi
    
    # 检查关键告警规则
    for alert_name in RedisDown RedisMemoryHigh RedisTooManyConnections BackendDown HostHighCpuLoad; do
        ALERT_EXISTS=$(echo "$RULES" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    groups = data.get('data', {}).get('groups', [])
    found = any(any(r.get('name') == '$alert_name' for r in g.get('rules', [])) for g in groups)
    print('YES' if found else 'NO')
except:
    print('ERROR')
" 2>/dev/null || echo "ERROR")
        
        if [ "$ALERT_EXISTS" = "YES" ]; then
            log_pass "告警规则 '$alert_name': 已配置"
        else
            log_fail "告警规则 '$alert_name': 未配置"
        fi
    done
else
    log_fail "无法访问 Prometheus 告警规则 API"
fi

# ------------------------------------------------------------
log_section "9. Alertmanager 状态"
# ------------------------------------------------------------

ALERT_API="http://localhost:9093/api/v2/status"
ALERT_STATUS=$(curl -s "$ALERT_API" 2>/dev/null || echo "ERROR")
if [ "$ALERT_STATUS" != "ERROR" ] && [ -n "$ALERT_STATUS" ]; then
    log_pass "Alertmanager 可访问"
    
    # 检查告警数
    ALERTS=$(curl -s "http://localhost:9093/api/v2/alerts" 2>/dev/null || echo "[]")
    ALERT_COUNT=$(echo "$ALERTS" | python3 -c "
import sys, json
try:
    alerts = json.load(sys.stdin)
    print(len(alerts))
except:
    print(0)
" 2>/dev/null || echo "0")
    
    if [ "$ALERT_COUNT" = "0" ]; then
        log_pass "当前活跃告警: 0 (正常)"
    else
        log_warn "当前活跃告警: $ALERT_COUNT (需关注)"
    fi
else
    log_fail "Alertmanager 不可访问"
fi

# ------------------------------------------------------------
log_section "10. Grafana 状态"
# ------------------------------------------------------------

GRAFANA_API="http://localhost:3000/api/health"
GRAFANA_STATUS=$(curl -s "$GRAFANA_API" 2>/dev/null || echo "ERROR")
if [ "$GRAFANA_STATUS" != "ERROR" ] && [ -n "$GRAFANA_STATUS" ]; then
    GRAFANA_DB=$(echo "$GRAFANA_STATUS" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    print(data.get('database', 'unknown'))
except:
    print('ERROR')
" 2>/dev/null || echo "ERROR")
    
    if [ "$GRAFANA_DB" = "ok" ]; then
        log_pass "Grafana 健康: ok"
    else
        log_warn "Grafana 数据库状态: $GRAFANA_DB"
    fi
else
    log_fail "Grafana 不可访问"
fi

# 检查数据源
DATASOURCES=$(curl -s -u admin:admin "http://localhost:3000/api/datasources" 2>/dev/null || echo "ERROR")
if [ "$DATASOURCES" != "ERROR" ] && [ "$DATASOURCES" != "[]" ]; then
    DS_COUNT=$(echo "$DATASOURCES" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    print(len(data))
except:
    print(0)
" 2>/dev/null || echo "0")
    
    if [ "$DS_COUNT" -gt 0 ]; then
        log_pass "Grafana 数据源: $DS_COUNT 个"
    else
        log_fail "Grafana 数据源: 0 (未配置)"
    fi
else
    log_warn "无法获取 Grafana 数据源 (可能需要认证)"
fi

# ------------------------------------------------------------
log_section "11. Redis Exporter 指标"
# ------------------------------------------------------------

EXPORTER_METRICS=$(curl -s http://localhost:9121/metrics 2>/dev/null || echo "ERROR")
if [ "$EXPORTER_METRICS" != "ERROR" ] && [ -n "$EXPORTER_METRICS" ]; then
    log_pass "Redis Exporter 可访问"
    
    # 检查关键指标
    if echo "$EXPORTER_METRICS" | grep -q "^redis_up"; then
        log_pass "redis_up 指标存在"
    else
        log_fail "redis_up 指标不存在"
    fi
    
    if echo "$EXPORTER_METRICS" | grep -q "^redis_memory_used_bytes"; then
        log_pass "redis_memory_used_bytes 指标存在"
    else
        log_fail "redis_memory_used_bytes 指标不存在"
    fi
    
    if echo "$EXPORTER_METRICS" | grep -q "^redis_connected_clients"; then
        log_pass "redis_connected_clients 指标存在"
    else
        log_fail "redis_connected_clients 指标不存在"
    fi
else
    log_fail "Redis Exporter 不可访问"
fi

# ------------------------------------------------------------
log_section "12. Node Exporter 指标"
# ------------------------------------------------------------

NODE_METRICS=$(curl -s http://localhost:9100/metrics 2>/dev/null || echo "ERROR")
if [ "$NODE_METRICS" != "ERROR" ] && [ -n "$NODE_METRICS" ]; then
    log_pass "Node Exporter 可访问"
    
    if echo "$NODE_METRICS" | grep -q "^node_cpu_seconds_total"; then
        log_pass "node_cpu 指标存在"
    else
        log_fail "node_cpu 指标不存在"
    fi
    
    if echo "$NODE_METRICS" | grep -q "^node_memory_MemAvailable_bytes"; then
        log_pass "node_memory 指标存在"
    else
        log_fail "node_memory 指标不存在"
    fi
else
    log_fail "Node Exporter 不可访问"
fi

# ------------------------------------------------------------
log_section "13. cAdvisor 容器指标"
# ------------------------------------------------------------

CADVISOR_METRICS=$(curl -s http://localhost:8080/metrics 2>/dev/null || echo "ERROR")
if [ "$CADVISOR_METRICS" != "ERROR" ] && [ -n "$CADVISOR_METRICS" ]; then
    log_pass "cAdvisor 可访问"
    
    if echo "$CADVISOR_METRICS" | grep -q "container_memory_usage_bytes"; then
        log_pass "container_memory 指标存在"
    else
        log_fail "container_memory 指标不存在"
    fi
else
    log_fail "cAdvisor 不可访问"
fi

# ------------------------------------------------------------
log_section "14. 故障转移测试(哨兵模式)"
# ------------------------------------------------------------

if [ "$SENTINEL_INFO" != "ERROR" ] && [ -n "$SENTINEL_INFO" ]; then
    log_info "执行故障转移测试(可选, 需手动确认)"
    log_info "测试命令:"
    echo "    1. docker stop redis-master"
    echo "    2. sleep 30"
    echo "    3. docker exec sentinel1 redis-cli -p 26379 SENTINEL get-master-addr-by-name mymaster"
    echo "    4. curl http://localhost:8000/api/health"
    echo "    5. docker start redis-master"
    log_warn "故障转移测试需手动执行(避免影响生产)"
else
    log_info "哨兵未部署, 跳过故障转移测试"
fi

# ============================================================
# 汇总报告
# ============================================================

log_section "汇总报告"

TOTAL=$((PASS + FAIL + WARN))
echo ""
echo "  检查项总数: $TOTAL"
echo -e "  ${GREEN}通过: $PASS${NC}"
echo -e "  ${RED}失败: $FAIL${NC}"
echo -e "  ${YELLOW}警告: $WARN${NC}"
echo ""

if [ "$FAIL" -eq 0 ]; then
    if [ "$WARN" -eq 0 ]; then
        echo -e "  ${GREEN}========================================${NC}"
        echo -e "  ${GREEN}  ✓ 集群与监控配置检查全部通过!${NC}"
        echo -e "  ${GREEN}========================================${NC}"
    else
        echo -e "  ${YELLOW}========================================${NC}"
        echo -e "  ${YELLOW}  ✓ 基本配置通过, 有 $WARN 个警告${NC}"
        echo -e "  ${YELLOW}========================================${NC}"
    fi
    EXIT_CODE=0
else
    echo -e "  ${RED}========================================${NC}"
    echo -e "  ${RED}  ✗ 有 $FAIL 项检查失败, 请修复后再上线${NC}"
    echo -e "  ${RED}========================================${NC}"
    EXIT_CODE=1
fi

echo ""
echo "  详细日志:"
echo "    - Prometheus Targets: http://localhost:9090/targets"
echo "    - Prometheus 告警: http://localhost:9090/rules"
echo "    - Prometheus 查询: http://localhost:9090/graph"
echo "    - Alertmanager: http://localhost:9093"
echo "    - Grafana: http://localhost:3000"
echo "    - Redis Exporter: http://localhost:9121/metrics"
echo ""

exit $EXIT_CODE
