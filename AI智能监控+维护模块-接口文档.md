# 竹香酒官网 · AI 智能监控 + AI 智能维护模块接口文档

> **版本**：v1.0  
> **日期**：2026-08-22  
> **基础 URL**：`http://localhost:8000`  
> **鉴权**：全部接口需 `X-Role: admin` 请求头  
> **内容类型**：`application/json`  
> **模块总数**：2 个模块 / 24 个接口

---

## 目录

- [一、通用约定](#一通用约定)
- [二、AI 智能监控模块（12 接口）](#二ai-智能监控模块12-接口)
  - [2.1 采集监控指标](#21-采集监控指标)
  - [2.2 查询指标列表](#22-查询指标列表)
  - [2.3 创建告警](#23-创建告警)
  - [2.4 查询告警列表](#24-查询告警列表)
  - [2.5 告警状态流转](#25-告警状态流转)
  - [2.6 创建故障事件](#26-创建故障事件)
  - [2.7 查询故障列表](#27-查询故障列表)
  - [2.8 故障状态流转](#28-故障状态流转)
  - [2.9 创建仪表盘](#29-创建仪表盘)
  - [2.10 查询仪表盘列表](#210-查询仪表盘列表)
  - [2.11 健康检查](#211-健康检查)
  - [2.12 监控统计](#212-监控统计)
- [三、AI 智能维护模块（12 接口）](#三ai-智能维护模块12-接口)
  - [3.1 创建维护任务](#31-创建维护任务)
  - [3.2 查询维护任务列表](#32-查询维护任务列表)
  - [3.3 执行/取消维护任务](#33-执行取消维护任务)
  - [3.4 创建并执行健康检查](#34-创建并执行健康检查)
  - [3.5 查询健康检查列表](#35-查询健康检查列表)
  - [3.6 查询健康检查详情](#36-查询健康检查详情)
  - [3.7 检测故障并创建自愈记录](#37-检测故障并创建自愈记录)
  - [3.8 查询自愈记录列表](#38-查询自愈记录列表)
  - [3.9 提交性能优化建议](#39-提交性能优化建议)
  - [3.10 查询优化建议列表](#310-查询优化建议列表)
  - [3.11 一键巡检全服务](#311-一键巡检全服务)
  - [3.12 维护模块统计](#312-维护模块统计)
- [四、状态机定义](#四状态机定义)
- [五、错误码说明](#五错误码说明)

---

## 一、通用约定

### 1.1 请求头

| 头部 | 必填 | 说明 |
|------|------|------|
| `X-Role` | 是 | 固定值 `admin`，缺失返回 403 |
| `Content-Type` | POST/PUT 必填 | `application/json` |

### 1.2 响应格式

**成功响应：**

```json
{
  "success": true,
  "data": { ... }
}
```

**列表响应：**

```json
{
  "success": true,
  "data": [ ... ],
  "count": 10
}
```

**错误响应：**

```json
{
  "detail": "错误描述"
}
```

### 1.3 分页约定

所有列表接口支持 `limit` 查询参数（默认 50，范围 1~500），按 `createdAt` 倒序返回。

---

## 二、AI 智能监控模块（12 接口）

### 2.1 采集监控指标

**POST** `/api/monitor/metrics`

采集系统/业务/性能/错误指标，触发阈值检测。

**请求体：**

```json
{
  "metricName": "cpu_usage",
  "metricType": "system",
  "metricValue": 85.5,
  "source": "server-01",
  "metricUnit": "%",
  "tags": { "host": "web-1", "env": "prod" },
  "threshold": { "warning": 80, "critical": 90 },
  "aiAutomationRate": 95.0
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| metricName | string | 是 | 指标名称 |
| metricType | string | 是 | 指标类型：`system`/`business`/`performance`/`error` |
| metricValue | number | 是 | 指标数值 |
| source | string | 是 | 采集来源 |
| metricUnit | string | 否 | 单位（默认空） |
| tags | object | 否 | 标签键值对 |
| threshold | object | 否 | 阈值配置 |
| aiAutomationRate | number | 否 | AI自动化率（默认 95.0） |

**响应：**

```json
{
  "success": true,
  "data": {
    "id": 1,
    "metricName": "cpu_usage",
    "metricType": "system",
    "metricValue": 85.5,
    "source": "server-01",
    "metricUnit": "%",
    "tags": { "host": "web-1", "env": "prod" },
    "threshold": { "warning": 80, "critical": 90 },
    "anomalyDetected": true,
    "createdAt": "2026-08-22T08:00:00"
  }
}
```

---

### 2.2 查询指标列表

**GET** `/api/monitor/metrics`

**查询参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| metric_name | string | 否 | 按指标名称筛选 |
| metric_type | string | 否 | 按指标类型筛选 |
| source | string | 否 | 按来源筛选 |
| limit | int | 否 | 查询条数（默认 50） |

**响应：**

```json
{
  "success": true,
  "data": [ { ... } ],
  "count": 10
}
```

---

### 2.3 创建告警

**POST** `/api/monitor/alerts`

**请求体：**

```json
{
  "alertName": "CPU使用率过高",
  "alertType": "threshold",
  "alertLevel": "critical",
  "source": "server-01",
  "metricId": 1,
  "threshold": { "warning": 80, "critical": 90 },
  "currentValue": 95.5,
  "description": "CPU使用率超过阈值",
  "notification": { "channels": ["email", "sms"] },
  "aiAutomationRate": 95.0
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| alertName | string | 是 | 告警名称 |
| alertType | string | 是 | 告警类型 |
| alertLevel | string | 是 | 告警级别：`info`/`warning`/`critical`/`fatal` |
| source | string | 是 | 告警来源 |
| metricId | int | 否 | 关联指标 ID |
| threshold | object | 否 | 触发阈值 |
| currentValue | number | 否 | 当前值（默认 0.0） |
| description | string | 否 | 告警描述 |
| notification | object | 否 | 通知配置（不填则按级别自动填充） |
| aiAutomationRate | number | 否 | AI自动化率（默认 95.0） |

---

### 2.4 查询告警列表

**GET** `/api/monitor/alerts`

**查询参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| alert_type | string | 否 | 按告警类型筛选 |
| alert_level | string | 否 | 按告警级别筛选 |
| alert_status | string | 否 | 按告警状态筛选 |
| limit | int | 否 | 查询条数 |

---

### 2.5 告警状态流转

**PUT** `/api/monitor/alerts/{alert_id}`

**路径参数：** `alert_id` (int) - 告警 ID

**请求体：**

```json
{
  "action": "acknowledge",
  "operator": "admin"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| action | string | 是 | 动作：`acknowledge`/`resolve`/`suppress` |
| operator | string | 否 | 操作人（默认 admin） |

**状态流转规则：**

| action | 源状态 | 目标状态 |
|--------|--------|---------|
| acknowledge | pending | acknowledged |
| resolve | acknowledged | resolved |
| suppress | 任意 | suppressed |

---

### 2.6 创建故障事件

**POST** `/api/monitor/incidents`

**请求体：**

```json
{
  "incidentName": "数据库连接超时",
  "incidentType": "database",
  "incidentLevel": "P1",
  "source": "db-server",
  "impact": { "affectedServices": ["order", "payment"], "userCount": 1000 },
  "alertIds": [1, 2, 3],
  "assignee": "ops-team",
  "aiAutomationRate": 85.0
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| incidentName | string | 是 | 故障名称 |
| incidentType | string | 是 | 故障类型 |
| incidentLevel | string | 是 | 故障级别：`P0`/`P1`/`P2`/`P3` |
| source | string | 是 | 故障来源 |
| impact | object | 否 | 影响范围 |
| alertIds | int[] | 否 | 关联告警 ID 列表 |
| assignee | string | 否 | 责任人 |
| aiAutomationRate | number | 否 | AI自动化率（默认 85.0） |

---

### 2.7 查询故障列表

**GET** `/api/monitor/incidents`

**查询参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| incident_type | string | 否 | 按故障类型筛选 |
| incident_level | string | 否 | 按故障级别筛选 |
| incident_status | string | 否 | 按故障状态筛选 |
| limit | int | 否 | 查询条数 |

---

### 2.8 故障状态流转

**PUT** `/api/monitor/incidents/{incident_id}`

**路径参数：** `incident_id` (int) - 故障 ID

**请求体：**

```json
{
  "action": "investigate",
  "operator": "admin",
  "rootCause": "数据库连接池耗尽",
  "mitigation": "",
  "postmortemDoc": ""
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| action | string | 是 | 动作：`investigate`/`mitigate`/`resolve`/`postmortem` |
| operator | string | 否 | 操作人 |
| rootCause | string | 否 | 根因（investigate 时填） |
| mitigation | string | 否 | 处置措施（mitigate 时填） |
| postmortemDoc | string | 否 | 复盘文档（postmortem 时填） |

**状态流转规则：**

| action | 源状态 | 目标状态 |
|--------|--------|---------|
| investigate | detected | investigating |
| mitigate | investigating | mitigating |
| resolve | mitigating | resolved |
| postmortem | resolved | postmortem |

---

### 2.9 创建仪表盘

**POST** `/api/monitor/dashboards`

**请求体：**

```json
{
  "dashboardName": "系统监控大盘",
  "dashboardType": "system",
  "owner": "admin",
  "widgets": [
    { "type": "gauge", "metric": "cpu_usage", "position": { "x": 0, "y": 0 } }
  ],
  "layout": { "columns": 3 },
  "filters": { "env": "prod" },
  "refreshInterval": 30,
  "isShared": true,
  "aiAutomationRate": 85.0
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| dashboardName | string | 是 | 仪表盘名称 |
| dashboardType | string | 是 | 类型：`system`/`business`/`incident`/`custom` |
| owner | string | 否 | 所属用户（默认 admin） |
| widgets | object[] | 否 | 组件配置 |
| layout | object | 否 | 布局配置 |
| filters | object | 否 | 全局过滤器 |
| refreshInterval | int | 否 | 刷新间隔秒（默认 30） |
| isShared | bool | 否 | 是否共享（默认 false） |

---

### 2.10 查询仪表盘列表

**GET** `/api/monitor/dashboards`

**查询参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| dashboard_type | string | 否 | 按仪表盘类型筛选 |
| owner | string | 否 | 按所属用户筛选 |
| limit | int | 否 | 查询条数 |

---

### 2.11 健康检查

**GET** `/api/monitor/health`

返回监控系统自身的健康状态。

**响应：**

```json
{
  "success": true,
  "data": {
    "status": "healthy",
    "pendingAlerts": 3,
    "fatalAlerts": 0,
    "activeIncidents": 1,
    "metricsCollected": 150
  }
}
```

---

### 2.12 监控统计

**GET** `/api/monitor/stats`

返回监控模块全局统计。

**响应：**

```json
{
  "success": true,
  "data": {
    "metricsCount": 150,
    "alertsCount": 30,
    "incidentsCount": 5,
    "dashboardsCount": 8,
    "alertLevelDistribution": { "info": 10, "warning": 15, "critical": 4, "fatal": 1 },
    "alertStatusDistribution": { "pending": 5, "acknowledged": 10, "resolved": 14, "suppressed": 1 },
    "incidentLevelDistribution": { "P0": 0, "P1": 1, "P2": 3, "P3": 1 },
    "incidentStatusDistribution": { "detected": 1, "investigating": 1, "mitigating": 1, "resolved": 2, "postmortem": 0 },
    "metricTypeDistribution": { "system": 50, "business": 40, "performance": 30, "error": 30 }
  }
}
```

---

## 三、AI 智能维护模块（12 接口）

### 3.1 创建维护任务

**POST** `/api/maintenance/tasks`

**请求体：**

```json
{
  "taskName": "每日数据库备份",
  "taskType": "backup",
  "target": "db-server",
  "triggerType": "scheduled",
  "params": { "backupType": "full", "retentionDays": 7 },
  "schedule": "0 2 * * *",
  "aiAutomationRate": 90.0
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| taskName | string | 是 | 任务名称 |
| taskType | string | 是 | 类型：`backup`/`cleanup`/`optimize`/`inspect`/`restart`/`scale` |
| target | string | 是 | 维护目标 |
| triggerType | string | 否 | 触发类型：`manual`/`scheduled`（默认 manual） |
| params | object | 否 | 任务参数 |
| schedule | string | 否 | 调度表达式（Cron） |
| aiAutomationRate | number | 否 | AI自动化率（默认 90.0） |

---

### 3.2 查询维护任务列表

**GET** `/api/maintenance/tasks`

**查询参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| task_type | string | 否 | 按任务类型筛选 |
| task_status | string | 否 | 按任务状态筛选 |
| limit | int | 否 | 查询条数 |

---

### 3.3 执行/取消维护任务

**PUT** `/api/maintenance/tasks/{task_id}`

**路径参数：** `task_id` (int) - 任务 ID

**请求体：**

```json
{
  "action": "execute",
  "result": { "backupSize": "2.5GB", "duration": 120 },
  "errorMessage": ""
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| action | string | 是 | 动作：`execute`/`cancel` |
| result | object | 否 | 执行结果（execute 时填） |
| errorMessage | string | 否 | 错误信息（execute 失败时填） |

**状态流转：**
- `execute`: pending → running → success（有 result）/ failed（有 errorMessage）
- `cancel`: pending/running → cancelled

---

### 3.4 创建并执行健康检查

**POST** `/api/maintenance/health`

创建检查项配置，若指定 `healthStatus` 则立即执行检查。

**请求体：**

```json
{
  "checkName": "订单服务HTTP检查",
  "serviceName": "order-service",
  "checkType": "http",
  "checkConfig": { "url": "http://order:8000/health", "method": "GET" },
  "threshold": { "responseTime": 1000 },
  "healthStatus": "healthy",
  "responseTime": 150
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| checkName | string | 是 | 检查项名称 |
| serviceName | string | 是 | 服务名称 |
| checkType | string | 是 | 检查类型：`http`/`tcp`/`resource`/`custom` |
| checkConfig | object | 否 | 检查配置 |
| threshold | object | 否 | 阈值配置 |
| healthStatus | string | 否 | 健康状态（指定则立即执行）：`healthy`/`degraded`/`unhealthy`/`unknown` |
| responseTime | int | 否 | 响应时间毫秒（默认 0） |

---

### 3.5 查询健康检查列表

**GET** `/api/maintenance/health`

**查询参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| service_name | string | 否 | 按服务名称筛选 |
| health_status | string | 否 | 按健康状态筛选 |
| limit | int | 否 | 查询条数 |

---

### 3.6 查询健康检查详情

**GET** `/api/maintenance/health/{check_id}`

**路径参数：** `check_id` (int) - 检查项 ID

---

### 3.7 检测故障并创建自愈记录

**POST** `/api/maintenance/recovery`

**请求体：**

```json
{
  "faultType": "service_down",
  "faultSource": "payment-service",
  "faultDescription": "支付服务无响应",
  "recoveryLevel": "auto",
  "aiAutomationRate": 90.0
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| faultType | string | 是 | 故障类型 |
| faultSource | string | 是 | 故障来源 |
| faultDescription | string | 否 | 故障描述 |
| recoveryLevel | string | 否 | 自愈级别：`auto`/`assisted`/`manual`（默认 auto） |
| aiAutomationRate | number | 否 | AI自动化率（默认 90.0） |

---

### 3.8 查询自愈记录列表

**GET** `/api/maintenance/recovery`

**查询参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| fault_type | string | 否 | 按故障类型筛选 |
| recovery_status | string | 否 | 按自愈状态筛选 |
| limit | int | 否 | 查询条数 |

---

### 3.9 提交性能优化建议

**POST** `/api/maintenance/optimizations`

**请求体：**

```json
{
  "optimizationType": "index",
  "target": "order_table",
  "proposal": "为 order_table.status 字段添加索引",
  "expectedBenefit": { "querySpeedup": "10x", "latencyReduction": "80%" },
  "executionPlan": { "sql": "CREATE INDEX idx_status ON order_table(status)" },
  "aiAutomationRate": 85.0
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| optimizationType | string | 是 | 优化类型 |
| target | string | 是 | 优化目标 |
| proposal | string | 是 | 优化建议 |
| expectedBenefit | object | 否 | 预期收益 |
| executionPlan | object | 否 | 执行计划 |
| aiAutomationRate | number | 否 | AI自动化率（默认 85.0） |

---

### 3.10 查询优化建议列表

**GET** `/api/maintenance/optimizations`

**查询参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| optimization_type | string | 否 | 按优化类型筛选 |
| optimization_status | string | 否 | 按优化状态筛选 |
| limit | int | 否 | 查询条数 |

---

### 3.11 一键巡检全服务

**POST** `/api/maintenance/inspect`

自动对所有核心服务执行健康检查，发现不健康服务自动创建故障自愈记录。

**请求体：** 无（空 body）

**响应：**

```json
{
  "success": true,
  "data": {
    "inspectedAt": "2026-08-22T08:00:00",
    "totalServices": 10,
    "healthyCount": 8,
    "degradedCount": 1,
    "unhealthyCount": 1,
    "healthCheckIds": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
    "recoveryIds": [1],
    "statusCount": {
      "healthy": 8,
      "degraded": 1,
      "unhealthy": 1,
      "unknown": 0
    }
  }
}
```

---

### 3.12 维护模块统计

**GET** `/api/maintenance/stats`

**响应：**

```json
{
  "success": true,
  "data": {
    "tasksCount": 50,
    "healthChecksCount": 100,
    "recoveriesCount": 5,
    "optimizationsCount": 10,
    "taskStatusDistribution": {
      "pending": 5,
      "running": 2,
      "success": 35,
      "failed": 3,
      "cancelled": 5
    },
    "optimizationStatusDistribution": {
      "proposed": 4,
      "approved": 2,
      "executing": 1,
      "completed": 2,
      "rejected": 1
    }
  }
}
```

---

## 四、状态机定义

### 4.1 监控模块状态机

#### 告警状态

```
pending → acknowledged → resolved
                ↓
           suppressed ← (任意状态可抑制)
```

| 状态 | 说明 |
|------|------|
| pending | 待处理（初始状态） |
| acknowledged | 已确认 |
| resolved | 已解决（终态） |
| suppressed | 已抑制（终态） |

#### 告警级别

| 级别 | 说明 | 默认通知渠道 |
|------|------|-------------|
| info | 信息 | 日志 |
| warning | 警告 | 日志 + 邮件 |
| critical | 严重 | 日志 + 邮件 + 短信 |
| fatal | 致命 | 日志 + 邮件 + 短信 + 电话 |

#### 故障状态

```
detected → investigating → mitigating → resolved → postmortem
```

| 状态 | 说明 |
|------|------|
| detected | 检测到（初始状态） |
| investigating | 调查中 |
| mitigating | 处置中 |
| resolved | 已解决 |
| postmortem | 已复盘（终态） |

#### 故障级别

| 级别 | 说明 | 响应时间 |
|------|------|---------|
| P0 | 致命 | 立即 |
| P1 | 严重 | 15分钟 |
| P2 | 中等 | 1小时 |
| P3 | 轻微 | 4小时 |

### 4.2 维护模块状态机

#### 任务状态

```
pending → running → success
                  → failed
pending/running → cancelled
```

| 状态 | 说明 |
|------|------|
| pending | 待执行（初始状态） |
| running | 执行中 |
| success | 成功（终态） |
| failed | 失败（终态） |
| cancelled | 已取消（终态） |

#### 任务类型

| 类型 | 说明 |
|------|------|
| backup | 备份 |
| cleanup | 清理 |
| optimize | 优化 |
| inspect | 巡检 |
| restart | 重启 |
| scale | 扩容 |

#### 健康状态

| 状态 | 说明 |
|------|------|
| healthy | 健康 |
| degraded | 降级 |
| unhealthy | 不健康 |
| unknown | 未知（默认） |

#### 自愈状态

```
detected → diagnosing → recovering → recovered
                                   → failed
                                   → manual_required
```

| 状态 | 说明 |
|------|------|
| detected | 检测到（初始状态） |
| diagnosing | 诊断中 |
| recovering | 恢复中 |
| recovered | 已恢复（终态） |
| failed | 恢复失败（终态） |
| manual_required | 需人工（终态） |

#### 自愈级别

| 级别 | 说明 |
|------|------|
| auto | 自动恢复 |
| assisted | AI辅助 |
| manual | 需人工介入 |

#### 优化状态

```
proposed → approved → executing → completed
         → rejected
```

| 状态 | 说明 |
|------|------|
| proposed | 建议中（初始状态） |
| approved | 已批准 |
| executing | 执行中 |
| completed | 已完成（终态） |
| rejected | 已驳回（终态） |

---

## 五、错误码说明

### 5.1 HTTP 状态码

| 状态码 | 说明 | 触发场景 |
|--------|------|---------|
| 200 | 成功 | 正常请求 |
| 403 | 禁止访问 | 缺少 `X-Role: admin` 头 |
| 404 | 资源不存在 | 查询的 ID 不存在（KeyError） |
| 409 | 冲突 | 状态非法/参数无效（ValueError） |
| 422 | 参数校验失败 | Pydantic 请求体校验不通过 |
| 500 | 服务器错误 | 未预期异常 |

### 5.2 错误响应示例

**403 权限不足：**

```json
{ "detail": "需要管理员权限" }
```

**404 资源不存在：**

```json
{ "detail": "告警不存在" }
```

**409 状态冲突：**

```json
{ "detail": "告警状态非法: pending → resolved (须为 acknowledged → resolved)" }
```

**422 参数校验：**

```json
{
  "detail": [
    {
      "loc": ["body", "alertLevel"],
      "msg": "field required",
      "type": "value_error.missing"
    }
  ]
}
```

---

## 附录：快速对接清单

### 监控模块接口速查

| # | 方法 | 路径 | 功能 |
|---|------|------|------|
| 1 | POST | `/api/monitor/metrics` | 采集监控指标 |
| 2 | GET | `/api/monitor/metrics` | 查询指标列表 |
| 3 | POST | `/api/monitor/alerts` | 创建告警 |
| 4 | GET | `/api/monitor/alerts` | 查询告警列表 |
| 5 | PUT | `/api/monitor/alerts/{alert_id}` | 告警状态流转 |
| 6 | POST | `/api/monitor/incidents` | 创建故障事件 |
| 7 | GET | `/api/monitor/incidents` | 查询故障列表 |
| 8 | PUT | `/api/monitor/incidents/{incident_id}` | 故障状态流转 |
| 9 | POST | `/api/monitor/dashboards` | 创建仪表盘 |
| 10 | GET | `/api/monitor/dashboards` | 查询仪表盘列表 |
| 11 | GET | `/api/monitor/health` | 健康检查 |
| 12 | GET | `/api/monitor/stats` | 监控统计 |

### 维护模块接口速查

| # | 方法 | 路径 | 功能 |
|---|------|------|------|
| 1 | POST | `/api/maintenance/tasks` | 创建维护任务 |
| 2 | GET | `/api/maintenance/tasks` | 查询任务列表 |
| 3 | PUT | `/api/maintenance/tasks/{task_id}` | 执行/取消任务 |
| 4 | POST | `/api/maintenance/health` | 创建并执行健康检查 |
| 5 | GET | `/api/maintenance/health` | 查询健康检查列表 |
| 6 | GET | `/api/maintenance/health/{check_id}` | 查询检查详情 |
| 7 | POST | `/api/maintenance/recovery` | 检测故障创建自愈 |
| 8 | GET | `/api/maintenance/recovery` | 查询自愈记录 |
| 9 | POST | `/api/maintenance/optimizations` | 提交优化建议 |
| 10 | GET | `/api/maintenance/optimizations` | 查询优化列表 |
| 11 | POST | `/api/maintenance/inspect` | 一键巡检 |
| 12 | GET | `/api/maintenance/stats` | 维护统计 |
