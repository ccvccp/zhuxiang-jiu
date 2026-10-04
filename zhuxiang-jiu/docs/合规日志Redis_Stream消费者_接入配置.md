# 合规日志 Redis Stream 消费者 · 接入配置示例

> 适用：[guardrail_stream_consumer.py](../backend/services/guardrail_stream_consumer.py)（commit `0fbd530`，阶段 1.5 资产，生产真链路已冒烟验证）
> 当前默认 `GUARDRAIL_STREAM_MODE=off`——本文全部配置均为**启用时**的操作示例，不启用则零影响。

---

## 一、环境变量清单（全量）

### 1.1 模式开关（必配 1 项即可启用）

| 变量 | 默认 | 说明 |
|---|---|---|
| `GUARDRAIL_STREAM_MODE` | `off` | `on`/`1`/`true` 启用。生产者出口切 XADD + main startup 拉起消费者 |

### 1.2 消费者行为（全部可选，默认即生产值）

| 变量 | 默认 | 说明 |
|---|---|---|
| `GUARDRAIL_STREAM_KEY` | `zhuxiang:guardrail:hit_logs` | Stream 键名（含死信 `:dead-letter` 派生） |
| `GUARDRAIL_STREAM_GROUP` | `gr_log_consumer_group` | 消费组名（多实例共用一组） |
| `GUARDRAIL_STREAM_BATCH` | `200` | 单批落库条数上限 |
| `GUARDRAIL_STREAM_FLUSH_SEC` | `2.0` | 最大刷盘等待（秒） |
| `GUARDRAIL_STREAM_MAXLEN` | `100000` | Stream MAXLEN（approximate 裁剪，防 OOM） |
| `GUARDRAIL_STREAM_MIN_IDLE_MS` | `30000` | XAUTOCLAIM 接管阈值（消息空闲 ms） |
| `GUARDRAIL_STREAM_MAX_RETRY` | `5` | 死信上限（retry_count ≥ 此值转 `:dead-letter`） |

### 1.3 关联变量（落库与连接，已有默认）

| 变量 | 默认 | 说明 |
|---|---|---|
| `GUARDRAIL_HIT_TTL_DAYS` | `180` | 落库后 gr_hit_log 键 TTL（0=永不过期） |
| `REDIS_URL` | `redis://127.0.0.1:6379/0` | 复用 backend 连接配置 |
| `STORE_MODE` | `asyncio` | 消费者仅 Redis 模式启动（内存模式自动 skip） |

> 消费者名自动生成 `hostname-pid`，无需配置（多实例防同名抢占）。

---

## 二、接入形态配置示例

### 形态 A：嵌入式（推荐起步——backend 容器内一并运行）

在 `/opt/zhuxiang/docker-compose.yml` 的 backend service `environment` 追加：

```yaml
services:
  zhuxiang-backend:
    # ... 现有配置不动 ...
    environment:
      - STORE_MODE=redis
      # ↓ 追加以下行(最小启用 = 1 行)
      - GUARDRAIL_STREAM_MODE=on
      # ↓ 可选调优(不配即用默认值)
      # - GUARDRAIL_STREAM_BATCH=500
      # - GUARDRAIL_STREAM_FLUSH_SEC=3.0
      # - GUARDRAIL_STREAM_MAX_RETRY=3
```

生效：

```bash
cd /opt/zhuxiang
docker compose up -d zhuxiang-backend   # 重建容器加载 env
docker logs zhuxiang-backend-1 2>&1 | grep stream
# 预期:
#   stream_consumer_started <hostname-pid> group=gr_log_consumer_group batch=200 maxlen=100000
```

### 形态 B：独立消费者 service（backend 与消费者分离部署）

```yaml
services:
  zhuxiang-backend:
    environment:
      - STORE_MODE=redis
      - GUARDRAIL_STREAM_MODE=on        # 仅生产者出口切 XADD

  gr-log-consumer:                       # 独立消费者(只消费不生产)
    image: <与 backend 同镜像>
    command: python services/guardrail_stream_consumer.py
    environment:
      - STORE_MODE=redis
      - REDIS_URL=redis://zhuxiang-redis:6379/0
      - GUARDRAIL_STREAM_BATCH=200
      - GUARDRAIL_STREAM_FLUSH_SEC=2.0
    restart: unless-stopped
    # 优雅停机窗口(停机信号→强制刷盘完成)
    stop_grace_period: 30s
```

### 形态 C：多实例水平扩展（Consumer Group 自动分摊）

```yaml
  gr-log-consumer:
    image: <与 backend 同镜像>
    command: python services/guardrail_stream_consumer.py
    environment:
      - STORE_MODE=redis
      - REDIS_URL=redis://zhuxiang-redis:6379/0
    deploy:
      replicas: 2                        # 同组多消费者, 消息自动分摊
    stop_grace_period: 30s
```

多实例要点：
- 消费者名 `hostname-pid` 天然唯一，同组不冲突
- 任一实例崩溃：其未 ACK 消息空闲 30s 后被存活实例 XAUTOCLAIM 接管
- 实例数 ≤ 消费压力即可（单实例 200 条/2s 批已远超当前量级）

### K8s 形态（如未来上云，用户方案对照）

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: gr-log-consumer
spec:
  replicas: 2
  template:
    spec:
      terminationGracePeriodSeconds: 90   # 停机刷盘窗口
      containers:
        - name: consumer
          image: <registry>/zxjiu-backend:<tag>
          command: ["python", "services/guardrail_stream_consumer.py"]
          env:
            - name: STORE_MODE
              value: "redis"
            - name: REDIS_URL
              valueFrom:
                secretKeyRef: {name: zxjiu-secrets, key: redis-url}
          livenessProbe:
            exec: {command: ["pgrep", "-f", "guardrail_stream_consumer"]}
            periodSeconds: 15
```

---

## 三、启用后验证（三步冒烟）

```bash
# ① 生产者侧: 触发一次真实命中(微信对小竹说"拼酒"), 观察 XADD
docker exec zhuxiang-backend-1 python -c "
import asyncio, os
os.environ['STORE_MODE']='redis'
from repositories.backend import get_redis_client
async def t():
    c = await get_redis_client()
    print('stream len:', await c.xlen('zhuxiang:guardrail:hit_logs'))
asyncio.run(t())"

# ② 消费者侧: 落库+ACK 日志
docker logs zhuxiang-backend-1 2>&1 | grep stream_flush
# 预期: stream_flush_ok n=1 acked=1

# ③ 积压健康(应为 0 或瞬时小值)
docker exec <redis容器> redis-cli XPENDING zhuxiang:guardrail:hit_logs gr_log_consumer_group
```

---

## 四、运维命令速查

| 场景 | 命令 |
|---|---|
| 消费积压 | `XPENDING <stream> <group>`——`pending` 持续增长=落库瓶颈或消费者宕机（对应摘要日志 `stream_pending`，5 分钟周期） |
| 死信查看 | `XRANGE zhuxiang:guardrail:hit_logs:dead-letter - + COUNT 10`——深度>100 触发人工排查（数据格式/DB 故障） |
| 死信重放 | 逐条 `XADD <stream> <原 fields, retry_count=0>` 后 `XDEL <dead-letter> <id>` |
| 强制清积压 | `XGROUP DELCONSUMER <stream> <group> <死实例名>`（XAUTOCLAIM 已覆盖大部分场景，慎用） |
| 临时降级 | 容器 env 改回 `GUARDRAIL_STREAM_MODE=off` 重建——生产者回落内存队列，Stream 存量由消费者下次启用时消化 |

## 五、回滚与切换语义

- **off→on**：平滑。生产者切 XADD；消费者启动先恢复自身 PEL 再消费新消息；两路径落库同表（gr_hit_log），无重复（内存队列与 Stream 互斥分流）
- **on→off**：生产者回落内存队列；Stream 中未消费消息**保留不丢**（下次 on 时继续消费；MAXLEN 100000 内）
- 落库语义不变：At-Least-Once + flush 失败回队重试 + 死信兜底 + TTL 180 天

---

## 六、相关资源

| 资源 | 位置 |
|---|---|
| 消费者实现 | `backend/services/guardrail_stream_consumer.py` |
| 生产者出口分流 | `backend/services/local_guardrail_service.py`（`AsyncHitLogger.log`） |
| 启动挂载 | `backend/main.py`（startup 开关段） |
| 测试 | `backend/test_guardrail_admin.py` [08] 段 |
| 演进决策 | `docs/合规命中日志存储演进路线图_Redis到ClickHouse.md` §五 决策记录 |
