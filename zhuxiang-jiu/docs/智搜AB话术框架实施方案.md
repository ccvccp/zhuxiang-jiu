# 智搜 A/B 话术框架实施方案 v1.0

> 出处：全站 Multi-Agent 智能体完善规划方案 GAP-3c（唯一待启动项）
> 方案原文（网站智能体.docx §四）："招商 Agent 同时配置 A/B 两套话术，
> 追踪留资转化率，优出 20% 自动晋升为默认 Prompt，并通知运营。"
> 状态：方案已出，待运营确认话术后实施 | 规划人：Trae × 用户

---

## 一、目标与红线改造

### 目标
回答引导话术（ROLE_VARIANTS 之类术）支持 A/B 双版本对照实验，
按用户反馈数据优胜劣汰——"越用越会说"。

### 与方案原文的关键差异（安全化改造）

| 方案原文 | 本实施 | 理由 |
|---|---|---|
| 自动晋升默认 Prompt | **晋升走建议书**：满足条件生成"晋升建议"，admin 手动确认后生效 | 对齐全站六模型铁律"建议书永不自动执行"——话术变更属治理动作 |
| 追踪留资转化率 | 追踪**有用率 + 动作卡点击率**双指标 | 站内反馈体系已有两轨信号，招商"咨询顾问"点击即留资意向代理 |
| 两周评估 | 样本量驱动（每变体 ≥30 反馈即评估，无固定周期） | 小流量下固定周期样本不足 |

**其余红线全沿用**：合规层话术（拦截回复）**永不参与 A/B**（硬规则
恒定）；shadow 档分流照常但**反馈不计入实验统计**（与进化冻结一致）；
防刷分闸复用（同决策同来源只计一次）。

---

## 二、架构设计

```
query 主链 L5(_compose)
    │
    ├─ 意图 ∈ 实验池(equity/agent/product 试点)?
    │    │
    │    ├─ 是 → 分流: variant = "A" if decisionId 奇 else "B"
    │    │        (确定性——同决策可复现, 留痕可归因)
    │    │        lead 话术取 VARIANT_LIB[intent][variant]
    │    │        decision["variant"] = variant (留痕)
    │    └─ 否 → 现行单版话术(零改动)
    │
    ├─ feedback / action-click
    │    └─ shadow 档跳过; 否则 hincr ab_stats:
    │         {intent}:{variant}:useless / useful / action
    │
    └─ 评估(实时惰性计算, 不跑定时任务):
         ab_stats() → 每意图两版样本/有用率/点击率/优出幅度
         满足 样本≥30 且优出≥20% → 生成晋升建议(看板+观测面)
              └─ admin POST /ab/promote {intent} 确认
                   → VARIANT_LIB 当前版切换 + 留痕 ab_decisions
```

### 1. 分流算法（确定性）
`variant = "A" if decisionId % 2 == 0 else "B"`——decisionId 为
store 原子自增序列，天然 50/50 均匀；同决策重放结果一致（审计
可归因）；不依赖随机数/用户画像（公平性红线：分流与会员等级无关）。

### 2. 话术变体库（VARIANT_LIB）

试点三意图 × 角色(guest/member) × 双版本，**文案由运营确认**：

```python
VARIANT_LIB = {
    "agent": {
        # A: 强调供应链(方案原文例)
        "A": {"guest": "为您找到招商政策摘要——源头酒厂直供供应链, "
                       "意向合作可提交留资",
              "member": "为您找到招商政策——供应链直供+区域保护, "
                        "认证代理可在会员中心查看专属政策"},
        # B: 强调营销赋能(方案原文例)
        "B": {"guest": "为您找到招商政策摘要——全域营销赋能扶持, "
                       "意向合作可提交留资",
              "member": "为您找到招商政策——营销赋能+培训体系, "
                        "认证代理可在会员中心查看专属政策"},
    },
    "equity": {
        "A": {"guest": "注册成为会员即可享受积分、会员价与生日礼等权益",
              "member": "为您查询当前会员等级对应的权益与升级路径"},
        "B": {"guest": "加入竹香会员——消费攒竹叶当钱花, 新人注册 "
                       "立得 100 竹叶",
              "member": "您的会员权益已就绪——等级越高会员价越优, "
                        "查看升级路径"},
    },
    "product": {
        "A": {"guest": "为您找到商品购买信息", "member": "为您找到商品购买信息"},
        "B": {"guest": "为您挑选了以下好酒", "member": "为您挑选了以下好酒"},
    },
}
```

不参与实验：brand/help/order/attract/chat（保留 ROLE_VARIANTS 单版）
与**全部合规拦截话术**（L2 硬规则恒定）。

### 3. 指标与评估口径

每意图每变体三计数（Redis Hash `zhuxiang:zs:ab_stats`）：

| 字段 | 含义 | 来源 |
|---|---|---|
| `{intent}:{v}:useful` | 有用反馈数 | submit_feedback(explicit) |
| `{intent}:{v}:useless` | 没用反馈数 | 同上 |
| `{intent}:{v}:action` | 动作卡点击数 | record_action_click |

**有用率** = useful / (useful+useless)；**点击率** = action /
该变体决策数（决策数从 decisions 留痕 variant 字段统计）。

**晋升建议触发**（两条件同时满足）：
- 两变体反馈样本（useful+useless）均 ≥ **30**
- 一版有用率优出另一版 ≥ **20% 相对幅度**

### 4. 晋升流程（人工裁决）
1. `ab_stats()` 观测面实时计算，满足条件返回 `promoteSuggestion`
2. 看板第七区块展示（两版指标对照 + 建议横幅）
3. admin `POST /api/search-ai/ab/promote {intent}` 确认 →
   `VARIANT_ACTIVE[intent]` 切换（store 持久化）+ `ab_decisions`
   留痕（before/after/操作人/时间）
4. 晋升后实验可重开（新一轮 A/B 文案由运营更新）

---

## 三、数据结构与 API

### 存储
- `zhuxiang:zs:ab_stats`（Hash）：指标计数（如上）
- `zhuxiang:zs:ab_active`（Hash）：`{intent: "A"|"B"}` 当前生效版
  （默认 A；晋升切换；redis 可一键重置）
- `zhuxiang:zs:ab_decisions`（表）：晋升/重置操作留痕
- `decisions` 留痕补 `variant` 字段（A/B/null=非实验）

### API（3 端点）
| 端点 | 面 | 说明 |
|---|---|---|
| GET /api/search-ai/ab-stats | 观测(admin) | 实验指标对照 + promoteSuggestion |
| POST /api/search-ai/ab/promote | 控制(admin) | 确认晋升 {intent, toVariant} |
| GET /api/search-ai/ab-decisions | 观测(admin) | 晋升操作留痕 |

### 看板
第七区块「A/B 话术实验」：每实验意图一行——两版样本/有用率/点击率
条形对照 + 优出徽标 + 满足条件时"建议晋升"横幅（含确认按钮，
confirm 后调 promote）。

---

## 四、实施工作量与分期

**P0（本次，约半天）**：
1. VARIANT_LIB（三意图试点文案占位——**待运营确认**）
2. 分流 + _compose 接入 + variant 留痕
3. 反馈/点击双轨计数（shadow 跳过）
4. ab-stats / ab-promote / ab-decisions 三端点
5. 看板第七区块
6. 测试（分流确定性/统计正确/晋升建议触发/红线：合规话术不实验、
   shadow 不计入、防刷分复用）+ 四套审计复验 + 生产部署

**P1（后续按数据）**：
- 实验池扩至 brand/help/order/attract
- 变体库 admin 可配（端点化，运营自助更新文案）

**运营参与清单（实施前确认）**：
- [ ] equity/agent/product 六组文案（A/B × guest/member）确认或修订
- [ ] 晋升阈值确认（样本 30 / 优出 20%）
- [ ] 晋升裁决人（admin 账号）确认

---

## 五、验收场景

| 场景 | 预期 |
|---|---|
| "我想做代理怎么申请" ×2 次 | 两次 lead 话术 A/B 交替（decisionId 奇偶） |
| A 版反馈 useful×30 + useless×10，B 版 useful×10 + useless×30 | ab-stats 出现晋升建议（B→A 优出） |
| admin 确认晋升 | 后续 agent 意图 lead 全走优胜版；ab_decisions 留痕 |
| "做代理有保底收益吗"（合规拦截） | 拦截话术恒定，不参与 A/B |
| shadow 档下反馈 | 分流照常、统计不计入 |
| 同决策重复反馈 | 只计一次统计（防刷分复用） |

---

*A/B 框架上线后，Multi-Agent 方案（网站智能体.docx）所列能力
**全部**落地——含安全化差异项（晋升人工裁决），规划闭环。*
