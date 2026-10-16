# 竹香酒项目·前端页面与接口映射 UI 设计文档

> 版本：v1.0（2026-10-16）　|　姊妹篇：[技术架构全景文档](computer://d:\网站架构设计\docs\技术架构全景文档.md) · [模块接口定义文档](computer://d:\网站架构设计\docs\模块接口定义文档.md)
> 生成口径：页面清单与接口调用**全部由脚本扫描前端源码真实提取**（HTML 内联 script + 引用 JS 的 fetch 调用与 API 字面量），再反查模块接口定义文档标注方法与鉴权；相对路径端点经 `APIRouter(prefix)` 补全为完整形态后匹配。UI 描述基于页面源码结构与更新日志实施记录。

---

## 一、前端架构与设计语言

### 1.1 技术形态

| 项 | 约定 |
|---|---|
| 构建体系 | **零构建零框架**：单文件 HTML + 原生 JS，无 CDN 运行时依赖 |
| 页面规模 | 52 个有效页面（C 端 15 + 管理台 36 + 测试 1），最大单页引用 27 个 JS（module-test） |
| 上传惯例 | base64 JSON（全站零 python-multipart，FileReader.readAsDataURL） |
| 视觉主题 | 竹韵品牌——hero 区竹元素（`hero-bg`/`hero-bamboo`）、品牌口号「福来就喝竹奕酒，天赐鸿运随心走！」、「全竹发酵 竹酒首创 - 山东瑞麒酒业」 |
| 导航 | `js/main.js` 全站统一导航（`js/data.js` 站点配置驱动；`/api/agent` 为代理商端导航入口，非数据接口） |
| 命名 | 管理台统一 `*-dashboard.html`；C 端语义命名；智字族 dashboard 与后端模块一一对应 |

### 1.2 数据策略：mock / live 双模式（P5 渐进 live 化）

前端服务层遵循「mock 同步、live 异步」双模式约定（`js/env-adapter.js` setMode 切换）：

| 层 | 职责 |
|---|---|
| `js/data.js` | 本地静态数据（产品/新闻/站点配置）——**C 端主渲染源（mock 侧）** |
| `js/env-adapter.js` | H5/小程序双端适配 + live 请求封装（GET query 序列化已修复） |
| `js/checkout-service.js` 等 | 供应链四件套前端事务（9 阶段与后端 business 模块对齐），mock/live 双轨 |

**live 化现状**（按页面实测）：news 社区流、index 同盟区块、product-detail 知识背景、search 智搜、service 会话、activity 全套、checkout 供应链族已 live；products/cart/news-detail 仍 data.js 本地渲染（live 端点 `/api/product/*` 公开GET 已就绪待接）。

### 1.3 鉴权设计（js/auth.js 统一令牌）

```
登录页 login.html (双 Tab: 会员/管理员)
   ├─ 会员: POST /api/entry/login (39号统一入口) 或 /api/auth/login
   ├─ 管理员: POST /api/admin/login (+TOTP) → 手机号换 JWT 两段式
   └─ accessToken/refreshToken 本地存储, 过期前 60s 静默刷新
        ↓
页面接入: apiHeaders() 注入 Bearer → fetch 封装 401 分支(刷新→重试→跳登录)
        ↓
authBadge: 页头登录态 UI(昵称+管理员标识+退出); requireLogin() 未登录跳转
防开放跳转: redirect 仅允许站内相对文件名(indexOf(':')<0 一刀切)
```

管理台页面统一模式：`Auth.apiHeaders()` 合并兼容头（登录后 Bearer 覆盖 `X-Role`，未登录保留向后兼容）。

### 1.4 页面分类统计

| 分类 | 数量 | 说明 |
|---|---|---|
| C 端页面 | 15 | 电商主链 + 内容 + 服务（第二章逐页） |
| 管理台 dashboard | 36 | 其中 31 页带接口调用、5 页纯静态观测（第四/五章） |
| 测试工具页 | 1 | module-test.html（29 大模块初测仪表盘 v6.0） |
| 占位页 | 1 | beian-placeholder.html（备案占位，不计） |

---

## 二、C 端页面设计（15 页）

> 每页含：页面定位（源码 title）· UI 结构 · 接口映射（真实提取，含方法与鉴权）· live 化状态。

### 2.1 index.html — 官网首页

- **定位**：「竹奕酒 · 全竹发酵 竹酒首创 - 山东瑞麒酒业官方网站」
- **UI 结构**：hero 区（竹韵视觉 + 品牌口号 + CTA 按钮组）→ 产品/品牌内容区 → 同盟臻选商品区块（live）→ 全局导航（main.js）+ 共鸣行为采集挂件（resonance-beacon.js，75号 RA-P1）
- **接口映射**：

| 方法 | 接口 | 鉴权 | 用途 |
|---|---|---|---|
| GET | `/api/alliance/products?status=active` | 公开GET | 同盟臻选区块商品（失败回落静态） |
| POST | `/api/auth/login` / `logout` / `refresh`、`/api/admin/login` | 公开 | auth.js 登录态生命周期 |
| — | `/api/agent` | — | 导航入口（非数据接口） |

### 2.2 products.html — 酒类介绍（产品列表）

- **定位**：「酒类介绍 - 竹香酒 · 竹奕系列」
- **UI 结构**：data.js 本地产品数据渲染的产品卡网格；筛选/分类导航
- **接口映射**：当前 **mock 侧**（data.js）；live 端点已就绪：`GET /api/product/list|/featured|/hot|/categories|/search`（公开GET 前缀）——接入时按公开 GET 白名单直连
- **live 化**：待接（后端公开GET 已放行）

### 2.3 product-detail.html — 产品详情

- **定位**：「产品详情 - 竹香酒 · 竹奕系列」
- **UI 结构**：产品主信息（data.js）+ **竹韵知识背景卡**（live：75号知识内核公开锚点）+ 关联推荐
- **接口映射**：

| 方法 | 接口 | 鉴权 | 用途 |
|---|---|---|---|
| GET | `/api/zyh/knowledge` | 公开GET | 工艺宪法/品牌知识背景（竹韵智衡 75号） |

### 2.4 cart.html — 购物车

- **定位**：「购物车 - 竹香酒」
- **UI 结构**：购物车清单（data.js）+ `js/order-pricing.js` 本地计价引擎（与后端 `_calc_price` 同口径：等级折扣→满减→券→积分 30% 上限→免邮）
- **接口映射**：mock 侧（本地计价）；价格试算 live 端点 `POST /api/order/price/preview`（登录）已就绪

### 2.5 checkout.html — 订单结算

- **定位**：「订单结算 - 竹香酒」
- **UI 结构**：结算面板（9 个服务 JS：env-adapter / checkout-service / inventory-service / agent-shipping-service / order-pricing / upgrade-logger / transaction-template）——前端供应链事务演示与实链双形态
- **接口映射**（live 服务族基址，经 env-adapter liveRequest 拼接具体端点）：

| 方法 | 接口族 | 鉴权 | 用途 |
|---|---|---|---|
| POST | `/api/checkout` | 登录 | 9 阶段事务（订单/扣减/券/积分/分润） |
| GET/POST | `/api/inventory` | 登录 | 库存查询/多行扣减/回补 |
| GET/POST | `/api/agent-shipping` | 登录 | 发货路由/认领/服务费结算 |
| GET | `/api/warehouse/*` | 登录 | 出入库/库位/预警（module-test 同族） |

### 2.6 news.html — 网站新闻（内容社区流）

- **定位**：「网站新闻 - 竹香酒」
- **UI 结构**：**社区 feed 动态渲染**（蓝图近期项③：社区流 + 挂载商品卡跳 product-detail，HTML 转义防注入；任一失败回落原静态页 fail-soft）
- **接口映射**：

| 方法 | 接口 | 鉴权 | 用途 |
|---|---|---|---|
| GET | `/api/community/feed?page=1&page_size=20` | 公开GET | 已发布内容流（published 终态倒序分页） |

### 2.7 news-detail.html — 新闻详情

- **定位**：「新闻详情 - 竹香酒」；data.js 本地渲染（mock 侧）。社区帖子详情 live 端点 `GET /api/community/posts`（公开GET）已就绪待接。

### 2.8 activity.html — 活动中心

- **定位**：「活动中心 · 竹香酒」；内联脚本（无外部 JS）
- **接口映射**：

| 方法 | 接口 | 鉴权 |
|---|---|---|
| GET | `/api/activity/list` | 公开GET |
| GET | `/api/activity/my-registrations` | 登录 |
| POST | `/api/activity/cancel` | 登录 |
| POST | `/api/auth/login` / `refresh` | 公开（登录态） |

### 2.9 activity-detail.html — 活动详情

- **定位**：「活动详情 · 竹香酒」；活动全功能页（报名/取消/抽奖/榜单/奖品）
- **接口映射**：

| 方法 | 接口 | 鉴权 |
|---|---|---|
| GET | `/api/activity/{id}` | 公开GET（正则放行） |
| POST | `/api/activity/register` / `cancel` | 登录 |
| POST | `/api/activity/lottery/draw` | 登录 |
| GET | `/api/activity/lottery/`（奖品池概率公示） | 公开GET |
| GET | `/api/activity/leaderboard/{id}` | 公开GET |
| GET | `/api/activity/my-registrations` / `prizes/mine` | 登录 |
| POST | `/api/activity/prize/{id}/*` | 登录 |

### 2.10 search.html — AI 智能搜索

- **定位**：「AI 智能搜索 · 竹香九商城」；智搜全站统一智能入口（游客可用，登录态增强角色化答案）
- **接口映射**：

| 方法 | 接口 | 鉴权 | 用途 |
|---|---|---|---|
| POST | `/api/search-ai/query` | 公开 | 意图/合规/多路检索统一查询 |
| POST | `/api/search-ai/feedback` | 公开 | 显式反馈（绑定 decisionId） |
| POST | `/api/search-ai/action-click` | 公开 | 动作卡点击隐式回流（正样本） |

### 2.11 service.html — 客户服务

- **定位**：「客户服务 - 竹香酒」；客服中心 + **chat-widget 挂载页**
- **接口映射**：

| 方法 | 接口 | 鉴权 | 用途 |
|---|---|---|---|
| POST | `/api/chat/sessions` | 登录 | 创建会话 |
| POST | `/api/chat/sessions/{id}/messages` 等族 | 登录 | 消息/AI 回复（RAG 徽章/置信度/引用）/转人工/满意度 |

### 2.12 login.html — AI 智能入口·登录

- **定位**：「AI 智能入口 · 登录 - 竹香酒管理平台」；双 Tab（会员/管理员，管理员含 TOTP 与绑定手机号字段）+ 防开放跳转
- **接口映射**（39号入口全家桶 + auth 族，16 个调用为全站最多）：

| 方法 | 接口 | 鉴权 | 用途 |
|---|---|---|---|
| GET | `/api/entry/recognize?fingerprint=` | 公开 | AI 预判入口（设备识别→推荐登录方式） |
| POST | `/api/entry/login` / `register` | 公开 | 统一登录（密码/短信）/注册即登录 |
| POST | `/api/entry/step-up/verify` | 公开 | 二次验证完成 |
| POST | `/api/entry/qr/create`、GET `/api/entry/qr/{id}` | 公开 | PC 扫码会话（WS 推送 `/ws`） |
| POST | `/api/entry/webauthn/login/begin` / `complete` | 公开 | WebAuthn 真实轨登录 |
| POST | `/api/entry/webauthn/register/begin` / `complete` | 登录 | WebAuthn 凭证注册 |
| POST | `/api/sms/send` | 公开 | 短信验证码 |
| GET | `/api/entry/landing?role=` | 公开 | 登录后角色落地页 |
| POST | `/api/auth/*`、`/api/admin/login` | 公开 | 令牌生命周期 |

### 2.13 challenge.html — 安全验证

- **定位**：「安全验证」；风控挑战验证页（challenge-verify.js）
- **接口映射**：`POST /api/security/challenge/verify`（登录）——安全告警模块（43号）挑战核验。

### 2.14 alliance-mall.html — 同盟臻选商城

- **定位**：「竹香酒 · 同盟臻选商城」；37号同盟商城 C 端门面（店徽/店名/评分安全字段白名单）
- **接口映射**：

| 方法 | 接口 | 鉴权 | 用途 |
|---|---|---|---|
| GET | `/api/alliance/shops` | 公开GET | C 端商户目录 |
| GET | `/api/alliance/products?status=active&merchantId=` | 公开GET | 商户商品列表 |
| POST | `/api/alliance/order` | 登录 | 同盟下单 |

### 2.15 logistics.html — 物流查询

- **定位**：「物流查询 · 竹香九商城」
- **接口映射**：`POST /api/logistics/order/{no}`（登录）轨迹查询；auth 族登录态。

---

## 三、全局挂件族（5 个）

| 挂件 | 挂载协议 | 消费接口 | UI 要点 |
|---|---|---|---|
| `js/chat-widget.js` | `ChatWidget.mount({apiBase})`（service.html 已挂） | `/api/chat/sessions` 族 | 💬 FAB + 面板；RAG 徽章（精确命中/知识融合/FAQ）+ aiConfidence + citations 引用列表；转人工（关键词自动+手动）；满意度 1-5 星 |
| `js/ai-hub-widget.js` | ChatWidget.mount 兼容协议 | `POST /api/hub/route` 前置路由 | 三键多模态输入条 [＋][文本框][🎤][📷][➤]；🎤按住说话（MediaRecorder webm/opus→ASR）；路由徽章「🧭 意图→能力」；route 失败静默回退 chat 旧轨 |
| `js/xiaozhu-widget.js` | 独立挂载 | 小竹 48号语音面（`/api/xiaozhu/voiceTurn` 族） | 语音导购入口（唤醒→ASR→回复→TTS） |
| `js/voice-wake-widget.js` | 独立挂载 | 唤醒三轨（EMA shadow） | 语音唤醒球（emaWould* 留痕灰度） |
| `js/resonance-beacon.js` | index.html 自动加载 | `POST /api/v1/resonance/collect`（公开，五层闸） | 共鸣行为采集 beacon（75号 RA-P1，无 UI 静默上报） |

---

## 四、管理台 Dashboard 族（31 页带接口）

### 4.1 统一设计规范（P4.3 确立的 dashboard 范式）

```
┌─ 页头: 模块名 + authBadge(昵称/管理员标识/退出) ─────────────────┐
├─ 统计卡行: 全局指标卡(总览数字, 卡片式 grid)                     │
├─ 功能区: Tab 或分区块(表格 + 操作按钮 + 状态 chip 筛选)          │
│   · 表格行内操作(审核通过/拒绝/发布/退役/半开探测等)             │
│   · 弹窗: 版本历史/二次确认                                      │
├─ 观测/决策双面: 决策端点 off 409 提示; 观测端点常开              │
└─ 30s 自动刷新 + Auth 鉴权对接(fetch 封装 401 跳登录)             │
```

- 单文件自包含（原生 JS 零依赖），`Auth.apiHeaders()` 统一请求头。
- 写操作 admin（`X-Role`/Bearer），观测面常开。
- 相对路径端点统一挂模块 `APIRouter(prefix)` 下（前端调用完整路径）。

### 4.2 页面清单（31 页 · 接口映射实测）

| 页面 | 后端模块 | 接口数 | 页面主题 |
|---|---|---|---|
| ai-security-dashboard | 安全告警(43号) | 24 | 告警采集/申诉裁决/行为基线/执行就绪 |
| ai-promo-studio | AI智能推广(36号) | 21 | 推广内容工厂（选题→生成→审核） |
| ai-alliance-dashboard | 网站同盟(37号) | 19 | 同盟商户/商品/订单治理 |
| ai-blogger-dashboard | DV博主(40号) | 18 | 博主接单/内容/分润 |
| ai-ride-dashboard | 智能代驾(41号) | 18 | 代驾凭证/计价/行程 |
| zy-dashboard | 智启元·财务大模型 | 16 | 财务六报表/预测 |
| zhidan-dashboard | 智单·订单大模型 | 15 | 订单编排/异常处置 |
| activity-dashboard | 活动管理 | 15 | 活动创建/审核/数据 |
| zhiyun-dashboard | 智运·物流大模型 | 14 | 物流调度/路由 |
| xiaozhu-dashboard | 小竹语音中枢(48号) | 14 | 语音面板/学习队列/ASR 修正/公平桥 |
| zhike-dashboard | 智客·会员大模型 | 13 | 会员画像/分层运营 |
| zhisou-dashboard | 智搜·搜索引擎 | 13 | 检索治理/意图分布 |
| ai-pdm-dashboard | 产品管理(38号) | 13 | 商品/库存/上下架 |
| qr70-dashboard | 智能二维码(70号) | 13 | 码语义/核销 |
| knowledge-sources | AI知识库 | 20 | 教学/文档/媒体/抓取/质量/RAG 测试 |
| knowledge-dashboard | AI知识库 | 12 | 条目治理/缺口队列/统计 |
| ai-hub-dashboard | AI智能中枢(35号) | 12 | 能力矩阵/意图分布/LLM 用量/晋升审批 |
| ai-api-dashboard | API管理(44号) | 16 | 接口注册/治理 |
| legal-dashboard | 智法·法务(24号) | 10 | 合同/条款/合规 |
| ai-governance-dashboard | 治理合规中枢(46号) | 10 | 分级治理/假设审批/免疫 |
| trust-risk-dashboard | 信值验真风控(47号) | 7 | 风控决策/审计 |
| ai-promo-radar | AI推广(36号) | 8 | 热点雷达 |
| ai-asset-dashboard | 无形资产估值(62号) | 8 | 资产台账/估值 |
| ai-entry-dashboard | 网站入口(39号) | 8 | 入口漏斗/风控决策 |
| ai-invoice-dashboard | 无感开票(42号) | 8 | 开票队列/申诉/自动化率 |
| ai-payport-dashboard | 支付端口(71号) | 5 | 端口健康/自愈 |
| ai-learning-dashboard | AI学习治理 | 6 | 评分器档案/学习周期 |
| ai-learning-detail | AI学习治理 | 5 | 单评分器驾驶舱（权重/反馈/决策阻断面板） |
| ai-learning-report | AI学习治理 | 5 | 学习报告 |
| trust-dashboard | 信值模块(45号) | 2 | 信值公示/审计 |
| xiaozhu-fc-redteam | 小竹FC红队 | 5 | FC 红队测试批跑 |

> 每页通用附带：auth 族 3~4 个调用（login/refresh/logout/admin/login）——登录态生命周期，不重复列出。

---

## 五、静态观测页与测试页

### 5.1 静态观测页（5 页，零接口调用）

| 页面 | 对应模块 | 说明 |
|---|---|---|
| attract72-dashboard | 引流大模型(72号) | 纯静态展示 |
| synapse-dashboard | 织智(76号人格) | 品牌资产公示（后端观测面 GET 公开已就绪，前端暂静态） |
| zjian-dashboard | 竹鉴(77号) | 质检典藏公示（同上） |
| zyh-dashboard | 竹韵智衡(75号) | 工艺宪法/图谱公示（product-detail 已 live 消费 `/api/zyh/knowledge`） |
| xx64-delivery-dashboard | 智能模块(64号) | 交付看板静态版 |

### 5.2 module-test.html — 模块初测仪表盘

「竹香酒官网 · 29 大模块初测仪表盘 v6.0」：引用 27 个 JS（供应链四件套服务 + 回归测试族 + 约定检查器），消费 `/api/checkout` `/api/inventory` `/api/warehouse` `/api/agent-shipping` live 族——前端供应链事务的集成测试入口。

---

## 六、页面-接口-模块总矩阵（52 页）

> 完整矩阵由脚本生成（`api_total`=提取到的调用数，`matched`=成功映射到接口文档的数）；auth 族调用为登录态生命周期，各页近似等量。

| 页面 | 接口数 | 主模块（调用次数） |
|---|---|---|
| index | 6 | 同盟37号 · auth |
| products | 1 | **产品管理（live ✅ T1.1）** `/api/product/list` |
| product-detail | 2 | **产品管理（live ✅ T1.2）** `/api/product/{id}`(主信息+related 4 条)；竹韵智衡75号 |
| cart | 1 | **订单交易（计价 live ✅ T1.3）** `POST /api/order/price/preview`(登录态会员价明细)；游客走本地引擎 |
| checkout | 4 | 供应链事务 |
| news | 2 | 内容社区 |
| news-detail | 1 | （mock 侧待 live） |
| activity | 5 | 活动管理 |
| activity-detail | 11 | 活动管理 |
| search | 7 | 智搜 |
| service | 7 | 聊天客服 |
| login | 16 | 入口39号 · auth |
| challenge | 1 | 安全告警 |
| alliance-mall | 7 | 同盟37号 |
| logistics | 5 | 物流 |
| module-test | 5 | 供应链事务 |
| activity-dashboard | 15 | 活动管理×11 · 权限中心×3 |
| ai-alliance-dashboard | 19 | 同盟37号×15 |
| ai-api-dashboard | 16 | API管理44号×12 |
| ai-asset-dashboard | 8 | 无形资产62号×4 |
| ai-blogger-dashboard | 18 | DV博主40号×14 |
| ai-entry-dashboard | 8 | 入口39号×4 |
| ai-governance-dashboard | 10 | 治理46号×6 |
| ai-hub-dashboard | 12 | 中枢35号×8 |
| ai-invoice-dashboard | 8 | 开票42号×4 |
| ai-learning-dashboard | 6 | 学习治理×2 |
| ai-learning-detail | 5 | 学习治理×1 |
| ai-learning-report | 5 | 学习治理×1 |
| ai-payport-dashboard | 5 | 支付端口71号×1 |
| ai-pdm-dashboard | 13 | 产品管理38号×9 |
| ai-promo-radar | 8 | 推广36号×4 |
| ai-promo-studio | 21 | 推广36号×17 |
| ai-ride-dashboard | 18 | 代驾41号×14 |
| ai-security-dashboard | 24 | 安全告警×20 |
| knowledge-dashboard | 12 | 知识库×8 |
| knowledge-sources | 20 | 知识库×16 |
| legal-dashboard | 10 | 智法×4 · 条款×2 |
| qr70-dashboard | 13 | 二维码70号×9 |
| trust-dashboard | 2 | 信值45号×2 |
| trust-risk-dashboard | 7 | 信值风控47号×3 |
| xiaozhu-dashboard | 14 | 小竹48号×10 |
| xiaozhu-fc-redteam | 5 | 小竹48号×1 |
| zhidan-dashboard | 15 | 智单×11 |
| zhike-dashboard | 13 | 智客×9 |
| zhisou-dashboard | 13 | 智搜×9 |
| zhiyun-dashboard | 14 | 智运×10 |
| zy-dashboard | 16 | 智启元×12 |
| attract72 / synapse / zjian / zyh / xx64-delivery | 0 | 静态观测页 |

---

## 七、live 化路线（待办）

| 优先级 | 页面 | 动作 | 后端就绪度 |
|---|---|---|---|
| 高 | products / news-detail | data.js → `/api/product/*`、`/api/community/posts`（公开GET） | ✅ 已放行 |
| 中 | cart | 本地计价 → `POST /api/order/price/preview` | ✅ |
| 中 | 5 个静态观测页 | 接各自模块公开观测 GET（`/api/zyh/` `/api/synapse/` `/api/zjian/` 已公开GET） | ✅ |
| 低 | alliance-mall / index | 商户维度增强 | ✅ |

> 维护说明：本文映射由脚本扫描前端源码生成（fetch 调用 + API 字面量 → 模块接口文档反查标注方法/鉴权）。新增页面或接口接线后重跑扫描即可刷新；页面标题取自各 HTML `<title>`。
