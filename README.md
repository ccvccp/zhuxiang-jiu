# 竹香酒网站架构设计

> 版本: v5.0 | 更新: 2026-08-19

## 项目概述

竹香酒官网全栈架构设计，包含 25 大业务模块、CI/CD 流水线、并发控制框架。

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

# 点击 [CI 流水线] 按钮运行 6 阶段 185 用例
```

| 阶段 | 内容 | 用例数 | 结果 |
|------|------|--------|------|
| CI-1 | 全量 25 模块 | 141 | ✅ PASS |
| CI-2 | 边界 mock | 8 | ✅ PASS |
| CI-3 | checkout 回归 | 4 | ✅ PASS |
| CI-4 | shipping 面板 | 12 | ✅ PASS |
| CI-5 | inventory 回归 | 4 | ✅ PASS |
| CI-6 | AiOps 模块 25 | 16 | ✅ PASS |
| **合计** | **6 阶段** | **185** | **100%** |

> **最近验证**: 2026-08-19 — 185/185 通过 (100.0%)，浏览器端到端确认无失败用例。

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

### 测试结果

| 指标 | 值 |
|------|-----|
| 括号匹配基础测试 | 15/15 (100%) — TC1-TC15 |
| 括号匹配边界测试 | 20/20 (100%) — BC1-BC20 (正则/模板/BOM/CRLF) |
| CI 确认流程测试 | 5/5 (100%) — Y/n/N/EOF/无JS |
| 终端测试小计 | **40/40 (100%)** |
| 浏览器 CI 流水线 | **185/185 (100%)** — 6 阶段全量回归 |
| E2E 拦截验证 | ✅ 3 类括号错误正确拦截 |
| E2E 放行验证 | ✅ 正常代码正确放行 |
| **总计** | **225/225 (100%)** |

> **最近验证**: 2026-08-19 — 终端 40/40 + 浏览器 185/185 = 225/225 全通过 (100.0%)

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
│   ├── module-test.html         # 测试面板
│   └── ...
└── README.md                    # 本文件
```
