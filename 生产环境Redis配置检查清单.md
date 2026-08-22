# 生产环境 Redis 配置检查清单

> 适用版本: commit `ab65d97` 及之后
> 生成时间: 2026-08-22

## 一、环境变量配置

### 1.1 必需环境变量

| 变量名 | 必需 | 生产推荐值 | 说明 | 验证命令 |
|--------|------|-----------|------|---------|
| `REDIS_URL` | ✅ | `redis://redis:6379/0` | Redis 连接地址 | `echo $REDIS_URL` |
| `LOCK_MODE` | ✅ | `redis` | 锁模式(必须 redis) | `echo $LOCK_MODE` |
| `STORE_MODE` | ✅ | `redis` | 存储模式(必须 redis) | `echo $STORE_MODE` |

### 1.2 配置验证清单

- [ ] `REDIS_URL` 格式正确: `redis://host:port/db`
- [ ] `LOCK_MODE=redis`(不是 asyncio)
- [ ] `STORE_MODE=redis`(不是 asyncio)
- [ ] `STORE_MODE` 与 `LOCK_MODE` 一致(都为 redis)
- [ ] Docker Compose 中已配置上述环境变量

### 1.3 Docker Compose 配置验证

检查 [docker-compose.yml](file:///d:/网站架构设计/docker-compose.yml) 第 7-9 行:

```yaml
environment:
  - REDIS_URL=redis://redis:6379/0    # 必须指向 redis 服务
  - LOCK_MODE=redis                    # 生产必须为 redis
  - STORE_MODE=redis                   # 生产必须为 redis
```

---

## 二、Redis 服务配置

### 2.1 Redis 实例配置

| 配置项 | 推荐值 | 说明 | 验证命令 |
|--------|--------|------|---------|
| Redis 版本 | 7.0+ | 使用 `redis:7-alpine` 镜像 | `docker exec redis redis-cli INFO server` |
| 持久化模式 | AOF | `--appendonly yes` | `docker exec redis redis-cli CONFIG GET appendonly` |
| 最大内存 | 512MB+ | 根据业务量调整 | `docker exec redis redis-cli CONFIG GET maxmemory` |
| 内存淘汰策略 | `allkeys-lru` | 防止 OOM | `docker exec redis redis-cli CONFIG GET maxmemory-policy` |
| 绑定地址 | `0.0.0.0` 或内网 | 容器内通信 | `docker exec redis redis-cli CONFIG GET bind` |

### 2.2 Redis 健康检查

- [ ] Redis 容器健康: `docker exec redis redis-cli ping` → `PONG`
- [ ] AOF 持久化开启: `docker exec redis redis-cli CONFIG GET appendonly` → `appendonly yes`
- [ ] 数据卷挂载: `redis-data:/data` 已配置
- [ ] Redis 容器自动重启: `restart: unless-stopped`

---

## 三、连接池配置

### 3.1 连接池参数(代码层)

当前代码使用 `redis.from_url()` 默认连接池配置:

| 参数 | 默认值 | 生产建议 | 位置 |
|------|--------|---------|------|
| `max_connections` | 50 | 100+ | [backend.py:39](file:///d:/网站架构设计/zhuxiang-jiu/backend/repositories/backend.py#L39) |
| `socket_timeout` | 默认 | 5s | 建议增加 |
| `socket_connect_timeout` | 默认 | 3s | 建议增加 |
| `retry_on_timeout` | False | True | 建议开启 |
| `decode_responses` | True | True | 已配置 ✓ |

### 3.2 双连接池验证

系统使用两个独立的 Redis 客户端:

| 客户端 | 用途 | 位置 |
|--------|------|------|
| `repositories.backend._redis_client` | 数据存储 CRUD | [backend.py:27](file:///d:/网站架构设计/zhuxiang-jiu/backend/repositories/backend.py#L27) |
| `core.locks._redis_client` | 分布式锁 | [locks.py:26](file:///d:/网站架构设计/zhuxiang-jiu/backend/core/locks.py#L26) |

- [ ] 两个客户端共享 `REDIS_URL`(连到同一 Redis 实例)
- [ ] 两个客户端独立连接池(避免锁操作与数据操作互相阻塞)

---

## 四、锁机制验证

### 4.1 锁参数配置

| 参数 | 值 | 说明 | 位置 |
|------|------|------|------|
| `_LOCK_TTL` | 10.0s | 锁超时自动释放 | [locks.py:21](file:///d:/网站架构设计/zhuxiang-jiu/backend/core/locks.py#L21) |
| `_LOCK_BLOCK_TIMEOUT` | 30.0s | 等待获取锁的最长时间 | [locks.py:22](file:///d:/网站架构设计/zhuxiang-jiu/backend/core/locks.py#L22) |
| `_ASYNC_LOCKS_MAX_SIZE` | 512 | asyncio 锁缓存上限(防泄漏) | [locks.py:23](file:///d:/网站架构设计/zhuxiang-jiu/backend/core/locks.py#L23) |

### 4.2 锁键格式验证

| 锁键 | 格式 | 用途 |
|------|------|------|
| 博主创建 | `lock:traffic:influencer:create:{user_id}` | 防重复创建 |
| 平台关联 | `lock:traffic:inf_platform:{inf_id}:{platform}` | 唯一约束保护 |
| 平台同步 | `lock:traffic:inf_platform_sync:{platform_id}` | 并发同步保护 |
| 推广码生成 | `lock:traffic:inf_code:{inf_id}:{platform}` | 推广码唯一性 |
| 流量归因 | `lock:traffic:attribute:{code_id}` | 统计原子更新 |

### 4.3 锁验证清单

- [ ] `LOCK_MODE=redis`(不是 asyncio)
- [ ] 锁 TTL 10s 合理(业务操作 < 10s)
- [ ] 等待超时 30s 合理(避免长时间阻塞)
- [ ] Redis 锁 watchdog 自动续期生效(redis-py 内置)
- [ ] 锁释放异常处理已配置([locks.py:86](file:///d:/网站架构设计/zhuxiang-jiu/backend/core/locks.py#L86))

---

## 五、Key 前缀与命名规范

### 5.1 Key 前缀

| 前缀 | 值 | 位置 |
|------|------|------|
| 全局前缀 | `zhuxiang:` | [backend.py:24](file:///d:/网站架构设计/zhuxiang-jiu/backend/repositories/backend.py#L24) |
| 锁前缀 | `lock:` | [locks.py:74](file:///d:/网站架构设计/zhuxiang-jiu/backend/core/locks.py#L74) |

### 5.2 博主模块 Key 命名

| Key 模式 | 示例 | 说明 |
|---------|------|------|
| `zhuxiang:traffic:influencer:{id}` | `zhuxiang:traffic:influencer:1` | 博主主表 |
| `zhuxiang:traffic:influencer_platform:{id}` | `zhuxiang:traffic:influencer_platform:1` | 平台账号 |
| `zhuxiang:traffic:inf_platforms_by_inf:{id}` | `zhuxiang:traffic:inf_platforms_by_inf:1` | 博主平台列表 |
| `zhuxiang:traffic:influencer_code:{id}` | `zhuxiang:traffic:influencer_code:1` | 推广码 |
| `zhuxiang:traffic:inf_code_by_code:{code}` | `zhuxiang:traffic:inf_code_by_code:KOL1_douyin_ABC12345` | 推广码索引 |
| `zhuxiang:traffic:inf_codes_by_inf:{id}` | `zhuxiang:traffic:inf_codes_by_inf:1` | 博主推广码列表 |
| `zhuxiang:traffic:traffic_influencer:seq` | `zhuxiang:traffic:traffic_influencer:seq` | 博主ID序列 |
| `zhuxiang:traffic:traffic_influencer_platform:seq` | - | 平台ID序列 |
| `zhuxiang:traffic:traffic_influencer_code:seq` | - | 推广码ID序列 |
| `lock:traffic:influencer:create:{user_id}` | `lock:traffic:influencer:create:2001` | 博主创建锁 |
| `lock:traffic:inf_platform:{inf_id}:{platform}` | - | 平台关联锁 |
| `lock:traffic:attribute:{code_id}` | - | 归因锁 |

### 5.3 验证命令

```bash
# 查看博主相关 Key
docker exec redis redis-cli KEYS "zhuxiang:traffic:influencer*"

# 查看推广码索引
docker exec redis redis-cli KEYS "zhuxiang:traffic:inf_code_by_code*"

# 查看锁
docker exec redis redis-cli KEYS "lock:traffic:*"

# 查看序列号
docker exec redis redis-cli GET "zhuxiang:traffic:traffic_influencer:seq"
```

---

## 六、数据持久化验证

### 6.1 AOF 持久化

- [ ] AOF 开启: `redis-cli CONFIG GET appendonly` → `yes`
- [ ] AOF 文件路径: `/data/appendonly.aof`
- [ ] AOF 刷新策略: `everysec`(默认,平衡性能与安全)
- [ ] 数据卷挂载: `redis-data:/data`

### 6.2 数据备份

- [ ] 定期备份 AOF 文件
- [ ] 备份频率: 每日至少 1 次
- [ ] 备份保留: 7 天
- [ ] 备份验证: 可从备份恢复

---

## 七、监控与告警

### 7.1 Redis 监控指标

| 指标 | 告警阈值 | 验证命令 |
|------|---------|---------|
| 内存使用率 | > 80% | `redis-cli INFO memory` |
| 连接数 | > 100 | `redis-cli INFO clients` |
| 慢查询 | > 10ms | `redis-cli SLOWLOG GET 10` |
| 键空间命中率 | < 90% | `redis-cli INFO stats` |
| 主从延迟 | > 1s | `redis-cli INFO replication` |

### 7.2 应用层监控

| 指标 | 告警阈值 | 位置 |
|------|---------|------|
| Redis 连接失败 | 任何一次 | `get_redis_client()` 异常 |
| 锁获取超时 | > 30s | `_LOCK_BLOCK_TIMEOUT` |
| 锁释放失败 | 任何一次 | [locks.py:87](file:///d:/网站架构设计/zhuxiang-jiu/backend/core/locks.py#L87) 日志 |
| Redis 操作延迟 | > 100ms | 应用层日志 |

---

## 八、安全配置

### 8.1 Redis 认证

- [ ] 生产环境已设置 Redis 密码: `requirepass`
- [ ] `REDIS_URL` 包含密码: `redis://:password@host:port/db`
- [ ] 密码不在代码中硬编码
- [ ] 密码通过环境变量或 Secrets 管理

### 8.2 网络安全

- [ ] Redis 不暴露公网: `bind 127.0.0.1` 或内网
- [ ] Docker 网络隔离: 使用自定义网络
- [ ] 防火墙规则: 仅允许 backend 容器访问 Redis

### 8.3 数据安全

- [ ] 敏感数据(如 platformUid)传输加密
- [ ] Redis 通信加密: `rediss://` (TLS)
- [ ] 定期清理过期数据

---

## 九、容器部署验证

### 9.1 Docker Compose 配置

检查 [docker-compose.yml](file:///d:/网站架构设计/docker-compose.yml):

```yaml
services:
  backend:
    environment:
      - REDIS_URL=redis://redis:6379/0    # ✅ 指向 redis 服务
      - LOCK_MODE=redis                   # ✅ 生产模式
      - STORE_MODE=redis                  # ✅ 生产模式
    depends_on:
      redis:
        condition: service_healthy        # ✅ 等待 Redis 健康
    command: >
      sh -c "cd /app &&
             python scripts/seed_redis.py || echo '[WARN] seed failed...';
             exec uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1 --no-access-log"
```

### 9.2 部署前验证清单

- [ ] Redis 容器健康检查通过: `docker exec redis redis-cli ping` → `PONG`
- [ ] Backend 容器依赖 Redis 健康: `condition: service_healthy`
- [ ] Backend 健康检查通过: `curl http://localhost:8000/api/health` → 200
- [ ] Seed 脚本执行成功: `python scripts/seed_redis.py`
- [ ] Uvicorn workers=1(避免多进程锁问题)

### 9.3 容器资源限制

| 资源 | 限制 | 说明 |
|------|------|------|
| Backend 内存 | 1g | `mem_limit: 1g` |
| Backend swap | 2g | `memswap_limit: 2g` |
| Backend 预留 | 512m | `mem_reservation: 512m` |
| OOM kill | 允许 | `oom_kill_disable: false` |
| Redis 内存 | 建议 512m+ | 通过 `maxmemory` 配置 |

---

## 十、故障恢复

### 10.1 Redis 宕机恢复

| 步骤 | 命令 | 说明 |
|------|------|------|
| 1 | `docker restart redis` | 重启 Redis 容器 |
| 2 | `docker exec redis redis-cli ping` | 验证 Redis 恢复 |
| 3 | `docker restart backend` | 重启 Backend(重新连接) |
| 4 | `curl http://localhost:8000/api/health` | 验证应用恢复 |
| 5 | `python scripts/seed_redis.py` | 重新 Seed 数据(幂等) |

### 10.2 数据丢失恢复

| 步骤 | 说明 |
|------|------|
| 1 | 停止 backend: `docker stop backend` |
| 2 | 恢复 AOF 备份: `cp backup/appendonly.aof /data/` |
| 3 | 重启 Redis: `docker restart redis` |
| 4 | 验证数据: `redis-cli DBSIZE` |
| 5 | 启动 backend: `docker start backend` |

---

## 十一、上线前最终检查

### 11.1 功能验证

- [ ] 博主创建成功: `POST /api/traffic/influencer/create`
- [ ] 平台关联成功: `POST /api/traffic/influencer/{id}/platform`
- [ ] 推广码生成成功: `POST /api/traffic/influencer/{id}/promo-code`
- [ ] 流量归因成功: `POST /api/traffic/influencer/attribute`
- [ ] 归因查询成功: `GET /api/traffic/influencer/{id}/attribution`
- [ ] Redis 中数据可见: `redis-cli KEYS "zhuxiang:traffic:influencer*"`

### 11.2 性能验证

- [ ] 博主创建延迟 < 100ms
- [ ] 平台关联延迟 < 100ms
- [ ] 归因查询延迟 < 200ms(多平台汇总)
- [ ] 并发归因无冲突(锁保护有效)

### 11.3 监控告警

- [ ] Redis 内存监控已配置
- [ ] Redis 连接数监控已配置
- [ ] 慢查询日志已开启
- [ ] 应用层 Redis 异常告警已配置

---

## 十二、回滚方案

### 12.1 代码回滚

```bash
# 回滚到上一个版本
git revert ab65d97 --no-edit
git push origin master

# 重新部署
docker-compose up -d --build backend
```

### 12.2 Redis 数据清理

```bash
# 清理博主相关数据(谨慎操作!)
docker exec redis redis-cli KEYS "zhuxiang:traffic:influencer*" | xargs -I {} docker exec redis redis-cli DEL {}

# 清理锁
docker exec redis redis-cli KEYS "lock:traffic:*" | xargs -I {} docker exec redis redis-cli DEL {}
```

---

## 十三、Redis 集群/哨兵配置(高可用)

> 适用场景: 生产环境高可用部署，单机模式可跳过本节

### 13.1 部署模式选择

| 模式 | 适用场景 | 可用性 | 复杂度 | 推荐 |
|------|---------|--------|--------|------|
| **单机模式** | 开发/测试 | 低 | ★ | 开发环境 |
| **主从模式** | 读密集型 | 中 | ★★ | 小规模生产 |
| **哨兵模式** | 自动故障转移 | 高 | ★★★ | 中规模生产(推荐) |
| **Cluster 集群** | 大数据量/高并发 | 极高 | ★★★★ | 大规模生产 |

### 13.2 哨兵模式(Sentinel)配置

#### 架构拓扑

```
                    +-----------+
                    | Sentinel1 | (监控)
                    +-----------+
                         |
    +-------------+      |      +-------------+
    | Redis Master|<-----+----->| Redis Slave |
    | (read/write)|      |      | (read only) |
    +-------------+      |      +-------------+
                         |
                    +-----------+
                    | Sentinel2 | (监控)
                    +-----------+
                         |
                    +-----------+
                    | Sentinel3 | (仲裁)
                    +-----------+
                         |
                    +-----------+
                    |  Backend  | (通过 Sentinel 连接)
                    +-----------+
```

#### Sentinel 配置文件 (`sentinel.conf`)

```conf
# sentinel.conf
port 26379
dir /data
sentinel monitor mymaster 192.168.1.100 6379 2
sentinel down-after-milliseconds mymaster 5000
sentinel failover-timeout mymaster 30000
sentinel parallel-syncs mymaster 1
sentinel deny-scripts-reconfig yes

# 安全配置
requirepass YourSentinelPassword
sentinel auth-pass mymaster YourRedisPassword
```

#### 参数说明

| 参数 | 推荐值 | 说明 |
|------|--------|------|
| `sentinel monitor` | `mymaster <ip> <port> <quorum>` | 监控的 master 名称/IP/端口/仲裁数 |
| `down-after-milliseconds` | 5000 (5s) | 节点失联多久判定为主观下线 |
| `failover-timeout` | 30000 (30s) | 故障转移超时时间 |
| `parallel-syncs` | 1 | 同时同步的 slave 数量 |
| `quorum` | 2 | 仲裁数(3 个 Sentinel 中 2 个同意才故障转移) |

#### Docker Compose 哨兵部署

```yaml
version: '3.8'

services:
  redis-master:
    image: redis:7-alpine
    command: redis-server --appendonly yes --requirepass ${REDIS_PASSWORD}
    volumes:
      - redis-master-data:/data
    networks:
      - redis-network

  redis-slave:
    image: redis:7-alpine
    command: redis-server --slaveof redis-master 6379 --masterauth ${REDIS_PASSWORD} --requirepass ${REDIS_PASSWORD}
    depends_on:
      - redis-master
    networks:
      - redis-network

  sentinel1:
    image: redis:7-alpine
    command: redis-sentinel /etc/redis/sentinel.conf
    volumes:
      - ./sentinel.conf:/etc/redis/sentinel.conf
    depends_on:
      - redis-master
      - redis-slave
    networks:
      - redis-network

  sentinel2:
    image: redis:7-alpine
    command: redis-sentinel /etc/redis/sentinel.conf
    volumes:
      - ./sentinel.conf:/etc/redis/sentinel.conf
    depends_on:
      - redis-master
      - redis-slave
    networks:
      - redis-network

  sentinel3:
    image: redis:7-alpine
    command: redis-sentinel /etc/redis/sentinel.conf
    volumes:
      - ./sentinel.conf:/etc/redis/sentinel.conf
    depends_on:
      - redis-master
      - redis-slave
    networks:
      - redis-network

  backend:
    environment:
      - REDIS_URL=redis+sentinel://sentinel1:26379,sentinel2:26379,sentinel3:26379/mymaster/0
      - LOCK_MODE=redis
      - STORE_MODE=redis
    depends_on:
      - sentinel1
      - sentinel2
      - sentinel3
    networks:
      - redis-network

volumes:
  redis-master-data:

networks:
  redis-network:
    driver: bridge
```

### 13.3 Cluster 集群配置

#### 架构拓扑

```
  +----------+   +----------+   +----------+
  | Node 1   |   | Node 2   |   | Node 3   |
  | Master 1 |   | Master 2 |   | Master 3 |
  | Slots    |   | Slots    |   | Slots    |
  | 0-5460   |   | 5461-10922|  | 10923-16383|
  +----------+   +----------+   +----------+
       |              |              |
  +----------+   +----------+   +----------+
  | Node 4   |   | Node 5   |   | Node 6   |
  | Slave 1  |   | Slave 2  |   | Slave 3  |
  +----------+   +----------+   +----------+
```

#### Cluster 配置

```conf
# redis-cluster.conf
cluster-enabled yes
cluster-config-file nodes.conf
cluster-node-timeout 5000
cluster-require-full-coverage yes
cluster-migration-barrier 1
cluster-allow-reads-when-down no

appendonly yes
maxmemory 512mb
maxmemory-policy allkeys-lru
```

#### 创建集群命令

```bash
# 创建 3 主 3 从集群
redis-cli --cluster create \
  192.168.1.101:6379 \
  192.168.1.102:6379 \
  192.168.1.103:6379 \
  192.168.1.104:6379 \
  192.168.1.105:6379 \
  192.168.1.106:6379 \
  --cluster-replicas 1
```

#### Backend 连接 Cluster

```python
# 代码层连接 Cluster
from redis.cluster import RedisCluster

client = RedisCluster(
    startup_nodes=[
        {"host": "192.168.1.101", "port": 6379},
        {"host": "192.168.1.102", "port": 6379},
        {"host": "192.168.1.103", "port": 6379},
    ],
    decode_responses=True,
    password="YourRedisPassword",
    max_connections=100,
)
```

### 13.4 高可用验证清单

#### 哨兵模式验证

- [ ] Sentinel 节点数量 ≥ 3 (奇数, 避免脑裂)
- [ ] Sentinel 仲裁数(quorum) = `(n/2) + 1`
- [ ] `down-after-milliseconds` 配置合理 (5s)
- [ ] `failover-timeout` 配置合理 (30s)
- [ ] 故障转移测试通过 (手动 kill master, 验证自动切换)
- [ ] Slave 只读模式开启 (`replica-read-only yes`)
- [ ] Backend 连接使用 Sentinel 地址 (不是直连 master)
- [ ] Sentinel 认证密码已配置 (`requirepass` + `auth-pass`)

#### Cluster 集群验证

- [ ] 至少 3 主 3 从 (6 节点)
- [ ] 16384 个 slot 全部分配
- [ ] `cluster-node-timeout` 配置合理 (5s)
- [ ] `cluster-require-full-coverage yes` (任一节点宕机集群不可用)
- [ ] 故障转移测试通过
- [ ] Slot 迁移测试通过
- [ ] Backend 使用 `RedisCluster` 客户端 (不是普通 `Redis`)

### 13.5 故障转移测试

```bash
# 1. 查看当前 master
docker exec sentinel1 redis-cli -p 26379 SENTINEL get-master-addr-by-name mymaster

# 2. 模拟 master 宕机
docker stop redis-master

# 3. 等待故障转移 (5-30s)
sleep 30

# 4. 查看新 master
docker exec sentinel1 redis-cli -p 26379 SENTINEL get-master-addr-by-name mymaster

# 5. 验证 backend 可正常访问
curl http://localhost:8000/api/health

# 6. 恢复原 master (变为 slave)
docker start redis-master
```

### 13.6 高可用监控指标

| 指标 | 告警阈值 | 验证命令 |
|------|---------|---------|
| Sentinel 在线数 | < 3 | `redis-cli -p 26379 SENTINEL masters` |
| Master 下线 | 任何一次 | `SENTINEL masters` 中 `s_down`/`o_down` |
| 故障转移次数 | > 0 (需关注) | `SENTINEL master <name>` 中 `failover-count` |
| Slave 延迟 | > 1s | `redis-cli INFO replication` 中 `master_repl_offset` |
| Cluster 状态 | != ok | `redis-cli CLUSTER INFO` 中 `cluster_state` |
| Slot 覆盖率 | < 100% | `redis-cli CLUSTER INFO` 中 `cluster_slots_assigned` |

---

## 十四、Prometheus 监控部署方案

### 14.1 监控架构

```
+-----------+     +-----------+     +-----------+
|  Redis    |     |  Backend  |     |  Docker   |
|  Exporter |     |  Metrics  |     |  Stats    |
+-----------+     +-----------+     +-----------+
      |                |                |
      +----------------+----------------+
                      |
                +-----------+
                | Prometheus | (采集 + 存储)
                +-----------+
                      |
                +-----------+
                | Grafana   | (可视化 + 告警)
                +-----------+
                      |
                +-----------+
                | AlertMgr  | (告警通知)
                +-----------+
```

### 14.2 组件清单

| 组件 | 版本 | 用途 | 端口 |
|------|------|------|------|
| redis-exporter | 1.55+ | Redis 指标采集 | 9121 |
| node-exporter | 1.6+ | 主机指标采集 | 9100 |
| cadvisor | 0.47+ | 容器指标采集 | 8080 |
| prometheus | 2.45+ | 指标存储 + 查询 | 9090 |
| grafana | 10.0+ | 可视化面板 | 3000 |
| alertmanager | 0.26+ | 告警路由 | 9093 |

### 14.3 Docker Compose 监控部署

```yaml
# docker-compose.monitoring.yml
version: '3.8'

services:
  redis-exporter:
    image: redis_exporter:1.55-alpine
    command:
      - --redis.addr=redis:6379
      - --redis.password=${REDIS_PASSWORD}
      - --redis.export-missing-keys=false
      - --check-key-groups="zhuxiang:*,lock:*"
    ports:
      - "9121:9121"
    networks:
      - monitoring

  node-exporter:
    image: node_exporter:1.6-alpine
    ports:
      - "9100:9100"
    networks:
      - monitoring

  cadvisor:
    image: gcr.io/cadvisor/cadvisor:v0.47.0
    ports:
      - "8080:8080"
    volumes:
      - /:/rootfs:ro
      - /var/run:/var/run:ro
      - /sys:/sys:ro
      - /var/lib/docker:/var/lib/docker:ro
    networks:
      - monitoring

  prometheus:
    image: prom/prometheus:v2.45.0
    ports:
      - "9090:9090"
    volumes:
      - ./prometheus/prometheus.yml:/etc/prometheus/prometheus.yml
      - ./prometheus/alert_rules.yml:/etc/prometheus/alert_rules.yml
      - prometheus-data:/prometheus
    command:
      - '--config.file=/etc/prometheus/prometheus.yml'
      - '--storage.tsdb.path=/prometheus'
      - '--storage.tsdb.retention.time=30d'
      - '--web.console.libraries=/etc/prometheus/console_libraries'
      - '--web.console.templates=/etc/prometheus/consoles'
      - '--web.enable-lifecycle'
      - '--web.enable-admin-api'
    networks:
      - monitoring

  alertmanager:
    image: prom/alertmanager:v0.26.0
    ports:
      - "9093:9093"
    volumes:
      - ./prometheus/alertmanager.yml:/etc/alertmanager/alertmanager.yml
    networks:
      - monitoring

  grafana:
    image: grafana/grafana:10.0.0
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_PASSWORD}
      - GF_USERS_ALLOW_SIGN_UP=false
    volumes:
      - grafana-data:/var/lib/grafana
      - ./grafana/provisioning:/etc/grafana/provisioning
      - ./grafana/dashboards:/var/lib/grafana/dashboards
    networks:
      - monitoring

volumes:
  prometheus-data:
  grafana-data:

networks:
  monitoring:
    driver: bridge
```

### 14.4 Prometheus 配置

#### `prometheus/prometheus.yml`

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s
  external_labels:
    monitor: 'zhuxiang-monitor'

rule_files:
  - alert_rules.yml

alerting:
  alertmanagers:
    - static_configs:
        - targets:
            - alertmanager:9093

scrape_configs:
  # Redis Exporter
  - job_name: 'redis'
    static_configs:
      - targets: ['redis-exporter:9121']
        labels:
          service: 'redis'
          env: 'production'

  # Node Exporter (主机指标)
  - job_name: 'node'
    static_configs:
      - targets: ['node-exporter:9100']
        labels:
          service: 'host'
          env: 'production'

  # cAdvisor (容器指标)
  - job_name: 'docker'
    static_configs:
      - targets: ['cadvisor:8080']
        labels:
          service: 'containers'
          env: 'production'

  # Backend 应用 (需在 main.py 暴露 /metrics)
  - job_name: 'backend'
    static_configs:
      - targets: ['backend:8000']
        labels:
          service: 'backend'
          env: 'production'
    metrics_path: '/metrics'
    params:
      format: ['prometheus']
```

#### `prometheus/alert_rules.yml`

```yaml
groups:
  # ============================
  # Redis 告警规则
  # ============================
  - name: redis_alerts
    rules:
      # Redis 宕机
      - alert: RedisDown
        expr: redis_up == 0
        for: 1m
        labels:
          severity: critical
          service: redis
        annotations:
          summary: "Redis 宕机"
          description: "Redis {{ $labels.instance }} 已宕机超过 1 分钟"

      # 内存使用率过高
      - alert: RedisMemoryHigh
        expr: (redis_memory_used_bytes / redis_memory_max_bytes) * 100 > 80
        for: 5m
        labels:
          severity: warning
          service: redis
        annotations:
          summary: "Redis 内存使用率过高"
          description: "Redis 内存使用率 {{ $value }}% 超过 80%"

      # 连接数过多
      - alert: RedisTooManyConnections
        expr: redis_connected_clients > 100
        for: 2m
        labels:
          severity: warning
          service: redis
        annotations:
          summary: "Redis 连接数过多"
          description: "Redis 当前连接数 {{ $value }} 超过 100"

      # 内存碎片率过高
      - alert: RedisMemoryFragmentation
        expr: redis_memory_fragmentation_ratio > 1.5
        for: 10m
        labels:
          severity: warning
          service: redis
        annotations:
          summary: "Redis 内存碎片率过高"
          description: "Redis 内存碎片率 {{ $value }} 超过 1.5, 建议重启 Redis"

      # 慢查询过多
      - alert: RedisSlowQueries
        expr: rate(redis_slowlog_last_id[5m]) > 0
        for: 5m
        labels:
          severity: warning
          service: redis
        annotations:
          summary: "Redis 存在慢查询"
          description: "Redis 过去 5 分钟内有新的慢查询"

      # 键空间命中率低
      - alert: RedisLowHitRate
        expr: (rate(redis_keyspace_hits_total[5m]) / (rate(redis_keyspace_hits_total[5m]) + rate(redis_keyspace_misses_total[5m]))) * 100 < 90
        for: 10m
        labels:
          severity: warning
          service: redis
        annotations:
          summary: "Redis 键空间命中率低"
          description: "Redis 命中率 {{ $value }}% 低于 90%"

      # 主从延迟过高
      - alert: RedisReplicationLag
        expr: redis_replication_offset_diff > 1000000
        for: 2m
        labels:
          severity: warning
          service: redis
        annotations:
          summary: "Redis 主从复制延迟过高"
          description: "主从延迟 {{ $value }} bytes 超过 1MB"

      # AOF 持久化异常
      - alert: RedisAOFDisabled
        expr: redis_persistence_loading == 0 and redis_config_appendonly != 1
        for: 1m
        labels:
          severity: critical
          service: redis
        annotations:
          summary: "Redis AOF 持久化未开启"
          description: "生产环境必须开启 AOF 持久化"

  # ============================
  # 哨兵/集群告警规则
  # ============================
  - name: redis_ha_alerts
    rules:
      # 哨兵在线数不足
      - alert: RedisSentinelDown
        expr: count(redis_sentinel_alive == 0) >= 2
        for: 1m
        labels:
          severity: critical
          service: redis-sentinel
        annotations:
          summary: "Redis Sentinel 节点下线"
          description: "{{ $value }} 个 Sentinel 节点下线"

      # 集群状态异常
      - alert: RedisClusterUnhealthy
        expr: redis_cluster_state != 1
        for: 1m
        labels:
          severity: critical
          service: redis-cluster
        annotations:
          summary: "Redis Cluster 状态异常"
          description: "Redis Cluster 状态非 ok"

      # Slot 未完全分配
      - alert: RedisClusterSlotsUnassigned
        expr: redis_cluster_slots_unassigned > 0
        for: 5m
        labels:
          severity: critical
          service: redis-cluster
        annotations:
          summary: "Redis Cluster Slot 未完全分配"
          description: "{{ $value }} 个 slot 未分配"

  # ============================
  # Backend 应用告警规则
  # ============================
  - name: backend_alerts
    rules:
      # Backend 宕机
      - alert: BackendDown
        expr: up{job="backend"} == 0
        for: 30s
        labels:
          severity: critical
          service: backend
        annotations:
          summary: "Backend 服务宕机"
          description: "Backend {{ $labels.instance }} 已宕机超过 30 秒"

      # API 响应时间过长
      - alert: BackendSlowResponse
        expr: histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m])) > 1
        for: 5m
        labels:
          severity: warning
          service: backend
        annotations:
          summary: "Backend API 响应缓慢"
          description: "P95 响应时间 {{ $value }}s 超过 1s"

      # 错误率过高
      - alert: BackendHighErrorRate
        expr: rate(http_requests_total{status=~"5.."}[5m]) / rate(http_requests_total[5m]) * 100 > 5
        for: 5m
        labels:
          severity: warning
          service: backend
        annotations:
          summary: "Backend 错误率过高"
          description: "5xx 错误率 {{ $value }}% 超过 5%"

  # ============================
  # 主机/容器告警规则
  # ============================
  - name: infra_alerts
    rules:
      # 主机 CPU 使用率过高
      - alert: HostHighCpuLoad
        expr: 100 - (avg by(instance)(rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100) > 80
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "主机 CPU 使用率过高"
          description: "CPU 使用率 {{ $value }}% 超过 80%"

      # 主机内存不足
      - alert: HostOutOfMemory
        expr: (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes) * 100 < 10
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "主机内存不足"
          description: "可用内存 {{ $value }}% 低于 10%"

      # 磁盘空间不足
      - alert: HostDiskAlmostFull
        expr: (node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"}) * 100 < 10
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "磁盘空间不足"
          description: "根分区可用空间 {{ $value }}% 低于 10%"

      # 容器 OOM
      - alert: ContainerOomKilled
        expr: increase(container_memory_failcnt[5m]) > 0
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "容器发生 OOM"
          description: "容器 {{ $labels.name }} 发生 OOM Kill"
```

#### `prometheus/alertmanager.yml`

```yaml
global:
  resolve_timeout: 5m
  smtp_smarthost: 'smtp.example.com:587'
  smtp_from: 'alerts@zhuxiang-jiu.com'
  smtp_auth_username: 'alerts@zhuxiang-jiu.com'
  smtp_auth_password: '${SMTP_PASSWORD}'

templates:
  - '/etc/alertmanager/templates/*.tmpl'

route:
  group_by: ['alertname', 'cluster', 'service']
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h
  receiver: 'default'

  routes:
    # 严重告警 → 立即通知
    - match:
        severity: critical
      receiver: 'critical-alerts'
      group_wait: 10s
      repeat_interval: 1h

    # 警告级别
    - match:
        severity: warning
      receiver: 'warning-alerts'
      group_wait: 2m
      repeat_interval: 4h

receivers:
  - name: 'default'
    email_configs:
      - to: 'ops-team@zhuxiang-jiu.com'

  - name: 'critical-alerts'
    email_configs:
      - to: 'ops-team@zhuxiang-jiu.com'
        send_resolved: true
    webhook_configs:
      - url: 'https://hooks.slack.com/services/xxx'
        send_resolved: true
    # 钉钉/企业微信 webhook (可选)
    # - url: 'https://oapi.dingtalk.com/robot/send?access_token=xxx'

  - name: 'warning-alerts'
    email_configs:
      - to: 'dev-team@zhuxiang-jiu.com'
        send_resolved: true

inhibit_rules:
  - source_match:
      severity: 'critical'
    target_match:
      severity: 'warning'
    equal: ['alertname', 'cluster', 'service']
```

### 14.5 Grafana 仪表盘

#### 推荐仪表盘模板

| Dashboard ID | 名称 | 说明 |
|-------------|------|------|
| 763 | Redis Dashboard for Prometheus | 官方 Redis 监控面板 |
| 11835 | Redis Overview | Redis 综合概览 |
| 893 | Node Exporter Dashboard | 主机指标监控 |
| 193 | Docker Monitoring | 容器监控 |
| 4279 | FastAPI Dashboard | FastAPI 应用监控 |

#### 导入方式

1. 访问 Grafana: `http://localhost:3000`
2. 登录 (admin / 配置的密码)
3. 左侧菜单 → Dashboards → Import
4. 输入 Dashboard ID → Load
5. 选择 Prometheus 数据源 → Import

#### 自定义仪表盘

在 `grafana/dashboards/` 目录创建以下 JSON 仪表盘文件:

| 文件名 | 内容 |
|--------|------|
| `redis-overview.json` | Redis 总体状态(内存/连接/QPS/命中率) |
| `redis-influencer.json` | 博主模块专项(锁/Key/序列号) |
| `backend-health.json` | Backend 健康状态(请求量/延迟/错误率) |
| `infra-host.json` | 主机资源(CPU/内存/磁盘) |

### 14.6 关键监控指标清单

#### Redis 指标 (通过 redis-exporter)

| 指标名称 | PromQL | 告警阈值 |
|---------|--------|---------|
| Redis 状态 | `redis_up` | `== 0` → Critical |
| 内存使用率 | `(redis_memory_used_bytes / redis_memory_max_bytes) * 100` | `> 80%` → Warning |
| 连接数 | `redis_connected_clients` | `> 100` → Warning |
| 内存碎片率 | `redis_memory_fragmentation_ratio` | `> 1.5` → Warning |
| 慢查询 | `rate(redis_slowlog_last_id[5m])` | `> 0` → Warning |
| 命中率 | `rate(redis_keyspace_hits_total[5m]) / (rate(redis_keyspace_hits_total[5m]) + rate(redis_keyspace_misses_total[5m]))` | `< 90%` → Warning |
| 主从延迟 | `redis_replication_offset_diff` | `> 1MB` → Warning |
| AOF 状态 | `redis_config_appendonly` | `!= 1` → Critical |
| 执行命令数 | `rate(redis_commands_processed_total[5m])` | 监控 |
| 过期 Key | `rate(redis_expired_keys_total[5m])` | 监控 |
| 驱逐 Key | `rate(redis_evicted_keys_total[5m])` | `> 0` → Warning |

#### Backend 指标 (需在 main.py 暴露 /metrics)

| 指标名称 | PromQL | 告警阈值 |
|---------|--------|---------|
| 服务状态 | `up{job="backend"}` | `== 0` → Critical |
| 请求量 | `rate(http_requests_total[5m])` | 监控 |
| 错误率 | `rate(http_requests_total{status=~"5.."}[5m])` | `> 5%` → Warning |
| P95 延迟 | `histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m]))` | `> 1s` → Warning |
| 锁等待 | `rate(redis_lock_wait_seconds_count[5m])` | 监控 |
| 锁超时 | `rate(redis_lock_timeout_total[5m])` | `> 0` → Warning |

### 14.7 部署验证清单

#### 监控组件部署

- [ ] redis-exporter 部署成功: `curl http://localhost:9121/metrics`
- [ ] node-exporter 部署成功: `curl http://localhost:9100/metrics`
- [ ] cadvisor 部署成功: `curl http://localhost:8080/metrics`
- [ ] prometheus 部署成功: 访问 `http://localhost:9090`
- [ ] alertmanager 部署成功: 访问 `http://localhost:9093`
- [ ] grafana 部署成功: 访问 `http://localhost:3000`

#### Prometheus 采集验证

- [ ] Targets 全部 UP: `http://localhost:9090/targets`
- [ ] Redis 指标可见: `redis_up == 1`
- [ ] 告警规则加载: `http://localhost:9090/rules`
- [ ] Alertmanager 连通: `http://localhost:9090/api/v1/alertmanagers`

#### Grafana 配置验证

- [ ] 数据源添加: Prometheus → `http://prometheus:9090`
- [ ] 仪表盘导入: Redis Dashboard (ID: 763)
- [ ] 告警通知测试: 手动触发告警 → 验证邮件/Slack

#### 告警通知验证

- [ ] 告警邮件可达: 测试邮件发送成功
- [ ] Slack webhook 有效: `#alerts` 频道收到测试告警
- [ ] 告警抑制规则生效: Critical 抑制 Warning
- [ ] 告警恢复通知: 问题解决后收到恢复邮件

### 14.8 监控部署完整命令

```bash
# 1. 创建监控配置目录
mkdir -p prometheus grafana/dashboards grafana/provisioning/datasources

# 2. 创建 .env 文件(密码配置)
cat > .env << EOF
REDIS_PASSWORD=YourStrongRedisPassword
GRAFANA_PASSWORD=YourStrongGrafanaPassword
SMTP_PASSWORD=YourSmtpPassword
EOF

# 3. 启动监控栈
docker-compose -f docker-compose.monitoring.yml up -d

# 4. 验证服务
docker-compose -f docker-compose.monitoring.yml ps

# 5. 验证 Prometheus 采集
curl http://localhost:9090/api/v1/targets | python -m json.tool

# 6. 验证 Redis 指标
curl http://localhost:9121/metrics | grep redis_up

# 7. 访问 Grafana 配置仪表盘
# 浏览器打开 http://localhost:3000 (admin / GRAFANA_PASSWORD)
```

---

## 附录: 快速验证脚本

```bash
#!/bin/bash
# redis-check.sh - Redis 配置快速验证

echo "=== Redis 连接验证 ==="
docker exec redis redis-cli ping

echo "=== 环境变量验证 ==="
docker exec backend env | grep -E "REDIS_URL|LOCK_MODE|STORE_MODE"

echo "=== Redis 持久化验证 ==="
docker exec redis redis-cli CONFIG GET appendonly

echo "=== 博主数据 Key 验证 ==="
docker exec redis redis-cli KEYS "zhuxiang:traffic:influencer*"

echo "=== 锁 Key 验证 ==="
docker exec redis redis-cli KEYS "lock:traffic:*"

echo "=== 序列号验证 ==="
docker exec redis redis-cli GET "zhuxiang:traffic:traffic_influencer:seq"

echo "=== Redis 内存使用 ==="
docker exec redis redis-cli INFO memory | grep used_memory_human

echo "=== Redis 连接数 ==="
docker exec redis redis-cli INFO clients | grep connected_clients
```
