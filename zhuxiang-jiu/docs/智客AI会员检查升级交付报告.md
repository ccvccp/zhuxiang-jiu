# 智客·AI智能会员大模型 检查升级交付总结报告

> 项目：智客（zk_，全站 AI 模型·会员域）检查与升级
> 周期：2026-10-03（单轮体检 → 补齐升级 → 生产活化）
> 状态：**引擎已活化并达到全站模型范式期望值**（commit 41f0ae7）
> 版本：v1.1-zhike
> 前序：同日智启元（8d7d647）、智单（d375725）已完成同模式升级

---

## 一、体检结论（升级前）

### 1.1 引擎本体：五层完整

| 层 | 服务 | 能力 |
|---|---|---|
| L3 数据织物 | zk_fabric_service | 会员/订单只读聚合（总览/单会员消费聚合） |
| P0 洞察 | zk_insight_service | 五域问答 + 五维 Sigmoid 健康度 + RFM 七类画像 |
| P1 留存 | zk_retention_service | 三信号流失预警 + LTV 预测 + 等级 What-if 沙盘 |
| P2 运营 | zk_operation_service | L1-L5 权益矩阵匹配 + 积分运营 + 沉睡唤醒分级 |
| P3 进化 | zk_evolution_service | ltvRetainFactor 反馈学习（clamp[0.4,0.8]）+ 三检测器 + 备忘 |

### 1.2 运行状态：四要素缺三 + 决策大脑零挂接

| 期望（全站范式） | 实况 | 判定 |
|---|---|---|
| 三态灰度 | 无 ZK_MODE | ❌ |
| 看板 | 20 端点零前端消费 | ❌ |
| 调度器 | 无——流失扫描/唤醒全手动 | ❌ |
| 业务流接入 | 注册/登录/下单/升级/签到**零处**触发 zk 决策 | ❌ |
| 决策留痕 | 九表双模式（Redis `zhuxiang:zk:*`） | ✅ |
| 建议书永不自动 | 唤醒/权益建议 disposition 全标注 | ✅ |
| 进化闭环 | ltvRetainFactor clamp | ✅ |

**特征性发现**：唯一跨模块接入是**反方向**的——73 号会员体验模型
（member73）把 `ZkFabricService` 当只读 DAO、`LEVEL_BENEFITS` 当
常量表消费。**zk 的底座被业务用了，大脑没有。**

---

## 二、升级交付（四件套，复用智启元/智单模式）

### 2.1 三态灰度（services/zk_mode_service.py，新建）

- `ZK_MODE`：off（决策面 409）/ shadow（进化冻结）/ assist
- 决策面 = evolution/feedback；分析查询面不拦；切档留痕

### 2.2 每日扫描调度器（services/zk_scan_scheduler.py，新建）

```
run_scan() 每轮:
  ① retention.churn_scan()   三信号流失预警(逐会员留痕 zk_churns)
  ② operation.wakeup_suggest() 沉睡唤醒分级建议(留痕 zk_wakeups)
  ③ evolution.detect()        消费/积分/注册三检测器
  → 红色流失与告警 logger.warning; POST /scan/run 手动触发
```

### 2.3 业务挂接（智客决策大脑首处真实业务流）

**会员升级自动附新等级权益建议书**：

- `member_service.consume` 升级分支（L1→L2→…→L5 判定处）挂
  `ZkOperationService.benefit_match(member_id)`
- `upgradeBenefits`（等级名+权益清单+"永不自动"口径）随返回——
  会员升级即刻看到新等级权益
- **fail-soft**：智客故障不阻断消费/升级主流程
- 未升级消费不附（无噪声）

### 2.4 看板（zhike-dashboard.html + js，新建，八区块）

| 区块 | 数据源 |
|---|---|
| ① 模式+总览 | /mode + /overview |
| ② 会员总览 | /overview 等级分布表 |
| ③ 流失预警 | /churns（红黄绿徽标+分值） |
| ④ 唤醒建议 | /wakeups（深/中/轻分级） |
| ⑤ 三检测器 | /detect |
| ⑥ 进化闭环 | /params（ltvRetainFactor+clamp） |
| ⑦ RFM 画像快照 | /portraits |
| ⑧ 反馈流 | /feedbacks + 工具栏"立即扫描" |

**20 个端点首次有前端消费。**

### 2.5 路由与注册

新增 3 端点（GET /mode、POST /mode/override、POST /scan/run）；
feedback 挂决策面门控；main.py 注册调度器。

---

## 三、生产实证（zxjiu.com）

| 项 | 结果 |
|---|---|
| 配置落盘 | `.env`：`ZK_MODE=assist` + `ZK_SCAN_AUTO=on` |
| 调度器启动 | `zk_scan_scheduler started interval=86400s` |
| **首轮流失扫描留痕** | `zk_churns:30`——真实会员三信号评分（登录间隔 2.12 天 vs 同层均值 2.45 / 近 30 天消费 0 → 评分 0.12 / green），**逐项可解释** |
| 鉴权 | admin 面未登录 401 正常 |

---

## 四、质量数据

| 层 | 规模 | 结果 |
|---|---|---|
| 专项回归 test_zhike.py | 72 断言（HTTP 型，受 auth 中间件 X-Role 直连 403 既有限制影响，与本次改动无关） | 既有状态 |
| 升级测试 test_zhike_upgrade.py | **10** 断言（服务层直调） | 全绿 |

新用例：默认 off / 决策面 409 / shadow 冻结 / assist 恢复 / 扫描
汇总 / 流失留痕 / 升级触发 / 升级附建议书 / 永不自动口径 /
**未升级不附建议书（反例）**。

---

## 五、运维速查

- 模式：`ZK_MODE`（生产 assist）；运行时切档
  `POST /api/member-ai/mode/override?mode=shadow`（空串清除）
- 扫描：`POST /api/member-ai/scan/run`（手动）；`ZK_SCAN_AUTO/
  ZK_SCAN_INTERVAL` 控制自动
- 看板：/zhike-dashboard.html（admin 登录，含"立即扫描"）
- 挂接：会员升级返回 `upgradeBenefits`（权益建议书）

---

## 六、范式达成与当日三连升级收官（升级后）

| 要素 | 智客现状 |
|---|---|
| 三态灰度 | ✅ ZK_MODE（本次补齐） |
| 决策留痕 | ✅ 原有九表 + 模式留痕 |
| 建议书永不自动 | ✅ 原有 + 挂接建议书显式标注 |
| 看板 | ✅ 八区块（本次补齐） |
| 调度/业务接入 | ✅ 每日扫描 + **升级流挂接**（本次补齐） |
| 进化闭环 | ✅ ltvRetainFactor clamp（原有） |

**2026-10-03 三连升级收官**：智启元（财务·退款税务扫描）→ 智单
（订单·退款风险建议书）→ 智客（会员·升级权益建议书）——三模型
同日完成"空转→活化"，全站七模型范式全员在线且各有真实业务流挂接。

---

*智客检查升级于 2026-10-03 完成：体检"底座有用大脑闲置"实证 →
四件套补齐 → 生产活化（首轮流失扫描真实留痕）。commit 41f0ae7。*
