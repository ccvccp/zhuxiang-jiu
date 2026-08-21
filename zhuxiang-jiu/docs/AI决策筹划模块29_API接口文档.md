# AI决策筹划模块（模块29）API 接口文档

> 版本: 1.0 · 日期: 2026-08-19 · 适用范围: `js/modules.js` 模块29 定义
> 架构: 6层(感知→知识→决策→编排→执行→反馈) · 双维服务(角色+模块)
> 核心原则: 模型提动作,规则定执行(先 Copilot 后 Agent)
> 参照: Microsoft Copilot / 阿里 AI中台 / Gartner 超自动化 / KPMG 全栈AI / 腾讯 AI×数据中台

---

## 目录

1. [架构总览](#1-架构总览)
2. [认证与角色权限](#2-认证与角色权限)
3. [通用响应结构](#3-通用响应结构)
4. [API 端点总览](#4-api-端点总览)
5. [感知层端点](#5-感知层端点)
6. [知识层端点](#6-知识层端点)
7. [决策层端点](#7-决策层端点)
8. [编排层端点](#8-编排层端点)
9. [执行层端点](#9-执行层端点)
10. [反馈层端点](#10-反馈层端点)
11. [系统端点](#11-系统端点)
12. [错误码表](#12-错误码表)
13. [限流与并发控制](#13-限流与并发控制)
14. [数据库表参考](#14-数据库表参考)
15. [Mock/Live 模式](#15-mocklive-模式)

---

## 1. 架构总览

### 1.1 六层架构

```
┌─────────────────────────────────────────────────────────────┐
│                     体验层 (Web/App/API)                      │
├─────────────────────────────────────────────────────────────┤
│  反馈层    AI智能反馈闭环 · AI智能复盘优化                      │
├─────────────────────────────────────────────────────────────┤
│  执行层    AI智能角色决策助理(Copilot→Agent)                   │
├─────────────────────────────────────────────────────────────┤
│  编排层    AI智能编排调度 · AI智能能力路由                       │
├─────────────────────────────────────────────────────────────┤
│  决策层    AI智能策略筹划 · AI智能预测推演                      │
│           AI智能治理决策 · AI智能风控决策                       │
├─────────────────────────────────────────────────────────────┤
│  知识层    AI智能知识中枢(RAG+组织记忆+语义图谱)                 │
├─────────────────────────────────────────────────────────────┤
│  感知层    数据采集(28模块) · 事件流 · 实时指标                  │
└─────────────────────────────────────────────────────────────┘
```

### 1.2 双维服务

| 服务维度 | 服务对象 | 核心能力 | 关键指标 |
|---------|---------|---------|---------|
| 角色服务 | 会员 L1-L5/SVIP | 选购助理·积分优化·等级规划 | 决策准确率 92% |
| 角色服务 | 代理商 D-S | 经营助理·库存规划·绩效分析 | 筹划效率 +60% |
| 角色服务 | 访客 | 导购·转化引导·产品发现 | 预测准确率 90% |
| 角色服务 | 网店主 | 运营决策·选品·直播·定价 | 编排成功率 95% |
| 角色服务 | 管理员 | 全局编排·风控·治理·审计 | 治理合规率 100% |
| 模块服务 | 01-28 全模块 | 跨域编排·能力路由·知识共享 | 能力复用率 78% |

### 1.3 API 基础路径

```
apiBase = '/api/decision'
```

所有端点路径均以 `/api/decision` 为前缀。

---

## 2. 认证与角色权限

### 2.1 角色体系

| 角色标识 | 角色名称 | 权限等级 | 可访问端点 |
|---------|---------|---------|-----------|
| `member` | 会员 (L1-L5/SVIP) | L1 | role-copilot, strategy-plan, forecast-simulate, knowledge-query |
| `agent` | 代理商 (D-S) | L2 | 上述 + orchestrate(本区域), feedback-loop |
| `guest` | 访客 | L0 | role-copilot(只读), knowledge-query(只读) |
| `store_owner` | 网店主 (SVIP/L5) | L3 | 上述 + orchestrate, capability-route, retrospective |
| `admin` | 管理员 | L4 | 全部端点 |

### 2.2 鉴权方式

```
Authorization: Bearer <token>
X-Role: member|agent|guest|store_owner|admin
X-Module-Scope: 01,02,15,...  (可访问的模块范围)
```

### 2.3 核心原则

> **模型提动作,规则定执行** — AI 可以提出候选策略/动作,但优惠额度、触达频率、敏感操作等必须经规则引擎校验。高风险动作进入人工审批,低风险动作自动执行。(参照腾讯 AI×数据中台)

---

## 3. 通用响应结构

### 3.1 成功响应

```json
{
    "success": true,
    "operation": "endpoint_name",
    "details": {
        "key1": "value1",
        "key2": "value2"
    },
    "logs": [
        { "stage": "阶段1-参数校验", "message": "...", "data": {} },
        { "stage": "阶段2-决策推理", "message": "...", "data": {} }
    ],
    "asyncOps": ["blockchain_notarize", "model_iteration"]
}
```

### 3.2 失败响应(校验中止)

```json
{
    "success": false,
    "error": "错误原因描述",
    "logs": [
        { "stage": "阶段1-参数校验", "message": "校验失败", "data": {} }
    ]
}
```

### 3.3 失败响应(事务失败)

```json
{
    "success": false,
    "operation": "endpoint_name",
    "error": "错误原因描述",
    "failedStage": "阶段3-AI决策",
    "logs": [...]
}
```

### 3.4 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `success` | boolean | 操作是否成功 |
| `operation` | string | 端点操作名称 |
| `details` | object | 返回数据详情 |
| `logs` | array | 执行阶段日志(含区块链存证) |
| `asyncOps` | array | 异步后续操作(非阻塞) |
| `error` | string | 失败原因(success=false 时存在) |
| `failedStage` | string | 失败阶段(事务失败时存在) |

---

## 4. API 端点总览

### 4.1 按层级分组

| 层级 | 端点 | 方法 | 路径 | 角色 |
|------|------|------|------|------|
| 感知层 | data-ingest | POST | /api/decision/data-ingest | admin |
| 知识层 | knowledge-query | GET | /api/decision/knowledge/query | 全角色 |
| 知识层 | knowledge-ingest | POST | /api/decision/knowledge/ingest | admin |
| 决策层 | strategy-plan | POST | /api/decision/strategy-plan | member+ |
| 决策层 | forecast-simulate | POST | /api/decision/forecast-simulate | member+ |
| 决策层 | governance | POST | /api/decision/governance | admin |
| 决策层 | risk-control | POST | /api/decision/risk-control | admin |
| 编排层 | orchestrate | POST | /api/decision/orchestrate | agent+ |
| 编排层 | capability-route | POST | /api/decision/capability-route | store_owner+ |
| 执行层 | role-copilot | POST | /api/decision/role-copilot | 全角色 |
| 反馈层 | feedback-loop | POST | /api/decision/feedback-loop | agent+ |
| 反馈层 | retrospective | POST | /api/decision/retrospective | store_owner+ |
| 系统 | health | GET | /api/decision/health | 全角色 |
| 系统 | mode | GET | /api/decision/mode | admin |
| 系统 | mode-switch | POST | /api/decision/mode/switch | admin |

---

## 5. 感知层端点

### 5.1 POST /api/decision/data-ingest

**描述**: 从 28 个业务模块采集数据,注入感知层。支持批量推送指标、事件、日志。

**角色**: admin

**请求参数**:

```json
{
    "source": "module:04",
    "dataType": "metrics",
    "payload": [
        { "key": "order_count", "value": 152, "timestamp": "2026-08-19T10:00:00Z" },
        { "key": "gmv", "value": 98000, "timestamp": "2026-08-19T10:00:00Z" }
    ],
    "realtime": true
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `source` | string | 是 | 数据来源,格式 `module:{id}` |
| `dataType` | string | 是 | 数据类型: `metrics`/`events`/`logs`/`alerts` |
| `payload` | array | 是 | 数据项数组,每项含 key/value/timestamp |
| `realtime` | boolean | 否 | 是否实时流,默认 false |

**响应**:

```json
{
    "success": true,
    "operation": "data-ingest",
    "details": {
        "ingestedCount": 2,
        "source": "module:04",
        "bufferState": "healthy",
        "processedAt": "2026-08-19T10:00:01Z"
    },
    "logs": [...]
}
```

---

## 6. 知识层端点

### 6.1 GET /api/decision/knowledge/query

**描述**: AI 智能知识中枢查询。基于 RAG + 组织记忆 + 语义图谱,返回可验证的业务上下文。

**角色**: 全角色(访客只读)

**查询参数**:

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `q` | string | 是 | 自然语言查询 |
| `scope` | string | 否 | 知识范围: `business`/`technical`/`compliance`/`history` |
| `topK` | number | 否 | 召回条数,默认 5 |

**请求示例**:

```
GET /api/decision/knowledge/query?q=会员L3留存消费标准&scope=business&topK=5
```

**响应**:

```json
{
    "success": true,
    "operation": "knowledge-query",
    "details": {
        "query": "会员L3留存消费标准",
        "recallRate": "93%",
        "results": [
            {
                "content": "L3 VIP留存消费: ¥2000/年",
                "source": "module:02/会员管理",
                "confidence": 0.98,
                "tags": ["会员", "L3", "留存消费"]
            }
        ],
        "context": {
            "semanticGraph": "user_domain",
            "orgMemory": "project_memory.md/L3 retention rule"
        }
    },
    "logs": [...]
}
```

### 6.2 POST /api/decision/knowledge/ingest

**描述**: 向知识中枢注入新知识(组织记忆、规则、经验教训)。

**角色**: admin

**请求参数**:

```json
{
    "category": "lesson_learned",
    "title": "团购订单<5万拦截规则",
    "content": "团购订单金额<¥50,000必须拦截并返回错误,不可回退T4(70%折扣)",
    "tags": ["团购", "风控", "规则"],
    "source": "project_memory.md"
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `category` | string | 是 | `rule`/`lesson_learned`/`constraint`/`red_line` |
| `title` | string | 是 | 知识标题 |
| `content` | string | 是 | 知识内容 |
| `tags` | array | 否 | 标签数组 |
| `source` | string | 否 | 来源文件/模块 |

**响应**:

```json
{
    "success": true,
    "operation": "knowledge-ingest",
    "details": {
        "knowledgeId": "KN-20260819-001",
        "category": "lesson_learned",
        "indexed": true,
        "graphUpdated": true
    },
    "asyncOps": ["embedding_update", "graph_rebuild"]
}
```

---

## 7. 决策层端点

### 7.1 POST /api/decision/strategy-plan

**描述**: AI 智能策略筹划。目标分解 + 资源评估 + 路径规划 + What-if 推演。

**角色**: member+

**请求参数**:

```json
{
    "role": "store_owner",
    "goal": "月销售额提升至¥10000",
    "constraints": {
        "budget": 5000,
        "timeframe": "30d",
        "riskTolerance": "medium"
    },
    "whatIfScenarios": [
        { "name": "方案A-直播引流", "params": { "channel": "livestream", "frequency": "weekly" } },
        { "name": "方案B-积分促复购", "params": { "channel": "points", "deduction": "30%" } }
    ]
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `role` | string | 是 | 角色标识 |
| `goal` | string | 是 | 目标描述 |
| `constraints` | object | 是 | 约束条件(budget/timeframe/riskTolerance) |
| `whatIfScenarios` | array | 否 | What-if 推演方案列表 |

**响应**:

```json
{
    "success": true,
    "operation": "strategy-plan",
    "details": {
        "goalDecomposed": [
            { "subGoal": "周销售额≥¥2500", "priority": "high" },
            { "subGoal": "复购率提升15%", "priority": "medium" },
            { "subGoal": "新客获取≥50人", "priority": "medium" }
        ],
        "resourceAssessment": {
            "budgetSufficient": true,
            "bottleneck": "流量获取"
        },
        "recommendedPath": "方案B-积分促复购",
        "whatIfResults": [
            { "name": "方案A-直播引流", "projectedGMV": "¥8500", "risk": "low" },
            { "name": "方案B-积分促复购", "projectedGMV": "¥10500", "risk": "low" }
        ],
        "planningEfficiency": "60%",
        "needsApproval": false
    },
    "logs": [...]
}
```

### 7.2 POST /api/decision/forecast-simulate

**描述**: AI 智能预测推演。季节性 + 趋势 + 容量预测 + 蒙特卡洛模拟。

**角色**: member+

**请求参数**:

```json
{
    "target": "sales_volume",
    "timeframe": "next_30d",
    "method": "monte_carlo",
    "iterations": 10000,
    "factors": ["seasonality", "trend", "promotion", "weather"]
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `target` | string | 是 | 预测目标: `sales_volume`/`inventory`/`capacity`/`churn` |
| `timeframe` | string | 是 | 预测范围: `next_7d`/`next_30d`/`next_90d` |
| `method` | string | 否 | `monte_carlo`/`lstm`/`arima`,默认 `monte_carlo` |
| `iterations` | number | 否 | 蒙特卡洛迭代次数,默认 10000 |
| `factors` | array | 否 | 影响因子列表 |

**响应**:

```json
{
    "success": true,
    "operation": "forecast-simulate",
    "details": {
        "target": "sales_volume",
        "forecast": {
            "p50": 320,
            "p75": 380,
            "p95": 450,
            "confidenceInterval": [280, 520]
        },
        "accuracy": "90%",
        "factorsWeight": {
            "seasonality": 0.35,
            "trend": 0.25,
            "promotion": 0.30,
            "weather": 0.10
        },
        "method": "monte_carlo",
        "iterations": 10000
    },
    "logs": [...]
}
```

### 7.3 POST /api/decision/governance

**描述**: AI 智能治理决策。模型提动作 + 规则定执行 + 权限校验 + 区块链追溯。

**角色**: admin

**请求参数**:

```json
{
    "proposedAction": {
        "action": "adjust_price",
        "target": "product:ZX-001",
        "params": { "newPrice": 298, "reason": "促销" }
    },
    "ruleCheck": true,
    "requireApproval": "auto"
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `proposedAction` | object | 是 | AI 提议的动作(含 action/target/params) |
| `ruleCheck` | boolean | 否 | 是否执行规则引擎校验,默认 true |
| `requireApproval` | string | 否 | `auto`/`manual`,默认 `auto` |

**响应**:

```json
{
    "success": true,
    "operation": "governance",
    "details": {
        "action": "adjust_price",
        "ruleCheckResult": {
            "passed": true,
            "violations": [],
            "checkedRules": 14
        },
        "permissionGranted": true,
        "executionMode": "auto",
        "blockchainNotarize": {
            "hash": "0x1a2b3c...",
            "type": "决策存证"
        },
        "complianceRate": "100%"
    },
    "asyncOps": ["blockchain_notarize", "audit_log"]
}
```

### 7.4 POST /api/decision/risk-control

**描述**: AI 智能风控决策。异常检测 + 合规校验 + 风险预警 + 自动熔断。

**角色**: admin

**请求参数**:

```json
{
    "checkType": "transaction_anomaly",
    "target": {
        "userId": "U10086",
        "transactionId": "ZX20260819-001",
        "amount": 9999
    },
    "thresholds": {
        "maxAmount": 10000,
        "frequencyPerDay": 5
    },
    "autoCircuitBreak": true
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `checkType` | string | 是 | `transaction_anomaly`/`compliance`/`fraud`/`abuse` |
| `target` | object | 是 | 检查目标(含 userId/transactionId/amount 等) |
| `thresholds` | object | 否 | 自定义阈值 |
| `autoCircuitBreak` | boolean | 否 | 是否自动熔断,默认 true |

**响应**:

```json
{
    "success": true,
    "operation": "risk-control",
    "details": {
        "riskLevel": "medium",
        "anomalies": [
            { "type": "amount_near_limit", "detail": "金额¥9999接近上限¥10000", "score": 0.72 }
        ],
        "circuitBreaker": "standby",
        "recommendation": "允许交易,标记观察",
        "coverage": "96%",
        "action": "pass_with_monitoring"
    },
    "logs": [...]
}
```

---

## 8. 编排层端点

### 8.1 POST /api/decision/orchestrate

**描述**: AI 智能编排调度。28 模块跨域工作流编排 + 任务分解 + 依赖管理。

**角色**: agent+

**请求参数**:

```json
{
    "workflow": "order_to_delivery",
    "modules": ["04", "05", "06", "28"],
    "context": {
        "orderId": "ZX20260819-001",
        "priority": "high"
    },
    "decompose": true,
    "parallel": true
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `workflow` | string | 是 | 工作流名称 |
| `modules` | array | 是 | 参与模块 ID 列表 |
| `context` | object | 是 | 工作流上下文 |
| `decompose` | boolean | 否 | 是否任务分解,默认 true |
| `parallel` | boolean | 否 | 是否并行执行无依赖任务,默认 true |

**响应**:

```json
{
    "success": true,
    "operation": "orchestrate",
    "details": {
        "workflow": "order_to_delivery",
        "tasks": [
            { "id": "T1", "module": "04", "name": "创建订单", "status": "pass", "depends": [] },
            { "id": "T2", "module": "05", "name": "收款", "status": "pass", "depends": ["T1"] },
            { "id": "T3", "module": "28", "name": "出库", "status": "pass", "depends": ["T2"] },
            { "id": "T4", "module": "06", "name": "物流发货", "status": "pending", "depends": ["T3"] }
        ],
        "parallelGroups": [["T1"], ["T2"], ["T3"], ["T4"]],
        "successRate": "95%",
        "duration": "1.2s"
    },
    "logs": [...]
}
```

### 8.2 POST /api/decision/capability-route

**描述**: AI 智能能力路由。原子能力插件池 + 动态组合 + 按需调度。

**角色**: store_owner+

**请求参数**:

```json
{
    "requiredCapabilities": ["nlp", "vision", "decision_reasoning"],
    "task": "product_image_review",
    "budget": {
        "maxLatency": 500,
        "maxCost": 0.05
    },
    "preferPlugins": ["nlp_bert_v2", "vision_resnet50"]
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `requiredCapabilities` | array | 是 | 需要的能力类型 |
| `task` | string | 是 | 任务名称 |
| `budget` | object | 否 | 预算约束(maxLatency/maxCost) |
| `preferPlugins` | array | 否 | 偏好插件 |

**响应**:

```json
{
    "success": true,
    "operation": "capability-route",
    "details": {
        "selectedPlugins": [
            { "id": "nlp_bert_v2", "type": "自然语言", "latency": "120ms", "cost": "¥0.01" },
            { "id": "vision_resnet50", "type": "计算机视觉", "latency": "180ms", "cost": "¥0.02" },
            { "id": "rule_engine_v1", "type": "决策推理", "latency": "30ms", "cost": "¥0.00" }
        ],
        "composition": "nlp_bert_v2 → vision_resnet50 → rule_engine_v1",
        "totalLatency": "330ms",
        "totalCost": "¥0.03",
        "reuseRate": "78%",
        "pluginPool": 120
    },
    "logs": [...]
}
```

---

## 9. 执行层端点

### 9.1 POST /api/decision/role-copilot

**描述**: AI 智能角色决策助理。为 5 类角色提供 Copilot 式决策支持(先 Copilot 后 Agent)。

**角色**: 全角色(按角色身份返回不同建议)

**请求参数**:

```json
{
    "role": "member",
    "level": "L3",
    "intent": "选购竹香酒",
    "context": {
        "browsing": ["ZX-001", "ZX-003"],
        "points": 12500,
        "budget": 500
    },
    "mode": "copilot"
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `role` | string | 是 | `member`/`agent`/`guest`/`store_owner`/`admin` |
| `level` | string | 否 | 角色等级(如 L1-L5/D-S) |
| `intent` | string | 是 | 用户意图描述 |
| `context` | object | 否 | 上下文(browsing/points/budget 等) |
| `mode` | string | 否 | `copilot`(建议,人执行)/`agent`(自动执行),默认 `copilot` |

**响应(会员选购助理)**:

```json
{
    "success": true,
    "operation": "role-copilot",
    "details": {
        "role": "member",
        "recommendations": [
            {
                "type": "product",
                "target": "ZX-001",
                "reason": "基于浏览记录推荐,竹香型适合您",
                "confidence": 0.92
            },
            {
                "type": "points_optimize",
                "target": "使用1250竹叶抵扣¥12.5",
                "reason": "当前积分12500,30%抵扣上限内",
                "confidence": 0.95
            }
        ],
        "decisionAccuracy": "92%",
        "executionMode": "copilot",
        "needsUserConfirm": true
    },
    "logs": [...]
}
```

---

## 10. 反馈层端点

### 10.1 POST /api/decision/feedback-loop

**描述**: AI 智能反馈闭环。数据采集 → 效果评估 → 模型迭代 → 插件升级,闭环延迟 <24h。

**角色**: agent+

**请求参数**:

```json
{
    "actionId": "ACT-20260819-001",
    "outcome": {
        "delivered": true,
        "clicked": true,
        "ordered": false,
        "complained": false
    },
    "cost": 0.05,
    "reflowMetrics": ["ctr", "conversion_rate", "roi"]
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `actionId` | string | 是 | 关联的决策动作 ID |
| `outcome` | object | 是 | 执行结果(delivered/clicked/ordered 等) |
| `cost` | number | 否 | 执行成本 |
| `reflowMetrics` | array | 否 | 需回流的指标 |

**响应**:

```json
{
    "success": true,
    "operation": "feedback-loop",
    "details": {
        "actionId": "ACT-20260819-001",
        "evaluation": {
            "ctr": "12%",
            "conversionRate": "3.5%",
            "roi": "2.1x"
        },
        "modelUpdate": {
            "triggered": true,
            "plugin": "nlp_bert_v2",
            "improvement": "+0.03 accuracy"
        },
        "feedbackLatency": "<24h",
        "blockchainNotarize": {
            "hash": "0x4d5e6f...",
            "type": "反馈追踪"
        }
    },
    "asyncOps": ["model_iteration", "plugin_upgrade", "data_reflow"]
}
```

### 10.2 POST /api/decision/retrospective

**描述**: AI 智能复盘优化。事后分析 + 根因定位 + 经验沉淀 + 策略优化。

**角色**: store_owner+

**请求参数**:

```json
{
    "event": "monthly_campaign_202608",
    "period": "2026-08-01/2026-08-31",
    "scope": ["module:09", "module:10", "module:04"],
    "depth": "root_cause"
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `event` | string | 是 | 复盘事件名称 |
| `period` | string | 是 | 复盘时间段 |
| `scope` | array | 否 | 涉及模块范围 |
| `depth` | string | 否 | `summary`/`root_cause`,默认 `summary` |

**响应**:

```json
{
    "success": true,
    "operation": "retrospective",
    "details": {
        "event": "monthly_campaign_202608",
        "analysis": {
            "gmv": "¥98,500",
            "target": "¥100,000",
            "achievement": "98.5%",
            "rootCause": "第二周流量下降23%,主因直播频率不足"
        },
        "lessonsLearned": [
            {
                "title": "直播频率与流量正相关",
                "detail": "周播3次以上流量稳定,2次以下下降20%+",
                "tag": "经验沉淀"
            }
        ],
        "strategyOptimization": "建议保持周播≥3次,增加互动环节",
        "coverage": "85%"
    },
    "asyncOps": ["knowledge_ingest", "strategy_update"]
}
```

---

## 11. 系统端点

### 11.1 GET /api/decision/health

**描述**: 健康检查,返回模块 29 运行状态。

**角色**: 全角色

**响应**:

```json
{
    "success": true,
    "operation": "health",
    "details": {
        "status": "healthy",
        "module": "AI决策筹划模块(29)",
        "aiRate": "95%",
        "layers": "感知→知识→决策→编排→执行→反馈",
        "pluginPool": 120,
        "uptime": "72h",
        "mockMode": false
    }
}
```

### 11.2 GET /api/decision/mode

**描述**: 查询当前运行模式(Mock/Live)。

**角色**: admin

**响应**:

```json
{
    "success": true,
    "operation": "mode",
    "details": {
        "mode": "mock",
        "apiBase": "/api/decision",
        "endpoints": 15,
        "aiCapabilities": 10
    }
}
```

### 11.3 POST /api/decision/mode/switch

**描述**: 切换运行模式(Mock ↔ Live)。

**角色**: admin

**请求参数**:

```json
{
    "mode": "live",
    "apiBase": "https://api.zhuxiang-jiu.com/api/decision"
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `mode` | string | 是 | `mock`/`live` |
| `apiBase` | string | 否 | Live 模式的后端地址 |

**响应**:

```json
{
    "success": true,
    "operation": "mode-switch",
    "details": {
        "mode": "live",
        "apiBase": "https://api.zhuxiang-jiu.com/api/decision"
    }
}
```

---

## 12. 错误码表

| 错误码 | HTTP 状态 | 说明 | 触发端点 |
|--------|----------|------|---------|
| `DECISION_001` | 400 | 参数缺失或格式错误 | 全部 |
| `DECISION_002` | 401 | 未认证(token 缺失或过期) | 全部 |
| `DECISION_003` | 403 | 角色权限不足 | 全部 |
| `DECISION_004` | 404 | 查询的知识/模块不存在 | knowledge-query |
| `DECISION_005` | 409 | 工作流冲突(已存在相同 workflowId) | orchestrate |
| `DECISION_006` | 422 | 规则校验未通过(模型提议被规则拒绝) | governance |
| `DECISION_007` | 422 | 风控拦截(高风险交易被熔断) | risk-control |
| `DECISION_008` | 422 | 预算不足(延迟/成本超出 budget) | capability-route |
| `DECISION_009` | 429 | 限流触发(请求频率超限) | 全部 |
| `DECISION_010` | 500 | 内部错误(AI 推理失败) | 全部 |
| `DECISION_011` | 500 | 模型不可用(模型服务宕机) | strategy-plan, forecast-simulate |
| `DECISION_012` | 500 | 知识图谱更新失败 | knowledge-ingest |
| `DECISION_013` | 503 | 插件池不可用(插件服务不可达) | capability-route |
| `DECISION_014` | 503 | 反馈回流失败(数据写入异常) | feedback-loop |

---

## 13. 限流与并发控制

### 13.1 限流策略

| 角色等级 | QPS 上限 | 并发上限 | 批量请求 |
|---------|---------|---------|---------|
| guest (L0) | 5 | 2 | 不支持 |
| member (L1) | 20 | 10 | 支持(≤50) |
| agent (L2) | 50 | 20 | 支持(≤100) |
| store_owner (L3) | 100 | 50 | 支持(≤200) |
| admin (L4) | 200 | 100 | 支持(≤500) |

### 13.2 并发安全

| 操作类型 | Mutex 锁键 | 说明 |
|---------|-----------|------|
| 编排调度 | `decision:orchestrate:{workflowId}` | 防止相同工作流并发执行 |
| 治理决策 | `decision:governance:{actionId}` | 防止相同动作重复执行 |
| 知识注入 | `decision:knowledge:ingest` | 序列化知识图谱更新 |
| 风控熔断 | `decision:risk:{userId}` | 防止单用户并发绕过风控 |
| 模式切换 | `decision:mode` | 模式切换全局锁 |

> 锁实现: FIFO 队列(非 while+await 自旋),参照项目 Mutex 规范。

---

## 14. 数据库表参考

### 14.1 核心表(15 张)

| 表名 | 说明 | 关键字段 |
|------|------|---------|
| `decision_actions` | 决策动作主表 | id, role, goal, proposed_action, status, blockchain_hash |
| `decision_strategies` | 策略筹划表 | id, action_id, goal_decomposed, recommended_path, what_if_results |
| `decision_forecasts` | 预测推演表 | id, target, method, p50, p75, p95, accuracy, factors_weight |
| `decision_orchestrations` | 编排调度表 | id, workflow, modules, tasks_json, success_rate, duration |
| `decision_capability_routes` | 能力路由表 | id, task, selected_plugins, composition, latency, cost |
| `decision_knowledge` | 知识中枢表 | id, category, title, content, tags, source, confidence, embedding |
| `decision_governance` | 治理决策表 | id, action_id, rule_check_result, permission, blockchain_hash |
| `decision_feedback` | 反馈闭环表 | id, action_id, outcome_json, evaluation, model_update, latency |
| `decision_risk_control` | 风控决策表 | id, check_type, target_json, risk_level, anomalies, action |
| `decision_retrospectives` | 复盘优化表 | id, event, period, analysis_json, lessons, strategy_optimization |
| `decision_role_copilot` | 角色助理表 | id, role, level, intent, recommendations_json, mode |
| `decision_plugin_pool` | 插件池表 | id, plugin_id, type, latency, cost, status, version |
| `decision_data_ingest` | 感知层采集表 | id, source, data_type, payload_json, realtime, processed_at |
| `decision_audit_log` | 审计日志表 | id, action_id, operation, actor_role, timestamp, blockchain_hash |
| `decision_ai_compliance` | AI 合规监控表 | id, module_id, legal_refs, compliance_rate, last_check |

### 14.2 索引(58 个)

| 索引类型 | 数量 | 示例 |
|---------|------|------|
| 主键索引 | 15 | `PRIMARY KEY (id)` |
| 唯一索引 | 8 | `UNIQUE (action_id)`, `UNIQUE (plugin_id)` |
| 普通索引 | 20 | `INDEX (role)`, `INDEX (category)`, `INDEX (risk_level)` |
| 复合索引 | 10 | `INDEX (source, data_type)`, `INDEX (role, level)` |
| 全文索引 | 5 | `FULLTEXT (title, content, tags)` — 知识中枢 RAG 检索 |

---

## 15. Mock/Live 模式

### 15.1 Mock 模式(默认)

- 数据来源: `MODULES.find(m => m.id === '29').mock()` 返回的内存对象
- 事务: 本地 localStorage 模拟,含 BEGIN/COMMIT/ROLLBACK
- 区块链: 模拟 hash `0x{timestamp_hex}`
- AI 推理: 基于 mock 数据的确定性判定(非真实 LLM 调用)
- 用途: 单元测试、开发调试、演示

### 15.2 Live 模式

- 数据来源: 后端 API `/api/decision/{endpoint}`
- 事务: 后端数据库真实事务
- 区块链: 真实链上存证
- AI 推理: 真实 LLM + RAG + 规则引擎
- 切换: `POST /api/decision/mode/switch { "mode": "live" }`

### 15.3 统一调用入口

```javascript
// Live 模式公共调用(参照 warehouse-service.js liveCall 模式)
async function liveCall(endpoint, params, method) {
    const url = apiBase + endpoint;
    return EnvAdapter.request({ url, method, data: params, timeout: 10000 });
}

// 各端点 Mock/Live 分发
async function strategyPlan(params) {
    if (mode === 'live') return liveCall('/strategy-plan', params, 'POST');
    // Mock 模式: 本地逻辑
    return mockStrategyPlan(params);
}
```

---

## 附录: 行业参考对照

| 参考来源 | 借鉴要素 | 本模块应用 |
|---------|---------|-----------|
| Microsoft Copilot | 插件化中台 + 角色助理 | role-copilot 端点 + 插件池 120 个 |
| 阿里 AI 中台 | 能力原子化 + 动态组合 | capability-route 端点 + 4 大类插件 |
| Gartner 超自动化 | 跨职能工作流编排 | orchestrate 端点 + 28 模块跨域 |
| KPMG 全栈 AI | Work Layer 意图→治理执行 | governance 端点 + 模型提动作规则定执行 |
| 腾讯 AI×数据中台 | 先 Copilot 后 Agent + 数据回流 | role-copilot mode 字段 + feedback-loop 回流 |

---

> 本文档随模块 29 迭代更新。测试覆盖: `decision-module-test.js` 16 用例(DTC1-DTC16),运行: `runDecisionModuleTest()`。
