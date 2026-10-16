# 复购预测与直播 QA 模块·优化建议

> 依据：[蓝图近期项四件单元测试报告](computer://d:\网站架构设计\docs\蓝图近期项四件单元测试报告.md) §7 观察点 + 两模块源码现状核实（2026-10-16，基于 commit `a553420` 工作区）
> 性质：规划建议文档，不改动代码；全部建议遵循项目既有铁律（observe 灰度 / 确定性优先 / fail-soft / 永不自动触达）。

---

## 一、观察点与现状核实

| 测试报告观察点 | 现状核实（文件锚点） | 缺口 |
|---|---|---|
| ② observe 积累建议书分布（upcoming/overdue 占比、qFactor 分布）后决策是否 enforce | [repurchase_predict_service.py](file:///d:/网站架构设计/zhuxiang-jiu/backend/services/repurchase_predict_service.py) 仅有 `scan_due`/`suggestions(limit=50)`/`dispatch` 三面；留痕逐条落 `repurchase_suggestions` 但**无聚合统计**；scan 仅手动 GET 触发，**无调度挂载** | observe 数据在留痕里但没有复盘视图，也无人保证每日稳定积累 |
| ④ stats 的 unsolved 率若持续高企→优先补 knowledge 条目而非调参 | [live_qa_service.py](file:///d:/网站架构设计/zhuxiang-jiu/backend/services/live_qa_service.py) `stats()` 为**即时快照**（无时间分桶）；unsolved 已 best-effort `record_gap` 但**无拉取闭环**；`LIVE_QA_SENSITIVE_WORDS` 硬编码（L59）；JSONL 单文件追加**无滚动**；公开端点**无频控** | 「unsolved 高→补条目」的决策链缺度量趋势与缺口运营出口 |

---

## 二、复购预测模块优化建议

### P0 · 观察闭环（enforce 决策的前提）

**R1 分布统计端点**
- 现状：`suggestions(limit=50)` 只能翻列表，无法回答「upcoming/overdue 各占多少、qFactor 三档分布如何、建议书日均产出、dispatch 转化率」。
- 建议：新增 `GET /api/member-ai/repurchase/stats`（admin）——聚合输出 `{total, byTier{upcoming,overdue}, byQFactor{1.2,1.0,0.8,skipped}, last7dDaily, dispatchRate(pending→sent), mode}`；纯读留痕聚合，确定性无 LLM。
- 验收：stats 数字与 suggestions 列表人工抽核对齐；补 2-3 个聚合用例。

**R2 扫描调度挂载**
- 现状：scan 仅手动触发——observe 留痕的「D+N 分布」依赖有人记得每天跑。
- 建议：复用 `zk_scan_scheduler` 形态（`_auto_enabled()` 默认关 + `_interval()` 可配），挂 `repurchase.scan_due(window_days=3)`；上线路径：手动跑 3 天 → 开 auto → stats 看稳定性。
- 验收：auto 关闭时零行为变化（既有铁律）；开启后连续 7 天 `last7dDaily` 无断档。

### P1 · 预测精度（当前口径的两处系统性偏差）

**R3 规格归一化周期**
- 现状：消耗周期=订单间隔中位数（`predict_member` L133），**不区分单次购买量**——一次囤 2 瓶的会员与每次 1 瓶的会员间隔天然不同，当前口径会系统性低估囤货者周期、高估预警频率。
- 建议：`interval_normalized = raw_interval × (基准规格量 / 该单规格量)`（按「等效瓶数/升数」归一）；规格字段缺失时回退原始间隔（fail-soft 不崩）。
- 验收：构造「2 瓶间隔 40 天 + 1 瓶间隔 20 天」样本，归一后中位数≈20 而非 30。

**R4 送礼季异常间隔剔除**
- 现状：中秋/春节礼赠订单进入间隔序列会拉长周期（送礼≠自饮消耗）。
- 建议：确定性剔除规则——间隔 > 2×当前中位数的样本标记 `gift_outlier` 剔除后重算一轮（非统计黑盒，可解释）；标记留痕供运营复核。
- 验收：注入一个 5×中位的异常间隔，断言被剔除且 `cv` 不被其放大（qFactor 档位不误降）。

**R5 分层建议书文案**
- 现状：`_build_suggestion`（L266）为统一模板。
- 建议：overdue 档按会员等级 × 竹信分分层（复用 zk `WAKEUP_TIERS` 三级范式：deep/medium/light 递进权益），仍为模板拼装确定性文案。
- 验收：同 tier 不同等级会员的建议书 suggestion 字段分层正确。

### P2 · 接入与运营效率

**R6 批量确认 dispatch**
- 现状：`dispatch(suggestion_id, operator)` 逐条确认——建议书量上来后不可运营。
- 建议：`dispatch_batch(suggestion_ids: list, operator)`（幂等：非 pending 跳过并计数），**operator 必填人工闸不破**；返回逐条结果汇总。
- 验收：混合 pending/sent 列表批量派发，sent 跳过计数正确、无重复发送。

**R7 qFactor 接入 IMTM 定价链（依赖 R1）**
- 现状：qFactor 仅建议书留痕（observe only），`growth80_pricing.compute_factors` 的 `fQUser` 恒 1.0 占位。
- 建议：compute_factors 增可选读取 repurchase 留痕（fail-soft：读不到/样本不足回 1.0，**不新增外部依赖**）；先 observe 双写对照——factors 快照增 `fQUserCurrent`(恒1.0) 与 `fQUserCandidate`(qFactor) 两字段，D+14 用 stats 分布复盘差值（wouldBePoints 已有假想值机制可复用）后再议 enforce。
- 验收：growth80_pricing 既有 8 用例零破坏（fail-soft 路径）；新增双写对照用例。

---

## 三、直播 QA 模块优化建议

### P0 · unsolved 闭环（观察点的直接兑现）

**L1 unsolved 率趋势与阈值告警**
- 现状：`stats()` 为即时快照——无法回答「持续高企」（趋势不可见）。
- 建议：①stats 增按小时分桶（进程内 ring buffer + JSONL 重放兜底）；②unsolved 率 > 40% 连续 3 桶 → admin 站内信告警（复用 message 模块，**告警发给管理员不属于用户触达**，不违铁律）。
- 验收：构造连续高 unsolved 序列断言告警恰好一次（告警本身幂等去重）。

**L2 缺口拉取与转草稿闭环**
- 现状：unsolved 已 best-effort `record_gap`（contextlib.suppress 包裹），但缺口沉在 knowledge 缺口池里无直播视角出口。
- 建议：admin `GET /api/live-qa/gaps`（按 live_qa 来源过滤缺口列表，含频次）+ `POST /api/live-qa/gaps/{id}/to-draft`（转知识**草稿**进 knowledge 既有 review-publish 流——**不绕过人工审核铁律**）。
- 验收：gap 拉取按来源过滤正确；to-draft 产物在 knowledge 待审列表且 status=pending。

### P1 · 检索质量与直播适配

**L3 直播专属知识源（category 优先检索）**
- 现状：knowledge entries 已有 `category` 维度（faq/brand 等，`search` 支持过滤），但 `rag_answer(question, provider)` **不支持 category 入参**——直播高频问题（价格/物流时效/真伪/口感/活动规则）与通用 FAQ 混在全库检索，召回噪声大。
- 建议：①`rag_answer` 增可选 `category: str = None`（默认 None 全库——既有调用方零变化）；②直播语料按 `category="live"` 录入（价格口径/当日活动规则/配送时效等运营维护）；③live_qa 传 category="live"，**检索空时自动回退全库**（fail-soft）。
- 验收：同问题 live 语料命中优先于全库泛答案；live 语料清空时行为与现状一致。

**L4 短答压缩层**
- 现状：rag_answer 答案面向客服场景，长度不控——直播弹幕口播/字幕需要 <50 字短答。
- 建议：answer 后加确定性压缩（取首句+关键数字，超长截断至句号；**禁止 LLM 摘要**——LLM 禁入关键路径）；`stats` 增 `truncatedRate` 观察压缩命中率。
- 验收：200 字答案压缩后 ≤50 字且以句号结尾；数字保留。

**L5 公开端点频控**
- 现状：`POST /api/live-qa/answer` 公开无限流——弹幕机器人可刷爆 rag/embedding 计算。
- 建议：按 `(roomId, IP)` 简单滑窗限流（如 10 次/分钟，超限返回固定话术并留痕 `rateLimited`）；IP 取值对齐项目既有取客户端 IP 的惯例。
- 验收：11 连发第 11 次被限流；不同 roomId 不互相影响。

### P2 · 治理与升格储备

**L6 语义变体归并**
- 现状：去重为精确匹配（`_norm_question` 压空白+lower）——「多少钱」与「这酒多少钱啊」是两次独立 rag 调用；`topQuestions` 同样按精确串计数。
- 建议：复用 knowledge 已缓存的 embedding，问题相似度 ≥0.9 归并同问（embedding 未启用/失败回退精确匹配——fail-soft）；topQuestions 升级为语义簇计数。
- 验收：两个变体问法第二次 cached=True；topQuestions 归并为一条。

**L7 LLM 影子轨（shadow）**
- 现状：provider 默认 rule，llm 轨存在但直播从未用。
- 建议：对 unsolved 问题在闲时批量跑 `provider="llm"` 对照留痕（shadow，不外发）——对齐 chat_upgrade 三态灰度范式；积累「rule 未答而 llm 能答」的差异样本，为未来 llm 轨升格提供依据。
- 验收：shadow 结果仅留痕（answer 返回仍为 rule 结果）；差异样本可在 stats 侧统计。

**L8 敏感词外置与留痕滚动**
- 现状：`LIVE_QA_SENSITIVE_WORDS` 硬编码（L59，9 竞品+8 违规）与 nexus74 六红线/品牌禁忌表双源；`live_qa.jsonl` 单文件无限增长。
- 建议：①敏感词表迁 env/配置段（或直接复用 nexus74 六红线引擎的词源）；②JSONL 按天滚动（`live_qa-YYYYMMDD.jsonl`）保留 30 天，超期清理。
- 验收：改词表不发版；30 天外文件被清理且 stats 不受影响（进程内为源）。

---

## 四、优先级总表

| 编号 | 建议 | 级别 | 依赖 | 工作量感 |
|---|---|---|---|---|
| R1 | repurchase 分布统计端点 | **P0** | — | 小 |
| R2 | 扫描调度挂载（默认关） | **P0** | — | 小 |
| L1 | unsolved 趋势分桶+阈值告警 | **P0** | — | 中 |
| L2 | 缺口拉取+转知识草稿 | **P0** | 既有 record_gap | 小 |
| R3 | 规格归一化周期 | P1 | 订单规格字段 | 小 |
| R4 | 送礼异常间隔剔除 | P1 | — | 小 |
| R5 | 分层建议书文案 | P1 | zk WAKEUP_TIERS 范式 | 小 |
| L3 | rag_answer category 过滤+live 语料 | P1 | rag_answer 扩参 | 中 |
| L4 | 短答压缩层 | P1 | — | 小 |
| L5 | 频控 | P1 | — | 小 |
| R6 | 批量确认 dispatch | P2 | — | 小 |
| R7 | qFactor 接入 IMTM（双写对照） | P2 | **R1 分布数据** | 中 |
| L6 | 语义变体归并 | P2 | embedding 启用 | 中 |
| L7 | LLM 影子轨 | P2 | — | 中 |
| L8 | 敏感词外置+留痕滚动 | P2 | — | 小 |

建议节奏：**P0 四件先行**（构成两模块各自的观察闭环，是后续一切 enforce/升格决策的数据前提）→ P1 按 R3/R4（精度偏差修正）与 L3（检索质量）优先 → P2 在 observe 数据积累 D+14 后随复盘决策。

---

## 五、铁律约束（所有建议的不可逾越项）

1. **永不自动触达**：R5/R6 的建议书与派发永远经人工确认闸（operator 必填）；L1 告警仅发管理员，不属于用户触达。
2. **LLM 禁入关键路径**：R 系全部确定性公式；L4 压缩禁 LLM 摘要；L7 影子轨仅留痕不外发。
3. **enforce 必有对照期**：R7 接入前必须先 observe 双写（fQUserCurrent vs Candidate）并经 D+14 分布复盘；任何 enforce 切换沿用 `multiplier_mode` 式三态闸。
4. **fail-soft 全覆盖**：R3 规格缺失、R7 留痕读不到、L3 live 语料空、L6 embedding 失败——一律回退现状行为，不崩不阻断。
5. **先审后显不破**：L2 转草稿必须走 knowledge 既有 review-publish 流，不新增任何绕过审核的发布路径。
