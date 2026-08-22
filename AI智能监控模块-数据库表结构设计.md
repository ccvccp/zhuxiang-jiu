# AI 智能监控模块 - 数据库表结构设计

> 模块定位：监控系统运行状态、业务指标采集、异常检测、告警推送、性能分析
>
> 架构对齐：双模式存储（内存字典 / Redis Hash），与 `repositories/backend.py` 的 `is_redis_mode()`、`get_redis_client()`、`get_in_memory_store()`、`_k()` 对齐。

## 一、表清单（P0 四表）

| 表名 | 中文名 | 职责 |
| --- | --- | --- |
| `monitor_metrics` | 监控指标表 | 指标采集：CPU / 内存 / QPS / 延迟 / 错误率 / 业务量 |
| `monitor_alerts` | 告警记录表 | 异常检测 → 分级 → 通知 |
| `monitor_dashboards` | 仪表盘配置表 | 自定义监控视图 |
| `monitor_incidents` | 故障事件表 | 故障追踪 → 定级 → 处理 → 复盘 |

## 二、状态机

### 2.1 告警状态机（`monitor_alerts.alertStatus`）

```
pending(待处理) ──acknowledge──> acknowledged(已确认)
pending(待处理) ──suppress──────> suppressed(已抑制)
acknowledged(已确认) ─resolve──> resolved(已解决)
acknowledged(已确认) ─suppress──> suppressed(已抑制)
resolved(已解决) ──suppress─────> suppressed(已抑制)
```

- 合法前驱态：
  - `acknowledged` ← `pending`
  - `resolved` ← `acknowledged`
  - `suppressed` ← `pending` / `acknowledged` / `resolved`
- 非法流转 → `ValueError`（路由层映射 409）

### 2.2 告警级别（`monitor_alerts.alertLevel`）

| 级别 | 含义 | 默认通知渠道 |
| --- | --- | --- |
| `info` | 信息 | 日志 |
| `warning` | 警告 | 站内信 |
| `critical` | 严重 | 短信 + 站内信 |
| `fatal` | 致命 | 电话 + 短信 + 站内信 |

### 2.3 故障状态机（`monitor_incidents.incidentStatus`）

```
detected(已发现) → investigating(调查中) → mitigating(处理中) → resolved(已解决) → postmortem(已复盘)
```

- 合法前驱态：
  - `investigating` ← `detected`
  - `mitigating` ← `investigating`
  - `resolved` ← `mitigating`
  - `postmortem` ← `resolved`
- 非法流转 → `ValueError`（路由层映射 409）

### 2.4 故障级别（`monitor_incidents.incidentLevel`）

| 级别 | 含义 | 响应时效 | 复盘要求 |
| --- | --- | --- | --- |
| `P0` | 致命 | 5 分钟 | 必须 |
| `P1` | 严重 | 30 分钟 | 必须 |
| `P2` | 中等 | 2 小时 | 可选 |
| `P3` | 轻微 | 24 小时 | 可选 |

## 三、表结构详设

### 3.1 `monitor_metrics` 监控指标表

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `id` | int | PK, 自增 | 指标 ID |
| `metricName` | str | not null | 指标名称（如 `cpu_usage` / `memory_usage` / `qps` / `latency_p99` / `error_rate` / `business_volume`） |
| `metricType` | str | not null | 指标类型（`system` / `business` / `performance` / `error`） |
| `metricValue` | float | not null | 指标数值 |
| `metricUnit` | str | default="" | 单位（`%` / `ms` / `req/s` / `count`） |
| `source` | str | not null | 采集来源（模块名 / 主机名 / 探针 ID） |
| `tags` | dict | default={} | 标签（`{"host":"api-1","region":"cn-east"}`） |
| `threshold` | dict | default={} | 阈值配置（`{"warning":80,"critical":90}`） |
| `anomalyDetect` | dict | default={} | 异常检测结果（`{"detected":false,"score":0.0}`） |
| `aiAutomationRate` | float | default=95.0 | AI 自动化率 |
| `createdAt` | str(ISO8601) | not null | 采集时间 |

**索引**：`(metricName, createdAt desc)`、`(source, createdAt desc)`

### 3.2 `monitor_alerts` 告警记录表

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `id` | int | PK, 自增 | 告警 ID |
| `alertName` | str | not null | 告警名称 |
| `alertType` | str | not null | 告警类型（`system` / `business` / `performance` / `security`） |
| `alertLevel` | str | not null | 级别（`info` / `warning` / `critical` / `fatal`） |
| `alertStatus` | str | default=`pending` | 状态（`pending` / `acknowledged` / `resolved` / `suppressed`） |
| `source` | str | not null | 告警来源 |
| `metricId` | int | nullable | 关联指标 ID |
| `threshold` | dict | default={} | 触发阈值 |
| `currentValue` | float | default=0.0 | 当前值 |
| `description` | str | default="" | 告警描述 |
| `notification` | dict | default={} | 通知配置（`{"channels":["sms","email"],"sent":false}`） |
| `acknowledgedBy` | str | default="" | 确认人 |
| `acknowledgedAt` | str | default="" | 确认时间 |
| `resolvedBy` | str | default="" | 解决人 |
| `resolvedAt` | str | default="" | 解决时间 |
| `suppressedBy` | str | default="" | 抑制人 |
| `suppressedAt` | str | default="" | 抑制时间 |
| `aiAutomationRate` | float | default=95.0 | AI 自动化率 |
| `createdAt` | str(ISO8601) | not null | 创建时间 |

**索引**：`(alertStatus, createdAt desc)`、`(alertLevel, createdAt desc)`、`(alertType, createdAt desc)`

### 3.3 `monitor_dashboards` 仪表盘配置表

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `id` | int | PK, 自增 | 仪表盘 ID |
| `dashboardName` | str | not null | 仪表盘名称 |
| `dashboardType` | str | not null | 类型（`system` / `business` / `incident` / `custom`） |
| `owner` | str | default="admin" | 所属用户 |
| `widgets` | list[dict] | default=[] | 组件配置（`[{"type":"chart","metric":"cpu_usage","span":6}]`） |
| `layout` | dict | default={} | 布局配置 |
| `filters` | dict | default={} | 全局过滤器 |
| `refreshInterval` | int | default=30 | 刷新间隔（秒） |
| `isShared` | bool | default=false | 是否共享 |
| `aiAutomationRate` | float | default=85.0 | AI 自动化率 |
| `createdAt` | str(ISO8601) | not null | 创建时间 |

**索引**：`(dashboardType, createdAt desc)`、`(owner, createdAt desc)`

### 3.4 `monitor_incidents` 故障事件表

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `id` | int | PK, 自增 | 故障 ID |
| `incidentName` | str | not null | 故障名称 |
| `incidentType` | str | not null | 故障类型（`system` / `business` / `security` / `data`） |
| `incidentLevel` | str | not null | 级别（`P0` / `P1` / `P2` / `P3`） |
| `incidentStatus` | str | default=`detected` | 状态（`detected` / `investigating` / `mitigating` / `resolved` / `postmortem`） |
| `source` | str | not null | 故障来源 |
| `alertIds` | list[int] | default=[] | 关联告警 ID |
| `impact` | dict | default={} | 影响范围（`{"users":1000,"services":["order"]}`） |
| `rootCause` | str | default="" | 根因分析 |
| `mitigation` | str | default="" | 处置措施 |
| `timeline` | list[dict] | default=[] | 时间线（`[{"at":"...","event":"检测"}]`） |
| `assignee` | str | default="" | 责任人 |
| `resolvedAt` | str | default="" | 解决时间 |
| `postmortemAt` | str | default="" | 复盘时间 |
| `postmortemDoc` | str | default="" | 复盘文档 |
| `aiAutomationRate` | float | default=85.0 | AI 自动化率 |
| `createdAt` | str(ISO8601) | not null | 创建时间 |

**索引**：`(incidentStatus, createdAt desc)`、`(incidentLevel, createdAt desc)`、`(incidentType, createdAt desc)`

## 四、双模式存储映射

### 4.1 内存模式

字典键直接挂在 `get_in_memory_store()` 单例上：

| 表名 | 内存字典键 | 序列号键 |
| --- | --- | --- |
| `monitor_metrics` | `monitor_metrics` | `_monitor_metrics_seq` |
| `monitor_alerts` | `monitor_alerts` | `_monitor_alerts_seq` |
| `monitor_dashboards` | `monitor_dashboards` | `_monitor_dashboards_seq` |
| `monitor_incidents` | `monitor_incidents` | `_monitor_incidents_seq` |

由 `MonitorRepository._ensure_store()` 懒初始化。

### 4.2 Redis 模式

Key 格式：`zhuxiang:monitor:{entity}:{id}`

| 实体 | Redis Key 示例 | 序列号 Key |
| --- | --- | --- |
| `metrics` | `zhuxiang:monitor:metrics:1` | `zhuxiang:monitor:metrics:seq` |
| `alerts` | `zhuxiang:monitor:alerts:1` | `zhuxiang:monitor:alerts:seq` |
| `dashboards` | `zhuxiang:monitor:dashboards:1` | `zhuxiang:monitor:dashboards:seq` |
| `incidents` | `zhuxiang:monitor:incidents:1` | `zhuxiang:monitor:incidents:seq` |

由 `_k("monitor", entity, id)` 生成，JSON 字符串值。

## 五、锁保护（RMW 操作）

| 业务操作 | 锁 Key 格式 |
| --- | --- |
| 采集指标 | `lock:monitor:metrics:{source}:{metricName}` |
| 创建告警 | `lock:monitor:alerts:{alertType}:{source}` |
| 确认/解决/抑制告警 | `lock:monitor:alerts:{alert_id}` |
| 创建故障 | `lock:monitor:incidents:{incidentType}:{source}` |
| 推进故障状态 | `lock:monitor:incidents:{incident_id}` |
| 创建仪表盘 | `lock:monitor:dashboards:{dashboardName}` |

通过 `core.locks.get_lock(key)` 获取；锁模式由 `LOCK_MODE` 切换（`asyncio` / `redis`）。

## 六、异常约定

| 异常类型 | 触发场景 | HTTP 状态码 |
| --- | --- | --- |
| `KeyError` | 记录不存在 / 状态非法查找 | 404 |
| `ValueError` | 参数无效 / 状态流转非法 / 级别非法 | 409 |
| 其他 | 未预期错误 | 500 |

路由层通过 `_handle(exc)` 统一映射。

## 七、模块依赖

- `repositories.backend`：`is_redis_mode()`、`get_redis_client()`、`get_in_memory_store()`、`_k()`
- `core.locks`：`get_lock(key)`
- `core.helpers`：`ts()`、`bc_hash()`
