# 竹香酒网站架构设计

> 版本: v7.0 | 更新: 2026-08-20

## 项目概述

竹香酒官网全栈架构设计，包含 27 大业务模块、CI/CD 流水线、并发控制框架、后端分层架构与 Redis 持久化迁移。

## 开发规范

### 1. Git 提交规范

提交前必须通过 pre-commit 钩子检查：

```powershell
# 一键安装钩子
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install-hooks.ps1

# 正常提交 (钩子自动触发)
git add zhuxiang-jiu/js/your-file.js
git commit -m "feat: 描述"

# 跳过检查 (紧急情况)
git commit --no-verify -m "hotfix: 描述"
```

### 2. Pre-commit 钩子

**位置**: `scripts/pre-commit` → 安装到 `.git/hooks/pre-commit`

**检查流程**:
1. 扫描暂存的 `zhuxiang-jiu/js/*.js` 文件
2. awk 状态机单次遍历，检查 3 类括号匹配 `()` `{}` `[]`
3. 跳过字符串和注释内的括号（消除假阳性）
4. CI 流水线确认（交互终端输入 Y/n）

**拦截规则**:
- 括号不匹配 → 硬拦截 (exit 1)，无需交互
- CI 未确认 → 阻止提交 (exit 1)
- 无 JS 变更 → 跳过 (exit 0)

**日志文件**: `.git/hooks/pre-commit.log`（追加模式，供审计）

### 3. CI 流水线

```bash
# 启动 CI 静态服务器
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/serve-ci.ps1

# 浏览器打开
http://localhost:8080/module-test.html

# 点击 [CI 流水线] 按钮运行 10 阶段 251 用例
```

| 阶段 | 内容 | 用例数 | 结果 |
|------|------|--------|------|
| CI-1 | 全量 27 模块 | 167 | ✅ PASS |
| CI-2 | 边界 mock | 8 | ✅ PASS |
| CI-3 | checkout 回归 | 4 | ✅ PASS |
| CI-4 | shipping 面板 | 12 | ✅ PASS |
| CI-5 | inventory 回归 | 4 | ✅ PASS |
| CI-6 | AiOps 模块 25 | 16 | ✅ PASS |
| CI-7 | 合作模块 15 | 16 | ✅ PASS |
| CI-8 | 采购模块 27 | 16 | ✅ PASS |
| CI-9 | 仓储模块 28 | 16 | ✅ PASS |
| CI-10 | 仓储服务 API | 12 | ✅ PASS |
| **合计** | **10 阶段** | **251** | **100%** |

> **最近验证**: 2026-08-19 — 251/251 通过 (100.0%)，浏览器端到端确认无失败用例。

### 4. 测试脚本

| 脚本 | 用例数 | 用途 |
|------|--------|------|
| `scripts/test-bracket-check.sh` | 15 | 括号匹配单元测试 |
| `scripts/test-pre-commit.sh` | 5 | CI 确认流程测试 |
| `scripts/bench-pre-commit.sh` | — | 性能基准对比 |

```bash
# 运行全部测试
sh scripts/test-bracket-check.sh
sh scripts/test-pre-commit.sh
sh scripts/bench-pre-commit.sh 10
```

---

## 测试报告摘要

> 完整报告: [docs/pre-commit-钩子测试报告.md](docs/pre-commit-钩子测试报告.md)
> 变更日志: [docs/CHANGELOG.md](docs/CHANGELOG.md)

### 测试结果

| 指标 | 值 |
|------|-----|
| 括号匹配基础测试 | 15/15 (100%) — TC1-TC15 |
| 括号匹配边界测试 | 20/20 (100%) — BC1-BC20 (正则/模板/BOM/CRLF) |
| CI 确认流程测试 | 5/5 (100%) — Y/n/N/EOF/无JS |
| 终端测试小计 | **40/40 (100%)** |
| 浏览器 CI 流水线 | **251/251 (100%)** — 10 阶段全量回归(27模块) |
| AiOps 模块25测试 | 16/16 (100%) — ATC1-ATC16 |
| 合作 模块15测试 | 16/16 (100%) — CTC1-CTC16 (合并原合作接口+OEM,含供应链闭环) |
| 采购 模块27测试 | 16/16 (100%) — PTC1-PTC16 (含合作闭环验证) |
| 仓储 模块28测试 | 16/16 (100%) — WTC1-WTC16 (含供应链闭环验证) |
| 仓储服务API测试 | 12/12 (100%) — WSTC1-WSTC12 (10个AI能力端点+事务+Mutex) |
| E2E 拦截验证 | ✅ 3 类括号错误正确拦截 |
| E2E 放行验证 | ✅ 正常代码正确放行 |
| 正则字面量修复验证 | ✅ BC1 `/\(/` + BC2 `/\{n\}/` 不再误计 |
| **总计** | **291/291 (100%)** |

> **最近验证**: 2026-08-19 — 终端 40/40 + 浏览器 251/251 = 291/291 全通过 (100.0%)
>
> **合作模块合并**: 2026-08-19 — 将原合作接口管理模块(15)与OEM代工定制模块(26)合并为AI智能合作定制模块(模块15)。合并后10个AI能力(资质审核→需求匹配→定制设计→配方勾调→瓶型包装→定价报价→保证金→生产品控→交付售后→客户风控)·aiRate 93%·16个单元测试(CTC1-CTC16)·含供应链闭环验证(合作15→采购27→仓储28)·总模块数28→27·CI流水线10阶段251用例。
> **模块28服务API新增**: 2026-08-19 — AI智能仓储服务API(warehouse-service.js)封装完成。10个AI能力端点·遵循inventory-service.js服务规范(FIFO Mutex+TransactionTemplate+Mock/Live双模式)·12个回归测试用例(WSTC1-WSTC12)。
> **模块28新增**: 2026-08-19 — AI智能仓储与库存优化模块(模块28)开发完成。12张数据库表(42索引)·10个AI能力·16个单元测试·与模块27(采购)和模块06(物流)形成采购→仓储→物流供应链闭环。
> **模块27新增**: 2026-08-19 — AI智能原料采购与供应商管理模块(模块27)开发完成。12张数据库表(38索引)·10个AI能力·16个单元测试·与合作模块15形成合作→采购闭环。
>
> **修复记录**: 2026-08-19 — pre-commit 钩子和基准脚本的 awk 逻辑同步增强版（含正则字面量识别），修复 `var re = /\(/g;` 假阳性拦截 Bug。4 处实现统一：test-lib.sh / pre-commit / bench-pre-commit.sh / .git/hooks/pre-commit。

### 性能基准

| 指标 | OLD (grep) | NEW (awk) | 提升 |
|------|-----------|-----------|------|
| 平均/文件 | 535 ms | 97 ms | **-81%** |
| 子进程/文件 | 4 | 1 | -75% |
| 括号类型 | 2 类 | 3 类 | +[] |
| 字符串/注释感知 | 无 | 有 | 消除假阳性 |

### 关键发现

`inventory-service.js` 在旧 grep 方案下 `()860/862` 误报不匹配（假阳性），新 awk 方案正确识别为 `()738/738` 匹配。优化同时提升了性能和准确性。

---

## 合作模块合并报告

> 完整文档: [合作接口管理模块设计文档.md](合作接口管理模块设计文档.md) · 2026-08-19 验证通过

### 合并概述

将原**合作接口管理模块(15)** 与 **OEM代工定制模块(26)** 合并为 **AI智能合作定制模块(模块15)**，消除两个模块在定制设计、包装设计、生产品控、定价报价、交付售后等 AI 能力上的功能重叠，形成统一的合作定制全链路。

| 维度 | 合并前(模块15) | 合并前(模块26) | 合并后(模块15) |
|------|----------------|----------------|----------------|
| 模块名 | 合作接口管理模块 | AI智能OEM代工定制模块 | **AI智能合作定制模块** |
| AI能力数 | 9 | 10 | **10**(去重整合) |
| aiRate | 85% | 93% | **93%** |
| 测试用例 | 4 | 10 | **10** |
| 单元测试 | 无 | 16(OTC1-OTC16) | **16**(CTC1-CTC16) |
| 合作模式 | 仅定制 | ODM+OEM | **ODM+OEM+定制** |

### 文件变更清单

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| [modules.js](zhuxiang-jiu/js/modules.js) | 修改 | 模块15重写(10个AI能力)·删除模块26·模块27引用更新 oemModule→cooperationModule |
| [cooperation-module-test.js](zhuxiang-jiu/js/cooperation-module-test.js) | 新增 | 16用例(CTC1-CTC16)·含供应链闭环验证 |
| [oem-module-test.js](zhuxiang-jiu/js/oem-module-test.js) | 删除 | 原16用例(OTC1-OTC16)已由 cooperation-module-test.js 替代 |
| [procurement-module-test.js](zhuxiang-jiu/js/procurement-module-test.js) | 修改 | PTC16 OEM闭环→合作闭环·oemModule→cooperationModule |
| [module-test.html](zhuxiang-jiu/module-test.html) | 修改 | 标题28→27·OEM面板→合作面板·CI按钮251用例·模块数27·版本v6.0 |
| [ai-ops-module-test.js](zhuxiang-jiu/js/ai-ops-module-test.js) | 修改 | CI-7 OEM→合作·CI-1 171→167·总计255→251 |
| [README.md](README.md) | 修改 | v6.0·27模块·CI 10阶段251用例·总计291/291 |

### 合并后10个AI能力(全链路)

```
资质审核 → 需求匹配 → 定制设计 → 配方勾调 → 瓶型包装
    → 定价报价 → 保证金 → 生产品控 → 交付售后 → 客户风控
```

### AI能力接口响应数据(Mock 实测)

> 测试方式: 本地静态服务器 + 浏览器端 `MODULES.find(m=>m.id==="15").mock()` 实际响应

| # | AI能力 | 响应字段 | 实测值 | 对标行业 |
|---|--------|---------|--------|----------|
| 1 | AI智能资质审核 | qualificationAccuracy | `96%` | 川酒智镜+工商/食药监交叉验证 |
| 2 | AI智能需求匹配 | demandMatch | `95%` | 1瓶起定·3天发货 |
| 3 | AI智能定制设计 | designSatisfaction | `90%` | AI文生图(瓶型/包装/酒标) |
| 4 | AI智能配方勾调 | recipeSatisfaction / blendingError | `90%` / `0.5%` | GC-MS风味图谱+ML优化 |
| 5 | AI智能瓶型包装 | designCycleReduction | `60%` | AI 3D建模+开模优化 |
| 6 | AI智能定价报价 | pricingAccuracy | `92%` | 动态定价(原料+复杂度+市场) |
| 7 | AI智能保证金管理 | depositCoverage | `100%` | 违约风险评估+阶梯比例 |
| 8 | AI智能生产品控 | schedulingBoost / qcCoverage / traceability | `40%` / `100%` / `100%` | 数字孪生排程+云边端视觉AI+区块链溯源 |
| 9 | AI智能交付售后 | deliveryAccuracy / deliveryDelay | `90%` / `5%` | 交付周期预测+智能退还 |
| 10 | AI智能客户风控 | repurchasePrediction / clientTags | `85%` / `200` | 200+标签画像+违约识别 |

### 供应链闭环验证

| 闭环路径 | 验证点 | 结果 |
|----------|--------|------|
| 合作15 → 采购27 | `procurementModule.mock().cooperationModule === 15` | ✅ |
| 采购27 → 仓储28 | `warehouseModule.mock().procurementModule === 27` | ✅ |
| OEM26 已合并 | `!MODULES.find(m => m.id === '26')` | ✅ |
| CTC16 闭环测试 | 合作15(✓)→采购27(✓)→仓储28(✓) OEM26已合并(✓) | ✅ PASS |
| PTC16 合作闭环 | cooperationModule=15 + 寻源+预测+补货 数据一致 | ✅ PASS |

### 单元测试结果(CTC1-CTC16)

> 测试入口: `module-test.html` → 🤝 合作模块15单元测试 · 16用例
> 运行方式: `window.runCooperationModuleTest()`

| 用例 | 名称 | 结果 |
|------|------|------|
| CTC1 | 模块15存在且字段完整 | ✅ PASS |
| CTC2 | 10个AI能力全部定义 | ✅ PASS |
| CTC3 | 10个测试用例全部定义 | ✅ PASS |
| CTC4 | AI智能资质审核: 审核准确率96% | ✅ PASS |
| CTC5 | AI智能需求匹配: 匹配精度95% | ✅ PASS |
| CTC6 | AI智能定制设计: 设计满意度90% | ✅ PASS |
| CTC7 | AI智能配方勾调: 配方满意度90%+勾调误差<0.5% | ✅ PASS |
| CTC8 | AI智能瓶型包装: 设计周期-60% | ✅ PASS |
| CTC9 | AI智能定价报价: 定价准确率92% | ✅ PASS |
| CTC10 | AI智能保证金管理: 预警覆盖率100% | ✅ PASS |
| CTC11 | AI智能生产品控: 排程+40%/质控100%/溯源100% | ✅ PASS |
| CTC12 | AI智能交付售后: 预测准确率90%/延期率<5% | ✅ PASS |
| CTC13 | AI智能客户风控: 复购预测85%+200标签 | ✅ PASS |
| CTC14 | mock数据核心字段完整 | ✅ PASS |
| CTC15 | mock数据扩展字段(技术栈/行业参考/定制项) | ✅ PASS |
| CTC16 | 供应链闭环: 合作15→采购27→仓储28 | ✅ PASS |

**通过: 16/16 (100.0%)**

### CI流水线验证(10阶段251用例)

| 阶段 | 内容 | 用例数 | 结果 |
|------|------|--------|------|
| CI-1 | 全量27模块 | 167 | ✅ PASS |
| CI-2 | 边界mock | 8 | ✅ PASS |
| CI-3 | checkout回归 | 4 | ✅ PASS |
| CI-4 | shipping面板 | 12 | ✅ PASS |
| CI-5 | inventory回归 | 4 | ✅ PASS |
| CI-6 | AiOps模块25 | 16 | ✅ PASS |
| **CI-7** | **合作模块15(合并后)** | **16** | **✅ PASS** |
| CI-8 | 采购模块27 | 16 | ✅ PASS |
| CI-9 | 仓储模块28 | 16 | ✅ PASS |
| CI-10 | 仓储服务API | 12 | ✅ PASS |
| **合计** | **10阶段** | **251** | **100%** |

> **验证时间**: 2026-08-19 · 浏览器端到端 · 251/251 全通过(100.0%)

### 合并前后对比

| 指标 | 合并前 | 合并后 | 变化 |
|------|--------|--------|------|
| 总模块数 | 28 | 27 | -1(消除重叠) |
| CI流水线 | 10阶段255用例 | 10阶段251用例 | -4(去重) |
| 合作域AI能力 | 9+10=19(重叠) | 10(去重整合) | 覆盖全链路 |
| aiRate | 85%(模块15) | 93% | +8% |
| 供应链闭环 | 合作→OEM→采购 | 合作15→采购27→仓储28 | 闭环更清晰 |
| 总测试数 | 295 | 291 | -4(去重) |

---

## 并发控制规范

> 完整文档: [docs/并发锁实施规范.md](docs/并发锁实施规范.md)

### Mutex 锁 key 命名

| 操作 | 锁 key | 说明 |
|------|--------|------|
| 库存扣减/回补 | `stock:{productId}` | 防止超卖 |
| 订单号生成 | `order:next` | 防止并发丢失 |
| 优惠券使用 | `coupon:{code}` | 防止重复使用 |
| 积分扣减 | `points:{memberLevel}` | 防止并发丢失 |
| 钱包操作 | `wallet:{agentId}` | 防止数据不一致 |
| 代理升降级 | `agent:{agentId}` | 串行化 UPDATE |

### 事务一致性

- 所有 rollback 遵循统一顺序: 记录 ROLLBACK → 恢复内存快照 → 持久化 → 日志
- 事务原子性校验: `BEGIN === COMMIT + ROLLBACK`
- `dbRef` 必须在 Mutex 锁回调内创建（防止 lost update）

---

## 技术文档索引

| 文档 | 路径 |
|------|------|
| 并发控制与事务一致性 | `docs/并发控制与事务一致性技术文档.md` |
| 超时竞争与防重入 | `docs/超时竞争与防重入机制技术设计文档.md` |
| 并发锁实施规范 | `docs/并发锁实施规范.md` |
| AI 智能性能测试报告 | `docs/AI智能性能测试报告.md` |
| 代码重构任务清单 | `docs/代码重构任务清单.md` |
| Pre-commit 钩子测试报告 | `docs/pre-commit-钩子测试报告.md` |

---

## 目录结构

```
网站架构设计/
├── .git/hooks/pre-commit        # 已安装的钩子
├── scripts/                     # 工具脚本
│   ├── pre-commit               # 钩子源文件 (awk 版)
│   ├── install-hooks.ps1        # 一键安装
│   ├── serve-ci.ps1             # CI 静态服务器
│   ├── test-bracket-check.sh    # 括号匹配测试 (15 用例)
│   ├── test-pre-commit.sh       # CI 流程测试 (5 用例)
│   └── bench-pre-commit.sh      # 性能基准对比
├── docs/                        # 技术文档
├── zhuxiang-jiu/                # 主项目
│   ├── js/                      # 业务逻辑
│   ├── backend/                 # FastAPI 后端(分层架构)
│   │   ├── main.py              # 入口(94 行, 仅 app 初始化 + 路由注册)
│   │   ├── core/                # 横切关注点(config/auth/errors/helpers/locks)
│   │   ├── repositories/        # 数据访问层(双模式: 内存/Redis)
│   │   ├── services/            # 业务逻辑层
│   │   ├── routes/              # HTTP 路由层
│   │   ├── scripts/             # 运维脚本
│   │   │   └── seed_redis.py    # Redis 数据初始化(幂等)
│   │   ├── conftest.py          # pytest 配置(强制内存模式)
│   │   ├── test_*.py            # 测试套件(190 主测试 + 36 Redis 集成测试)
│   │   └── probe_concurrency*.py # 并发探针(单进程/多进程)
│   ├── module-test.html         # 测试面板
│   └── ...
├── docker-compose.yml           # Docker 编排(Redis + 后端)
└── README.md                    # 本文件
```

---

## 后端架构分层(Phase 1)

> 完整方案: [docs/redis-persistence-migration-plan.md](docs/redis-persistence-migration-plan.md)

### 分层架构

竹香酒 FastAPI 后端采用 DDD 风格分层,将原 922 行单文件 `main.py` 拆解为 4 层:

```
routes/      → 参数校验 + 调 service + 格式化响应(HTTP 层)
services/    → 业务逻辑 + 事务边界 + 锁管理(业务层)
repositories/→ 数据访问 + 双模式切换(数据层)
core/        → 横切关注点(config/auth/errors/helpers/locks)
```

### 各层职责

| 层 | 职责 | 不允许 |
|----|------|-------|
| `routes/` | 参数解析、调用 service、HTTP 响应格式化 | 业务逻辑、数据访问 |
| `services/` | 业务规则、RMW 操作加锁、抛领域异常 | HTTP 概念、直接操作数据源 |
| `repositories/` | CRUD、双模式切换(内存/Redis) | 业务规则 |
| `core/` | 配置、认证、错误处理、锁工厂 | 业务逻辑 |

### 关键设计

- **`_mock_store` 单例**:`repositories/store.py` 持有,所有 Repository 共享;`main.py` 重新导出保持 `from main import _mock_store` 测试契约
- **异常约定**:Service 抛 `KeyError` → Route 转 404;抛 `ValueError` → Route 转 409(冲突)
- **锁的位置**:涉及 RMW 的 `agent_upgrade`、`inventory_deduct/restock` 在 service 层调用 `core.locks.get_lock`,锁键不变(`agent:{id}` / `stock:{productId}`)

---

## Scheme B+ Redis 持久化迁移

### 背景

原后端使用进程内字典 `_mock_store` + `asyncio.Lock`,在多 worker 部署下存在两个问题:
1. **状态分裂**:每个 worker 独立持有 `_mock_store`,数据不一致
2. **锁失效**:`asyncio.Lock` 仅进程内互斥,跨进程并发下超卖/钱包丢失

### 迁移目标

- **锁层**:`asyncio.Lock` → `redis.asyncio.Lock`(跨进程互斥)
- **存储层**:`_mock_store` 字典 → Redis Hash/List/String(跨进程共享)
- **兼容性**:测试零改动(`conftest.py` 强制内存模式)

### 5 阶段实施

| 阶段 | 内容 | 状态 |
|------|------|------|
| Phase 1 | Repository 双模式(内存/Redis)+ Service async 改造 | ✅ 完成 |
| Phase 2 | `LOCK_MODE` 默认值 `asyncio` → `redis` | ✅ 完成 |
| Phase 3 | `scripts/seed_redis.py` 初始化脚本 + docker-compose 集成 | ✅ 完成 |
| Phase 4 | Redis 集成测试(36 用例)+ CI 集成 | ✅ 完成 |
| Phase 5 | 文档与发布(本 README) | ✅ 完成 |

### 单一开关设计

环境变量 `LOCK_MODE` 同时控制锁和存储(通过 `STORE_MODE` 默认跟随 `LOCK_MODE`):

| 环境变量 | 取值 | 行为 |
|---------|------|------|
| `LOCK_MODE=asyncio` | 默认(开发/测试) | `asyncio.Lock` + 内存字典 |
| `LOCK_MODE=redis` | 默认(生产) | `redis.asyncio.Lock` + Redis 持久化 |
| `STORE_MODE=asyncio` | 覆盖存储模式 | 仅影响存储,不影响锁 |
| `STORE_MODE=redis` | 覆盖存储模式 | 仅影响存储,不影响锁 |

### Redis 数据结构

| 实体 | Redis 类型 | Key 格式 | 说明 |
|------|-----------|---------|------|
| 代理商 | Hash | `zhuxiang:agent:{id}` | id/name/level/wallet |
| 库存 | Hash | `zhuxiang:inventory:{productId}` | stock/reserved |
| 仓储库位 | Hash | `zhuxiang:warehouse:slots` | field=slot, value=productId |
| 入库日志 | List | `zhuxiang:warehouse:inbound_log` | RPUSH JSON |
| 出库日志 | List | `zhuxiang:warehouse:outbound_log` | RPUSH JSON |
| 订单 | List | `zhuxiang:orders` | RPUSH JSON |
| 区域认领 | Hash | `zhuxiang:shipping_claims` | field=region, value=agent_id |

### 原子性保证

| 操作 | Redis 命令 | 说明 |
|------|----------|------|
| 库存扣减 | `HINCRBY key stock -qty` | 原子减法,负数表示超卖(锁保护下不会发生) |
| 钱包累加 | `HINCRBYFLOAT key wallet amount` | 原子浮点加法 |
| 区域认领 | `HSET shipping_claims region agent_id` | 原子写入 |
| 分布式锁 | `SET lock_xxx token NX PX 10000` | TTL 10s 防死锁 |

### 并发安全验证

通过 3 个并发探针场景验证 Redis 分布式锁:

| 场景 | 并发数 | 验证点 | 结果 |
|------|-------|-------|------|
| 超卖防护 | 100 并发扣减 1 件 | 最终库存 = 500 - 100 = 400 | ✅ 无超卖 |
| 钱包精度 | 50 并发充值 1000 | 最终余额 = 50000 + 50000 = 100000 | ✅ 精确累加 |
| 混合操作 | 50 扣减 + 50 回补 | 最终库存 = 500(不变) | ✅ 无丢失更新 |

### 测试覆盖

| 测试集 | 用例数 | 说明 |
|--------|-------|------|
| 主测试套件(内存模式) | 190 | 通过 `conftest.py` 强制内存模式,零改动 |
| Redis 集成测试 | 36 | 5 类: seed/CRUD/Service/并发/持久化 |
| 并发探针(单进程) | 4 | asyncio.Lock 验证(单 worker) |
| 并发探针(多进程) | 4 | Redis 分布式锁验证(多 worker) |
| **合计** | **234** | **100% 通过** |

### CI 流水线(10 阶段)

| Job | 名称 | 运行环境 | 说明 |
|-----|------|---------|------|
| 1 | ps-version-check | Windows | PowerShell 版本兼容性 |
| 2 | python-tests | Ubuntu | 主测试套件(190 用例) |
| 3 | python-tests-windows | Windows | 跨平台验证 |
| 4 | docker-build-test | Linux | Docker 镜像构建 |
| 5 | force-param-test-windows | Windows | Git -Force 参数测试 |
| 6 | force-param-test-linux | Linux/pwsh | 同上 |
| 7 | concurrency-probe | Ubuntu | asyncio 单进程锁探针 |
| 8a | concurrency-probe-redis | Ubuntu | Redis 多进程锁探针 |
| 8c | redis-integration-tests | Ubuntu | Redis 集成测试(36 用例) |
| 9 | test-summary | Ubuntu | 跨平台一致性汇总 |

### 本地运行

```powershell
# 方式 1: Docker 一键启动(推荐, 自动 seed)
cd "d:\网站架构设计"
docker compose up

# 方式 2: 本地 Redis + 手动启动
# 先启动 Redis(Windows 版或 WSL)
py zhuxiang-jiu\backend\scripts\seed_redis.py
cd zhuxiang-jiu\backend
uvicorn main:app --reload --port 8000

# 方式 3: 纯内存模式(无需 Redis, 仅开发用)
$env:LOCK_MODE = "asyncio"
$env:STORE_MODE = "asyncio"
cd zhuxiang-jiu\backend
uvicorn main:app --reload --port 8000
```

### 测试运行

```powershell
cd zhuxiang-jiu\backend

# 主测试套件(190 用例, 内存模式)
py -m pytest --ignore=test_redis_integration.py -v

# Redis 集成测试(36 用例, 需 Redis)
$env:LOCK_MODE = "redis"
$env:STORE_MODE = "redis"
py -m pytest test_redis_integration.py -m redis -v

# 并发探针(单进程)
py probe_concurrency.py

# 并发探针(多进程, 需 Redis)
$env:LOCK_MODE = "redis"
py probe_concurrency_multiworker.py
```

---

## 技术文档索引

| 文档 | 路径 |
|------|------|
| Redis 持久化迁移方案 | `docs/redis-persistence-migration-plan.md` |
| 并发控制与事务一致性 | `docs/并发控制与事务一致性技术文档.md` |
| 超时竞争与防重入 | `docs/超时竞争与防重入机制技术设计文档.md` |
| 并发锁实施规范 | `docs/并发锁实施规范.md` |
| AI 智能性能测试报告 | `docs/AI智能性能测试报告.md` |
| 代码重构任务清单 | `docs/代码重构任务清单.md` |
| Pre-commit 钩子测试报告 | `docs/pre-commit-钩子测试报告.md` |
