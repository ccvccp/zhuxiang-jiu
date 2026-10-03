# 智启元·AI智能财务大模型 检查升级交付总结报告

> 项目：智启元（zy_，全站第一大 AI 模型·财务域）检查与升级
> 周期：2026-10-03（单轮体检 → 补齐升级 → 生产活化）
> 状态：**引擎已活化并达到六模型范式期望值**（commit 8d7d647）
> 版本：v1.1-zyqiyuan

---

## 一、体检结论（升级前）

### 1.1 引擎本体：功能完整

四层确定性财务引擎（LLM 禁入财务数字链）：

| 层 | 服务 | 能力 |
|---|---|---|
| L3 数据织物 | zy_data_service | 订单/商品只读聚合→月度财务时序（收入/退款/成本/税/净利），零回写 |
| P0 问答分析 | zy_qa_service | 五域意图路由 + 杜邦三因素 + 归因四因素 + 健康度五维 |
| P1 预测沙盘 | zy_forecast_service | 滚动预测（0.6近期+0.4全期）+ What-if 三维 + 驱动相关性 |
| P2 税务优化 | zy_tax_service | 四结构税负模拟 + 政策库 + 风险热力图五维 |
| P3 进化决策 | zy_evolution_service | 反馈闭环调参（trendWeight clamp[0,0.8]）+ 异常三检测 + 90日资金排程 + DCF 备忘 |

### 1.2 运行状态：彻底空转（四零）

| 期望（六模型范式） | 实况 | 判定 |
|---|---|---|
| 业务流接入 | 支付/订单/结算/退款**零挂接**（仅智法数据湖只读借数 1 处） | ❌ |
| 调度器 | 无——异常检测/资金排程纯被动（仅 GET 时计算） | ❌ |
| 三态灰度 | 无 ZY_MODE 开关，无门控 | ❌ |
| 看板 | 15 个 GET 端点**零前端消费**（"仅测试在跑"） | ❌ |
| 决策留痕 | zy_feedback/memos/logs 双模式存储 ✓ | ✅ |
| 建议书永不自动 | 纯只读引擎，零回写业务库 ✓ | ✅ |
| 进化闭环 | feedback→trendWeight ±0.1 clamp ✓ | ✅ |

**易混澄清**：`zyh-dashboard` 是竹韵·智衡（75 号）的看板——智启元
此前没有任何看板。

---

## 二、升级交付（对齐智运/智搜补齐模式）

### 2.1 三态灰度（services/zy_mode_service.py，新建）

- `ZY_MODE`：off（默认，决策面 409）/ shadow（分析可用·进化冻结）/
  assist（生产档）；读取链 override > env > off
- **决策面门控**：evolution/feedback（参数进化）与 tax/policies POST
  （政策库写入）off 时 409；分析查询面不拦
- **shadow 冻结语义**（对齐智搜）：反馈只留痕不调 trendWeight——
  观测期数据不污染进化参数
- 切档留痕进 zy_logs

### 2.2 每日扫描调度器（services/zy_scan_scheduler.py，新建）

引擎活化核心——从"拉取式面板"升级为"主动值守"：

```
run_scan() 每轮:
  ① anomalies()        月度净收入三检测(spike/drop/surge)
  ② cash_schedule(30)  近 30 日资金缺口推演
  ③ risk_heatmap()     税务风险五维
  → daily_scan 全量留痕(engine=scheduler)
  → 发现异常单独 alert 留痕(观测面快速定位)
```

- 开关：`ZY_SCAN_AUTO=on` × `ZY_MODE != off`；间隔 `ZY_SCAN_INTERVAL`
  （默认 86400s）
- `POST /api/zy/scan/run`：admin 手动触发单轮
- 铁律：扫描只读不写业务库；建议永不自动执行

### 2.3 看板（zy-dashboard.html + js/zy-dashboard.js，新建）

八区块（对齐智运看板范式：同源默认 + 401 汉化 + 30s 自刷 +
宽松取值降级渲染）：

| 区块 | 数据源 |
|---|---|
| ① 模式态+总览 | /mode + /status（反馈数/trendWeight/异常数） |
| ② 月度财务时序 | /series?months=12（收入/退款/成本/税/净利表） |
| ③ 杜邦+健康度 | /dupont（ROE 三因素）+ /health（五维条形） |
| ④ 滚动预测+驱动 | /forecast?horizon=6 + /drivers |
| ⑤ 税务风险热力图 | /tax/risk-heatmap（五维+综合等级） |
| ⑥ 异常自发现 | /evolution/anomalies |
| ⑦ 资金智能调度 | /evolution/cash-schedule?days=30 |
| ⑧ 进化反馈流 | /evolution/feedbacks + 工具栏"立即扫描"按钮 |

**15 个 GET 端点首次有前端消费。**

### 2.4 路由与注册

- 新增 3 端点：GET /api/zy/mode、POST /api/zy/mode/override、
  POST /api/zy/scan/run（全 admin）
- /status 补 mode 字段；feedback 与政策写入挂决策面门控
- main.py 启动块注册 zy 扫描调度器（对齐 42 个既有 scheduler 范式）

---

## 三、生产实证（zxjiu.com）

| 项 | 结果 |
|---|---|
| 配置落盘 | `/opt/zhuxiang/.env`：`ZY_MODE=assist` + `ZY_SCAN_AUTO=on` |
| 调度器启动 | 容器日志：`zy_scan_scheduler started interval=86400s` |
| **首轮扫描** | 留痕 `logId 48 engine=scheduler action=daily_scan`，**anomalyCount=1（drop）——活化即刻产出真实信号** |
| 鉴权 | admin 面未登录 401（中间件正常） |
| 健康 | /api/decision/health 200 |

---

## 四、质量数据

| 层 | 规模 | 结果 |
|---|---|---|
| 专项回归 test_zhiqiyuan.py | 43 → **49** 断言 | 全绿 |
| 新增用例 | 6：默认 off / 决策面 409 / shadow 冻结 / assist 恢复 / 扫描成功 / 扫描留痕 | 全过 |
| 既有 43 用例（数据织物/问答/预测/税务/进化） | 零回归 | 全过 |

---

## 五、运维速查

- 模式：`ZY_MODE`（生产 assist）；运行时切档
  `POST /api/zy/mode/override?mode=shadow`（空串清除）
- 扫描：`POST /api/zy/scan/run`（手动）；`ZY_SCAN_AUTO/ZY_SCAN_INTERVAL`
  控制自动；扫描留痕查 `GET /evolution/logs?engine=scheduler`
- 看板：/zy-dashboard.html（admin 登录，工具栏含"立即扫描"）
- 三态语义：off=决策面 409 | shadow=分析可用·**进化冻结** |
  assist=生产档（进化生效+扫描值守）

---

## 六、七模型范式达成对照（升级后）

| 范式要素 | 智启元现状 |
|---|---|
| 三态灰度 | ✅ ZY_MODE（本次补齐） |
| 决策留痕可审计 | ✅ 原有 + 本次补 mode/扫描留痕 |
| 建议书永不自动 | ✅ 原有（零回写） |
| 看板 | ✅ 八区块（本次补齐） |
| 调度/业务接入 | ✅ 每日扫描值守（本次补齐；业务挂接为拉取式数据织物设计，无需逐单 hook） |
| 进化闭环 | ✅ trendWeight clamp（原有） |

**全站七模型（智启元/智法/智运/智图/智单/智客/智搜）至此全部具备
完整范式。**

---

*智启元检查升级于 2026-10-03 完成：体检四零实证 → 三件套补齐
（灰度/调度/看板）→ 生产活化（首轮扫描真实检出异常）。commit 8d7d647。*
