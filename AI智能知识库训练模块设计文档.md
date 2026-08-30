# AI智能知识库训练模块设计文档 v2.0

> **版本**: v2.0（P0~P3.6 全部已实现：RAG/Embedding/多模态视觉/抓取清洗/视频抽帧+ASR）
> **状态**: 十二期开发完成（P0~P2.5 + P3.1 RAG + P3.2 chat 接线 + P3 检索倒排索引 + P3.3 LLM 轨 + P3.4 消费方扩展 + P3.5 Embedding 语义检索 + 多模态 GLM-4V 视觉理解 + 抓取 LLM 智能清洗 + P3.6 视频抽帧+ASR 本地化）
> **定位**: 站点统一知识底座——通过对话教学、文档/图片/视频上传、全网抓取三源持续训练，经治理流水线（合规筛查/相似去重/人工审核/自动过审）沉淀为可检索、可进化、可分发的知识资产，供 chat/product/attract 等模块消费。

---

## 一、模块定位与设计原则

### 1.1 业务目标

通过与模块对话，上传资料、图片、提问问题，使本站知识库具备强大的准确和服务能力；同时具备全网抓取和筛选有利于本站成长的知识的能力（参照 AI 大模型训练范式创新设计，适应本站需求）。

### 1.2 设计原则

| 原则 | 说明 |
|---|---|
| **独立模块，不合并** | 知识训练/治理/进化是独立领域，chat 等模块仅作消费方调用检索 API |
| **AI智能优先 + 规则引擎兜底** | 合规筛查/品牌禁忌/医药加严为确定性规则；质量分/自动过审/分发为智能判定 |
| **双模式存储** | 内存（开发/测试）/ Redis（生产）透明切换，`STORE_MODE` 控制 |
| **纯标准库检索** | 字符 2-gram 稀疏向量 + 余弦相似度，中文友好，无外部 Embedding 依赖（P3 升级点） |
| **治理优先** | 一切来源的知识必须经流水线（合规/去重/审核）方可 published 被检索 |

### 1.3 对接模块（不合并）

| 模块 | 关系 |
|---|---|
| chat | 检索消费方（新库优先，旧FAQ兜底）；未命中回写缺口 |
| product | 消费方（P3.4 落地）：详情页附加 knowledgeBackground（经 distribution_suggest 拉取高质量条目） |
| attract | 复用其违禁词库做合规筛查；消费方（P3.4 落地）：内容生成注入知识素材 + knowledgeRefs 溯源 |
| message | 紧急缺口通知消费方（P2.5 落地） |

---

## 二、存储设计（repositories/knowledge_repository.py）

### 2.1 实体与 Redis 键（前缀 `zhuxiang:knowledge:`）

| 实体 | Redis 键 | 类型 | 说明 |
|---|---|---|---|
| 知识条目 entries | `entry:{id}` + `entry:index` + `entry:index:{status}` | string(JSON) + set | 治理流水线主实体 |
| 版本快照 versions | `version:{id}` | list | 每次发布留痕，支持审计回溯 |
| 知识缺口 gaps | `gap:{id}` + `gap:index` + `gap:index:{status}` | string + set | chat 未命中问题队列 |
| 教学会话 teach_sessions | `teach:{id}` + `teach:seq` | string + incr | 对话式教学载体 |
| 文档 documents | `doc:{id}` + `doc:seq` | string + incr | 上传文档元数据+分块统计 |
| 抓取种子源 crawl_sources | `crawl:source:{id}` + `crawl:source:seq` | string + incr | 白名单抓取源 |

序列号：`knowledge:seq` / `gap:seq`（Redis incr）。

### 2.2 条目核心字段

```
id, question, answer, keywords, category,
source(manual/chat_teaching/document/crawl/media/migration),
status(pending/approved/published/rejected/retired),
complianceScore, hitCount, missCount, qualityScore,
vector(内部字段, 对外投影剥离), version,
createdBy, reviewedBy(0=自动过审), publishedAt, createdAt, updatedAt
```

### 2.3 检索向量与倒排索引

- `tokenize`: 字符 2-gram 分词（中文友好，"竹香酒多少钱" → {竹香:1, 香酒:1, ...}）
- `build_vector`: question 为主 + keywords 加权 ×2
- `cosine`: 稀疏向量余弦相似度
- **倒排索引（P3 检索升级）**: token → entry_id（内存 dict[str, set] / Redis Set `zhuxiang:knowledge:inv:{token}`），仅 published 条目入索引；`save_entry` 时经 `_indexed_tokens`（随条目持久化）精确增删同步；`meta` 计数键跟踪索引条目数用于就绪判定（indexed == published）；索引未就绪（存量 Redis 数据）时 `search_published` 自动回退全量扫描，`POST /api/knowledge/search/rebuild-index`（管理端）显式重建
- 行为无损依据: 余弦>0 必有共同 token，索引召回与全量扫描等价
- `SEARCH_SCAN_LIMIT=2000`: 仅回退路径的规模保护（索引路径候选集不受限）

---

## 三、核心流程

### 3.1 治理流水线（P0）

```
创建(pending) ──审核──> approved ──发布──> published ──退役──> retired
   │                        │                        │
   └── 拒绝 ──> rejected    └── 版本快照留痕          └── 可查不可检索
                    │
                    └── 可编辑重提(回 pending)
```

**创建规则**（`create_entry`）:
- question/answer 非空
- 合规分 ≥70（复用 attract 违禁词库，100-30×命中数，违禁词直接拒绝入库）
- 品牌表述禁忌检查（D-17）：品牌名与浸泡/泡制/配制酒**断言式共存**拒绝，否定词（并非/不是/而非…）视为澄清放行
- 与既有条目（非 retired）相似度 <0.85（防重复，仅比 question 向量）

**品牌基准（D-17）**: `BRAND_BASELINE_ENTRIES` 3 条种子（酿造工艺/原料/浸泡澄清），幂等 published 入库。
> 本网产品事实：竹笋、竹茎、竹叶 + 国家级森林公园徂徕山富硒山泉水 + 专有菌群古法酿制（发酵型），**竹叶浸泡与基酒融合是本网大忌讳**。

**迁移（D-13）**: 旧 chat FAQ 一次性幂等迁移（source=migration，直接 published 跳过审核，重复与品牌禁忌跳过，旧表保留只读）。

### 3.2 统一检索（P0，消费方 API）

`search(query, category, top_k=5, min_similarity=0.10, record_hit=True)`:
- n-gram 向量余弦 top-k，低于 0.10 视为噪声不返回
- **计数口径（P2.5）**: 命中 → top-1 计 hitCount；未命中但有最近邻候选（低于置信阈值）→ 最近邻计 missCount（质量分命中率的数据来源）；无候选 → 不计数
- `record_hit=False` 供管理端测试检索（避免污染统计）

### 3.3 知识缺口队列（P0）

- `record_gap`: chat 未命中时调用，同问题（归一化文本）去重累计 askCount，全局锁保护
- `resolve_gap`: resolve（关联新条目）/ ignore
- **紧急缺口通知（P2.5）**: `notify_urgent_gaps()`——open 且 askCount≥3 且未提醒过（`urgentNotifiedAt` 幂等标记）的缺口，站内信（message.batch_send）通知全部启用状态管理员，接入质量调度器每轮扫描，best-effort 不阻断。**缺口→通知→教学 飞轮闭环**。

### 3.4 三源接入（P1）

| 源 | 入口 | 轨道 | 说明 |
|---|---|---|---|
| **对话式教学** | teach sessions + ask/submit | — | ask 检索已有知识作答（教学机会识别）；submit Q+A 入库(source=chat_teaching, pending) 并**自动 resolve 匹配的开放缺口**（余弦≥0.5） |
| **文档** | ingest_document | — | 分块器（按空行分段，超长段按句号二次切分，单块≤500字），自动生成问题（优先块内问句，否则标题+块首截断），逐块 pending |
| **图片** | ingest_image | rule（llm 轨 P3） | 管理员配描述（title+description+url+tags），一条 media 条目 |
| **视频** | ingest_video | rule（llm 轨 P3） | 时间轴分段为检索单元（一段一条，含时间码引用），desc 必填 |
| **全网抓取** | crawl sources + ingest/run | rule（llm 轨 P3） | 白名单制（种子源须指定主题域）；ingest=管理员粘贴正文；run=urllib 拉取 URL→提取正文→入库 |

**主题域过滤（D-15，五大域）**: wine(酒文化) / bamboo(竹子相关) / bamboo_culture(竹文化) / bamboo_med(竹医药) / brand(品牌文化)。内容未命中任何域拒绝入库。

**医药加严（D-15）**: 竹医药域内容禁用疗效断言词（治愈/根治/包治/疗效确切/抗癌/降三高/包好/神药）；典籍/文献可引用（标注出处放行）。

### 3.5 智能进化（P2）

**质量分**（`compute_quality_score`, 0-100）:
```
质量分 = 命中率×40% + 新鲜度×30% + 来源可信度×30%
- 命中率: hitCount/(hitCount+missCount), 零调用给 50(中性)
- 新鲜度: 90 天线性衰减(publishedAt 起算)
- 来源可信度: migration 0.9 > manual 0.75 > chat_teaching 0.65 > document/crawl 0.55 > media 0.5
```

**质量淘汰**（`quality_sweep`）: qualityScore<30 且发布超 60 天 → 降级 retired（知识过时）；其余刷新分数。**增量写入（P2.5）**: 分数未变且不退役的条目跳过重写（skipped 统计），避免每轮全量 save_entry。

**缺口摘要**（`gaps_summary`）: 高频缺口聚合（askCount 倒序）+ 主题域归属，驱动优先补知识。

**渐进信任自动过审（D-16）**（`auto_approve_run`）:
- 条件（全部满足）：来源（migration 除外）**最近 5 条审核决定全部通过**（P2.5 修正：按 updatedAt 倒序取最近 N 条，rejected 计入窗口并打断连胜）+ 来源历史条目平均质量分 ≥65 + 条目自身合规分 ≥80（高于人工线 70）
- 满足自动 approve（reviewedBy=0 标识），**仍需人工发布（保留发布权）**

**跨模块分发建议**（`distribution_suggest`）: 高质量分（≥60）+ 高命中 published 条目，按消费方主题偏好加权（product→产品类 / attract→品牌文化类 / chat→faq/order/policy/compliance），域外降权不排除。

### 3.6 后台调度器（P2）

`knowledge_quality_scheduler`（默认 6h 周期，全局限跑锁）:
- 质量淘汰 sweep + 自动过审 + **紧急缺口通知（P2.5 接入）**
- 环境开关: `KNOWLEDGE_QUALITY_AUTO=off` 关闭；`KNOWLEDGE_QUALITY_SCAN_INTERVAL=N`（下限 300s）

### 3.7 chat 消费链路

```
用户提问 → chat_service._search_knowledge
  → knowledge_svc.search(top_k=1, record_hit=True)   # 新库优先
  → 未命中 → _record_knowledge_gap → 缺口队列
  → 旧 chat_knowledge 关键词匹配兜底（迁移过渡期）
```
> P2.5 修复：此前调用参数 `limit=1` 与签名 `top_k` 不符，TypeError 被 best-effort 降级静默吞掉，新库检索从未生效。

---

## 四、API 端点（32 个 = 31 管理端 X-Role:admin + 1 公开）

| 分组 | 端点 | 方法 |
|---|---|---|
| 条目(5) | /api/knowledge/entries；/api/knowledge/entries/{id}；/api/knowledge/entries/{id}/versions | POST/GET/GET/PUT/GET |
| 治理(3) | /api/knowledge/entries/{id}/review；.../publish；.../retire | POST |
| 缺口(2) | /api/knowledge/gaps；/api/knowledge/gaps/{id}/resolve | GET/POST |
| 迁移/种子(2) | /api/knowledge/migrate-chat；/api/knowledge/seed-brand | POST |
| 统计/检索(2) | /api/knowledge/stats；/api/knowledge/search（**公开**） | GET/POST |
| 教学(4) | /api/knowledge/teach/sessions；.../{id}/ask；.../{id}/teach | POST/GET/POST/POST |
| 文档(2) | /api/knowledge/documents | POST/GET |
| 多模态(2) | /api/knowledge/media/image；/api/knowledge/media/video | POST |
| 抓取(4) | /api/knowledge/crawl/sources；.../ingest；.../run | POST/GET/POST/POST |
| P2(6) | /api/knowledge/quality/sweep；/quality/report；/gaps/summary；/auto-approve/run；/distribution/suggest；/quality/status | POST/GET/GET/POST/GET/GET |

异常约定（项目统一）: KeyError → 404 / ValueError → 409 / 其余 → 500。

---

## 五、关键决策记录

| 决策 | 内容 |
|---|---|
| D-13 | 旧 chat FAQ 一次性幂等迁移，旧表保留只读 |
| D-14 | 多模态双轨：rule 轨（管理员配描述/时间轴）先行，llm 轨（视觉大模型/抽帧+ASR）预留 P3 |
| D-15 | 抓取白名单制 + 五大主题域（酒文化/竹子相关/竹文化/竹医药/品牌文化）+ 医药疗效断言加严 |
| D-16 | 渐进信任自动过审：连胜 5 条 + 平均质量分 + 合规分≥80，保留人工发布权 |
| D-17 | 品牌基准知识与表述禁忌（浸泡/泡制/配制酒断言式表述拦截，否定词澄清放行） |
| D-18 | RAG 问答层：置信分级路由（direct≥0.55 / synthesized≥0.25 / unsolved）+ rule 轨融合生成 + 引用溯源 + provider 双轨（详见第九章） |

---

## 六、测试覆盖

| 文件 | 规模 | 覆盖 |
|---|---|---|
| test_knowledge_routes.py | 124 断言 | P0 治理/检索/缺口/迁移/统计 + P1 教学/文档/多模态/抓取（含 crawl/run 智能清洗 5 项） + P2 全部 6 项 + P2.5 修复 7 项 + P3.1 RAG 8 项 + P3 检索索引 8 项 + P3.3 LLM 轨 3 项 + P3.5 Embedding 7 项 + 多模态 llm 轨 6 项 + P3.6 抽帧+ASR 5 项（本地抽帧+ASR 时间轴 / 无 ffmpeg 降级 / ingest_video 本地轨生效+治理去重 / transcribe 未配置 key / transcribe 文件不存在；ffmpeg/下载/视觉/转写四重 mock 全链） |
| test_knowledge_quality_scheduler.py | 10 断言 | 单轮扫描/淘汰/保留/开关/周期下限/启动幂等/停止 |
| test_product_routes.py | 82 断言 | 产品 7 端点全量 + P3.4 知识背景 2 项（知识库空不阻断 / 品牌种子后附加 entryId/answer/qualityScore + 步骤日志） |
| test_attract_routes.py | 71 断言 | 引流 21 接口全量 + P3.4 知识注入 3 项（未命中回退硬编码 / detail 槽位注入 / knowledgeRefs 溯源） |

---

## 七、P3 规划

| 方向 | 内容 | 优先级 | 状态 |
|---|---|---|---|
| **RAG 问答层** | 检索 top-k 融合生成答案 + 引用条目 ID 溯源，对接 chat 智能接待引擎（详见第九章 D-18 设计） | 高 | **P3.1 + P3.2 已实施** |
| **检索升级** | **倒排索引 + Embedding 语义向量均已实施**：倒排索引（token→entry_id，Redis Set `zhuxiang:knowledge:inv:{token}`）突破 SEARCH_SCAN_LIMIT=2000 截断，仅加载共同 token 候选；**P3.5 Embedding 语义检索**（`KNOWLEDGE_EMBEDDING=on` 开启，默认 off）：`llm_client.embed()` 批量向量化（OpenAI 兼容 `/embeddings`，智谱 embedding-3），published 条目入库自动注入语义向量、rebuild-index 批量回填存量，检索时与全量含向量条目做稠密余弦——同义改写（字面无共同 token）也可命中；embed 失败/未回填自动回退 2-gram 路径 | 高 | **已实施** |
| **LLM 轨落地** | **RAG synthesize + 多模态视觉理解 + 抓取智能清洗 + 视频抽帧+ASR 全部已实施**：`services/llm_client.py`（urllib 纯标准库，chat/vision/embed/transcribe 四方法），`rag_answer(provider="llm")` synthesized 分支大模型合成（幻觉治理 prompt）；**多模态 llm 轨**：`ingest_image(provider="llm")` GLM-4V 图片描述自动生成、`ingest_video(provider="llm")` GLM-4V 视频时间轴自动生成（`KNOWLEDGE_MEDIA_LLM` 开关）；**抓取 llm 轨**：`crawl_run(provider="llm")` LLM 语义级去噪（`KNOWLEDGE_CRAWL_LLM` 开关）；**P3.6 视频抽帧+ASR 本地化**：GLM-4V 直接视频理解失败后的本地回退链——ffmpeg 抽关键帧（每 10s 一帧，上限 6 帧）→ 逐帧 GLM-4V 描述 + 音轨按 30s 分段 → `llm_client.transcribe()`（GLM-ASR，手工 multipart 纯标准库）转写 → 合成"画面/语音"时间轴；本地无 ffmpeg/下载失败优雅降级回退 rule 轨（`ASR_MODEL` 环境变量） | 中 | **已全部实施** |
| **消费方扩展** | **已实施（P3.4）**：product 详情页经 `distribution_suggest("product")` 拉取高质量知识附加 `knowledgeBackground` 字段（营销文案与品牌知识文本重叠低，按质量分拉取比按产品名检索更贴合语义）；attract 内容生成按选题 keywords 检索知识（sim≥0.25）注入 detail 槽位 + `knowledgeRefs` 溯源，未命中回退硬编码文案；均 best-effort（知识库异常不阻断主流程） | 中 | **已实施** |

---

## 八、迭代历史

| 阶段 | 日期 | 内容 |
|---|---|---|
| P0 知识底座 | 2026-08-30 | 治理流水线 + 统一检索 + 缺口队列 + FAQ 迁移（13 端点） |
| P1 三源接入 | 2026-08-30 | 对话教学 + 文档分块 + 多模态 rule 轨 + 白名单抓取（12 端点，D-14/D-15） |
| P2 智能进化 | 2026-08-30 | 质量淘汰/缺口摘要/渐进信任/分发建议/后台调度器（6 端点，D-16） |
| P2.5 数据闭环 | 2026-08-30 | chat 检索接线修复 / missCount 埋点 / KEYS→SCAN / sweep 增量化 / 连胜判定修正 / 缺口通知飞轮 |
| P3.1 RAG 核心 | 2026-08-30 | rag_answer（置信分级路由 direct≥0.50 实测校准 / synthesized≥0.25 / unsolved）+ rule 轨融合生成 + 引用溯源 + 计数联动 + POST /api/knowledge/ask 公开端点（33 端点） |
| P3.2 chat 接线 | 2026-08-30 | chat 消费 rag_answer（RAG 优先/旧 FAQ 兜底），回复透出 citations + ragMode，aiConfidence 动态化（RAG 相似度/legacy 固定 0.85） |
| P3 检索升级 | 2026-08-30 | 倒排索引（token→entry_id，`inv:{token}` Set + meta 计数），save_entry 同步/索引就绪判定/存量回退全量/rebuild 端点（34 端点），突破 2000 条截断 |
| P3.3 LLM 轨 | 2026-08-30 | services/llm_client.py（urllib OpenAI 兼容端点，LLM_API_KEY/BASE_URL/MODEL/TIMEOUT/ENABLED 环境变量），RAG synthesized 分支大模型合成 + 幻觉治理 prompt + 自动回退 rule |
| P3.4 消费方扩展 | 2026-08-30 | product 详情附加 knowledgeBackground（distribution_suggest 拉取，best-effort）；attract 生成链路知识注入 detail 槽位（sim≥0.25，截断 80 字）+ content 记录 knowledgeRefs 溯源（未命中回退硬编码，record_hit=False 计数口径归 chat） |
| P3.5 Embedding 语义向量 | 2026-08-30 | llm_client.embed()（OpenAI 兼容 /embeddings，EMBED_BATCH_SIZE=16 分批）；KNOWLEDGE_EMBEDDING 开关（默认 off）；published 入库自动注入语义向量 + rebuild-index 存量回填（embeddingBackfilled）；search/rag_answer 语义路径（稠密余弦全量比对，同义改写可命中），embed 失败/未回填自动回退 2-gram；真实智谱 embedding-3 验证：同义改写 direct 命中 conf 0.71-0.92（2-gram 同题 unsolved） |
| 多模态 LLM 视觉理解 | 2026-08-30 | llm_client.vision()（OpenAI 兼容 content 数组 image_url/video_url，GLM-4V）；ingest_image/ingest_video provider 双轨（llm 轨 GLM-4V 自动生成图片描述/视频时间轴 JSON+围栏容错，失败回退 rule 人工输入）；KNOWLEDGE_MEDIA_LLM 开关（默认 off）+ VISION_MODEL/LLM_VISION_TIMEOUT；入库仍走治理流水线（违禁词/去重/pending 审核）；真实智谱 glm-4v-flash 验证图片描述自动生成 |
| 抓取智能清洗 | 2026-08-30 | crawl_run provider 双轨（llm 轨在 extract_html_text 正则去标签基础上 LLM 语义级去噪提炼正文，输入截断 20000 字控成本，"无正文"哨兵出口；失败回退 rule）；KNOWLEDGE_CRAWL_LLM 开关（默认 off）；主题域过滤/医药加严/分块入库治理流程两轨完全一致；专用 CrawlRunRequest 模型；真实智谱 glm-4-flash 验证：导航/广告/版权噪声全去除、正文完整保留 |
| P3.6 视频抽帧+ASR | 2026-08-30 | llm_client.transcribe()（GLM-ASR 手工 multipart 纯标准库，ASR_MODEL 默认 glm-asr-2512，text/segments 双响应兼容）；_video_local_analyze 本地回退链：视频下载（≤50MB）→ ffmpeg 抽帧（每 10s，上限 6 帧）+ 音轨 30s 分段 → 逐帧 GLM-4V + 逐段 ASR → "画面/语音"时间轴；无 ffmpeg/下载失败/空结果优雅降级 None→rule 轨；真实验证：Windows TTS 生成中文语音 → GLM-ASR 转写正确（"竹香酒采用竹笋竹茎竹叶为原料利用专有菌群古法酿制"） |

---

## 九、P3.1 RAG 问答层详细设计（D-18）

> **决策编号**: D-18（2026-08-30 设计，已实施：P3.1 核心 + P3.2 chat 接线 + P3.3 llm 轨 + P3.5 语义检索路径均落地）
> **目标**: 从"FAQ 精确命中"（top-1 答案直接返回）升级为"检索增强问答"（top-k 召回 + 置信分级路由 + 融合生成 + 引用溯源），对齐成熟 RAG 范式，纯标准库 rule 轨先行，llm 轨已接入（P3.3）。

### 9.1 核心机制：置信分级路由

单一阈值（现状 min_similarity=0.10）无法区分"该直接引用"与"该融合补充"。RAG 入口按 top-1 相似度分三级路由：

```
                        ┌─ top1 ≥ 0.50 ──> direct（直接引用）
                        │   单条目精确命中，返回该条目答案 + 单条引用
 top-k 召回(k=3) ──> ───┤
                        ├─ 0.25 ≤ top1 < 0.50 ──> synthesized（融合生成）
                        │   多条目去重后融合，答案带 [n] 引用标注
                        │   （仅 1 条时退化为单条引用式融合）
                        │
                        ├─ 0.10 ≤ top1 < 0.25 ──> unsolved（低置信）
                        │   最近邻计 miss + 兜底回复（不融合——
                        │   低相似条目融合反而引入噪声）
                        └─ 无候选(余弦=0) ──> unsolved（无最近邻，不计 miss）
```

**阈值校准（实施实测）**: entry 向量含 keywords ×2 加权，完全同文本余弦约 0.51（低于 1.0），direct 阈值由设计值 0.55 校准为 **0.50**（恰好放行同文本命中，且测试验证改写问题落 synthesized）。

### 9.2 rule 轨融合生成算法（纯标准库）

```
输入: question, hits(top-k 条目, 含相似度)
1. 答案去重: 条目间问题向量余弦 ≥0.85 视为同义 → 保留相似度最高者
2. 排序: 按(相似度, hitCount)降序
3. 融合模板:
   开场: "关于「{question[:20]}」，为您整理以下信息："
   主体: 每条 "{n}. {answer}"（answer 截断 200 字，去尾部句号截断）
   收尾: "以上信息仅供参考，如需人工服务可联系在线客服。"
4. 引用溯源: citations = [{entryId, question, similarity, source}]
```

输出结构与 direct 共用：`{answer, mode, citations, confidence}`。

### 9.3 provider 双轨（对齐 attract D-11 / 知识库 D-14 惯例）

```python
async def rag_answer(self, question: str, provider: str = "rule") -> dict:
    # provider="llm"(P3.3 已接入): synthesized 分支经 llm_client
    # 以 top-k 为上下文大模型合成(幻觉治理 prompt + [n] 引用标注);
    # 未配置 key/请求失败自动回退 rule 轨 _rag_synthesize,
    # 检索/分级/引用溯源/计数联动两条轨道完全一致
```

llm 轨（P3.3 已实施）: top-k 条目作为 context 喂给大模型生成，system prompt 限定仅依据给定资料回答+引用标注保留（幻觉治理）；`services/llm_client.py`（urllib 纯标准库，OpenAI 兼容 `/chat/completions`，环境变量 `LLM_API_KEY`/`LLM_BASE_URL`/`LLM_MODEL`/`LLM_TIMEOUT`/`LLM_ENABLED`），未配置 key 或失败返回 None 由调用方回退 rule。

### 9.4 计数联动（沿用 P2.5 口径）

| mode | 计数 |
|---|---|
| direct / synthesized | top-1 条目计 hit |
| unsolved（有候选） | 最近邻计 miss |
| unsolved（无候选） | 不计数 |

`record_hit`/`record_miss` 复用既有 repository 方法，质量分命中率权重自然吃到 RAG 数据。

### 9.5 API 设计

**新增端点**: `POST /api/knowledge/ask`（**公开**，与 /search 同级——面向终端用户的问答统一入口）

```
请求: {"question": "...", "provider": "rule"(可选)}
响应: {
  "answer": "融合后的答案文本",
  "mode": "direct|synthesized|unsolved",
  "citations": [{"entryId": 1, "question": "...",
                 "similarity": 0.62, "source": "manual"}],
  "confidence": 0.85,        # direct: top1 相似度
                            # synthesized: top-k 平均
                            # unsolved: 0
  "gapRecorded": false       # unsolved 时自动 record_gap(chat 链路已有,
}                           #  ask 端点仅标记不重复记录——幂等约束)
```

### 9.6 chat 模块接线（P3.2，已实施；P3.3 llm 轨联动已实施）

```
_search_knowledge 升级(已实施):
  provider = KNOWLEDGE_CHAT_LLM 环境变量开关(on→llm 轨, 默认 off→rule 轨)
  rag = await knowledge_svc.rag_answer(user_content, provider=provider)
  → mode != unsolved: 返回 {answer(融合后), citations, confidence, ragMode}
  → unsolved: 走旧 FAQ 兜底 → 仍无 → 缺口飞轮(不变)

回复增强(已实施):
  aiConfidence 动态化: RAG 命中=相似度, 旧 FAQ 兜底=固定 0.85
  回复体透出 citations(前端可渲染"知识来源"角标) 与 ragMode
  (direct/synthesized/legacy, legacy=旧FAQ兜底)

P3.3 联动(已实施): KNOWLEDGE_CHAT_LLM=on 时 synthesized 态走
大模型合成; llm 轨不可用(key 失效/请求失败)自动回退 rule 轨,
行为与开关关闭一致——生产默认零成本, 按需开启。
```

chat 的转人工判定（unresolvedCount）逻辑不变。

### 9.7 实施排期

| 阶段 | 内容 | 状态 |
|---|---|---|
| **P3.1 RAG 核心** | rag_answer（分级路由+rule 轨融合+计数联动）+ /ask 端点 + 测试（direct/synthesized/unsolved 三态、去重、引用、计数、llm 轨回退 rule） | **✅ 已实施** |
| **P3.2 chat 接线** | chat_service 消费 rag_answer，回复带 citations + 动态置信度，chat 测试回归 | **✅ 已实施** |
| **P3.3 LLM 轨** | provider="llm" 接入大模型 synthesize，引用携带与幻觉治理 + chat 链路 KNOWLEDGE_CHAT_LLM 开关联动 | **✅ 已实施** |
| **P3.4 消费方扩展** | product 详情 knowledgeBackground + attract 生成链路知识注入与 knowledgeRefs 溯源，product/attract 测试回归 | **✅ 已实施** |
| **P3.5 Embedding 语义向量** | llm_client.embed 批量向量化 + 入库注入/存量回填 + search/rag_answer 语义路径（回退 2-gram），真实智谱端到端验证 | **✅ 已实施** |
| **P3.6 视频抽帧+ASR 本地化** | llm_client.transcribe（GLM-ASR 手工 multipart）+ _video_local_analyze 本地回退链（ffmpeg 抽帧+逐帧 GLM-4V+音轨分段 ASR），ingest_video 三级回退链，无 ffmpeg 优雅降级，真实 TTS→ASR 端到端验证 | **✅ 已实施** |

### 9.8 边界与约束

- **检索瓶颈已突破**: P3 倒排索引（token→entry_id 候选召回，突破 2000 条全量扫描截断）+ P3.5 Embedding 语义检索（同义改写可命中，稠密余弦全量比对不受截断）均已实施；两条路径以 KNOWLEDGE_EMBEDDING 开关切换，与 RAG 问答层两线正交可并行
- **不引入多轮对话**: 会话上下文管理属 chat 模块职责（其设计文档第三期），本期单轮问答
- **brand_taboo 合规兜底**: 融合答案拼接到条目答案原文，条目入库时已过品牌禁忌，RAG 层不重复校验（无新文本生成，rule 轨无幻觉；llm 轨接入时需增加输出侧校验）
- **缺口幂等**: ask 端点 unsolved 时不直接 record_gap（chat 链路已记录），避免同问题双计数
- **P3.5 阈值沿用与成本**: embedding 模式下 RAG 分级阈值（direct≥0.50/synthesized≥0.25）沿用 2-gram 校准值——语义相似度分布整体偏高（实测同义改写 0.7-0.9），生产开启后如发现 direct 过多可另行校准；每次检索一次 embed 调用（2048 维），默认 off 零成本，按需开启
