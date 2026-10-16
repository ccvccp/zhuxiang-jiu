# 竹香酒项目·前端 UI 设计规范与交互文档

> 版本：v1.0（2026-10-16）　|　文档链：[技术架构全景文档](computer://d:\网站架构设计\docs\技术架构全景文档.md) → [模块接口定义文档](computer://d:\网站架构设计\docs\模块接口定义文档.md) → [前端页面与接口映射UI设计文档](computer://d:\网站架构设计\docs\前端页面与接口映射UI设计文档.md) → **本文**
> 定位：上一份映射文档回答「页面调哪些接口」；本文回答「页面长什么样、由哪些组件构成、接口字段如何落到 UI、交互状态如何流转」——**UI 设计规范层**。
> 依据：设计令牌实录自 [css/style.css](file:///d:/网站架构设计/zhuxiang-jiu/css/style.css)；组件类名与 DOM 结构实录自 dashboard 族源码（ai-hub-dashboard 等）；字段绑定以 ai-hub / chat-widget 为实证锚点。

---

## 一、设计系统（Design Tokens）

### 1.1 色彩体系（style.css `:root` 实录）

| 令牌 | 值 | 语义 |
|---|---|---|
| `--color-primary` | `#4a7c59` | 竹绿主色——品牌核心（按钮/链接/高亮/导航激活态） |
| `--color-primary-dark` | `#355c44` | 深竹绿（hover/按压态） |
| `--color-primary-light` | `#6b9b7c` | 浅竹绿（辅助/次级强调） |
| `--color-accent` | `#c9a961` | 金色辅色——高端感（价格/勋章/臻选标识） |
| `--color-bg` | `#f8f6f0` | 米白页面背景 |
| `--color-bg-card` | `#ffffff` | 卡片背景 |
| `--color-text` | `#2c2c2c` | 正文 |
| `--color-text-light` | `#6b6b6b` | 辅助文字（stat-label/说明） |
| `--color-border` | `#e5e0d5` | 边框/分隔线 |
| `--color-danger` | `#c0392b` | 警告/价格/危险操作 |

阴影三档全部带竹绿色相：`--shadow-sm/md/lg = 0 {2,4,8}px {8,16,32}px rgba(74,124,89,{.08,.12,.16})`——卡片浮起感与品牌色统一。

### 1.2 字体

| 场景 | 字体栈 |
|---|---|
| 全局 body | `"Microsoft YaHei", "PingFang SC", "Helvetica Neue", Arial, sans-serif` |
| 品牌标题/口号/书法感文案 | `"STKaiti", "KaiTi", serif`（楷体——hero 口号、章节题字） |

楷体仅用于品牌叙事层，功能界面一律雅黑/苹方——传统质感与可读性分层。

### 1.3 圆角 / 间距 / 栅格 / 动效

| 令牌 | 值 | 应用 |
|---|---|---|
| `--radius-sm/md/lg` | `4px / 8px / 16px` | 输入框 / 按钮·卡片 / 大面板·hero |
| `--container-width` | `1200px` | 全站内容容器 |
| `--transition` | `all 0.3s ease` | 全局统一过渡（hover/展开/淡入） |

---

## 二、布局模板（四型）

全站 52 页收敛为四个布局模板，新页按型复用。

### 2.1 T1 · C 端品牌页（index/products/news/product-detail/activity/search/service）

```
┌──────────────────────────────────────────────────┐
│ 导航栏(main.js 全站注入): 品牌字 + 菜单 + 登录态   │ ← 滚动吸顶
├──────────────────────────────────────────────────┤
│ hero 区: hero-bg(底纹) + hero-bamboo(竹元素)      │ ← radius-lg, 楷体口号
│   口号(楷体) + CTA 按钮组(primary/outline 双款)   │
├──────────────────────────────────────────────────┤
│ 内容区(--container-width 1200px):                 │
│   · 产品卡网格 / 图文列表 / 搜索结果流             │
│   · live 区块: 社区feed/同盟臻选/知识背景卡        │
│   ·   (fetch 失败 → 回落静态 fail-soft)           │
├──────────────────────────────────────────────────┤
│ 页脚: 备案/联系方式/品牌信息                       │
└──────────────────────────────────────────────────┘
  挂件位: 右下角 💬chat / 🧭ai-hub / 语音唤醒球(浮动)
```

### 2.2 T2 · 管理台 Dashboard（31 页统一范式）

```
┌──────────────────────────────────────────────────┐
│ 页头: 模块名(汉字+号数) ──── authBadge(昵称·管理员│
│       标识·退出按钮)                              │
├──────────────────────────────────────────────────┤
│ 统计卡行: [stat-value + stat-label]×N             │ ← 白卡+shadow-sm
│   (如 ai-hub: 能力总数/已上架/红灯/黄灯/7日意图)   │    初值一律 "--"
├──────────────────────────────────────────────────┤
│ Tab/分区切换: 能力矩阵│意图分布│用量成本│晋升审批  │
│ ┌──────────────────────────────────────────────┐ │
│ │ 数据表格: 行内 badge 状态灯 + btn-mini 操作   │ │
│ │   (半开探测/批准/拒绝/上下架→二次确认)        │ │
│ │ 弹窗: 版本历史/详情抽屉                       │ │
│ └──────────────────────────────────────────────┘ │
├──────────────────────────────────────────────────┤
│ 30s 自动刷新(setInterval) + 手动刷新按钮          │
└──────────────────────────────────────────────────┘
```

### 2.3 T3 · 登录页（login.html）

双 Tab（会员/管理员）卡片居中（`--radius-lg` + `shadow-md`）；管理员 Tab 含 TOTP 与绑定手机号字段；扫码区右侧 180s 倒计时二维码；防开放跳转校验在提交前后双重执行。

### 2.4 T4 · 浮动挂件（不占布局，fixed 定位）

FAB 主按钮（`--color-primary` 圆形 56px）+ 展开面板（右下角 380px 宽，`--radius-lg`）；三键多模态输入条 `[＋][文本框][🎤][📷][➤]`。

---

## 三、组件规格库

### 3.1 状态徽章 badge（dashboard 族通用）

| 变体 | 语义（实证自 ai-hub/ai-learning；T3.2 扫查 31 页后修订） |
|---|---|
| `badge green` | 正常/已学习/通过 |
| `badge red` | 红灯（熔断/下架）/异常/未知 |
| `badge yellow` | 警示黄灯（观察/待审/降级预警）——12 页事实标准（`#fdf3e3/#a06b00` 或 `#fff7e0/--color-warn` 两系），T3.2 收编为规范语义 |
| `badge weak` | 弱化/跳过/灰态（`#f4f4f4` 底灰字；初版"黄灯"措辞与实现不符，按实现修订） |
| `badge on` / `badge off` | 上架/下架开关态 |
| `badge + light 变量` | 红黄绿三色灯（capabilityMatrix 健康判定） |

**扩展分类色阶**（非状态灯，业务标签用，登记白名单）：`blue`（信息/通用标签）、`gold`（金标/会员）、`purple`（专属/回流）、`orange`（运营/活动）、`manual`（人工态）。**禁止**新增自定义状态类表达 green/red/yellow 语义（T3.2 已修：xx64 `ok`→green、pdm `pass`/`fail`→green/red）。

### 3.2 迷你操作按钮 btn-mini

表格行内操作：默认款（探测/批准）+ `btn-mini danger`（拒绝/下架——`--color-danger` 文字/边框）；破坏性操作前弹 confirm 二次确认。

### 3.3 统计卡 stat-card

`白底 + shadow-sm + radius-md`；数值 `stat-value`（大号主色/警示色）+ 标签 `stat-label`（`--color-text-light` 小字）；**加载初值统一 `--`**，30s 刷新覆盖。

### 3.4 C 端卡片族

产品卡（图 + 名称 + 价格 `--color-accent` 金色 + CTA）/ 挂载商品卡（社区流内嵌，跳 product-detail）/ 知识背景卡（product-detail 内，75号工艺锚点）/ 同盟商户卡（店徽+店名+评分，安全字段白名单）。

### 3.5 输入与反馈

输入框 `radius-sm` + focus 态 primary 描边；提示条 success/warning/danger 三色（左 4px 色条 + 浅底）；骨架不引入——加载态用 `--` 占位与按钮 disabled。

---

## 四、字段级数据绑定（接口响应 → DOM）

> 端点级映射见上一份文档；本章下沉到**响应字段落到哪个 UI 元素**。

### 4.1 实证锚点一：ai-hub-dashboard 能力矩阵

```
GET /api/hub/ops/overview
  ├─ capabilityMatrix[].name      → 表格列「能力」
  ├─ [].windowSuccessRate         → 成功率列 + badge light(red/yellow/green)
  ├─ [].enabled                   → badge on/off + btn-mini 上下架文案切换
  ├─ [].health.p95_ms             → 延迟列
  └─ 统计: caps 总数/enabled/red/yellow 计数 → stat-value 五连卡
POST /api/hub/ops/circuit/{id}/probe → btn-mini「半开探测」→ 行内刷新
GET  /api/hub/ops/usage?days=7    → LLM 用量表(日/调用/失败/估算成本/合计条)
GET  /api/hub/ops/learning/approvals → 晋升审批表 + 批准/拒绝按钮
```

### 4.2 实证锚点二：chat-widget 消息气泡（RAG 透明化）

```
POST /api/chat/sessions/{id}/messages
  ├─ aiReply.ragMode     → 气泡徽章「精确命中/知识融合/FAQ」
  ├─ aiReply.aiConfidence→ 置信度百分比徽章(direct=条目相似度, 兜底 0.30)
  ├─ aiReply.citations[] → 「知识来源」角标列表(entryId/question/similarity)
  └─ 转人工触发          → unresolvedCount 达 3 → 面板内嵌转人工提示条
```

### 4.3 通用绑定范式（全站约定）

- **观测/决策双面绑定**：决策端点（off 时 409）→ 按钮组禁用 + 黄条提示「MODE=off，观测面可看」；观测端点 → 表格常开。
- **空态语义**：列表空 → 居中灰字 + 上下文说明（如 approvals 空态「无挑战者，total=0 正常」）。
- **数字归一**：金额千分位、播放量「1.2万」中文单位、百分比保留 1-2 位——与后端契约容错口径一致。
- **HTML 转义**：所有后端文本经 escapeHtml 后入 DOM（社区 feed 防注入实证）；JS 注释中 `</script>` 写作 `<\/script>`。

---

## 五、交互状态流转规范

### 5.1 请求五态

| 态 | 表现 | 出处约定 |
|---|---|---|
| 加载 | stat 值 `--` / 按钮 disabled / 表格「加载中…」 | 30s 自动刷新兜底 |
| 成功 | 数据渲染 + 绿色 toast/提示条（弱反馈，不打断） | |
| 空态 | 居中灰字 + 场景说明（区分「无数据」与「未配置」） | |
| 401 | fetch 封装统一拦截 → 静默 refresh 一次 → 失败清会话跳 login.html | auth.js |
| 409/失败 | 红/黄提示条透出后端 `error` 文案；灰度 off 的 409 给出开关名提示（范本：payport-dashboard.js「决策面未开放——容器需 PAY71_MODE=shadow/assist」） | 决策面惯例 |
| 空态（T3.3 标准） | `.empty-state`（style.css 全站标准：居中灰字+场景文案）；dashboard 族既有 `.dash-empty` 页面级定义沿用；加载初值统一 `--`（T3.4 已统一观测页 17 处+module-test 3 处） | §3.3 |

### 5.2 灰度门控的 UI 表达

AI 能力族的 off/observe/enforce 在 UI 上一律可感知：模式徽章（页头 chip，如「enforce」绿 /「observe」灰）+ 决策按钮禁用态 + stats 面板常开——「观测面不受开关影响」为全站铁律。

### 5.3 破坏性操作

`btn-mini danger` → confirm 二次确认（含对象名回显）→ 成功绿条 + 行内状态翻转（badge 刷新），失败红条保留现场。

---

## 六、逐页模板归类速查（52 页 → 4 模板）

| 模板 | 页面 |
|---|---|
| T1 品牌页 | index · products · product-detail · news · news-detail · activity · activity-detail · search · service · alliance-mall · logistics · cart · checkout · challenge |
| T2 管理台 | 31 个 *-dashboard（映射见上一份文档 §4.2/§5） |
| T3 登录 | login |
| T4 挂件 | chat-widget · ai-hub-widget · xiaozhu-widget · voice-wake-widget · resonance-beacon（无 UI） |
| 测试 | module-test（T2 变体，27 JS 编排） |
| 静态 | attract72 · synapse · zjian · zyh · xx64-delivery（T2 骨架无接口，live 化后自动获得数据态） |

---

## 七、可访问性与响应式基线

- **对比度**：竹绿 `#4a7c59` 于白底 4.6:1（正文级达标）；辅助文字 `#6b6b6b` 5.7:1；金色 `#c9a961` 仅用于大号价格数字（不用于小字正文）。
- **触达区**：btn-mini 最小 28px 高（表格内），主按钮 40px+，FAB 56px。
- **响应式**：`--container-width` 1200px 定宽为主，窄屏单列堆叠（产品卡 grid auto-fill）；挂件面板 min(380px, 92vw)。
- **减动效**：全局 `--transition 0.3s` 轻量；无自动播放音视频；语音功能均提供文字等价物（ASR 转写预览条可编辑再发）。

---

## 八、设计红线（从工程教训固化）

1. **换肤铁律**：site-theme 运行时换肤「永不关停」——组件不得硬编码颜色，一律消费 CSS 变量令牌。
2. **live 失败必回落**：所有 live 区块（社区流/同盟/知识背景）失败回落静态版，不白屏。
3. **不编造占位数据**：加载态用 `--`，缺数据显「--」或空态文案，不用假数字填充。
4. **admin 写操作二次确认 + 对象名回显**；401 全局接管，页面不自行处理。
5. **新 dashboard 必复用 T2 模板与 §3 组件**——统计卡/badge 语义全站一致（red=异常、weak=观察、on/off=开关）。

> 维护说明：设计令牌以 [css/style.css](file:///d:/网站架构设计/zhuxiang-jiu/css/style.css) `:root` 为单一事实源；组件类名以 dashboard 族源码为准；本文与映射文档（端点级）配套使用，改令牌/组件须同步更新本文。
