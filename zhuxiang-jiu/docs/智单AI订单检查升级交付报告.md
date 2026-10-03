# 智单·AI智能订单大模型 检查升级交付总结报告

> 项目：智单（zd_，全站 AI 模型·订单域）检查与升级
> 周期：2026-10-03（单轮体检 → 补齐升级 → 生产活化）
> 状态：**引擎已活化并达到七模型范式期望值**（commit d375725）
> 版本：v1.1-zhidan
> 前序：同日智启元（zy_）已完成同模式升级（commit 8d7d647）

---

## 一、体检结论（升级前）

### 1.1 引擎本体：五层完整

| 层 | 服务 | 能力 |
|---|---|---|
| L3 数据织物 | zd_fabric_service | 订单域只读聚合（九态/GMV/退款率/客单价/履约/三维聚合/日时序），零回写 |
| P0 洞察 | zd_insight_service | 五域自然语言问答 + 四维健康体检 + 三维画像 |
| P1 预测 | zd_forecast_service | 履约 ETA 加权预测 + What-if 三维推演 + 单量滚动预测 |
| P2 风控 | zd_risk_service | 四因子退款评分（建议书）+ 三类异常订单扫描 |
| P3 进化 | zd_evolution_service | 反馈驱动 etaRecentWeight 学习（clamp[0.4,0.8]）+ 三检测器 + 决策备忘 |

### 1.2 运行状态：四要素缺三 + 业务零挂接

| 期望（七模型范式） | 实况 | 判定 |
|---|---|---|
| 三态灰度 | 无 ZD_MODE（少数无模式开关的大模型模块之一） | ❌ |
| 看板 | 20 个端点**零前端消费** | ❌ |
| 调度器 | 无——体检/异常/检测器全靠手动调端点 | ❌ |
| 业务流接入 | 下单/支付/发货/退款/超时五链 **0 处**引用 zd | ❌ |
| 决策留痕 | checkups/portraits/anomalies/feedbacks/memos 五表双模式 | ✅ |
| 建议书永不自动 | 纯旁路只读，零回写订单域 | ✅ |
| 进化闭环 | etaRecentWeight 反馈学习 | ✅ |

---

## 二、升级交付

### 2.1 三态灰度（services/zd_mode_service.py，新建）

- `ZD_MODE`：off（决策面 409）/ shadow（进化冻结）/ assist
- 决策面 = evolution/feedback（参数进化）；分析查询面不拦
- shadow 冻结：反馈只留痕不调 etaRecentWeight；切档留痕进
  feedbacks 表

### 2.2 每日扫描调度器（services/zd_scan_scheduler.py，新建）

```
run_scan() 每轮:
  ① insight.checkup()   四维体检(留痕 checkups)
  ② risk.anomaly_scan() 异常订单扫描(留痕 anomalies)
  ③ evolution.detect()  单量三检测器(告警 logger.warning)
```

- 开关 `ZD_SCAN_AUTO=on` × `ZD_MODE != off`；`POST /scan/run` 手动触发

### 2.3 业务流挂接（智单首处真实业务接入）

**退款申请 `apply_return` 自动附风险建议书**：

- 用户申请退货 → 自动调 `refund_score(order_id)` 四因子评分
- 建议书随订单持久化（`refund.riskAssist`）——管理员审核时直接可见
- 返回体同步附带（含"仅供参考，永不自动"口径标注）
- **fail-soft**：智单故障不阻断退款主流程

### 2.4 看板（zhidan-dashboard.html + js，新建，八区块）

| 区块 | 数据源 |
|---|---|
| ① 模式+总览 | /mode + /overview（九态/GMV/退款率/客单价/履约） |
| ② 九态分布 | /overview statusDistribution 条形 |
| ③ 四维体检 | /checkup（等级徽标+四维条形） |
| ④ ETA+单量预测 | /eta + /forecast?periods=6 |
| ⑤ 三检测器 | /detect |
| ⑥ 异常订单 | /anomalies（高频/囤货/秒退） |
| ⑦ 进化闭环 | /params + /feedbacks + 工具栏"立即扫描" |
| ⑧ 近期订单 | /orders?limit=10 |

**20 个端点首次有前端消费。**

### 2.5 路由与注册

- 新增 3 端点：GET /mode、POST /mode/override、POST /scan/run
- feedback 挂决策面门控；main.py 注册调度器

---

## 三、生产实证（zxjiu.com）

| 项 | 结果 |
|---|---|
| 配置落盘 | `.env`：`ZD_MODE=assist` + `ZD_SCAN_AUTO=on` |
| 调度器启动 | `zd_scan_scheduler started interval=86400s` |
| **首轮体检留痕** | `checkups:6`——33 单四维体检（状态分布 34.55 分/资金安全 69.7 分等逐项解释） |
| 鉴权 | admin 面未登录 401 正常 |

---

## 四、质量数据

| 层 | 规模 | 结果 |
|---|---|---|
| 专项回归 test_zhidan.py | 65 断言（HTTP 型，受 auth 中间件 X-Role 直连 403 **既有限制**影响，与本次改动无关——同 test_zw_mode 类） | 既有状态 |
| 升级测试 test_zhidan_upgrade.py | **10** 断言（服务层直调，规避上述限制） | 全绿 |

新用例覆盖：默认 off / 决策面 409 / shadow 冻结 / assist 恢复 /
扫描汇总 / 体检留痕 / 异常留痕 / 挂接返回建议书 / 永不自动口径 /
建议书随单持久化。

---

## 五、运维速查

- 模式：`ZD_MODE`（生产 assist）；运行时切档
  `POST /api/order-ai/mode/override?mode=shadow`（空串清除）
- 扫描：`POST /api/order-ai/scan/run`（手动）；`ZD_SCAN_AUTO/
  ZD_SCAN_INTERVAL` 控制自动
- 看板：/zhidan-dashboard.html（admin 登录，含"立即扫描"）
- 挂接：退款申请自动附建议书——审核人看订单 `refund.riskAssist`

---

## 六、七模型范式达成（升级后）

| 要素 | 智单现状 |
|---|---|
| 三态灰度 | ✅ ZD_MODE（本次补齐） |
| 决策留痕 | ✅ 原有五表 + 模式/扫描留痕 |
| 建议书永不自动 | ✅ 原有 + 挂接建议书显式标注 |
| 看板 | ✅ 八区块（本次补齐） |
| 调度/业务接入 | ✅ 每日扫描 + **退款流挂接**（本次补齐） |
| 进化闭环 | ✅ etaRecentWeight clamp（原有） |

**同日智启元（zy）与智单（zd）先后完成"空转→活化"，全站七模型
（智启元/智法/智运/智图/智单/智客/智搜）范式全员在线。**

---

*智单检查升级于 2026-10-03 完成：体检四缺实证 → 四件套补齐（灰度/
调度/挂接/看板）→ 生产活化（首轮体检真实留痕）。commit d375725。*
