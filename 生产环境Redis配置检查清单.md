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
