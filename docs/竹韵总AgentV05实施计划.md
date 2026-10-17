# 竹韵总 Agent V0.5 实施计划（启封）

> 依据：用户《竹香酒全站总Agent AI 规划方案》（人格/Prompt 工程/变现路径）+ 工程映射摸底（2026-10-17）。
> V0.5 目标：**用最小改动让"竹韵"活过来**——挂件挂载 + 人格注入 LLM 对话轨 + 三场景 + 关怀护栏；验证"用户对 AI 人格接受度"这一方案最大不确定假设。变现层（订阅/定制/圈层）不在本版范围。

## 一、工程现状锚点（摸底结论）

| 锚点 | 事实 | 对 V0.5 的意义 |
|---|---|---|
| zyh 问答（75号） | **纯确定性检索**（三层守门+知识内核，LLM 禁入红线） | 人格不注入 zyh；走 chat 链 |
| chat 对话链 | `/api/chat/sessions` → chat_service 三态灰度（CHAT_LLM_MODE off/shadow/assist，默认 off）→ knowledge_svc.rag_answer(provider="llm") → `_rag_llm_synthesize` system prompt 组装 → provider_client（智谱 GLM，LLM_API_KEY） | **人格注入点=synthesize 的 system 前缀** |
| rag_answer 缓存 | 60s 结果缓存 key=`question\|provider\|category` | persona 维度必须入 key（同问不同场景不同答） |
| ai-hub-widget | chat-widget 升级版（兼容 mount 协议），多模态 mic/cam，**未挂载任何页面**；T4.4 已修 800ms 探测竞速（实测 49ms） | 挂载 index + service（service 替换 chat-widget，同 id 体系零迁移） |
| 生产 LLM 轨 | CHAT_LLM_MODE 未设=off（rule 轨）；海外曾跑 GLM（智谱月度账单脚本在案） | V0.5 生产开 **shadow**（留痕不呈现）灰度 |

## 二、改造清单（4 文件新增/修改）

### 1. 新增 `backend/services/zhuyun_persona.py`（人格层，纯函数零依赖）
- `ZHUYUN_BASE`：人格基座 system 段（角色"竹韵"=竹香酒数字酿造师；性格温润从容；价值观悦己适度品味；语言习惯东方意境、长短错落；禁用"亲/哦/呢"电商腔与"作为AI"破沉浸语；**事实层铁律：仅依据编号资料回答不编造**——与 RAG 幻觉治理兼容）
- `SCENES`：三场景段——`night`（22-6 时，围炉夜话：文字简短、推柔和回甘款、可附一句治愈短句，忌长篇）/ `gift`（送礼宴请：礼仪顾问，突出文化寓意包装质感，语言尊贵）/ `complaint`（客诉：收起诗意先共情道歉再给方案，大站担当）
- `detect_scene(content, hour)`：正则+时段规则判定（V0.5 不用 LLM 分类——确定性、零成本、可测）
- `care_gate(content)`：**关怀护栏（硬验收项）**——命中"借酒消愁/麻醉/买醉/心情不好喝点烈的"等正则即返回关怀回复模板（推荐饮茶/水/陪伴话术+视情绪提供心理援助热线线索，**绝不推酒**），调用方跳过 LLM 轨直接返回
- `build_system(scene)`：基座+场景段组装（无场景=纯基座）

### 2. 修改 `backend/services/knowledge_service.py`（最小参数透传）
- `rag_answer` / `_rag_answer_uncached` / `_rag_llm_synthesize` / `_dual_generate` 增可选参 `persona_system: str | None = None`（默认 None=现状零变化）
- synthesize 组装：`system = (persona_system + "\n\n") if persona_system else ""` + 既有幻觉治理 system
- **缓存 key 追加 persona 维度**：`{question}|{provider}|{category}|{persona_hash}`（persona 为 None 时维持旧 key 形态——既有缓存命中不受影响）

### 3. 修改 `backend/services/chat_service.py`（LLM 轨编排）
- `_search_knowledge`（LLM 轨调用 rag_answer 处）前置编排：
  1. `care_gate(content)` 命中 → 直接返回关怀回复（provider 标记 `zhuyun_care`，shadow 模式同样前置——护栏优先于一切轨道）
  2. `detect_scene(content, 时段)` → `build_system(scene)`
  3. `rag_answer(persona_system=...)`（rule 轨不受 persona 影响——rule 轨是知识原文）
- 响应附 `zhuyun: {"scene": ..., "persona": true/false}` 元数据（观测人格生效面，看板可查）

### 4. 前端挂载（2 文件）
- `service.html`：`js/chat-widget.js` 引用替换为 `js/ai-hub-widget.js`（兼容协议，cw-* 同 id 体系零迁移）+ `AIHubWidget.mount({role:'member'})` 自启
- `index.html`：body 尾新增挂件 script + 自启（T4.4 竞速已保证探测失败 1s 内隐藏多模态键）
- `ai-hub-widget.js` 面板文案人格露出：标题"竹香AI 智能助手"→"竹韵 · 数字酿造师"（sub 同步）

## 三、验证与灰度

**L1 本地（无 LLM key，全部可跑）**
1. 单测：persona 组装（基座/三场景/无场景）、care_gate 正反例、缓存 key 隔离（同问 persona 有无→两 key）
2. e2e（8001 实例）：挂件挂载 index/service（探测隐藏/发消息 rule 轨/无 pageerror）；关怀闸问题实测返回关怀模板；既有 chat rule 轨回归无损

**L2 生产 shadow（后续部署动作，不在本提交）**
- 生产 nssm env 注入 `CHAT_LLM_MODE=shadow`（LLM 答案留痕不呈现，admin 审计人格质量）→ 观察 3-7 天 → assist 呈现
- 生产 .env 已有 LLM_API_KEY（智谱）为前提；观测指标：shadowLlm 人格样本抽检、care_gate 触发率、场景分布

**红线**：zyh 75号确定性链路零改动；关怀护栏先于一切轨道；rule 轨行为零变化（persona 仅作用 LLM 轨）。

## 四、明确不做（V0.5 出界项登记）
千人千面 UI/BGM、AR、NFT 数字藏品（合规红线）、按月盲盒（酒类流通资质）、数字酒窖跨模块画像、佐餐图像识别、情绪 LLM 识别（V0.5 场景判定为确定性规则）——待 V0.5 人格接受度数据后再立项。

## 五、完成记录（2026-10-17）

**L1 本地验证 27 pass / 0 fail 全绿**：
- persona 单测 12/12（基座组装/三场景注入/深夜时段判定/care 闸正反例不误伤/文案不推酒）
- 后端 chat 实测 5/5：注册取 token → 会话创建 → care 闸生效（`ragMode=zhuyun_care`）→ 常规问题 rule 轨兜底正常 → 客诉场景正常（rule 回复或转人工）
- 挂件 e2e 10/10：index/service 两页注入/竹韵标题/竹韵开场/关怀回复端到端/零 pageerror

**过程中的三个关键发现**：
1. **chat 端点 JWT 范式**：`/api/chat/*` 不在中间件公开白名单，compat 无 token 时 `X-Member-Id` 兼容头被剥离 → 路由层 `_require_member_id` 401。验证与挂件均须 `Authorization: Bearer` 双头（挂件经 `Auth.apiHeaders()` 自动叠加，宿主页已引 auth.js）。游客在挂件发消息将 401——guest 降级路径登记为后续决策项。
2. **挂件欢迎语时序缺陷（顺手修复）**：`toggle()` 先执行 `loadHistory()`（同步置 `loaded=true`），本地竹韵开场白恒不渲染，用户只见后端 rule 轨 system 欢迎语。修复：开场白先行渲染 + loadHistory 呈现层跳过后端 system 欢迎语（仅显示层，后端 rule 轨零变化，admin 客服台不受影响）。
3. **客诉消息必转人工系既有设计**：客诉含"投诉/退款"命中 P1-8 转人工触发引擎，会话转人工后 AI 停答——非 V0.5 回归；care 闸消息未误触情绪累积（其后客诉仍获 AI 回复）。

**红线核对**：zyh 75号确定性链零改动（未触碰）；关怀护栏先于一切轨道（`_search_knowledge` 开头前置，实测生效）；rule 轨零变化（persona 仅注入 LLM 轨 system 前缀，None 时缓存 key 维持旧形态）。

**后续动作（不在本次提交）**：L2 生产 shadow——nssm env 注入 `CHAT_LLM_MODE=shadow` + 前端 service/index 挂件上线（随下次部署）；观察 3-7 天后切 assist。
