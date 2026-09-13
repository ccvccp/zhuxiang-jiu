# 74号·NexusFlow（智枢·流）AI智能全域发布大模型 全链交付总结

> 文档版本：v1.0 · 2026-09-13
> 规划方案：docs/74号_NexusFlow智枢流_AI智能全域发布大模型_创新规划方案.md
> 源文档：《NexusFlow (智枢·流).doc》（D:\网站文档\）
> 合并基线：72号·AI智能自动引流大模型（感知子系统——逻辑合并物理复用）
> 治理范式：71号（模式四档/免疫双保险/红队/46号审批链）+ 73号（观测面常开/快环门控/六域封闭注册表）

---

## 一、交付总览

| 维度 | 数据 |
|---|---|
| 分期数 | 6 期（P1 规则中枢 → P6 发布后复盘） |
| 服务层 | 6 文件（p1-p6_service + registry，~4200 行） |
| 数据表 | 14 表（前缀 nexus74） |
| API 端点 | 46 端点（/api/nexus74） |
| 专项断言 | 250 断言（50+39+48+35+39+39）全绿 |
| 对接平台 | 6 平台（微信公众号 A 档 API 直连 + 抖音/小红书/知乎/B站/头条 B 档半自动） |
| 酒类合规 | 六红线确定性引擎（R1-R6）+ safe_harbor 例外 + 警示语注入 |
| 生产状态 | 已部署 zxjiu.com（47.236.61.117），shadow 影子期运行中 |
| Git 提交 | 8 提交（6 期 + 1 热修 + 部署同步） |

---

## 二、六期交付明细

### P1 规则中枢与合规引擎（50 断言）
- **封闭注册表**：六平台域 + 适配器分级（A 档仅微信/B 档五平台）+ 意图四域 + 平台选择矩阵（意图×平台适配分查表）+ 规则四类型 + 合规四态 + 酒类六红线词表 + 法律高危子集（酒驾→legal_risk）+ 边界词/安全港/安全口感词 + 人设状态机三态 + 启动自检（14 组宪法校验）
- **确定性合规引擎**：四态优先级 legal_risk（法律子集或 R3+R5 组合）> block（任一硬红线）> review_required（边界无安全港或酒类缺警示语可修复）> pass（安全口感豁免）
- **规则库**：对象化 Schema 播种 10 条（六红线通用+平台专属，含置信度/法规依据/safe_harbor）
- **平台人格档案**：六平台种子（语气/句式/Emoji 密度/标签风格/默认人设）
- **警示语注入**：R6 修复（footer 显著位置/字号 12/幂等）

### P2 内容适配管线（39 断言）
- **源内容登记**：意图感知分类（资讯/教程/种草/观点）+ 关键词/素材标记
- **平台矩阵**：Top-N 确定性降序（seeding→小红书 0.95 首选）
- **确定性适配**：六平台标题模板（微信深度前缀/抖音钩子/小红书 Emoji+感叹+20 字截断/知乎问句/B站品类框/头条要点）+ 摘要模板 + 标签生成（前缀/上限）+ 人设话术包三态
- **合规前置**：block/legal_risk 拒绝适配 409；R6 可修复自动注入复检
- **交付位 MODE 门控**：off 409 / shadow 留痕不交付 / assist 交付

### P3 发布编排与自愈（48 断言）
- **三级适配器诚实工程**：A 档微信直连（无凭证→auth_expired 归因不伪造成功；DRYRUN 演练/FAILSIM 测试注入）；B 档五平台半自动（awaiting_manual+操作包+回执铁律公示）；C 档预留
- **发布状态机六态**：shadowed/awaiting_manual/published/rejected/throttled/failed
- **自愈重试**：归因分级（api_transient 指数退避 60→120→240→480，上限 3 满→switch_B_manual；content_violation 不可自动重试；auth_expired 配置或换档）
- **B 档回执登记**：数据诚实铁律（未登记视为未发布；状态机 awaiting_manual→终态；重复 409）
- **前置保护**：静默窗（北京夜间默认 23-7 可配置）+ 单平台每日封顶 3 + 影子期跳过
- **发布显式性铁律**：B 档 full 亦 awaiting_manual；auto 自主仅 full 档 A 档且 compliance pass

### P4 数据回流与学习进化（35 断言）
- **指标回流**：read/like/comment/share 四类 + 互动率公式（快照式更新）
- **形式学习**：平台×意图分组，样本≥3 且 avg≥0.15 正样本/<0.03 负样本/中性不留痕
- **审核回流**：passed/rejected/throttled 首次幂等 + rejected/throttled→负样本学习
- **负反馈强化**：同平台连续驳回≥2 → 46号 submit_change（kind=config，**pending 人工签核——进化不自动生效铁律**）；互斥降级不阻塞回流
- **46号档案**：batch 47 `nexus_publishing` 入册
- **全域汇总**：平台聚合+审核分布（观测面常开不受 MODE）

### P5 元认知收官（39 断言）
- **漂移三信号**（快环不受 MODE）：publish_anomaly（当日≥30）/ rejection_anomaly（驳回率>0.3 样本≥5）/ review_anomaly（复核率>0.4 样本≥5）
- **免疫双保险**：信号数≥2 自动冻结（保护方向）→ publish/retry 409 → 解冻须 NEXUSFLOW74_IMMUNITY=1 环境变量（人工专属，免疫自动永不解冻）
- **红队四向量**（含生产热修战果）：
  - RT-01 红线穿透：违规源适配→合规前置 409 拦截
  - RT-02 频次轰炸：伪造 3 篇当日→第 4 篇封顶熔断（伪造记录用后清理）
  - RT-03 越权直发：assist auto 拒绝 + **full B 档仍人工**
  - RT-04 回执伪造：A 档回执拒绝 + B 档重复登记拒绝
- **进化日志七类** + 模型状态完整版 + **转段脚本**（status/shadow/assist/full/off/verify + 免疫守卫 + .env 回滚保险）

### P6 发布后复盘（39 断言）
- **单篇复盘**：四维合成（指标/审核/合规/形式面）→ 结论五态（effective/neutral/ineffective/**blocked 优先于互动率**/pending_data）+ 确定性建议九码（模板拼接+数字插值）
- **分组复盘**：平台×意图聚合（终态数/互动率均值/状态与审核分布/最优最差）→ 组合策略三态 + 样本不足附加建议
- **关联学习**：形式学习（平台）+ 负样本（本篇）IDs 回链
- **复盘留痕**：scope 筛选 + 字典公示 + 可重复执行（重跑刷新）

---

## 三、五层神经中枢架构（源文档 V2.0 本站化落地）

| 层 | 源文档设计 | 本站实现 | 交付期 |
|---|---|---|---|
| ① 记忆与人设层 | 长期记忆库/动态人设状态机/跨平台身份映射 | 平台人格档案+人设状态机三态（professional/observer/companion 话术包） | P1/P2 |
| ② 感知与认知层 | 72号子系统+规则中枢 RAG | 意图感知+平台矩阵+**对象化规则库（Schema/例外/置信度/测试用例——向量库诚实降维为结构化检索）**+六红线引擎 | P1/P2 |
| ③ 决策与编排层 | Multi-Agent/预算感知 | 平台选择矩阵+适配管线+频次封顶/静默窗（**Multi-Agent 降维为确定性流水线——LLM 铁律调和**） | P2/P3 |
| ④ 执行与工具层 | 原生多模态/评论区干预 | 三级适配器诚实工程（A 档直连/B 档人工+回执/C 档预留）+ 自愈重试 | P3 |
| ⑤ 进化与对齐层 | RLAIF 在线强化 | 形式学习+负样本+46号审批链（**RLAIF 降维为阈值学习+人工签核——进化不自动生效**） | P4/P5/P6 |

---

## 四、铁律调和实录（源文档 vs 本站宪法）

| 源文档主张 | 本站铁律 | 调和结果 |
|---|---|---|
| LLM 生成内容（AIGC 核心） | LLM 禁入判定链 | 合规/适配/决策 100% 确定性（模板+词表+公式）；LLM 仅可选辅助改写（默认 off） |
| Multi-Agent 协作（策划/创作/审核/运营 Agent） | 决策可追溯 | 降维为四阶段确定性流水线，每阶段留痕 |
| RLAIF 在线权重更新 | 进化不自动生效 | 阈值学习留痕 + 46号审批链 pending 签核 |
| 向量数据库 RAG | 本站无向量库 | 对象化 Schema + 结构化关键词检索（保留例外条款/置信度/测试用例核心） |
| 全自动发布愿景 | 发布显式性 | B 档永远人工+回执；full 仅 A 档低风险域 |
| 源文档结语"人机协同最稳妥" | 天然契合 | 确定性引擎 90% + 人工创意与平台操作 10% |

---

## 五、数据模型（14 表，Redis 五清单序列化）

| 表 | 键前缀 | 期 | 用途 |
|---|---|---|---|
| nexus_personas | nexus:personas | P1 | 平台人格档案 |
| nexus_rules | nexus:rules | P1 | 合规规则库（对象化 Schema） |
| nexus_sources | nexus:sources | P2 | 源内容登记 |
| nexus_adaptations | nexus:adaptations | P2 | 适配版本（六平台模板） |
| nexus_publications | nexus:publications | P3 | 发布记录（状态机六态） |
| nexus_silence | nexus:silence | P3 | 静默窗配置 |
| nexus_metrics | nexus:metrics | P4 | 指标回流 |
| nexus_audits | nexus:audits | P4 | 审核回流 |
| nexus_learnings | nexus:learnings | P4 | 学习留痕（三类） |
| nexus_retrospects | nexus:retrospects | P6 | 复盘留痕 |
| nexus_immunity | nexus:immunity | P5 | 免疫冻结 |
| nexus_redteam | nexus:redteam | P5 | 红队批次 |
| nexus_evolution_log | nexus:evolog | P5 | 进化日志（七类） |
| nexus_seq | nexus:seq | 全期 | 发号器 |

---

## 六、API 清单（46 端点）

**P1（9）**：GET/POST /rules · GET /rules/{id} · POST /compliance/check · POST /compliance/scan · GET /compliance/dict · POST /warning/inject · GET /personas · GET /persona/state

**P2（7）**：POST /sources · GET /sources · GET /sources/{id} · GET /platform/matrix · POST /adapt · POST /adapt/batch · GET /adaptations

**P3（9）**：POST /publish · GET /publications · GET /publications/{id} · POST /publications/{id}/retry · POST /publications/{id}/receipt · GET /quota/status · POST /silence · GET /healthz · GET /model/status

**P4（4）**：POST /metrics/{id} · POST /metrics/audit · GET /metrics/summary · GET /learnings

**P5（9）**：POST /meta/drift · GET /immunity · POST /immunity/monitor · POST /immunity/freeze · POST /immunity/unfreeze · POST /redteam · GET /redteam/runs · GET /evolution/log · GET /model/status（完整版）

**P6（4）**：POST /retro/{id} · POST /retro/group · GET /retrospects · GET /retro/dict

**门控矩阵**：观测面（规则/人格/矩阵/汇总/复盘/漂移/免疫看板）常开不受 MODE；决策面（adapt/publish/retry/redteam）off 409；快环（drift/monitor）不受 MODE；回执登记不受 MODE（数据诚实优先）。

---

## 七、验证矩阵

| 验证维度 | 方法 | 结果 |
|---|---|---|
| 专项断言 | 6 期测试套件 | 250/250 全绿 |
| 源文档测试矩阵 | 酒类合规案例（姐妹们冲 block/酒驾 legal_risk/不上头+护肝 block/低度纯粮 pass/微醺 review/品鉴+警示语 pass） | 全部实证 |
| 全链回归 | P1-P6 互相回归（每期交付后） | 零破坏 |
| 既有模块回归 | 71/72/73号 + 前端 | 零破坏（每期断言） |
| Ruff lint | 全部交付文件 | 全绿 |
| 生产部署 | 三层验证（容器 env/本地 API/公网） | 全绿 |
| shadow 语义 | 全链演示（登记→适配→批量→发布留痕） | delivered=False/shadowed/externalId 空 |
| 红队周检 | 生产四向量（两批次） | allDefended×2 零污染 |
| 免疫机制 | 洪水造数→自动冻结→双保险解冻→复通 | 生产实证（P5 测试+73号热修验证） |

---

## 八、生产部署与运营史

| 时间（UTC） | 事件 |
|---|---|
| 2026-09-13 10:44 | 五期首部署（17 文件，off 观测期） |
| 2026-09-13 10:49 | **shadow 影子期开启**（转段脚本+回滚保险件） |
| 2026-09-13 10:53 | 首次红队周检——**抓出 redteam seq 键通配符相撞缺陷**（74号现症+73号潜在），热修部署（isdigit 尾键过滤范式），二次红队全防御 |
| 2026-09-13 11:21 | P6 复盘引擎部署（shadow 保持，shadowed 状态守卫生产实证） |

**当前状态**：mode=shadow · 免疫 active · 影子期起点 2026-09-13T10:49:32Z → **assist 转段资格日 ≥ 2026-09-20**

**运营工具**：
- 转段脚本 `backend/scripts/nexus74_transfer.sh`（status/shadow/assist/full/off/verify）
- 回滚保险 `.env.bak-nexus74-shadow`（一键 off 恢复原件）
- 红队周检 `POST /api/nexus74/redteam`（每周；四向量+数据清理）
- 漂移巡检 `POST /api/nexus74/meta/drift`（每日；三信号应空）

---

## 九、缺陷台账（过程修复实录）

| # | 缺陷 | 根因 | 修复 | 发现期 |
|---|---|---|---|---|
| 1 | f-string 跨行断链（合规拒绝消息） | 列表推导内嵌 f-string 断行 | 预计算 hit_labels | P2 |
| 2 | 全局异常处理器包装 {success,error} | 断言误用 detail 字段 | 断言改 error | P2 |
| 3 | Starlette 查询串 + 解码为空格 | ISO 时刻 URL 直传 | qn() %2B 编码助手 | P3 |
| 4 | B 档 auto 语义误伤（越权检查未分档） | A 档前置检查未限定 tier | tier=="A" 限定 | P5 回归 |
| 5 | **Redis redteam seq 键通配符相撞（500）** | 表尾名与 seq 实体同名（仅 redteam 单复数同形） | isdigit 尾键过滤+isinstance 双保险（73号潜在同修） | **生产周检** |
| 6 | FastAPI 注册序（/metrics/audit 被 {id} 拦截 422） | 参数路径先注册 | 固定路径前移（P6 沿用） | P4 |
| 7 | 建议字典闭括号缺失 ×2 | 字符串内 ")" 干扰计数 | 补齐 | P6 |
| 8 | 链式比较 bool 传 all() | 断言写法 | 列表排序比较 | P6 |

**教训沉淀**：① Redis 通配符扫描必须过滤 seq 键（71号范式已证，新模块仓储须沿用）；② FastAPI 固定路径先于参数路径注册；③ 全局异常包装体断言用 error 字段；④ 每期交付跑全链回归（本项捕获 #4）。

---

## 十、运营约定与下一步

**转段路径**：off → shadow（当前）→ [≥7 天+评估签核] → assist（适配包人工确认位+B 档发布）→ [稳定≥7 天+红队周检] → full 评估（仅 A 档低风险自主）。

**assist 期闭环**：源登记 → 适配（人工确认位）→ B 档发布（人工操作）→ 回执登记 → 指标回流 → **单篇/分组复盘**（P6 就绪）→ 形式学习/负样本 → 46号审批链。

**后续可选**：A 档微信真实凭证接入（WECHAT_MP_APPID 配置后 healthz→ready）；C 档视频剪辑预留；前端发布工作台（管理端 UI 消费 46 端点）。

---

*本总结遵循源文档"人机协同"哲学：确定性引擎承担合规保障与策略执行，人工聚焦创意原点与平台操作确认——Nexus（智枢）连接内容与平台，Flow（流）让内容顺势而流。*
