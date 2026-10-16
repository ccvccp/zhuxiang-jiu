# 小竹「Agent AI 升级规划」与现有实现对照分析

> 投喂日期：2026-10-16　|　蓝图：《小竹 Agent AI 升级规划与架构设计方案》（四层架构「脑体魂」+ Multi-Agent/MCP/RAG/数据飞轮 + Phase 1-3 路线图 + SRE 风险章）
> 对照方法：与前两份蓝图（IMTM/商城 A-Commerce）同法——小竹族全源码摸底（xiaozhu48/joyvoice78/voice50·82·83/agent75/knowledge/turnFeedback 回流/TTS 参数面）后逐项映射，本研判不改代码。

---

## 一、概要与研判结论

**总判断：三份蓝图一脉相承的结论再次成立——小竹不是「待升级的语音助手」，而是一座已按 Agent 纪律建成大半的智能体；蓝图的正确用法是用它的四层架构语言重组既有件，主攻两个真正的空位：动态 Persona 引擎与跨模块统一记忆层。**

1. **蓝图 Phase 1（Copilot）完成度 100% 且超配**：工具调用（FC 注册表 24 条 + 三重校验管道 + 沙箱白名单 + 幂等窗 + 冷静期）、RAG 全站问答（knowledge 向量/双路/语义缓存 + 商品事实注入强制据实答）、身份识别与角色路由（member↔trustId 信值绑定 + 等级话术 + 偏好重排）全部在生产运行——且带蓝图未设想的**隐私预算原子扣减、explainability_ref、PII 脱敏、音频即转即删**四重合规纵深。
2. **Phase 2（Agent）完成度约六成**：蓝图引以为傲的三个机制项目已有**生产级同构实现且更细**——「操作确认卡片」↔4 位屏幕确认码（双因子：60s 一次性 consentToken + 声纹绑定）、「权限沙箱」↔FC tier 三级 + executor 白名单（import 期自检不一致拒绝启动）、「情绪计算」↔78号五处实质反哺（话术变体/LLM 上下文行/留痕/**语速系数入 TTS 预合成缓存键**/延迟观测）。缺口集中在：四角色 Sub-Agent 拓扑、动态 Persona 引擎（画像止步于话术前缀与商品重排，**不进 LLM prompt**）、情绪→音色路由、跨会话长期记忆注入。
3. **Phase 3（Super Agent）完成度约四成但数据燃料全在**：数据飞轮多轨并进（👎自动入学习队列→人工采纳 ASR 误听表、失败挖掘三分类、voice82 四维 reward、v92 无感评分流）、知识库自更新已自动化（6h 定时爬虫 + 合规分≥80 auto_publish + 质量淘汰降级）、影子双跑（agent75 shadows 同输入后台对照只记 metrics 零污染）。缺的是：Self-Play 红蓝对抗、微调流水线（LoRA/DPO）、**灰度达标后的自动 promote**（82号 assist 默认关、门槛已设——进化机制建好了但没开闸）。
4. **与商城蓝图在 MCP 上汇合**：小竹 FC 注册表本质是「私有协议的 MCP」；包一层标准 MCP server 面即可同时服务外部超级入口（微信/车机）与 agent75 调度——两份蓝图在此同一件事上收敛，合并推进。
5. **技术栈选型大面积不采信**（单机自研纪律，同 IMTM 的 K8s 否决逻辑）：LangGraph↔agent75 自研编排已同构、Milvus/Qdrant↔自研 embedding 缓存规模够用、Neo4j↔无图谱需求、LangSmith↔track 分轨+voice48_fc_audit+traceId 贯穿已覆盖观测诉求。

---

## 二、逐项对照矩阵（按蓝图四层）

### 2.1 感知与路由层（「眼耳与神经」）

| # | 蓝图项 | 现有实现 | 判定 | 差距 |
|---|---|---|---|---|
| 1 | 多模态融合（语音/文本/图像/UI上下文） | 语音 `voiceTurn`（原生录音 mp3→ASR）+ 文本 `sendText` 同链免唤醒窗 + G3 视觉轨商品图理解 + 屏幕状态（confirmOrder 4 位码页面契约） | **A 已有** | 图像输入限于商品图域，通用拍照识物未开放 |
| 2 | 意图与情绪计算 | 78号 JoyVoice：`detect_user_mood` 词表三态（negative/hesitant/positive）+ 播报侧 care/steady + **五处反哺**（兜底轮话术变体/LLM mood_context_line/turn 留痕/MOOD_SPEED 语速系数乘入 TTS 预合成/延迟观测 mood 维度） | **A 超预期**（蓝图以为要新建，实际已在产） | 音频特征级情绪（语气/语速/声纹→情绪）无——当前情绪源是文本词表 |
| 3 | 智能路由器（按角色×复杂度分发） | 角色感知：member↔trustId 读 45 号信值、等级 1-5、偏好标签；复杂度分档：G2 深浅信号 `_deep_model_for` + LLM 深档 6s/快速档 2.5s 超时分流；意图：规则轨优先→LLM 兜底（意图缓存 3600s/300s） | **B 部分有** | 蓝图的「B端商家/运营/客服」角色路由无——当前路由维度只有会员等级，无岗位角色轴 |

### 2.2 认知大脑层（「思考与记忆」）

| # | 蓝图项 | 现有实现 | 判定 | 差距 |
|---|---|---|---|---|
| 4 | 动态 Persona 引擎（画像+情绪→实时组装 System Prompt） | **同构范式已有但未 Persona 化**：`context_desc` 拼装（最近 5 轮 + 情绪行 + 商品事实注入 + 指令白名单 + 已用指令聚合）；等级只作用于 `LEVEL_TITLES` 话术前缀与 `_pref_score` 商品重排（带 preferenceApplied 防茧房标记）——**画像不进 LLM prompt** | **B 范式有、引擎无** | 主攻位①：画像三因子（等级/偏好/情绪）+角色轴进 context_desc 组装为 persona 行——有 mood_context_line 直接先例，改造面小 |
| 5 | 双路记忆（短期 Working / 长期向量记忆） | 短期：会话内 5 轮上下文 + 指代消解 + 已用指令聚合；长期：偏好标签（近 30 单 Counter most_common 3）+ voice82 画像三表（role/dialog/biz）——**但跨会话记忆不注入对话** | **B 半有** | 主攻位②：voice82 画像三表与 preferenceTags 统一为跨会话记忆层，按会话注入（82号 off 默认下先 observe 留痕） |
| 6 | 规划与反思（任务拆解/自我纠错） | agent75：Planner→Executor→Critic 基线评审 + 三级兜底链（技能→prompt_tool→直连）+ 挫败安抚前缀；xiaozhu 复杂任务无自主拆解（指令直达八类+FC 单步） | **B 骨架有** | 多步任务编排（查库存→算利润→写文案类链式）无——技能面扩容后自然获得 |

### 2.3 调度与执行层（「手与脚」）

| # | 蓝图项 | 现有实现 | 判定 | 差距 |
|---|---|---|---|---|
| 7 | MCP 标准接入（全站 API 封装为标准 Tool） | FC 注册表 24 条（operationId/tier/privacyCost/consentPhrase/safeMessage + **描述即护栏**：❌禁令内嵌）+ 网关三重校验（consentToken 60s 一次性 + 隐私预算原子扣减 + explainability 必填）+ executor 沙箱白名单（`_validate_registry` import 期自检）+ 审计流水 voice48_fc_audit | **A 私有协议版超配** | 标准协议面：包 MCP server 层对外（与商城蓝图 P4 汇合）——内部注册表不动 |
| 8 | RPA 与 UI 自动化 | channels-bot 全族（B站/抖音/快手/小红书/头条/西瓜 RPA 发布机器人）+ sv73 发布队列 rpa_pending + xhs-bot 21 轮全自动 | **A 已有**（用于内容发布域） | 小竹会话内触发 RPA 的桥接无（语音→RPA 任务下发） |
| 9 | 权限沙箱（RBAC 限 Tool 范围） | SAFE_READONLY 22 / SAFE_WRITE / SENSITIVE 4 三级 + 高敏冷静期 600s×3 次 + 幂等窗 10s + **行级：order.query 只查本会话会员**（member_id_hint 链路） | **A 已有超配** | B端/运营角色的 Tool 面未划分（当前 FC 面只服务 C 端会员） |

### 2.4 自主进化层（「灵魂与成长」）

| # | 蓝图项 | 现有实现 | 判定 | 差距 |
|---|---|---|---|---|
| 10 | 数据飞轮（成功/失败对话自动收集） | 👎 自动入学习队列（learn_enqueue→learn_suggest LLM 只产建议→learn_adopt 人工采纳）+ 失败挖掘三分类（fallback/repeat/negative，`_mine_failure`）+ v92 无感评分流（intent/track/latencyMs 逐轮）+ dashboard 负反馈 top intent | **A 已有** | 信号→训练数据管道止于人工环（见 #12） |
| 11 | 知识库自更新（自动抓取清洗入库） | knowledge_crawl_scheduler 6h 定时（81号 run_gated 闸门）+ 七域相关性过滤 + 合规分≥80 auto_publish（高于人工线 70，走完整 review→publish 治理链）+ 质量巡检 6h 淘汰降级 + 渐进信任自动过审 | **A 已有**（蓝图「无需人工干预」项目的安全版——80 分门槛+合规链保留） | 无（此项目实现比蓝图更稳） |
| 12 | Bad Case 挖掘与微调（Teacher 评估/LoRA/DPO） | LLM 建议轨（XIAOZHU_LEARN_LLM 默认 off，红线「LLM 只产建议不直接入表」）+ agent75 影子双跑（testing 技能同输入对照只记 metrics）+ 淘汰线（invocations≥50 成功率<0.6 降级） | **B 数据燃料全在，流水线无** | DPO 数据集构建可先行（👍/👎+转人工信号归集）；LoRA/DPO 微调受算力约束列远期 |
| 13 | 对抗生成（Self-Play 红蓝对抗） | 无正式机制；近亲：xiaozhu_fc_redteam（FC 红队测试）+ zyh 韧性压力推演 | **C 全新** | 可复用 FC redteam 形态扩为 7x24 自动压测（P4） |
| 14 | Tool 使用反思（失败→避坑指南入记忆） | code_tool 失败三级兜底链 + 全程审计留痕；**避坑记忆无**（失败不反哺下次决策） | **C 全新** | 小改造：失败签名→「工具避坑表」留痕，调用前查表（P3） |

### 2.5 核心能力方案（蓝图第二节）

| # | 蓝图项 | 现有实现 | 判定 |
|---|---|---|---|
| 15 | 千人千面（动态 Prompt+情感 TTS） | 话术前缀（等级称呼）+ 偏好重排 + 情绪语速（MOOD_SPEED 三档入 TTS 缓存键）+ JoyVoice 六档音色档案（`/voices` 端点策展）；**画像不进 prompt、情绪不切音色** | **B**（语速已自适应，persona/音色两轴待接） |
| 16 | 四角色 Sub-Agent（Shopping/Business/Operation/CS） | 角色轴只有 C 端会员分层；agent75 技能面 5 件内置（classify/answer×2/draft/critic）、slot 三类；**模型池两档同底座**（glm-flash temp0.1/glm-reason temp0.4，分级路由机制就绪未真分家） | **C 拓扑全新、骨架现成** |
| 17 | 高危操作确认卡（Human-in-the-loop） | 4 位屏幕确认码（高敏执行标配）+ consentToken 双因子 + 冷静期 + AGENT75_KILL/XIAOZHU_MODE 一键回退 | **A 超配** |
| 18 | 死循环熔断（max_iterations/timeout） | LLM 超时快失败（2.5s/6s）+ hook_timeout 熔断轮 + 意图缓存 + executor 冷静期；agent75 任务级 timeout 未设步数上限 | **B**（单步护栏全，多步循环上限无——随 #6 多步编排一起补） |

---

## 三、采信分级

- **A 级（已有/超配，直接以蓝图语言重新包装）**：#1 多模态、#2 情绪计算、#7 工具调用、#9 权限沙箱、#10 数据飞轮、#11 知识自更新、#17 确认卡——7 项。**对外叙事可用蓝图原文描述现状**。
- **B 级（范式有、需补装成引擎）**：#3 角色路由（加岗位轴）、#4 Persona 引擎（画像进 prompt）、#5 长期记忆（三表统一注入）、#6 规划反思（技能面扩容）、#8 语音→RPA 桥、#12 微调数据准备、#15 千人千面补两轴、#18 多步熔断——8 项，**排期主力**。
- **C 级（全新）**：#13 Self-Play、#14 工具避坑记忆、#16 四角色 Sub-Agent 拓扑——3 项（#16 是 Phase 2 主体工程）。
- **不采信（单机自研纪律三连否决）**：LangGraph/AutoGen 替换 agent75、Milvus/Qdrant/Neo4j 引入、LangSmith/Phoenix 引入、全自动知识发布（80 分门槛与治理链保留）、近中期任何模型微调上线。

---

## 四、四层架构映射（蓝图 ↔ 本站）

```
感知路由层   ←→  唤醒三轨+EMA shadow / ASR误听自学习+热词 / 规则轨优先LLM兜底
                 78号情绪五处反哺 / G2深浅分档+超时快失败 / LAT四段+voice83 SLA
认知大脑层   ←→  context_desc 拼装范式(5轮+情绪行+商品事实+指令白名单)
                 偏好标签重排(防茧房) / voice82画像三表 ——缺: persona引擎+跨会话注入
调度执行层   ←→  FC注册表24条+三重校验+沙箱自检 / 4位确认码+consentToken双因子
                 agent75技能slot+code_tool三级兜底+影子双跑 / channels-bot RPA族
自主进化层   ←→  👎学习队列(人工采纳环) / 失败挖掘三分类 / v92无感评分
                 knowledge 6h爬虫+80分auto_publish+质量淘汰 / 82号off/shadow/assist
                 ——缺: 自动promote开闸决策 / 避坑记忆 / Self-Play
```

三条项目红线与蓝图风险章的对应：**「LLM 不产数字」**↔蓝图幻觉高危应对（实现于 classify prompt 封闭枚举+绝不提金额+据实答）；**「进化不自动上线」**↔蓝图 Human-in-the-loop（learn_adopt/82号门槛/技能审批）；**「kill-switch 常备」**↔蓝图成本失控应对（AGENT75_KILL/XIAOZHU_MODE/各 MODE 闸）。**蓝图想建的安全层，项目已按更细粒度建成。**

---

## 五、差距与排期建议（对齐蓝图 Phase 1-3，按实际重排）

**P2·近期（纯软件小改造，与既有灰度节奏同批）**
1. **动态 Persona 引擎最小版（#4/#15）**：等级/偏好/情绪三因子组装 persona 行进 `context_desc`（mood_context_line 直接先例）；LLM 轨默认 off 下先在规则轨话术层验证分层效果。验收：同问题不同等级/情绪会员话术差异可断言。
2. **情绪→音色路由（#15）**：JoyVoice 六档档案 + MOOD_SPEED 语速已有，补「情绪→voice」路由表，observe 态只留痕 `wouldVoice`（对齐 wake EMA 的 emaWould* 范式）。
3. **agent75 技能面扩容（#6 前置）**：xiaozhu FC 24 action 与 agent75 技能注册表打通（技能面 5→20+），复用 slot 生命周期与淘汰线。

**P3·中期（Phase 2 主体）**
4. **四角色 Sub-Agent 拓扑（#16）**：以 agent75 为 Router Agent，按角色（C端/B端商家/运营/客服）划分技能面与 FC tier 权限；B 端 Tool 面（商家后台/Text2SQL/改价）逐步开。**每角色独立 kill-switch**。
5. **跨会话长期记忆注入（#5）**：voice82 画像三表 + preferenceTags 统一记忆层，会话启动注入（82号 assist 语义，observe 先行）。
6. **多步编排与步数熔断（#6/#18）**：技能链式调用（拆解子任务）+ max_steps 硬上限超限转人工告警（对齐 L1 告警形态）。
7. **工具避坑记忆（#14）**：code_tool/FC 失败签名→避坑表，调用前查表短路。

**P4·远期（Phase 3 与两蓝图汇合处）**
8. **MCP server 面（#7 外联）**：FC 注册表包标准 MCP 协议层，服务外部超级入口与 agent75 调度（与商城蓝图 V4.0 同一件事，合并推进；先只读工具面）。
9. **82号 assist 开闸**：shadow≥200 会话且正反馈≥0.55 门槛达标后，按 observe→assist 灰度推进（**进化机制已建成，缺的只是开闸决策与数据**）。
10. **DPO 数据集构建（#12 数据面）**：👍/👎+转人工信号归集为偏好对（只备数据不微调）；**Self-Play 压测（#13）**：FC redteam 扩为定时自动红蓝对抗（发现盲区→缺口队列→草稿闭环，复用 L2 机制）。

---

## 六、结论

蓝图把小竹的升级叙述为「从工具到数字员工的范式跃迁」，但源码摸底显示：**这场跃迁的前半程（Copilot 期全部 + Agent 期六成）已按更严格的工程纪律完成**——蓝图的 MCP 对应 FC 三重校验、确认卡对应 4 位码、数据飞轮对应学习队列、知识自更新对应 6h 爬虫治理链，且每一处都多了隐私预算/审计/kill-switch 三件保险。真正的跃迁剩余量精确定位在两处：**认知层的 persona 化与记忆统一**（让画像从排序层进入表达层）、**进化层的开闸与对抗**（让已建成的 82号灰度与影子机制从 off 走向 assist）。技术栈替换类建议（LangGraph/Milvus/Neo4j/LangSmith）全部不采信——单机自研栈在当前规模下是更优解，这一判断与 IMTM、商城两份研判保持一致。
