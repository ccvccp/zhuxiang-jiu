# 71号·AI智能支付端口大模型 assist 期运行监控报告（模板）

> 适用：`PAY71_MODE=assist` 辅助生产期持续监控（周期性填写——建议日报/周报双频）
> 前置：影子期评估报告已签核、转段已完成（shadow→assist，2026-09-13）
> 填写说明：`____` 为填写占位；采集端点均需 `X-Role: admin` 头；`□` 为勾选项
> 语义提醒：assist = 71号调配结果作为**建议注入 69号 P1 路由情境参考**；资金路由仍由 69号 P1 唯一执行（advisoryOnly 铁律）；监控重点是**建议质量**而非执行效果

---

## 一、监控周期基本信息

| 项 | 值 |
|---|---|
| 报告类型 | □ 日报　□ 周报 |
| 监控周期 | ____ 至 ____（UTC+8） |
| 转段后累计运行 | ____ 天（自 2026-09-13 起） |
| 模块版本 | v1-pay71-registry（`f7c4c90` 及以后） |
| 生产环境 | https://zxjiu.com（47.236.61.117） |

## 二、运行状态总览

采集：`GET /api/pay71/model/status`、`GET /api/pay71/immunity`、`docker ps`

| 项 | 值 | 判定 |
|---|---|---|
| mode | assist（应恒定） | □ |
| kill | false | □ |
| 容器状态 | healthy 全程 | □ |
| 免疫态 | active（frozen 即触发处置流程 §七） | □ |
| 红队最近批次 | allDefended=____/4（应 4/4） | □ |
| 治理分级 | PAY71_EVOLUTION_LEVEL=____（默认 L0） | □ |

## 三、建议质量监控（assist 期核心）

### 3.1 预判建议（P2）

采集：`GET /api/pay71/predict/records?limit=200`

| 指标 | 本期 | 上期 | 趋势 | 备注 |
|---|---|---|---|---|
| 预判总量 | ____ | ____ | | 流量基准 |
| habit 轨占比 | ____% | ____% | | 习惯数据积累进度 |
| 推荐通道分布 | qr __ / wechat __ / alipay __ / bank __ / unionpay __ / credit_tv __ / biometric __ | | | credit_tv 应仅 tvEligible 样本 |
| free 档占比 | ____% | ____% | | 免密梯度健康参考 60-90% |
| 拆分建议触发 | ____ | ____ | | ¥5000+ 样本数 |
| 撤销（revoke）数 | ____ | ____ | | 预载撤销使用率 |

### 3.2 调配建议（P3 帕累托）

采集：`GET /api/pay71/allocation/records?limit=200`

| 指标 | 本期 | 上期 | 趋势 |
|---|---|---|---|
| 调配总量 | ____ | ____ | |
| 情境分布 | balanced __ / price_sensitive __ / high_value __ / large_amount __ | | |
| 权重源分布 | factory __% / override __% | | override 须对应已批准外部信号建议书 |
| 帕累托前沿规模均值 | ____ | | 健康参考 2-4 |
| 复现抽检 | ____ 组一致 ____ | | 确定性核心断言（恒须 100%） |

### 3.3 建议-实际对照（assist 期新增核心指标）

> 对照 71号 assist 建议与 69号 P1 实际路由结果（留痕均可查）

| 指标 | 值 | 阈值/口径 |
|---|---|---|
| 对照样本数 | ____ | ≥30 组/周 |
| 推荐一致率 | ____% | 趋势参考（非硬门槛） |
| 免密档一致率 | ____% | 梯度对齐参考 |
| 分歧样本归因（择要 3 例） | | 例：习惯数据不足/情境档选择差异/帕累托前沿并列 |
| 建议采纳信号 | 69号路由参考中出现 71号情境权重特征：□有 □无 | 注入链路生效观察 |

### 3.4 对账核验质量（P4）

采集：`GET /api/pay71/recon/verifies?limit=200`、`/recon/records`、`/recon/baseline`

| 指标 | 本期 | 上期 |
|---|---|---|
| 核验总量 | ____ | |
| matched 率 | ____% | |
| 差错分布 | amount __ / timeout __ / duplicate __ / partial __ | |
| auto_healed / manual_referral / failed | __ / __ / __ | |
| 幂等防重触发 | ____ | |
| 渠道延迟档 | instant __ / fast __ / slow __ | |

### 3.5 风控叙事质量（P5）

采集：`GET /api/pay71/narrative/records?limit=200`、`/narrative/library`

| 指标 | 本期 | 上期 |
|---|---|---|
| 叙事总量 | ____ | |
| 判定分布 | caution __ / legitimate __ / neutral __ | |
| 误拦归因回流 | ____（context_blind __ / baseline_stale __ / threshold_high __） | |
| 案例库 | pending __ / confirmed_fraud __ / confirmed_legit __ | |

## 四、进化与治理（P7）

采集：`GET /api/pay71/evolution/governance`、`/evolution/hypotheses`

| 指标 | 本期 | 备注 |
|---|---|---|
| 漂移信号数 | ____ | 按域分布 |
| 假设状态 | proposed __ / submitted __ / published __ / rejected __ | 进化永不自动生效口径保持 |
| 46号审批关联 | changeId 覆盖 ____ 条 | 第 44 批档案 |
| 参数 active 版本 | ____ | 每参数 ≤1 |

## 五、安全免疫（P8）

采集：`GET /api/pay71/immunity`、`POST /api/pay71/immunity/redteam`（周期性复跑建议每周一次）

| 指标 | 本期 | 阈值 |
|---|---|---|
| 免疫态 | active | frozen 即走处置流程 |
| 红队复跑 | allDefended=____/4 | 4/4（否则冻结+修复） |
| 红队批次累计 | ____ 次 | |
| 冻结事件 | ____ 次 | 每次须有闭环留痕 |

## 六、审计与合规（P6）

采集：`GET /api/pay71/audit/tracegraph`、`/audit/evidence`、`/audit/report`

| 指标 | 本期 |
|---|---|
| 五域链路图计数 | 治理 __ / 自愈 __ / 调配 __ / 核验 __ / 叙事 __ |
| 证据链组装/导出 | ____ / ____ |
| 哈希校验抽检 | ____ 次全 valid □ |
| 合规健康报告 | ____ 份，redLineTouches 恒 0 □ |

## 七、异常处置记录（本周期内）

> 无异常填"无"；有则逐条登记（frozen/5xx/回滚事件均须闭环）

| # | 时间 | 事件 | 处置 | 状态 |
|---|---|---|---|---|
| 1 | | □免疫冻结 □决策面 5xx □回滚 □其他 | | □闭环 |

异常处置路径：
- **免疫冻结**：排查→修复部署→`PAY71_IMMUNITY=1` 授权→`POST /immunity/unfreeze` 留痕
- **决策面 5xx**：定位→热修/回滚（`bash scripts/pay71_transfer.sh off`）→复验
- **紧急情况**：`PAY71_KILL=1`（进程级）或 `POST /evolution/kill`（数据面）

## 八、回归巡检

| 项 | 结果 |
|---|---|
| 69号 `/api/pay69/channels` | ____（200） |
| 70号 `/api/qr70/model/status` | ____（200） |
| 前端 `https://zxjiu.com/` | ____（200） |
| API 健康 `/api/decision/health` | ____（200） |
| 71号九期 /dict 巡检 | ____/8 = 200 |

## 九、结论与建议

| 判定 | □ 正常运行　□ 需关注（事项：____）　□ 需处置（见 §七） |
|---|---|

建议事项：____（例：L1 分级开放评估 / 外部信号建议书审批 / assist→全量生效评估）

## 十、签核

| 角色 | 姓名 | 日期 |
|---|---|---|
| 监控填写 | | |
| 运维复核 | | |

---

*模板版本：v1（2026-09-13，随 assist 转段同步建立）；归档命名 `71号_assist运行监控_YYYYMMDD.md`；日报留存 30 天、周报长期归档。*
