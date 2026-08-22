# AI 智能维护模块 - 数据库表结构设计

> 模块定位：自动化运维、健康检查、故障自愈、性能优化、资源调度
>
> 设计原则：双模式存储（内存字典 / Redis Hash），状态机驱动，RMW 操作加锁保护
>
> 参考模块：合规合法智能监控模块（compliance）

---

## 一、表清单（P0 四表）

| 表名 | 中文名 | 职责 |
| --- | --- | --- |
| `maintenance_tasks` | 维护任务表 | 定时/手动任务：备份/清理/优化/巡检/重启/扩容 |
| `maintenance_health` | 健康检查表 | 服务健康状态检查项配置 + 检查结果 |
| `maintenance_recovery` | 故障自愈表 | 故障检测 → 诊断 → 自动恢复策略 → 执行结果 |
| `maintenance_optimization` | 性能优化表 | 优化建议 → 审批 → 执行 → 效果评估 |

---

## 二、状态机定义

### 2.1 任务状态（taskStatus）

```
pending(待执行) → running(执行中) → success(成功) / failed(失败)
                                   ↓
                            cancelled(已取消)
```

| 状态 | 值 | 说明 | 允许的后继状态 |
| --- | --- | --- | --- |
| 待执行 | `pending` | 任务已创建未启动 | running, cancelled |
| 执行中 | `running` | 任务正在执行 | success, failed, cancelled |
| 成功 | `success` | 任务执行成功（终态） | - |
| 失败 | `failed` | 任务执行失败（终态） | - |
| 已取消 | `cancelled` | 任务被取消（终态） | - |

### 2.2 任务类型（taskType）

| 类型 | 值 | 说明 |
| --- | --- | --- |
| 备份 | `backup` | 数据/配置/日志备份 |
| 清理 | `cleanup` | 临时文件/过期数据清理 |
| 优化 | `optimize` | 索引/缓存/参数优化 |
| 巡检 | `inspect` | 服务/资源巡检 |
| 重启 | `restart` | 服务/进程重启 |
| 扩容 | `scale` | 横向/纵向扩容 |

### 2.3 健康状态（healthStatus）

| 状态 | 值 | 说明 |
| --- | --- | --- |
| 健康 | `healthy` | 服务正常 |
| 降级 | `degraded` | 服务可用但性能下降 |
| 不健康 | `unhealthy` | 服务不可用 |
| 未知 | `unknown` | 尚未检查或检查失败 |

### 2.4 自愈状态（recoveryStatus）

```
detected(检测到) → diagnosing(诊断中) → recovering(恢复中) → recovered(已恢复)
                                                       ↓
                                              failed(恢复失败) / manual_required(需人工)
```

| 状态 | 值 | 说明 | 允许的后继状态 |
| --- | --- | --- | --- |
| 检测到 | `detected` | 故障已识别 | diagnosing, manual_required |
| 诊断中 | `diagnosing` | 正在分析根因 | recovering, manual_required |
| 恢复中 | `recovering` | 正在执行恢复策略 | recovered, failed, manual_required |
| 已恢复 | `recovered` | 自动恢复成功（终态） | - |
| 恢复失败 | `failed` | 自动恢复失败（终态） | - |
| 需人工 | `manual_required` | 需人工介入（终态） | - |

### 2.5 自愈级别（recoveryLevel）

| 级别 | 值 | 说明 | 流程 |
| --- | --- | --- | --- |
| 自动 | `auto` | 全自动恢复 | detected → diagnosing → recovering → recovered |
| 辅助 | `assisted` | AI 辅助人工确认 | detected → diagnosing → recovering → recovered |
| 人工 | `manual` | 必须人工处理 | detected → manual_required |

### 2.6 优化状态（optimizationStatus）

```
proposed(建议中) → approved(已批准) → executing(执行中) → completed(已完成)
                ↓
          rejected(已驳回)（终态）
```

| 状态 | 值 | 说明 | 允许的后继状态 |
| --- | --- | --- | --- |
| 建议中 | `proposed` | 优化建议已生成 | approved, rejected |
| 已批准 | `approved` | 管理员批准 | executing, rejected |
| 执行中 | `executing` | 正在执行优化 | completed |
| 已完成 | `completed` | 优化执行完成（终态） | - |
| 已驳回 | `rejected` | 优化建议被驳回（终态） | - |

---

## 三、表结构详细设计

### 3.1 maintenance_tasks（维护任务表）

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| id | int | PK, auto_increment | 任务 ID |
| taskName | varchar(128) | NOT NULL | 任务名称 |
| taskType | varchar(32) | NOT NULL | 任务类型（backup/cleanup/optimize/inspect/restart/scale） |
| taskStatus | varchar(32) | NOT NULL, default 'pending' | 任务状态 |
| triggerType | varchar(16) | NOT NULL, default 'manual' | 触发类型（manual/scheduled） |
| target | varchar(128) | NOT NULL | 维护目标（服务/模块名） |
| params | json | | 任务参数 |
| schedule | varchar(64) | | 调度表达式（cron） |
| executedAt | datetime | | 执行开始时间 |
| completedAt | datetime | | 执行完成时间 |
| result | json | | 执行结果 |
| errorMessage | text | | 错误信息 |
| aiAutomationRate | float | default 90.0 | AI 自动化率 |
| createdAt | datetime | NOT NULL | 创建时间 |

**索引**：
- `idx_tasks_type` (taskType)
- `idx_tasks_status` (taskStatus)
- `idx_tasks_target` (target)
- `idx_tasks_created` (createdAt DESC)

### 3.2 maintenance_health（健康检查表）

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| id | int | PK, auto_increment | 检查项 ID |
| checkName | varchar(128) | NOT NULL | 检查项名称 |
| serviceName | varchar(128) | NOT NULL | 服务名称 |
| checkType | varchar(32) | NOT NULL | 检查类型（http/tcp/resource/custom） |
| healthStatus | varchar(32) | NOT NULL, default 'unknown' | 健康状态 |
| checkConfig | json | | 检查配置（url/port/threshold） |
| checkResult | json | | 检查结果（响应时间/状态码/指标） |
| lastCheckAt | datetime | | 最后检查时间 |
| responseTime | int | | 响应时间（毫秒） |
| threshold | json | | 阈值配置 |
| aiAutomationRate | float | default 95.0 | AI 自动化率 |
| createdAt | datetime | NOT NULL | 创建时间 |

**索引**：
- `idx_health_service` (serviceName)
- `idx_health_status` (healthStatus)
- `idx_health_type` (checkType)
- `idx_health_created` (createdAt DESC)

### 3.3 maintenance_recovery（故障自愈表）

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| id | int | PK, auto_increment | 自愈记录 ID |
| faultType | varchar(64) | NOT NULL | 故障类型 |
| faultSource | varchar(128) | NOT NULL | 故障来源（服务/模块） |
| faultDescription | text | | 故障描述 |
| recoveryStatus | varchar(32) | NOT NULL, default 'detected' | 自愈状态 |
| recoveryLevel | varchar(16) | NOT NULL, default 'auto' | 自愈级别（auto/assisted/manual） |
| diagnoseResult | json | | 诊断结果（根因/影响范围） |
| recoveryStrategy | json | | 恢复策略（动作列表） |
| executionResult | json | | 执行结果 |
| detectedAt | datetime | NOT NULL | 检测时间 |
| recoveredAt | datetime | | 恢复时间 |
| aiAutomationRate | float | default 90.0 | AI 自动化率 |
| createdAt | datetime | NOT NULL | 创建时间 |

**索引**：
- `idx_recovery_type` (faultType)
- `idx_recovery_status` (recoveryStatus)
- `idx_recovery_level` (recoveryLevel)
- `idx_recovery_source` (faultSource)
- `idx_recovery_created` (createdAt DESC)

### 3.4 maintenance_optimization（性能优化表）

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| id | int | PK, auto_increment | 优化记录 ID |
| optimizationType | varchar(64) | NOT NULL | 优化类型 |
| target | varchar(128) | NOT NULL | 优化目标（服务/模块/资源） |
| optimizationStatus | varchar(32) | NOT NULL, default 'proposed' | 优化状态 |
| proposal | text | NOT NULL | 优化建议 |
| expectedBenefit | json | | 预期收益（性能提升/成本降低） |
| executionPlan | json | | 执行计划 |
| actualBenefit | json | | 实际收益 |
| approvedBy | varchar(64) | | 审批人 |
| executedAt | datetime | | 执行时间 |
| completedAt | datetime | | 完成时间 |
| aiAutomationRate | float | default 85.0 | AI 自动化率 |
| createdAt | datetime | NOT NULL | 创建时间 |

**索引**：
- `idx_opt_type` (optimizationType)
- `idx_opt_status` (optimizationStatus)
- `idx_opt_target` (target)
- `idx_opt_created` (createdAt DESC)

---

## 四、双模式存储设计

### 4.1 内存模式（STORE_MODE=asyncio）

存储于 `repositories/store.py` 的 `_mock_store` 字典，键如下：

| 键 | 类型 | 说明 |
| --- | --- | --- |
| `maintenance_tasks` | dict[int, dict] | 任务表 |
| `maintenance_health` | dict[int, dict] | 健康检查表 |
| `maintenance_recovery` | dict[int, dict] | 故障自愈表 |
| `maintenance_optimization` | dict[int, dict] | 优化表 |
| `_maintenance_task_seq` | int | 任务自增序列 |
| `_maintenance_health_seq` | int | 健康检查自增序列 |
| `_maintenance_recovery_seq` | int | 自愈记录自增序列 |
| `_maintenance_optimization_seq` | int | 优化记录自增序列 |

### 4.2 Redis 模式（STORE_MODE=redis）

Key 格式：`zhuxiang:maintenance:{entity}:{id}`

| 实体 | Key 示例 | 说明 |
| --- | --- | --- |
| task | `zhuxiang:maintenance:task:1` | 任务记录 |
| health | `zhuxiang:maintenance:health:1` | 健康检查记录 |
| recovery | `zhuxiang:maintenance:recovery:1` | 自愈记录 |
| optimization | `zhuxiang:maintenance:optimization:1` | 优化记录 |
| seq | `zhuxiang:maintenance:task:seq` | 任务自增序列 |

值采用 JSON 字符串（`ensure_ascii=False`）。

---

## 五、并发安全设计

### 5.1 锁 Key 格式

`lock:maintenance:{entity}:{id}`

> 注：`lock:` 前缀由 `core/locks.py` 的 `_RedisLockWrapper` 自动添加，
> Service 层传入的 key 为 `maintenance:{entity}:{id}`。

| 操作 | 锁 Key | 说明 |
| --- | --- | --- |
| 执行任务 | `maintenance:task:{task_id}` | 任务状态流转 RMW |
| 取消任务 | `maintenance:task:{task_id}` | 任务状态流转 RMW |
| 运行健康检查 | `maintenance:health:{check_id}` | 健康状态更新 RMW |
| 故障诊断 | `maintenance:recovery:{recovery_id}` | 自愈状态流转 RMW |
| 故障恢复 | `maintenance:recovery:{recovery_id}` | 自愈状态流转 RMW |
| 批准优化 | `maintenance:optimization:{optimization_id}` | 优化状态流转 RMW |
| 执行优化 | `maintenance:optimization:{optimization_id}` | 优化状态流转 RMW |

### 5.2 异常约定

| 异常类型 | HTTP 状态码 | 触发场景 |
| --- | --- | --- |
| `KeyError` | 404 | 记录不存在 |
| `ValueError` | 409 | 状态非法/参数无效/状态流转违规 |

---

## 六、ER 关系图

```
┌─────────────────────┐
│ maintenance_tasks  │
│  (维护任务)         │
│  - taskType         │
│  - taskStatus       │
└──────────┬──────────┘
           │ inspect 任务触发
           ▼
┌─────────────────────┐         ┌─────────────────────────┐
│ maintenance_health │ ───────► │ maintenance_recovery    │
│  (健康检查)         │ 故障检出 │  (故障自愈)              │
│  - healthStatus    │         │  - recoveryStatus       │
└─────────────────────┘         │  - recoveryLevel       │
                                └────────────┬────────────┘
                                             │ 故障根因分析
                                             ▼
                                ┌─────────────────────────┐
                                │ maintenance_optimization│
                                │  (性能优化)              │
                                │  - optimizationStatus   │
                                └─────────────────────────┘
```

**业务联动**：
1. 巡检任务（inspect）执行 → 触发健康检查（health）
2. 健康检查发现不健康 → 自动创建故障自愈记录（recovery）
3. 故障恢复后 → 可生成性能优化建议（optimization）
4. 一键巡检（POST /api/maintenance/inspect）串联上述全流程

---

## 七、API 端点分布（12 个）

| 端点 | 方法 | 路径 | 鉴权 | 说明 |
| --- | --- | --- | --- | --- |
| 创建任务 | POST | `/api/maintenance/tasks` | admin | 创建维护任务 |
| 任务列表 | GET | `/api/maintenance/tasks` | admin | 查询任务列表 |
| 更新任务 | PUT | `/api/maintenance/tasks/{task_id}` | admin | 执行/取消任务 |
| 创建健康检查 | POST | `/api/maintenance/health` | admin | 创建健康检查项 |
| 健康检查列表 | GET | `/api/maintenance/health` | admin | 查询健康检查列表 |
| 健康检查详情 | GET | `/api/maintenance/health/{check_id}` | admin | 查询单个健康检查 |
| 检测故障 | POST | `/api/maintenance/recovery` | admin | 检测并创建自愈记录 |
| 自愈记录列表 | GET | `/api/maintenance/recovery` | admin | 查询自愈记录列表 |
| 提交优化建议 | POST | `/api/maintenance/optimizations` | admin | 提交性能优化建议 |
| 优化建议列表 | GET | `/api/maintenance/optimizations` | admin | 查询优化建议列表 |
| 一键巡检 | POST | `/api/maintenance/inspect` | admin | 全服务巡检 |
| 统计 | GET | `/api/maintenance/stats` | admin | 维护统计 |

**路由顺序约定**：静态路径优先于动态路径注册（如 `GET /health` 先于 `GET /health/{check_id}`）。
