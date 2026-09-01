# AI智能中枢模块设计文档（35号·全站AI大模型总调度）

> 版本: v1.0（2026-09-01）
> 状态: 设计定稿，待 P0 开发
> 模块编号: **35**
> 模块名: **AI智能中枢模块（AI Hub）**——统一AI智能入口 + 多模态交互引擎 + 全站AI能力编排中枢

---

## 1. 定位与命名说明

### 1.1 命名冲突说明（重要）

本站已有两个名字相近的模块，新模块**必须避开**：

| 已有模块 | 占用名称 | 实际职责 |
|---|---|---|
| role 模块（v1.2，2026-08-29 交付） | "AI智能管理模块" | 全站**角色经济中枢**：认领/契约/分润/信用 |
| perm 模块（33号） | "权限AI智能管理模块" | 权限管理 |

用户需求描述的"利用AI大模型优势、智能自主学习分析本站运维及服务、统筹调配本站各AI智能模块、训练和管理全网AI智能能力、多场景输入（文字/语音）"——其本质是**全站AI大模型的中枢调度器 + 统一交互入口**，与上述两个模块职责完全不同。故本模块命名为：

> **35号·AI智能中枢模块（竹香AI·AI Hub）**

一句话定位：

> **AI智能中枢 = 全站AI能力的"操作系统"：向下注册并编排所有AI模块（17个评分器/RAG/ASR/视觉/监管大脑/引流…），向上提供唯一的多模态智能入口（文字/语音/图片），对内承担AI能力的训练、监控与健康治理。**

### 1.2 设计原则

1. **不重复建设**：ASR/RAG/视觉/自学习均已有实现，本模块做"编排与暴露"，不重写算法。
2. **模型提动作，规则定执行**：延续模块29确立的全站AI原则——LLM 只产建议，规则引擎定执行。
3. **入口极简**：任何AI能力 ≤3 次点击可达（对标微信"发现页"两跳原则）；角色进入零学习成本。
4. **优雅降级**：所有 LLM 轨失败自动回退规则轨（延续 `llm_client` 的 None 回退惯例）。

---

## 2. 现状盘点（本模块的四大空白与四大地基）

### 2.1 空白（本模块要补的）

| # | 空白 | 现状证据 |
|---|---|---|
| G1 | **无统一AI入口** | `chat-widget.js` 纯文本输入，无语音/图片；各AI能力散落在 knowledge-dashboard 等独立页面 |
| G2 | **ASR 未暴露** | `llm_client.transcribe()` 已实现但仅内嵌于知识库视频入库回退链，无 `/asr` 端点 |
| G3 | **无真实编排** | decision 模块 `/orchestrate`、`/capability-route` 为硬编码模拟（fabricated 任务列表、写死 95%/1.2s） |
| G4 | **AI能力无统一治理视图** | LLM 成功率/回退率散在 `/metrics`，17 个评分器健康无集中看板 |

### 2.2 地基（直接复用）

| 地基 | 位置 | 复用方式 |
|---|---|---|
| GLM-ASR 语音识别 | `services/llm_client.py: transcribe()` | 暴露为 `/api/hub/asr`，供前端按住说话 |
| GLM-4V 视觉理解 | `llm_client.vision()` | 图片输入走 knowledge media 链路 |
| RAG 知识底座 | knowledge_service（direct/synthesized/legacy 三轨） | 意图命中知识类后转交 |
| 17 评分器自学习 | `ai_learning_scheduler` + `SCORER_REGISTRY`（3批） | 编入能力注册表，纳入训练管理 |

---

## 3. 对标成熟平台分析

| 平台 | 可借鉴模式 | 本站适配 |
|---|---|---|
| **微信** | ① 按住说话（press-to-talk）+ 松手转文字预览 ② 会话列表聚合 ③ "智能助手"统一服务入口 | 语音输入交互范式；AI入口聚合各模块服务 |
| **淘宝小蜜** | ① 多模态输入条（文字/语音/拍照）② 意图识别→路由到订单/售后/物流技能组 ③ AI解决率看板 | 意图路由器设计；AI Ops 指标 |
| **抖音** | ① 搜索框语音图标 ② 全屏沉浸式交互 ③ 底部常驻AI助手（豆包） | 入口常驻化、界面极简 |
| **ChatGPT App** | ① 输入条三键：文本框+麦克风+相机 ② 流式输出 ③ 多模态统一进一个会话流 | **输入条三键布局**（本模块前端核心） |
| **Coze/Dify** | ① 插件(能力)注册表 ② 工作流编排 ③ 能力健康监控 | AI能力注册表与编排中枢的架构参照 |

**创新点**（区别于单纯照搬）：将"角色经济中枢(role)"的分润体系与AI编排结合——**AI入口按角色展示差异化能力面板**（会员见导购/售后，客服见工单/分润，管理员见AI治理），实现"一次对话入口，千人千面能力"。

---

## 4. 总体架构

```
┌─────────────────────────────────────────────────────────────┐
│                     统一AI智能入口（前端）                     │
│   竹香AI 助手条: [文本框] [🎤按住说话] [📷图片] [＋更多]        │
│   角色自适应面板: 会员 / 客服 / 管理员 / 工人                  │
└──────────────────────┬──────────────────────────────────────┘
                       │ WebSocket/REST
┌──────────────────────▼──────────────────────────────────────┐
│              多模态输入引擎（Input Engine）                     │
│  文本 → 意图分类器(LLM轨/规则轨)                              │
│  语音 → ASR转写 → 文本轨（保留原始音频URL）                     │
│  图片 → GLM-4V描述 → 视觉意图轨                               │
└──────────────────────┬──────────────────────────────────────┘
                       │ 统一意图 IntentRequest
┌──────────────────────▼──────────────────────────────────────┐
│           意图路由器 + AI能力编排中枢（Orchestrator）            │
│  能力注册表(Capability Registry) ← 动态注册全站AI能力           │
│  路由策略: 意图×角色×健康度×成本 → 目标模块                      │
│  扇出/聚合: 复合任务拆解为多个能力调用并聚合结果                  │
└──────┬───────────┬───────────┬───────────┬──────────────────┘
       ▼           ▼           ▼           ▼
   knowledge    chat/role   ai_learning  其他模块
   (RAG问答)   (工单分润)   (评分器训练)  (产品/物流...)
       │           │           │           │
       └───────────┴─────┬─────┴───────────┘
                         ▼
┌─────────────────────────────────────────────────────────────┐
│              AI训练与治理（AI Ops & Training）                 │
│  能力健康监控(成功率/延迟/回退率) · 学习周期管理 · 治理看板      │
└─────────────────────────────────────────────────────────────┘
```

三大引擎 + 一个治理面：

1. **统一AI智能入口**（前端）：多模态输入条 + 角色自适应能力面板
2. **多模态输入引擎**（后端 P0）：ASR 端点 + 消息模型落地（chat_routes 已预留 messageType）
3. **AI能力编排中枢**（后端 P1）：能力注册表 + 意图路由器，替换 decision 模块的模拟编排
4. **AI训练与治理**（后端 P2）：看板 + 学习周期 + 健康熔断

---

## 5. 详细设计

### 5.1 统一AI智能入口（前端）

#### 5.1.1 入口形态（对标 ChatGPT 三键输入条 + 微信按住说话）

改造 `chat-widget.js` → 升级为 `ai-hub-widget.js`（保留原挂载协议 `ChatWidget.mount()` 兼容）：

```
┌──────────────────────────────────┐
│  竹香AI                [—][✕]   │
│  ┌────────────────────────┐    │
│  │ (AI回复区: 流式+引用溯源) │    │
│  │  ragMode徽章/置信度      │    │
│  └────────────────────────┘    │
│  [＋] [文本输入框......] [🎤][📷][➤]│
└──────────────────────────────────┘
```

- **🎤 按住说话**（微信范式）：`PointerEvent` 按下开始录音（`MediaRecorder` API，webm/opus），松手上传 → ASR → **文字预览条**（可改再发，微信/讯飞双保险范式）→ 发送。上限 60s/2MB（对齐 chat 模块设计文档 3.3 节）。
- **📷 图片输入**：`accept="image/*"` + 移动端唤起相机，上传走 knowledge media 链路 → GLM-4V。
- **＋更多**：文件上传（P2）、位置、快捷指令面板。
- **界面原则**：单色主视觉 + 16px 图标 + 8px 网格，无弹窗无跳转，所有能力面板内完成（"简洁干净高质量"）。

#### 5.1.2 角色自适应能力面板（"方便角色进入"）

入口检测 `X-Member-Id` + `X-Role`（复用 role 模块的鉴权头惯例）：

| 角色 | 入口默认能力 | 数据来源 |
|---|---|---|
| 游客 | 商品问答 / 年龄门 / 转人工 | knowledge RAG |
| 会员(member) | 上述 + 订单查询 / 售后 / 积分 / 推荐码收益 | order/credit/promocode |
| 客服(staff) | 上述 + 工单队列 / 我的分润预估 / 排班 | role（派单/分润） |
| 管理员(admin) | 上述 + AI治理面板入口 / 全站AI健康 / 今日异常扫描 | hub.ops |

面板实现：会话顶部横向能力 chips（≤6个），点击即注入对应"意图快捷指令"（如点"订单查询"自动填入"查我的最近订单"），**零学习成本**。

### 5.2 多模态输入引擎（P0 核心）

#### 5.2.1 语音链路（新增端点）

```
POST /api/hub/asr
  multipart/form-data: audio (webm/mp3/wav, ≤2MB, ≤60s), lang=zh
  → 200 { success, text, duration_ms, model, fallback_rule: false }
  → 失败时 200 { success:false, error }（前端提示改用键盘输入，不阻断）
```

实现：直接包装 `llm_client.transcribe()`（已支持 wav/mp3 手工 multipart；webm 由服务端先经 ffmpeg 转 mp3——ffmpeg 依赖已在 knowledge P3.6 引入，缺失时优雅降级返回 415）。埋点进 `core/metrics.py`（`hub_asr_total/success/fallback`），与全站 Prometheus 体系一致。

#### 5.2.2 消息模型落地

chat_routes 已预留 `messageType: text/image/video/voice/file + mediaUrl/mediaThumb/mediaSize`——本模块前端真正开始发送：

```json
{ "content": "这瓶酒多少钱", "messageType": "voice",
  "mediaUrl": "/media/voice/xxx.webm", "mediaSize": 182400,
  "asrText": "这瓶酒多少钱" }
```

会话历史渲染时 voice 消息显示"🔊 12″ + 转写文字"双行（微信范式），转写文本参与后续 RAG。

#### 5.2.3 意图分类器（规则轨优先，LLM 增强）

```
规则轨（<5ms，先跑）: 正则/关键词 → intent
  例: 价格|多少钱 → product.price; 查订单|物流 → order.query;
      转人工 → chat.human; 分润|结算 → role.profit
LLM 轨（LLM_ENABLED=on 时，规则未命中再跑）:
  GLM-4-flash few-shot → {intent, confidence, entities}
  失败回退规则轨兜底 intent=chat.general
```

意图集 v1（12个）：`product.price / product.recommend / order.query / order.aftersale / knowledge.qa / chat.human / role.profit / role.dispatch / credit.query / ops.health / media.image_qa / chat.general`。

### 5.3 AI能力编排中枢（P1 核心）

#### 5.3.1 能力注册表（Capability Registry）

Redis Hash `zhuxiang:hub:capabilities`（field=capability_id）：

```json
{
  "id": "knowledge.rag",
  "name": "知识库问答",
  "module": "knowledge",
  "intents": ["knowledge.qa", "product.price"],
  "roles": ["guest", "member", "staff", "admin"],
  "endpoint": "internal:knowledge_service.search_and_answer",
  "health": {"success_rate_7d": 0.93, "p95_ms": 820, "fallback_rate": 0.07},
  "cost_weight": 1.0,
  "enabled": true
}
```

初始注册（静态 seed，运行时可增删）：
`knowledge.rag / hub.asr / hub.vision / chat.human / role.dispatch / role.profit / role.ai_brain / learning.cycle(17评分器) / attract.growth / monitor.scan / maintain.check`

#### 5.3.2 意图路由器

```
路由决策 = intent 匹配 → 角色过滤(enabled.roles) → 健康熔断(7日成功率<0.5 自动摘除)
         → 多命中时按 health×0.6 + cost_weight×0.4 排序取最优
```

**对 decision 模块的处理**：`/api/decision/orchestrate` 与 `/capability-route` 的模拟实现由本中枢真实化接管（decision_routes 内部调用 `hub_service.route()`，保留原响应契约不动前端）——补上 G3 空白。

#### 5.3.3 复合任务编排（扇出-聚合）

```
用户: "查下我的订单，顺便这酒有优惠吗，转人工吧"
→ 拆解: [order.query(本地) ∥ product.coupon(本地RAG)] → 聚合回复 + chat.human(转接)
```

v1 仅支持 ≤3 个并行能力 + 1 个后置动作（转人工/建工单），复杂工作流留 P2+。

### 5.4 AI训练与治理（P2 核心）

1. **治理看板**（`ai-hub-dashboard.html`，参照 knowledge-dashboard 独立页惯例）：
   - 能力健康矩阵（成功率/延迟/回退率红黄绿）
   - LLM 用量与成本（复用 `/metrics` 数据 + Redis 日聚合）
   - 17 评分器学习周期时间线
2. **训练管理**：学习周期手动触发/暂停（复用 `ai_learning_scheduler` 的周期机制，新增 `/api/hub/ops/learning/retrigger`）；评分器晋升/回退审批流。
3. **熔断与自愈**：能力连续失败 → 自动摘除路由 → 恢复探测（半开）→ 重新上架。旁路通知 admin（复用通知惯例）。

---

## 6. 数据模型（Redis，键前缀 `zhuxiang:hub:`）

| 键 | 类型 | 内容 |
|---|---|---|
| `hub:capabilities` | Hash | 能力注册表（field=cap_id, value=JSON） |
| `hub:intent_stats:{yyyymmdd}` | Hash | 意图日计数（field=intent, value=n） |
| `hub:asr:usage:{mid}` | String(TTL 24h) | 会员日语音用量（限流 200次/日） |
| `hub:route:health:{cap_id}` | Hash | 健康滚动窗口（success/fail/p95样本） |
| `hub:feedback:{sid}` | List | 入口会话反馈（复用 chat 满意度） |

语音文件存本地卷 `hub-media/`（compose 挂载），URL 走静态服务（P0 简化，不上 OSS）。

---

## 7. API 设计（本模块自有端点，14个）

| 分组 | 端点 | 说明 |
|---|---|---|
| 输入 | `POST /api/hub/asr` | 语音转文字（P0） |
| 输入 | `POST /api/hub/input/intent` | 意图分类（调试/前端预路由用） |
| 入口 | `GET /api/hub/panel?role=` | 角色能力面板配置（chips） |
| 入口 | `GET /api/hub/health` | 入口健康（聚合各能力绿灯） |
| 路由 | `POST /api/hub/route` | 意图→能力路由（内部+外部） |
| 路由 | `GET /api/hub/capabilities` | 注册表查询（admin） |
| 路由 | `POST /api/hub/capabilities/{id}/toggle` | 上下架（admin） |
| 编排 | `POST /api/hub/orchestrate` | 复合任务编排（替代 decision 模拟） |
| 治理 | `GET /api/hub/ops/overview` | 治理看板数据（admin） |
| 治理 | `GET /api/hub/ops/intents?days=7` | 意图分布统计 |
| 治理 | `POST /api/hub/ops/learning/retrigger` | 重跑学习周期（admin） |
| 治理 | `GET /api/hub/ops/circuit/{cap_id}` | 熔断状态查询 |
| 媒体 | `POST /api/hub/media/voice` | 语音文件上传（返回URL） |
| 媒体 | `POST /api/hub/media/image` | 图片上传（转 GLM-4V 轨） |

代码组织完全复制 role 四层样板：
`routes/hub_routes.py`（register_hub_routes）+ `services/hub_service.py` + `repositories/hub_repository.py` + `test_hub_routes.py`；`routes/__init__.py` 与 `main.py` 两处注册 + openapi_tags 补"AI智能中枢模块(35)"。

## 8. 环境变量（.env.example 追加）

```ini
# ---- AI智能中枢(35) ----
HUB_ENABLED=on                      # 总开关(关闭时入口纯文本/直连chat旧轨)
HUB_ASR_DAILY_LIMIT=200              # 会员日语音次数
HUB_INTENT_LLM=off                   # 意图分类LLM增强轨(默认规则轨)
HUB_CIRCUIT_MIN_SUCCESS=0.5          # 熔断阈值(7日成功率)
# ASR_MODEL / LLM_MODEL / VISION_MODEL 复用既有定义,不新增
```

---

## 9. 分期计划

| 期 | 内容 | 验收 |
|---|---|---|
| **P0 统一入口+语音** | ai-hub-widget.js（三键输入条/按住说话/角色面板chips）+ `/api/hub/asr` + voice 消息模型落地 + 意图规则轨 + `GET /panel` | 联调清单风格 E2E：游客语音问价→RAG答；member点chip查订单；管理员见治理入口占位 |
| **P1 编排中枢** | 能力注册表 + 路由器 + 熔断 + decision/orchestrate 真实化接管 + `/api/hub/route|capabilities` | 12意图路由准确率≥90%（回归用例）；摘除能力自动绕行 |
| **P2 训练治理** | ai-hub-dashboard.html + 学习周期管理 + 意图统计 + 用量成本聚合 | 看板三视图齐；重跑学习周期幂等；熔断自愈演示 |

每期交付遵循全站惯例：四层代码 + E2E 测试（LOCK_MODE/STORE_MODE=asyncio）+ 更新日志 + 总体架构文档3.1清单补录（35号）。

## 10. 测试与验收要点

1. **语音链路降级**：无 LLM_API_KEY 时 `/asr` 返回明确错误码，前端降级提示（不白屏）；ffmpeg 缺失时 webm 返回 415。
2. **路由熔断**：模拟 knowledge 连续失败 → 自动摘除 → `chat.general` 兜底 → 恢复探测后重新上架。
3. **角色隔离**：guest 访问 `role.dispatch` 意图 → 401（复用 role 鉴权惯例）。
4. **兼容性**：旧 `ChatWidget.mount()` 调用方（index 等 28 个页面）零改动可用（降级纯文本）。
5. ** Prometheus**：`hub_asr_total / hub_route_total{intent,capability} / hub_circuit_state` 三组指标进 `/metrics`。
