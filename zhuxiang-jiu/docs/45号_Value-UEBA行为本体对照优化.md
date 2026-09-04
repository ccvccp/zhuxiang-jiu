# 45号·Value-UEBA 行为本体对照优化报告
## ——模板对齐差距分析 + P6 UEBA 守门层工程落法

> 状态: 已实施 · 2026-09-04
> 输入: 《Value-UEBA 行为本体分类学模板》(用户提供的基准文档)
> 落法: P6 UEBA 守门层(四守门 × 显式参数激活 × 零侵入)

---

## 一、对照结论总览

模板四大域与本仓 45号实现的映射关系——**核心骨架
高度吻合, 差距集中在"UEBA 分析要点"层**:

| 模板域 | 模板关键要素 | 45号现有落法 | 状态 |
|--------|-------------|--------------|------|
| 法治合规域 L1(50%) | 履约/违规/处罚/确权, 事件触发/日更 | L1×3 因子(司法合规/行政监管/履约确权) + L1 熔断引擎 | ✅ 已覆盖 |
| 社会伦理域 L2(30%) | 诚信互动/舆论/平台信用, 实时流 | L2×3 因子(平台言行/社区口碑/伦理证据) | ✅ 结构覆盖 |
| 贡献价值域 L3(20%) | 捐赠/志愿/开源/创新, 申报制 | L3×3 因子(净贡献/影响力/长尾) + 因果净贡献 | ✅ 已覆盖 |
| 修复对冲域(跨层) | 针对性/通用性修复, 复发状态机 | P2 即时修复引擎(α×β×γ×验真×天花板) | ✅ 核心一致 |

**逐字段精确对齐项**(模板定义 = 本仓实现):

| 模板字段 | 模板口径 | 45号落法 |
|----------|---------|---------|
| time_delta_hours | γ(t)=e^(-λt), λ=0.1 | `gamma_of` LAMBDA=0.1 **完全一致** |
| repair_action_type | β 针对 1.5/通用 0.5/象征 0.2 | BETA_MAP 1.5/1.3/0.5/0.3(映射表) |
| cap_remaining | 初始扣分×α−已修复 | `cap = \|delta\|×α` 天花板 |
| verification_confidence | 多模态融合, <0.5 不计入 | verify_pipeline 三道关, conf<0.7 不入分(**更严**) |
| 角色多态 | 同一行为类双角色属性集 | FACTOR_LABELS person/org 差异化 |
| 因果推断 | 剔除自然增长 | `net_contribution` 反事实基线 |
| 熔断分级 | severe/criminal 分级处置 | FUSE_ALPHA general 1.0/severe 0.3/criminal 0 |

## 二、差距分析(P6 优化前的缺口)

模板"UEBA 分析要点"列中四个**确定性守门指标**在
45号 P0-P5 中无对应落法:

| # | 模板字段 | 模板规则 | 缺口影响 |
|---|---------|---------|---------|
| ① | consistency_score | 跨平台一致性 <0.3 触发伪善预警 | 真伪鉴别无 UEBA 输入——多平台言行不一者可正常加分 |
| ② | self_promotion_ratio | 宣传占比 >0.7 触发"作秀"降权 | L3 层防刷缺关键指标——高频宣传低实质贡献可刷分 |
| ③ | recurrence_risk | 再犯风险 >0.7 降低修复效率 | 惯犯与初犯同通道——"刷违规-刷修复"循环无成本递增 |
| ④ | voluntary_flag | 主动提交额外 +5% 激励 | 自愿披露无差异化激励(模板"鼓励自愿披露"导向) |

## 三、P6 UEBA 守门层工程落法

### 3.1 设计铁律

- **显式参数激活**: `consistency`/`selfPromotion`/`voluntary`
  缺省 None → 守门不生效——既有 343 项测试与生产调用
  **零影响**(回归全绿验证)
- **纯乘性修正**: 守门只折损/激励 delta, 不改变九因子
  结构/宪法 50/30/20/修复 αβγ 数学——本体骨架不动
- **确定性纯函数**: 四守门无 IO 无状态(计数由调用方查库
  传入), 单元可测
- **负向不折损**: 一致性/作秀守门仅作用于正向事件——
  扣分不折扣, 伪善只影响加分(防"认领扣分"套利)

### 3.2 四守门数学

```
① consistency_gate(consistency)
   < 0.3 → 正向 delta × 0.5(hypocrisy_alert 伪善预警)

② self_promotion_gate(ratio)
   > 0.7 → 正向 delta × 0.5(self_promotion_discount 作秀降权)

③ recurrence_risk(n) = n/(n+2)   # UEBA 历史序列平滑
   n: 同因子违规事件总数(含当前)
   n=0→0, 1→0.33, 2→0.5, 4→0.67, 5→0.71(首次越限)
   risk > 0.7 → 修复效率 × 0.5(惯犯通道收窄)

④ voluntary_bonus(voluntary, positive)
   voluntary ∧ 正向 → delta × 1.05(自愿披露激励)
```

### 3.3 接入点(外科手术式)

| 守门 | 接入位置 | 激活方式 |
|------|---------|---------|
| ①② | `TrustProfileService.record_event` | 调用方显式传 `consistency`/`self_promotion`(L2/L3 正向事件) |
| ③ | `TrustRepairService.submit_repair` | **自动**——同因子违规计数查库计算(n≥5 触发) |
| ④ | `TrustRadarService.submit_deposit` | 调用方显式传 `voluntary=True` |

路由透传: `POST /trust/roles/{id}/events` body 增加
`consistency`/`selfPromotion`; `POST /trust/deposits` body
增加 `voluntary`。

### 3.4 留痕与可观测

- 事件守门命中: 响应 `uebaGates` 数组 + 事件 summary
  追加 `[UEBA伪善预警: ...]` 归因文本
- 修复守门命中: 响应 `recurrenceRisk`/`repairEfficiency`/
  `recurrenceNote` 三字段
- 存证激励: 响应 `voluntaryBonus`/`voluntaryNote`

## 四、模板中暂不落地的项(工程判断)

| 模板项 | 不落地原因 |
|--------|-----------|
| OWL/RDF 形式化本体 | 当前九因子+事件流已承载语义; 图谱化在数据量级不足时是过度工程(月度评审会再评估) |
| GNN 拓扑分析(anomaly_tag) | mock 态无真实社交图谱数据; intent_check 已覆盖表演式向善的核心场景 |
| SDG 对标加成 | 政策导向加分需要外部标准映射表, 列入外部待办 |
| 对数归一化(amount_scale) | 现有事件 delta 直接给定, 金额归一化属雷达采集层特性(real 轨接入时一并处理) |
| 每月本体评审会 | 治理流程项(46号治理中枢的公平性审计+变更审批总线已提供工程替代) |

## 五、验收与一致性声明

- **专项测试**: `test_trust_value_p6_ueba.py` 39 项
  (四守门数学边界/接入语义/None 零影响回归保护/HTTP 透传)
- **回归保护**: 45号 P0-P5 全量 343 项零新增失败
  (deposit delta==14.5 / repair gain==20.0 等既有精确
  断言原样通过——显式参数激活的零影响验证)
- **Docker 实机验收**: 见 `verify_trust_value_p6_live.py`
  (四守门 E2E ×2 轮幂等)
- **一致性**: 不新增评分档案(第 28 档案结构不变);
  不改宪法权重; 不改 α/β/γ 修复数学; 守门是乘性修正层

---

*45号 P6(2026-09-04): Value-UEBA 模板不是推倒重来的图纸,
而是照出四个盲区的镜子——一致性预警补真伪鉴别, 作秀降权
补 L3 防刷, 再犯风险补惯犯通道, 自愿激励补披露导向。
本体四大域的骨架本已在仓, 这次补的是 UEBA 的眼。*
