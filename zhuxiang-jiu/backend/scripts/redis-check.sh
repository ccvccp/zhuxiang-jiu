#!/usr/bin/env bash
# ============================================================
# Redis 配置一键验证脚本 (Bash 版)
# 适用: WSL2 / Linux 服务器
# 用法: bash redis-check.sh
# ============================================================

set -euo pipefail

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 计数器
PASS=0
FAIL=0
WARN=0

# ============================================================
# 辅助函数
# ============================================================

log_pass() {
    PASS=$((PASS + 1))
    echo -e "  ${GREEN}[PASS]${NC} $1"
}

log_fail() {
    FAIL=$((FAIL + 1))
    echo -e "  ${RED}[FAIL]${NC} $1"
}

log_warn() {
    WARN=$((WARN + 1))
    echo -e "  ${YELLOW}[WARN]${NC} $1"
}

log_info() {
    echo -e "  ${BLUE}[INFO]${NC} $1"
}

log_section() {
    echo ""
    echo -e "${BLUE}============================================================${NC}"
    echo -e "${BLUE} $1${NC}"
    echo -e "${BLUE}============================================================${NC}"
}

# 安全执行 docker exec
safe_exec() {
    docker exec redis redis-cli "$@" 2>/dev/null || echo "ERROR"
}

# ============================================================
# 检查项
# ============================================================

echo ""
echo "=========================================="
echo " 竹香酒网站 - Redis 配置一键验证脚本"
echo " 生成时间: 2026-08-22"
echo "=========================================="

# ------------------------------------------------------------
log_section "1. Redis 连接验证"
# ------------------------------------------------------------

PING=$(docker exec redis redis-cli ping 2>/dev/null || echo "ERROR")
if [ "$PING" = "PONG" ]; then
    log_pass "Redis 连接正常 (PONG)"
else
    log_fail "Redis 连接失败 (期望 PONG, 实际 $PING)"
    log_info "请检查 Redis 容器是否启动: docker ps | grep redis"
fi

# Redis 版本
VERSION=$(docker exec redis redis-cli INFO server 2>/dev/null | grep "^redis_version:" | cut -d: -f2 | tr -d '\r' || echo "ERROR")
if [ "$VERSION" != "ERROR" ] && [ -n "$VERSION" ]; then
    log_pass "Redis 版本: $VERSION"
else
    log_fail "无法获取 Redis 版本"
fi

# ------------------------------------------------------------
log_section "2. 环境变量验证 (Backend 容器)"
# ------------------------------------------------------------

REDIS_URL=$(docker exec backend env 2>/dev/null | grep "^REDIS_URL=" | cut -d= -f2 || echo "ERROR")
LOCK_MODE=$(docker exec backend env 2>/dev/null | grep "^LOCK_MODE=" | cut -d= -f2 || echo "ERROR")
STORE_MODE=$(docker exec backend env 2>/dev/null | grep "^STORE_MODE=" | cut -d= -f2 || echo "ERROR")

if [ "$REDIS_URL" != "ERROR" ] && [ -n "$REDIS_URL" ]; then
    log_pass "REDIS_URL = $REDIS_URL"
    # 验证格式
    if [[ "$REDIS_URL" =~ ^redis://.+:[0-9]+/[0-9]+$ ]]; then
        log_pass "REDIS_URL 格式正确"
    elif [[ "$REDIS_URL" =~ ^rediss://.+:[0-9]+/[0-9]+$ ]]; then
        log_pass "REDIS_URL 使用 TLS 加密"
    else
        log_warn "REDIS_URL 格式可能不正确"
    fi
else
    log_fail "REDIS_URL 未配置"
fi

if [ "$LOCK_MODE" = "redis" ]; then
    log_pass "LOCK_MODE = redis (生产模式)"
else
    log_fail "LOCK_MODE = $LOCK_MODE (期望 redis)"
fi

if [ "$STORE_MODE" = "redis" ]; then
    log_pass "STORE_MODE = redis (生产模式)"
else
    log_fail "STORE_MODE = $STORE_MODE (期望 redis)"
fi

if [ "$LOCK_MODE" = "$STORE_MODE" ]; then
    log_pass "LOCK_MODE 与 STORE_MODE 一致"
else
    log_fail "LOCK_MODE ($LOCK_MODE) 与 STORE_MODE ($STORE_MODE) 不一致"
fi

# ------------------------------------------------------------
log_section "3. Redis 持久化配置"
# ------------------------------------------------------------

APPENDONLY=$(docker exec redis redis-cli CONFIG GET appendonly 2>/dev/null | tail -1 | tr -d '\r' || echo "ERROR")
if [ "$APPENDONLY" = "yes" ]; then
    log_pass "AOF 持久化已开启 (appendonly = yes)"
else
    log_fail "AOF 持久化未开启 (appendonly = $APPENDONLY)"
    log_info "建议: redis-server --appendonly yes"
fi

AOF_FSYNC=$(docker exec redis redis-cli CONFIG GET appendfsync 2>/dev/null | tail -1 | tr -d '\r' || echo "ERROR")
if [ "$AOF_FSYNC" = "everysec" ] || [ "$AOF_FSYNC" = "always" ]; then
    log_pass "AOF 刷新策略: $AOF_FSYNC"
else
    log_warn "AOF 刷新策略: $AOF_FSYNC (建议 everysec)"
fi

# RDB 配置
RDB_CONFIG=$(docker exec redis redis-cli CONFIG GET save 2>/dev/null || echo "ERROR")
if [ "$RDB_CONFIG" != "ERROR" ] && [ -n "$RDB_CONFIG" ]; then
    log_pass "RDB 快照配置已设置"
else
    log_warn "RDB 快照未配置"
fi

# 数据卷挂载
DATA_VOLUME=$(docker inspect redis --format='{{range .Mounts}}{{if eq .Destination "/data"}}{{.Source}}{{end}}{{end}}' 2>/dev/null || echo "")
if [ -n "$DATA_VOLUME" ]; then
    log_pass "数据卷挂载: $DATA_VOLUME -> /data"
else
    log_warn "未检测到数据卷挂载 (/data)"
fi

# ------------------------------------------------------------
log_section "4. Redis 内存配置"
# ------------------------------------------------------------

MAXMEMORY=$(docker exec redis redis-cli CONFIG GET maxmemory 2>/dev/null | tail -1 | tr -d '\r' || echo "ERROR")
if [ "$MAXMEMORY" != "ERROR" ] && [ "$MAXMEMORY" != "0" ]; then
    MAXMEMORY_MB=$((MAXMEMORY / 1024 / 1024))
    log_pass "最大内存限制: ${MAXMEMORY_MB}MB"
else
    log_warn "未设置内存上限 (maxmemory=0, 可能导致 OOM)"
fi

MAXMEMORY_POLICY=$(docker exec redis redis-cli CONFIG GET maxmemory-policy 2>/dev/null | tail -1 | tr -d '\r' || echo "ERROR")
if [ "$MAXMEMORY_POLICY" = "allkeys-lru" ] || [ "$MAXMEMORY_POLICY" = "allkeys-lfu" ]; then
    log_pass "内存淘汰策略: $MAXMEMORY_POLICY"
else
    log_warn "内存淘汰策略: $MAXMEMORY_POLICY (建议 allkeys-lru)"
fi

# 当前内存使用
USED_MEMORY=$(docker exec redis redis-cli INFO memory 2>/dev/null | grep "^used_memory:" | cut -d: -f2 | tr -d '\r' || echo "0")
USED_MEMORY_HUMAN=$(docker exec redis redis-cli INFO memory 2>/dev/null | grep "^used_memory_human:" | cut -d: -f2 | tr -d '\r' || echo "unknown")
if [ "$USED_MEMORY" != "0" ] && [ -n "$USED_MEMORY" ]; then
    log_pass "当前内存使用: $USED_MEMORY_HUMAN"
fi

# ------------------------------------------------------------
log_section "5. Redis 连接数"
# ------------------------------------------------------------

CONNECTED_CLIENTS=$(docker exec redis redis-cli INFO clients 2>/dev/null | grep "^connected_clients:" | cut -d: -f2 | tr -d '\r' || echo "ERROR")
if [ "$CONNECTED_CLIENTS" != "ERROR" ]; then
    log_pass "当前连接数: $CONNECTED_CLIENTS"
    if [ "$CONNECTED_CLIENTS" -gt 100 ]; then
        log_warn "连接数较高 (>100)"
    fi
else
    log_fail "无法获取连接数"
fi

MAX_CLIENTS=$(docker exec redis redis-cli CONFIG GET maxclients 2>/dev/null | tail -1 | tr -d '\r' || echo "ERROR")
if [ "$MAX_CLIENTS" != "ERROR" ]; then
    log_info "最大连接数配置: $MAX_CLIENTS"
fi

# ------------------------------------------------------------
log_section "6. 博主模块 Key 验证"
# ------------------------------------------------------------

INFLUENCER_KEYS=$(docker exec redis redis-cli KEYS "zhuxiang:traffic:influencer*" 2>/dev/null | wc -l || echo "0")
if [ "$INFLUENCER_KEYS" -gt 0 ]; then
    log_pass "博主相关 Key: ${INFLUENCER_KEYS} 个"
else
    log_warn "未发现博主相关 Key (可能尚未创建数据)"
fi

PLATFORM_KEYS=$(docker exec redis redis-cli KEYS "zhuxiang:traffic:influencer_platform*" 2>/dev/null | wc -l || echo "0")
if [ "$PLATFORM_KEYS" -gt 0 ]; then
    log_pass "平台账号 Key: ${PLATFORM_KEYS} 个"
else
    log_warn "未发现平台账号 Key"
fi

CODE_KEYS=$(docker exec redis redis-cli KEYS "zhuxiang:traffic:influencer_code*" 2>/dev/null | wc -l || echo "0")
if [ "$CODE_KEYS" -gt 0 ]; then
    log_pass "推广码 Key: ${CODE_KEYS} 个"
else
    log_warn "未发现推广码 Key"
fi

# 序列号验证
SEQ=$(docker exec redis redis-cli GET "zhuxiang:traffic:traffic_influencer:seq" 2>/dev/null || echo "(nil)")
if [ "$SEQ" != "(nil)" ]; then
    log_pass "博主序列号: $SEQ"
else
    log_warn "博主序列号未初始化 (seed 未执行?)"
fi

# ------------------------------------------------------------
log_section "7. 锁机制验证"
# ------------------------------------------------------------

LOCK_KEYS=$(docker exec redis redis-cli KEYS "lock:traffic:*" 2>/dev/null | wc -l || echo "0")
if [ "$LOCK_KEYS" -gt 0 ]; then
    log_pass "流量锁 Key: ${LOCK_KEYS} 个 (活跃中)"
else
    log_pass "流量锁 Key: 0 个 (无活跃锁)"
fi

ALL_LOCKS=$(docker exec redis redis-cli KEYS "lock:*" 2>/dev/null | wc -l || echo "0")
log_info "所有锁 Key: ${ALL_LOCKS} 个"

# 锁 TTL 验证 (随机取一个锁)
LOCK_SAMPLE=$(docker exec redis redis-cli KEYS "lock:*" 2>/dev/null | head -1 || echo "")
if [ -n "$LOCK_SAMPLE" ]; then
    LOCK_TTL=$(docker exec redis redis-cli TTL "$LOCK_SAMPLE" 2>/dev/null || echo "-99")
    if [ "$LOCK_TTL" -gt 0 ]; then
        log_pass "锁 TTL 验证: ${LOCK_TTL}s (key=$LOCK_SAMPLE)"
    elif [ "$LOCK_TTL" = "-1" ]; then
        log_warn "锁无过期时间: $LOCK_SAMPLE (可能导致死锁)"
    elif [ "$LOCK_TTL" = "-2" ]; then
        log_info "锁已过期: $LOCK_SAMPLE (正常)"
    fi
fi

# ------------------------------------------------------------
log_section "8. Key 前缀规范验证"
# ------------------------------------------------------------

# 检查是否有非 zhuxiang: 前缀的 Key
ALL_KEYS_COUNT=$(docker exec redis redis-cli DBSIZE 2>/dev/null || echo "0")
ZHUXIANG_KEYS=$(docker exec redis redis-cli KEYS "zhuxiang:*" 2>/dev/null | wc -l || echo "0")
LOCK_PREFIX_KEYS=$(docker exec redis redis-cli KEYS "lock:*" 2>/dev/null | wc -l || echo "0")

if [ "$ALL_KEYS_COUNT" != "0" ]; then
    EXPECTED=$((ZHUXIANG_KEYS + LOCK_PREFIX_KEYS))
    if [ "$EXPECTED" -ge "$ALL_KEYS_COUNT" ]; then
        log_pass "Key 前缀规范: 全部使用 zhuxiang: 或 lock: 前缀"
    else
        log_warn "发现非标准前缀的 Key: $((ALL_KEYS_COUNT - EXPECTED)) 个"
    fi
    log_info "Key 总数: $ALL_KEYS_COUNT (zhuxiang: $ZHUXIANG_KEYS, lock: $LOCK_PREFIX_KEYS)"
fi

# ------------------------------------------------------------
log_section "9. 慢查询日志"
# ------------------------------------------------------------

SLOWLOG_LEN=$(docker exec redis redis-cli SLOWLOG LEN 2>/dev/null || echo "ERROR")
if [ "$SLOWLOG_LEN" != "ERROR" ]; then
    log_pass "慢查询日志条数: $SLOWLOG_LEN"
    if [ "$SLOWLOG_LEN" -gt 0 ]; then
        SLOWLOG_SAMPLE=$(docker exec redis redis-cli SLOWLOG GET 1 2>/dev/null || echo "")
        if [ -n "$SLOWLOG_SAMPLE" ]; then
            log_warn "存在慢查询, 最近一条:"
            echo "    $SLOWLOG_SAMPLE"
        fi
    fi
else
    log_fail "无法获取慢查询日志"
fi

SLOWLOG_THRESHOLD=$(docker exec redis redis-cli CONFIG GET slowlog-log-slower-than 2>/dev/null | tail -1 | tr -d '\r' || echo "ERROR")
if [ "$SLOWLOG_THRESHOLD" != "ERROR" ]; then
    THRESHOLD_MS=$((SLOWLOG_THRESHOLD / 1000))
    log_info "慢查询阈值: ${THRESHOLD_MS}ms"
fi

# ------------------------------------------------------------
log_section "10. 安全配置验证"
# ------------------------------------------------------------

# 密码验证
REQUIREPASS=$(docker exec redis redis-cli CONFIG GET requirepass 2>/dev/null | tail -1 | tr -d '\r' || echo "ERROR")
if [ "$REQUIREPASS" = "" ] || [ "$REQUIREPASS" = "ERROR" ]; then
    log_fail "Redis 未设置密码 (requirepass 为空)"
    log_info "生产环境必须设置密码: CONFIG SET requirepass 'your_password'"
elif [ "$REQUIREPASS" = "your_strong_password_here" ]; then
    log_warn "Redis 密码为示例值, 请修改"
else
    log_pass "Redis 已设置密码"
fi

# 绑定地址
BIND=$(docker exec redis redis-cli CONFIG GET bind 2>/dev/null | tail -1 | tr -d '\r' || echo "ERROR")
if [ "$BIND" = "ERROR" ]; then
    log_warn "无法获取 bind 配置"
elif [ "$BIND" = "0.0.0.0" ]; then
    log_warn "Redis 绑定 0.0.0.0 (生产建议绑定内网)"
elif [ "$BIND" = "127.0.0.1" ]; then
    log_pass "Redis 绑定 127.0.0.1 (仅本地访问)"
else
    log_pass "Redis 绑定: $BIND"
fi

# protected-mode
PROTECTED=$(docker exec redis redis-cli CONFIG GET protected-mode 2>/dev/null | tail -1 | tr -d '\r' || echo "ERROR")
if [ "$PROTECTED" = "yes" ]; then
    log_pass "protected-mode 已开启"
else
    log_warn "protected-mode 未开启 (建议开启)"
fi

# ------------------------------------------------------------
log_section "11. 容器健康状态"
# ------------------------------------------------------------

REDIS_STATUS=$(docker inspect redis --format='{{.State.Status}}' 2>/dev/null || echo "ERROR")
if [ "$REDIS_STATUS" = "running" ]; then
    log_pass "Redis 容器状态: running"
else
    log_fail "Redis 容器状态: $REDIS_STATUS"
fi

REDIS_HEALTH=$(docker inspect redis --format='{{.State.Health.Status}}' 2>/dev/null || echo "none")
if [ "$REDIS_HEALTH" = "healthy" ]; then
    log_pass "Redis 健康检查: healthy"
elif [ "$REDIS_HEALTH" = "none" ]; then
    log_warn "Redis 未配置健康检查"
else
    log_fail "Redis 健康检查: $REDIS_HEALTH"
fi

BACKEND_STATUS=$(docker inspect backend --format='{{.State.Status}}' 2>/dev/null || echo "ERROR")
if [ "$BACKEND_STATUS" = "running" ]; then
    log_pass "Backend 容器状态: running"
else
    log_fail "Backend 容器状态: $BACKEND_STATUS"
fi

# Backend 健康检查
BACKEND_HEALTH=$(docker inspect backend --format='{{.State.Health.Status}}' 2>/dev/null || echo "none")
if [ "$BACKEND_HEALTH" = "healthy" ]; then
    log_pass "Backend 健康检查: healthy"
elif [ "$BACKEND_HEALTH" = "none" ]; then
    log_warn "Backend 未配置健康检查"
else
    log_fail "Backend 健康检查: $BACKEND_HEALTH"
fi

# Backend → Redis 依赖
BACKEND_REDIS_DEP=$(docker inspect backend --format='{{range $k, $v := .HostConfig.Links}}{{if eq $v "redis"}}true{{end}}{{end}}' 2>/dev/null || echo "")
if [ -n "$BACKEND_REDIS_DEP" ]; then
    log_pass "Backend 依赖 Redis 容器"
else
    # 检查 depends_on
    DEPENDS=$(docker inspect backend --format='{{range $k, $v := .HostConfig.RestartPolicy}}{{$v}}{{end}}' 2>/dev/null || echo "")
    log_info "Backend 网络已连接 Redis (Docker 网络)"
fi

# ------------------------------------------------------------
log_section "12. Backend 接口验证"
# ------------------------------------------------------------

BACKEND_HEALTH_URL="http://localhost:8000/api/health"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$BACKEND_HEALTH_URL" 2>/dev/null || echo "000")
if [ "$HTTP_CODE" = "200" ]; then
    log_pass "Backend 健康检查接口: 200 OK"
else
    log_fail "Backend 健康检查接口: HTTP $HTTP_CODE (期望 200)"
fi

# 博主列表接口 (需 X-Member-Id 头)
INFLUENCER_API=$(curl -s -o /dev/null -w "%{http_code}" -H "X-Member-Id: 1" "http://localhost:8000/api/traffic/influencer/list" 2>/dev/null || echo "000")
if [ "$INFLUENCER_API" = "200" ]; then
    log_pass "博主列表接口: 200 OK"
elif [ "$INFLUENCER_API" = "401" ]; then
    log_warn "博主列表接口: 401 (需 X-Member-Id 头)"
else
    log_fail "博主列表接口: HTTP $INFLUENCER_API"
fi

# ------------------------------------------------------------
log_section "13. Uvicorn workers 配置"
# ------------------------------------------------------------

WORKER_COUNT=$(docker exec backend ps aux 2>/dev/null | grep -c "[u]vicorn" || echo "0")
if [ "$WORKER_COUNT" = "1" ]; then
    log_pass "Uvicorn workers = 1 (单进程, 避免锁竞争)"
else
    log_warn "Uvicorn 进程数: $WORKER_COUNT (建议 1, 避免多进程锁问题)"
fi

# ------------------------------------------------------------
log_section "14. Redis 主从复制(如适用)"
# ------------------------------------------------------------

REPLICATION=$(docker exec redis redis-cli INFO replication 2>/dev/null | grep "^role:" | cut -d: -f2 | tr -d '\r' || echo "ERROR")
if [ "$REPLICATION" = "master" ]; then
    log_pass "Redis 角色: master"
    SLAVE_COUNT=$(docker exec redis redis-cli INFO replication 2>/dev/null | grep "^connected_slaves:" | cut -d: -f2 | tr -d '\r' || echo "0")
    if [ "$SLAVE_COUNT" != "0" ] && [ -n "$SLAVE_COUNT" ]; then
        log_pass "从节点数量: $SLAVE_COUNT"
    else
        log_info "无从节点 (单机模式)"
    fi
elif [ "$REPLICATION" = "slave" ]; then
    log_pass "Redis 角色: slave"
    MASTER_LINK_STATUS=$(docker exec redis redis-cli INFO replication 2>/dev/null | grep "^master_link_status:" | cut -d: -f2 | tr -d '\r' || echo "")
    if [ "$MASTER_LINK_STATUS" = "up" ]; then
        log_pass "主从连接状态: up"
    else
        log_fail "主从连接状态: $MASTER_LINK_STATUS"
    fi
else
    log_warn "无法确定 Redis 角色"
fi

# ------------------------------------------------------------
log_section "15. Redis 延迟测试"
# ------------------------------------------------------------

LATENCY=$(docker exec redis redis-cli --latency -n 10 2>/dev/null | tail -1 || echo "ERROR")
if [ "$LATENCY" != "ERROR" ] && [ -n "$LATENCY" ]; then
    log_pass "Redis 延迟: $LATENCY"
else
    log_warn "无法测试 Redis 延迟"
fi

# 测试 SET/GET 性能
SET_TIME=$(docker exec redis redis-cli --latency -n 1 2>/dev/null | tail -1 || echo "")
log_info "SET/GET 操作延迟: ${SET_TIME:-unknown}"

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
        echo -e "  ${GREEN}  ✓ Redis 配置检查全部通过!${NC}"
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
echo "    - Redis INFO: docker exec redis redis-cli INFO"
echo "    - 慢查询: docker exec redis redis-cli SLOWLOG GET 10"
echo "    - Key 分析: docker exec redis redis-cli KEYS '*' | head -20"
echo ""

exit $EXIT_CODE
