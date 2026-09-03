# 44号·AI智能API管理模块 实施计划 v1.0

> 配套：[43号P6 全景收官总结报告](43号P6_全景收官总结报告.md)（安全免疫系统收官——43号管"谁在攻击"，44号管"谁在消费、消费得如何"）
> 定位：参照 Kong / Apigee / 阿里云API网关 / AWS API Gateway / Postman AI 的**API 全生命周期治理**能力，以**遵循规则**（治理有据：套餐/流控/配额/生命周期）、**方便快捷**（自助秒级：Key 申请/在线调试/文档即得）为理念，用本仓既有 AI 范式（N因子评分器 + Hedge 学习档案 + LLM 三态 + UEBA 基线）实现**智能自主管理 API**。
> 调研结论（2026-09-03）：
> 1. **API 资产无台账**：55 个路由文件 ~108 个路由注册散落在代码中，无统一注册表——"有哪些 API/归哪个模块/什么状态"要翻源码；FastAPI `app.routes` 可自发现，零侵入建台账可行；
> 2. **消费方无凭证体系**：鉴权只有 JWT 会员会话（`JWTAuthMiddleware`）——面向"程序化消费方"（开放平台/第三方对接/内部微服务）的 API Key + AppCode 双头凭证空白（现有 `X-Role` 头仅是测试捷径，非凭证）；
> 3. **流量无治理**：43号 安全网关的频次计数管"恶意防"（单 IP），无**正常消费治理**（per-Key QPS/日配额/套餐分档/超限 429+Retry-After）；
> 4. **调用观测有底座无视图**：`_http_metrics_middleware` 已埋 path+code 计数与延迟直方图（core/metrics.py 含 snapshot），monitor_routes 是系统级监控——**API 维度**（哪个接口被谁调用多少/P95/错误率）与**消费方维度**（Top Key/配额命中率）均无；
> 5. **AI 档案可复用**：ai_learning_service 已有 17+ 可学习档案（Hedge 冠军/挑战者），评分器范式成熟（第26档案 ThreatGateScorer 六因子）——新增 API 健康评分器（拟第27档案 `ApiHealthScorer`）零基建；
> 6. **LLM 三态就绪**：`llm_client.chat()`（LLM_ENABLED 默认 off）——NL API 助手/智能文档走 mock/real 三态（41/42号范式），未配置零影响。

---

## 一、设计理念与平台参照

| 平台 | 取长点 | 本模块落点 |
|------|--------|-----------|
| 阿里云 API 网关 | API 分组/插件化流控/AppCode 简单鉴权/订阅审批 | 台账模块归属字段 + AppCode 双头凭证 + Key 审批流（自动批为默认） |
| Kong | key-auth/rate-limiting 插件、analytics | Key 中间件插件式挂载（默认 off）+ 固定窗口流控 + 调用统计 |
| Apigee | 开发者门户/套餐(Product)/配额/变现雏形 | 套餐三档（free/basic/pro）+ 消费方自助门户区块 |
| AWS API Gateway | usage plan + throttling 分层 | 日配额 + QPS 双限，超限 429 标准语义 |
| Postman AI / Datadog Watchdog | NL 检索 API、智能异常说明 | NL 助手三态 + 异常事件中文归因 |
| **本仓创新** | 评分器+学习闭环+UEBA 基线 | API 健康五因子评分（第27档案）+ 异常裁决真值 Hedge 回流 + per-API 流量基线 |

**三大理念落法**：
- **遵循规则**：一切治理动作有据（套餐配额/流控阈值/生命周期状态机/审批留痕），规则可配置可审计；
- **方便快捷**：会员自助申请 Key（默认自动批，秒级发放）/ 在线调试（try-out 直接调）/ 文档即得（自发现+LLM 增强）；
- **智能自主**：异常自检（基线偏离）→ 建议自提（配额推荐）→ 学习自愈（裁决回流调档案权重）——AI 不拍板"封禁"（那是 43号的职责），只拍板"建议与展示"。

---

## 二、总体架构与分期总览

```
请求 ──▶ ApiKeyMiddleware(44号, 默认 off: 开启后仅拦截台账 status=open 的 API)
          │ X-Api-Key/X-AppCode → Key 校验 + 流控(QPS) + 配额(日) → 429/401
          ▼
       JWTAuthMiddleware(既有) → SecurityGatewayMiddleware(43号) → 业务路由
          │
          └─ 调用日志(异步采样) ──▶ Redis 计数/明细 ──▶ 观测看板 + AI 分析
                                              │
                        裁决真值 ──▶ Hedge 第27档案(ApiHealthScorer) 回流
```

| 编号 | 方向 | 核心价值 | 依赖 | 成本 | 顺序 |
|------|------|---------|------|------|------|
| P0 | API 资产中心 | 路由自发现台账（55 文件 108 路由一屏可见：归属/状态/文档标记） | 无 | 低 | ★★★ 先行 |
| P1 | 开发者凭证 | Key 自助申请/审批/吊销/续期 + AppCode 双头鉴权 | P0 | 中 | ★★★ |
| P2 | 流量治理 | 套餐三档 + per-Key QPS/日配额 + 429 语义 | P1 | 中 | ★★★ |
| P3 | 调用观测+健康评分 | 调用日志管道 + Top/P95/错误率看板 + 第27档案五因子 | P0 | 中 | ★★ |
| P4 | AI 智能自治 | per-API 流量基线异常检测 + 智能配额推荐 + NL 助手 | P3 | 中 | ★★ |
| P5 | 治理闭环 | 异常裁决回流 + 生命周期状态机 + 智能文档门户 | P1/P3/P4 | 中 | ★★ 压轴 |

**统一口径**（P0-P6 惯例）：专项测试 + security/monitor 回归零新增 + Docker 实机验收 + 提交推送；**默认零影响**（`API_MANAGER_MODE=off` 时中间件直通、看板空载）。

---

## 三、P0：API 资产中心

### 现状与差距

| 维度 | 现状 | 差距 |
|------|------|------|
| API 清单 | 散落 55 个路由文件 | 统一台账（path/method/module/status/owner） |
| 归属 | 仅 tags 部分标注 | 模块归属字段（44/43/42…号 + 业务域） |
| 状态 | 无 | 生命周期字段（P5 状态机消费）：development/published/deprecated/offline |
| 外部 API | 无概念 | 手动登记（供 P4 NL 助手检索第三方依赖） |

### 实施方案

**① 路由自发现**（零侵入——启动时扫描 `app.routes`，非逐文件解析）：

```python
# services/api_registry_service.py
async def sync_registry(app) -> dict:
    """扫描 FastAPI app.routes → 台账 upsert(幂等)

    - 只收 APIRoute(跳过 mount/静态)
    - module 归属推导: router 前缀 → 路由文件名映射表(静态字典)
      + tags 兜底; 推不出的归 "uncategorized"(台账可见可改)
    - 既有路由零改动(只读扫描)
    - /metrics、/docs 等基础设施路径跳过
    """
```

- 存储：`repositories/api_manager_repository.py`（44号独立仓储，双模式，`api44_registry` 表；Redis hash key=`method|path`）
- 变更留痕：每次 sync 输出 diff（新增/消失路由数）→ 供 P3"变更频率"因子与 P5 弃用预警

**② 端点**（3 个起步）：`GET /admin/apis`（台账列表，module/status 过滤）/ `PATCH /admin/apis/{id}`（人工修正归属/状态）/ `POST /admin/apis/sync`（手动重扫 + diff 返回）
**③ 前端**：新看板页 `ai-api-dashboard.html` ①区资产总览（按模块分组计数 + 状态色点 + 变更 diff 行）

### 测试与验收（专项预计 18 项 + 实机 6 项）

同步幂等（两次 sync 无 diff）/ 新增路由发现 / module 推导（前缀映射/tags 兜底/uncategorized）/ 人工修正持久 / HTTP 层 403/200/过滤器。实机：真实 108+ 路由全量入台账、diff 为零、业务零影响。

### 关键风险

| 风险 | 对策 |
|------|------|
| 路由前缀重叠（多 router 同前缀） | 映射表按"注册顺序首个命中"，台账显示路由函数名辅助人工判定 |
| 动态路由参数（/orders/{id}） | 保留模板路径入台账（与 metrics path 口径对齐：注册态模板 vs 运行态实际——P3 观测按模板聚合归一） |

---

## 四、P1：开发者凭证（方便快捷核心）

### 实施方案

**① Key 模型**（`api44_keys` 表）：

```python
{"keyId": int, "memberId": int, "name": "我的集成",   # 消费方命名
 "apiKey": "zk_" + secrets.token_hex(16),             # 明文仅签发时返回一次
 "appCode": "ac_" + secrets.token_hex(8),             # 阿里云式双头: Key 定身份, AppCode 定应用
 "tier": "free", "status": "active|revoked|expired",
 "createdAt": ts, "expireAt": ts + 90d,               # 默认 90 天, 续期延展
 "lastUsedAt": None, "requestCount": 0}
```

**② 自助流程**（秒级）：
- `POST /apis/keys`（会员登录态）：申请即发（**默认自动批**——审批流开关 `API_KEY_AUTO_APPROVE=on`；off 时进管理队列人工批，参考 42号申诉通道范式）→ 响应含 Key 明文（仅此一次，存储侧存 SHA-256 摘要 + 前 8 位展示位）
- `GET /apis/keys`（我的 Key 列表：前缀展示/状态/用量摘要）/ `POST /apis/keys/{id}/revoke`（自助吊销）/ `POST /apis/keys/{id}/renew`（续期 90 天）
- 管理端：`GET /admin/apis/keys`（全量+过滤）/ `POST /admin/apis/keys/{id}/revoke`（管理员吊销）

**③ 鉴权中间件**（`ApiKeyMiddleware`，挂 JWTAuth 之前）：

```python
class ApiKeyMiddleware:
    """44号消费方凭证网关(默认 off 全直通)

    开启后仅拦截: 台账 status=published 且路由声明需 Key 的 API
    双头校验: X-Api-Key(身份) + X-App-Code(应用) 均匹配才放行
    缓存: key 摘要 → Key 记录 (TTL 60s, 校验零 Redis 热路径往返的 90%)
    fail-open: 校验基础设施异常放行并留痕(治理不阻断业务, 区别于 43号 fail-open 同铁律)
    """
```

- 与 JWT 关系：Key 通过后 `inject_identity` 注入 `memberId`（复用既有身份注入，业务路由零感知）；**既有非开放 API 完全不受影响**（未标 published 直通）

### 测试与验收（专项预计 24 项 + 实机 8 项）

申请即发/明文一次性/摘要存储/双头匹配（单头 401）/过期拒绝/续期延展/自助吊销生效/缓存失效口径/审批开关两态/middleware off 直通/published 之外不拦截/fail-open。实机：真容器申请→调用开放 API→留痕→吊销→401 全链路。

### 关键风险

| 风险 | 对策 |
|------|------|
| Key 泄露 | 吊销秒级（缓存 TTL 60s 内收敛）+ 台账 lastUsedAt 异常时空告警可观测（P3） |
| 会员滥用（无限开 Key） | 每 memberId 上限 5 把（超限 409），管理端可见 |

---

## 五、P2：流量治理（遵循规则核心）

### 实施方案

**① 套餐模型**（`api44_tiers` 代码常量起步，环境变量可覆盖）：

```python
TIERS = {"free":  {"qps": 5,   "daily": 1000},
         "basic": {"qps": 20,  "daily": 10000},
         "pro":   {"qps": 100, "daily": 100000}}
# 管理端可 per-Key 覆盖(tier 字段之外 customQps/customDaily, 白名单式调参留痕)
```

**② 双限执行**（Redis INCR+EXPIRE 固定窗口——43号频次计数同范式，前缀 `api44:rl:{keyId}:{window}` / `api44:qa:{keyId}:{yyyymmdd}`）：
- QPS 超 → `429 {"detail": "QPS 超限", "retryAfter": 1}`；日配额超 → `429 {"detail": "日配额耗尽", "retryAfter": 秒至次日}` + 标准 `Retry-After` 头
- 内存模式：时间戳窗口列表（锁模式既有范式）

**③ 端点**：`GET /admin/apis/tiers`（套餐视图+各档在用 Key 数）/ `PATCH /admin/apis/keys/{id}/limits`（per-Key 覆盖留痕）——**规则变更全部留痕可审计**（遵循规则）

### 测试与验收（专项预计 22 项 + 实机 8 项）

三档阈值边界（恰 QPS/超 1）/ 429 语义与 Retry-After / 日切重置 / per-Key 覆盖优先 / 并发窗口原子性（多协程同打）/ off 全放行。实机：容器内压测 free 档（并发 10 拿到稳定 429 比例）/ 日配额耗尽恢复。

### 关键风险

| 风险 | 对策 |
|------|------|
| 治理误伤业务 | 双限仅作用于 Key 调用面（非会员 JWT 面）；`API_MANAGER_MODE=off` 一键直通 |
| 窗口临界突刺（固定窗口固有） | 接受（与 43号同口径）+ P4 异常检测兜底观测突发 |

---

## 六、P3：调用观测 + API 健康评分（第27档案）

### 实施方案

**① 调用日志管道**（仅 Key 面采样，JWT 面走既有 metrics 不重复埋点）：

```python
# ApiKeyMiddleware 通过后异步留痕(fire-and-forget, 不阻塞响应):
#   Redis: INCR api44:stat:{keyId}:{yyyymmdd}:{apiTemplate}  请求数
#          INCR api44:err:{keyId}:{yyyymmdd}:{apiTemplate}  5xx/429 数
#          HINCRBY api44:lat:{keyId}:{yyyymmdd} p50/p95 累计值(近似分位)
#   明细: ZSET 滚动窗口(最近 1000 条/key, LRANGE 看板展开)
```

**② 观测端点**：`GET /admin/apis/usage`（三视图：per-API 总量/P95/错误率、per-Key Top 消费、per-档配额命中率）+ `GET /apis/keys/{id}/usage`（消费方自查——方便快捷：自己的用量自己看）
**③ API 健康评分器**（拟**第27档案 `ApiHealthScorer`**，五因子——ThreatGateScorer 范式平移）：

```
成功率(0.30) / P95延迟达标(0.25) / 流量稳定度(0.15, 变异系数)
 / 配额命中率(0.15, 常年贴顶=该升档) / 变更频率(0.15, 近期路由 diff 频繁降分)
→ 0-100 健康分 → 四档: healthy(≥75) / watch(50-75) / strained(30-50) / critical(<30)
→ 台账/看板色点外显; 不自动处置(建议型, 区别于 43号处置型)
```

### 测试与验收（专项预计 26 项 + 实机 8 项）

三视图聚合正确性（造调用数据核对）/ P95 近似口径 / 五因子两态（healthy/critical 造数）/ 档案权重读写（ai_learning 既有基建复用）/ 采样零阻塞（延迟断言）。实机：真实调用灌入 → 看板数字对齐 → 评分落档。

### 关键风险

| 风险 | 对策 |
|------|------|
| 日志写放大 | 只记 Key 面（默认无 Key 流量=零成本）；ZSET 封顶 LTRIM |
| P95 近似偏差 | 口径明示"累计桶近似"，文档注明 |

---

## 七、P4：AI 智能自治

### 实施方案

**① 流量异常检测**（UEBA 基线范式平移——43号 `security_baselines` 思路，44号 `api44:baseline:{apiTemplate}`）：
- 日度调度器重建 per-API 基线（7 天窗口：日总量均值/标准差/分时形态）
- 三检测器：**尖峰**（当日 > μ+3σ）/ **骤降**（< μ-3σ 且绝对量 ≥ 阈值，防冷启动误报——43号"空窗评估"经验）/ **错误激增**（错误率环比 ×3 且样本 ≥ 20）
- 产出异常事件（含中文归因文案：`"订单查询 API 今日调用量 12,340 次，为基线 8 倍(μ=1,540)，主要来自 Key#7(zk_ab12…free 档)"`）——**只记录不处置**

**② 智能配额推荐**（规则+统计，非 LLM——确定性优先）：
- `GET /admin/apis/keys/{id}/recommend`：近 7 日 P95 用量 × 安全系数 1.3 → 推荐档位/自定义阈值（`"该 Key 连续 6 日贴顶 free 档(命中率 97%)，建议升 basic(预计余量 2.1×)"`）
**③ NL API 助手**（LLM 三态，41/42号范式）：
- `POST /apis/assistant`（会员可用）：`{"q": "昨天哪个接口最慢?"}` → mock 态走确定性模板（读取 usage 视图拼装事实句），real 态 `llm_client.chat()` 注入台账+usage 上下文生成；LLM_ENABLED=off 时 mock 模板仍可用（零依赖）
- 语义检索 API（"有没有查物流的接口"）→ 台账 path/模块名/tags 关键词匹配（mock 轨），real 轨 LLM 泛化

### 测试与验收（专项预计 28 项 + 实机 8 项）

尖峰/骤降/错误激增各自触发与不触发（冷启动空窗）/ 推荐计算口径（贴顶判定/系数）/ 助手 mock 确定性问答 / real 态注入上下文结构 / off 零影响。实机：灌基线→造尖峰→事件留痕→助手问答（mock 轨）。

### 关键风险

| 风险 | 对策 |
|------|------|
| LLM 幻觉（NL 助手编造数据） | 事实句由代码生成（模板+真实 usage），LLM 仅做**编排与润色**——数字永远来自查询层（42号发票摘要同铁律） |
| 异常误报噪声 | 与 43号 D5 同款硬样本阈值（样本 ≥20 才触发）+ 事件留痕不推送（P5 闭环后接既有告警通道可选） |

---

## 八、P5：治理闭环（压轴）

### 实施方案

**① 异常事件裁决回流**（43号 P2 学习闭环范式）：
- 管理端事件队列 `GET /admin/apis/anomalies` + `POST /admin/apis/anomalies/{id}/decide`（真异常/误报）
- 真值批量回流 → Hedge **第27档案 ApiHealthScorer 权重调优**（`ai_learning_service` 既有 17 档案之外的增量注册——复用 collect/run/status 三连范式）
**② 生命周期状态机**（遵循规则）：
```
development → published(开放, Key 面生效) → deprecated(弃用预警: 调用响应头 X-Api-Deprecated + 台账黄标 + 看板倒计时) → offline(410 Gone)
  转换全部人工触发(管理端), 台账留痕; 弃用期默认 30 天, 有存量调用时看板红字提示
```
- `GET /apis/catalog`（对外目录：published 接口的自助文档——path/method/描述/所需档位，方便快捷的对外门面）
**③ 智能文档门户**：published API 的"在线调试"区块（看板内 try-out——填参数直接调，会员 JWT 或 Key 二选一）+ LLM 生成接口说明（real 态增强，mock 态显示路由 docstring 摘要）

### 测试与验收（专项预计 26 项 + 实机 8 项）

状态机全转换+非法转换拒绝/弃用头注入/410/offline 隔离/裁决回流权重变化断言/目录仅 published/try-out 双鉴权轨。实机：发布→Key 调用→弃用→预警头→下线→410 全生命周期 E2E + 裁决回流学习轮。

### 关键风险

| 风险 | 对策 |
|------|------|
| 误下线在用 API | offline 前置检查：近 7 日该 API Key 面调用量 >0 则阻断并提示（软护栏，可强制 override 留痕） |
| 学习闭环污染 | 沿用 42/43号口径：裁决即真值，管理员确认为唯一真源 |

---

## 九、整体里程碑与验收口径

| 阶段 | 内容 | 验收门槛 |
|------|------|---------|
| P0 | 资产中心 | 专项 18 + 实机 6（108+ 路由全量台账） |
| P1 | 开发者凭证 | 专项 24 + 实机 8（申请→调用→吊销 E2E） |
| P2 | 流量治理 | 专项 22 + 实机 8（压测 429 稳定边界） |
| P3 | 观测+第27档案 | 专项 26 + 实机 8（数据对齐+评分落档） |
| P4 | AI 自治 | 专项 28 + 实机 8（基线→尖峰→事件→问答） |
| P5 | 治理闭环 | 专项 26 + 实机 8（生命周期 E2E+回流）+ **44号收官总结报告** |
| 收官 | 全量 | security 21 套 + monitor 回归零新增失败 + 全项目回归环境项口径 |

**依赖关系**：P0→P1→P2 主线串行（台账/凭证/治理递进）；P3 可与 P2 并行；P4 依赖 P3；P5 收口全部。

**预计端点**：新增 ~18 个（会员面 6 + 管理面 12；34 个 security 端点之外独立计数）。

---

## 十、与既有模块的一致性声明

- **零不可逆变更**：`API_MANAGER_MODE=off` 默认（中间件直通/看板空载/台账只读不碰路由）；既有 55 路由文件零改动（P0 只读扫描）
- **fail-open 铁律**：治理故障不阻断业务（校验/流控基础设施异常→放行+留痕——与 43号网关同款）
- **外部依赖永不阻塞**：LLM off 时 NL 助手走确定性模板轨；无 Key 流量时观测零成本
- **AI 不越权**：44号 AI 全部**建议型**（评分/推荐/归因/文档），处置型动作（封禁/限死）仍归 43号与人工——职责边界即安全边界
- **范式复用清单**：双模式仓储(38-43号) / INCR 固定窗口(43号) / 评分器+Hedge 档案(ai_learning) / LLM 三态(41/42号) / 事件裁决真值回流(42/43号) / 看板按需加载+链式刷新(43号面板)
- **编号衔接**：第27档案 `ApiHealthScorer`（第26档案 ThreatGateScorer 之后顺延）

---

## 十一、外部待办（非开发项）

| 项 | 动作 | 就绪方式 |
|----|------|---------|
| LLM_KEY（已有则复用） | NL 助手/文档 real 轨 | `.env` LLM_ENABLED=on（mock 轨全功能可测） |
| 真实第三方消费方 | 开放 API 试运行 | 台账标 published + 发 Key |

---

*44号计划（2026-09-03）：43号把"攻击面"管完，44号把"消费面"管好——资产有台账、接入有钥匙、用量有规则、健康有评分、异常有眼睛、演进有闭环。参照大平台的治理骨架，长在本仓的 AI 脊柱上：每一层智能都复用已验证的范式（评分器/学习档案/基线检测/LLM三态），每一个动作都默认零影响。*
