# 43号 P2·UEBA 行为基线实现方案 v1.0

> 配套：[43号设计文档](AI智能安全管理模块43_设计文档.md) §2.4
> 定位：**每个角色的每个动作都建行为基线，偏离即预警**——管理端凌晨批量导出、会员高频秒杀击穿 P95、未授权功能试探，在发生当下就被识别并联动威胁评分。
> 数据源现状（已调研）：管理端 `admin_operation_logs` 完整（action/ip/device/createdAt，哈希链防篡改）；会员端无登录历史，但网关为全量请求必经点（可零侵入采集）+ `points_logs(source=login)`/`orders` 可历史回填。

---

## 1. 总体架构：四层流水线

```
┌─ 采集层 BehaviorCollector ─────────────────────────┐
│ ① 网关顺带采集(会员端, 零侵入): 网关已在解析       │
│    member_id/path/method → 顺带记三维计数          │
│    (memberId × hour × module), 不记全量流水        │
│ ② 审计日志读取(管理端, 只读): admin_operation_logs │
│ ③ 历史回填(基线冷启动): points_logs(登录) +        │
│    orders(下单) + payments(支付) + reviews(评价)    │
└──────────────────┬────────────────────────────────┘
                   ▼
┌─ 基线层 BaselineBuilder(每日定时/手动重建) ───────┐
│ 按 角色维度 聚合近30天:                           │
│   hours[24] 时段直方图 / avgOpsPerHour 均值        │
│   p95OpsPerHour 频率P95 / moduleDist 功能分布      │
│ 双层基线: 个人基线 + 同角色全局基线(冷启动兜底)    │
└──────────────────┬────────────────────────────────┘
                   ▼
┌─ 检测层 DeviationDetector(实时+离线) ─────────────┐
│ D1 时段偏离: 非常规时段操作(基线权重<5%)           │
│ D2 频率偏离: 小时操作数 > P95×突变系数             │
│ D3 功能偏离: 首次触碰敏感功能(导出/批量/权限)      │
│ D4 试探偏离: 403堆积/越权路径模式(联动P1事件)      │
└──────────────────┬────────────────────────────────┘
                   ▼
┌─ 联动层 ────────────────────────────────────────────┐
│ ① 行为预警入 security_events(severity=behavior)     │
│    → 复用P1裁决通道(管理端四步法)                  │
│ ② 高危偏离注入 threat_gate 六因子中                │
│    identity_risk 因子(网关评分时实时查基线)        │
└─────────────────────────────────────────────────────┘
```

## 2. 采集层设计

### 2.1 网关顺带采集（会员端主数据源，零侵入）

网关中间件已在提取 `member_id/path/method`（43号 P0 实现），在 `process_request` 的正常请求分支顺带记数：

```python
# services/security_service.py process_request() else 分支追加
if member_id:
    await self.repo.count_behavior(member_id, path, hour)
```

存储为**三维计数直方图**（非全量流水，防爆炸）：
- Redis：`zhuxiang:security43:behavior:{memberId}` Hash，field `{hour}|{module}`，INCR
- 内存：`_security43_behavior` 嵌套 dict 计数

**path → module 映射**（静态表，复用 P0 的敏感端点前缀口径）：

| 前缀 | module |
|---|---|
| `/api/order` | order |
| `/api/payment` / `/api/wallet` | payment |
| `/api/product/*/reviews` | review |
| `/api/points` | points |
| `/api/security` | security |
| `/api/admin` | admin |
| 其他 | other |

### 2.2 审计日志读取（管理端主数据源，只读）

`admin_operation_logs` 已含 `action/ip/createdAt`，直接复用：
- action 语义映射（对齐 `AdminOperationScorer.SENSITIVITY` 既有口径）
- 时段/频率从 `createdAt` 聚合，功能分布从 `module/resourceType` 聚合

### 2.3 历史回填（基线冷启动种子）

重建基线时批量拉取：
- `points_logs WHERE source=login` → 会员登录时段分布
- `orders` → 下单频率/时段
- 仅用于初始基线构建（30 天窗口），不持续读取

## 3. 基线层设计

### 3.1 数据模型（前缀 security43）

```
security_behavior_baselines   行为基线(双层: 个人+角色全局)
  key: actorKey(member:1 / admin:2 / role:admin_global)
  { role, hours[24](归一化直方图), avgOpsPerHour,
    p95OpsPerHour, moduleDist{module:count},
    sensitiveTouches{module:count}, sampleDays, updatedAt }
```

### 3.2 构建算法（窗口重算，非 EWMA）

**选择理由**：窗口重算可解释、可重建、无累积漂移；EWMA 需调参且历史不可溯。30 天窗口每日全量重算，数据量（个人×24小时×~10模块）单条 <1KB，可承受。

```
每日定时(AI_LEARNING_AUTO 调度器同款机制, 默认off):
  for actor in 活跃会员(近30天有行为计数) ∪ 管理员(有审计日志):
    hourly_ops = 从三维计数/审计日志聚合(actor, 近30天)
    baseline.hours[h] = hourly_ops[h] / total          # 归一化直方图
    baseline.avgOpsPerHour = mean(hourly_ops)
    baseline.p95OpsPerHour = percentile95(逐日逐小时样本)
    baseline.moduleDist = module 计数直方图
  for role in roles:
    role_global = 同角色全部 actor 的基线合并(加权平均)
```

### 3.3 冷启动兜底（防新用户全报警）

```
取基线顺序:
  个人基线(sampleDays ≥ 7) → 用个人基线
  否则 → 同角色全局基线(role_global 存在) → 用全局
  否则 → 中性(不检测, 返回 None)
```

## 4. 检测层设计（四检测器）

### 4.1 D1 时段偏离（网关实时）

```
当前请求 hour=h, 该 actor 基线 hours[h] < 0.05 且 0≤h≤23
  → 偏离记 1 点; 若叠加敏感功能 → 记 2 点
评分口径: behavior_score = 100 - 偏离点数 × 40(clamp 0-100)
```

### 4.2 D2 频率偏离（网关实时）

```
当前小时该 actor 操作数(三维计数实时可查) > baseline.p95OpsPerHour × 突变系数(默认3)
  → 偏离记 2 点(击穿 P95 是强信号)
```

注：与 P0 的 `request_rate` 因子（60s 窗口防 CC）互补不重复——request_rate 拦瞬时洪峰，D2 识别"缓慢但异常"的行为漂移。

### 4.3 D3 功能偏离（每日离线 + 网关实时）

```
实时: 当前请求 module ∈ 敏感功能表(export/batch/grant/withdraw)
      且该 actor 的 sensitiveTouches[module] == 0(首次触碰)
  → 偏离记 2 点
离线: 每日对比 moduleDist 新增模块(30天从未用过今日首次)
  → 行为预警事件(severity=behavior)
```

### 4.4 D4 试探偏离（复用 P1 事件流水）

P0 的 `identity_risk` 因子已含"未认证打敏感端点"。D4 扩展为：
- 同 actor 24h 内 403 响应堆积 ≥3 次（越权试探模式）
- 数据源：路由层 403 响应（网关响应侧统计，或从 security_events 已留痕的 challenge/block 事件反推）

P2 范围做 403 堆积（网关响应钩子）；完整序列建模（登录→直奔敏感端点的跳步检测）留 P3。

## 5. 联动层设计

### 5.1 行为预警入事件流水

偏离总分 <60 时生成 `security_events` 记录：
- `action: "behavior_alert"`（新档位，可申诉可裁决，复用 P1 全链路）
- `factors` 携带四检测器明细
- 管理端在既有"事件流水/裁决"端点直接可见——**零新增管理界面**

### 5.2 identity_risk 因子注入（threat_gate 六因子增强）

网关管线 `process_request` 第 ⑤ 步评分前查询：

```python
# 现有: identity_score = scan_identity(path, member_id)
behavior = await self._behavior_deviation(member_id, path, hour)  # TTL缓存60s
if behavior is not None:
    identity_score = min(identity_score, behavior)   # 取较差值
```

- 基线查询走 Redis Hash 单键读取（微秒级）+ 60s 进程内缓存，满足网关低延迟
- **只降不升**：行为偏离只作为减分项，不做加分（防基线污染反向利用）

### 5.3 防滥用与防误报

- 基线污染防护：攻击者前 30 天"养号"制造宽松基线 → D2 突变系数 ≥3 保证养号后突变仍检出；D3 敏感功能首次触碰与历史无关，不可被养号稀释
- 误报防护：behavior_alert 默认 `verdict=pending` 走人工裁决（P1 通道），不自动处置；enforce 态下也仅降分不直接 block
- 冷启动用户（全局基线也缺失）完全豁免 → 新会员零误报

## 6. API 设计（3 端点，挂 /api/security/admin/behavior）

| 端点 | 方法 | 用途 |
|------|------|------|
| `POST /behavior/rebuild` | POST | 手动重建基线（30 天窗口，幂等） |
| `GET /behavior/baselines?role=&actor=` | GET | 基线查询（时段直方图/P95/功能分布） |
| `GET /behavior/deviations` | GET | 偏离记录（近 24h 偏离明细，含四检测器命中） |

## 7. 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `SECURITY_UEBA_MODE` | on | UEBA 总开关（off 则跳过采集与检测） |
| `SECURITY_UEBA_WINDOW_DAYS` | 30 | 基线窗口 |
| `SECURITY_UEBA_BURST_FACTOR` | 3 | D2 频率突变系数（击穿 P95×N） |
| `SECURITY_UEBA_HOUR_WEIGHT` | 0.05 | D1 时段冷门阈值（基线权重低于此为非常规时段） |

## 8. 测试策略

1. **专项**：三维计数采集（网关路径）/ 基线构建（直方图归一化/P95/冷启动兜底）/ D1-D4 四检测器直测 / identity_risk 联动（基线偏离时网关评分下降）/ behavior_alert 事件入流水可裁决 / UEBA off 零影响 / TTL 缓存命中
2. **全系回归**：`pytest -m "not redis"` 1189 基线零新增
3. **实机验收**：Docker 全链路（正常流量→建基线→模拟凌晨批量导出→behavior_alert 留痕→裁决→误报恢复）

## 9. 分步实施顺序

| 步 | 内容 | 依赖 |
|----|------|------|
| 1 | 仓储扩展：behavior 三维计数 + baselines 表 | 无 |
| 2 | 采集层：网关顺带计数 + 审计日志读取器 | 步1 |
| 3 | 基线层：BaselineBuilder + 冷启动兜底 + rebuild 端点 | 步2 |
| 4 | 检测层：D1-D4 + behavior_score 合议 | 步3 |
| 5 | 联动层：identity_risk 注入 + behavior_alert 入事件流水 | 步4 |
| 6 | 专项测试 + 全系回归 + Docker 实机验收 + 提交 | 步5 |

---

*方案基于 2026-09-03 数据源调研（admin_operation_logs 审计链完整/网关必经点/points_logs 可回填）；实施按 §9 六步推进，每步可独立验证。*
