# auth 中间件 X-Role 直连 403 问题——独立立项方案

> 立项编号建议：AUTH-TEST-01
> 立项日期：2026-10-03
> 实施进度：**P0 已完成**（commit 53349e7，189 断言复活）；
> **P1 已完成**（test_support.mint_token + test_auth_support.py
> 12/12——Bearer 真链/剥离/回滚开关/strict 四组语义常驻回归）；
> P2 待启动
> 来源：当日三连升级（智单/智客）交付中暴露的全站既有问题（非本次引入）
> 优先级：P1（测试基建债，持续侵蚀 HTTP 型回归能力）
> 性质：**纯测试侧工程，零生产改动、零部署风险**

---

## 一、问题定义

### 1.1 现象

直跑式 HTTP 测试（`python test_xxx.py`）携带 `X-Role: admin`
请求管理端点，统一被拒 403：

| 受影响测试 | 断言规模 | 当日处置 |
|---|---|---|
| test_zhidan.py | 65 断言 | 被迫新写服务层直调版（test_zhidan_upgrade 10/10）替代 |
| test_zhike.py | 72 断言 | 同上（test_zhike_upgrade 10/10） |
| test_zw_mode.py | 智运灰度 HTTP 面 | 同类失败（更早暴露） |
| 其余直跑型 TestClient 测试 | 约 100+ 文件存量 | 潜在同类（凡走管理端点者） |

### 1.2 与生产的边界（重要）

- **生产无此问题**：生产 verify 脚本（如 verify_sv73_prod.py）走
  `docker exec` 容器内 AuthService 登录 → 真 Bearer → 全链 200，
  并反向验证"无 Bearer 裸头 401"语义——**正确范式已存在**。
- 本问题只影响**本地/CI 测试面**，但代价是：HTTP 型回归
  （中间件 → 路由层 `_require_admin` → 服务层全链）长期测不到，
  服务层直调版只覆盖了后两环。

---

## 二、根因分析（链路）

```
安全加固(历史, 正确且必要):
  compat 模式默认剥离客户端伪造身份头
  (_strip_identity_headers: X-Member-Id / X-Role)
        ↓
conftest.py 为 pytest 设三环境变量:
  LOCK_MODE=asyncio / STORE_MODE=asyncio(内存模式)
  AUTH_COMPAT_TRUST_HEADERS=1(测试信任旧头)
        ↓
但项目测试惯例是直跑脚本(python test_xxx.py, __main__ 模式)
        ↓
conftest.py 只在 pytest 启动时加载 —— 直跑不经过
        ↓
三环境变量全部未设:
  ① X-Role 被中间件剥离 → 路由层 _require_admin 403   ← 当日暴露
  ② STORE_MODE 落回 redis 默认 → 测试直连开发 Redis    ← 潜在更严重
  ③ LOCK_MODE 落回 redis 默认 → 并发锁走 Redis         ← 同上
```

结论：**不是中间件的 bug，是"pytest 配置"与"直跑脚本"两套运行
惯例的断层**。安全语义（剥离伪造头）应保留；测试面需要自己的
自举机制。

---

## 三、方案选型

| 方案 | 思路 | 评估 |
|---|---|---|
| **A. 测试自举（止血）** | 公共 `test_support.py` 模块加载即设三环境变量，直跑测试 import 之 | 最小改动、立即恢复全部存量直跑测试；仍是"信任旧头"，但仅测试进程内 |
| **B. 真 token 测试轨（治本）** | 测试内调 AuthService 铸 Bearer（admin/member 双角色），HTTP 测试走真鉴权链 | 测的即生产路径，顺带覆盖 strict 模式；改造量按文件渐进 |
| C. 中间件加测试豁免头 | 如 `X-Test-Auth` + 环境开关 | **不推荐**：增加攻击面，违背"测试面=生产面"原则 |

**决策：A + B 组合，P0/P1 分期**。C 否决。

---

## 四、分期实施计划

### P0 止血：测试自举模块（恢复存量直跑能力）

1. 新建 `backend/test_support.py`：
   - 模块级 `os.environ.setdefault(...)`：`LOCK_MODE=asyncio`、
     `STORE_MODE=asyncio`、`AUTH_COMPAT_TRUST_HEADERS=1`
   - 用 `setdefault` 语义：显式环境（如 redis 集成测试 marker）优先
   - 附 `reset_mock_store()` 等测试公共工具可后续并入
2. 修复三个已知受影响文件（各加一行 import，置于 main 导入前）：
   - test_zhidan.py（65 断言）
   - test_zhike.py（72 断言）
   - test_zw_mode.py
3. conftest.py 保留不动（pytest 通道双保险）。

**验收**：三文件直跑全绿（65/65、72/72、zw 通过）。

### P1 治本：真 token 测试轨 + strict 回归

1. `test_support.mint_token(role)`：内存模式下
   `AuthService().register/login` 拿 accessToken
   （本地版参照 verify_sv73_prod.admin_token 容器版范式）。
2. 迁移策略：**新增 HTTP 测试一律 Bearer**；存量渐进迁移
   （每次触碰某文件时顺手迁，不做一次性大改）。
3. strict 模式回归扩展：`AUTH_MODE=strict` 下
   - 无 Bearer → 401
   - member token 访问 admin 端点 → 403（中间件注入 x-role=member，
     路由层拒绝——全链语义）
   - （test_authmode_stage_a.py 已有 stage A 基础，扩展为常驻回归）

**验收**：新增 `test_auth_support.py` 覆盖上述三条语义全绿。

### P2 防回归：制度固化

1. 测试运行规范入档：直跑测试必须 `import test_support`
   （首行约定），新文件模板带此行。
2. 教训入全站教训汇编：**"conftest 只在 pytest 生效——直跑脚本
   必须自举环境"**（第十七条）。
3. 可选：若后续引入 CI，pytest marker 双通道
   （内存默认 + `@pytest.mark.redis` 独立 job，conftest 注释已预留）。

---

## 五、测试与验收总表

| 验收项 | 通过标准 |
|---|---|
| test_zhidan.py 直跑 | 65/65 |
| test_zhike.py 直跑 | 72/72 |
| test_zw_mode.py 直跑 | 全绿 |
| test_auth_support.py（新增） | 铸 token→Bearer 200；裸头 403；strict 无 Bearer 401；member 访 admin 403 |
| 生产 | **零改动**（无部署、无 .env 变更） |

---

## 六、风险与回滚

| 风险 | 等级 | 缓解 |
|---|---|---|
| `AUTH_COMPAT_TRUST_HEADERS=1` 语义放大 | 低 | 仅测试进程内 setdefault；生产 .env 从未设置（.env.example 注明仅限 JWT 故障应急） |
| 直跑测试误连 Redis（STORE_MODE 未自举前的既有隐患） | 中 | P0 的 test_support 顺带根治（内存模式三件套同设） |
| P1 迁移引入新缺陷 | 低 | 渐进迁移 + 每次迁完跑该文件全量断言 |

回滚：删除 import 行即回到现状；test_support.py 无副作用可常驻。

---

## 七、工作量评估

| 期 | 内容 | 产出 |
|---|---|---|
| P0 | test_support.py + 三文件修复 + 验收 | 存量 137+ 断言直跑能力恢复 |
| P1 | mint_token + strict 回归扩展 + 新测试 | 真 token 测试轨就位 |
| P2 | 规范入档 + 教训汇编 | 制度固化 |

（P0 约 半日，P1 约 一日，P2 约 半日；均为测试侧，可穿插进行。）

---

## 八、立项结论

该问题当日以"服务层直调"规避了交付阻塞，但 HTTP 型全链回归
（中间件/路由鉴权层）持续缺位是结构性测试债。P0 成本极低、
收益立即（137+ 断言复活），建议即刻启动；P1 使测试面与生产面
同构，是中长期正解。

> 关联文档：
> - docs/20261003_工作复盘总结.md（遗留事项 #1）
> - docs/全站AI大模型交付总报告_20261003.md（v2 §遗留）
> - backend/core/auth_middleware.py / backend/conftest.py（根因现场）
> - backend/verify_sv73_prod.py（生产真 Bearer 正确范式）
