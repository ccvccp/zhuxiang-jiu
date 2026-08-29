# AI智能大模型自动引流模块设计文档

> 版本：v1.0　日期：2026-08-29　定位：站外流量获取引擎（拉新侧总入口，与站内 traffic/promotion 分销体系互补成环）
>
> 核心理念：**AI产内容、会员传网络、短链收流量、归因算清楚、预算跟着ROI走**
>
> 成本主张：**不买量**——大模型生成内容边际成本趋近于零，分发复用已建成的会员分销矩阵（promotion ZXBJ码）与KOL体系，奖励预算按渠道ROI动态分配，把每一分推广费花在可验证的转化上。

---

## 1. 模块定位

### 1.1 要解决的问题（现状断链）

| 现状问题 | 具体表现 | 本模块对策 |
|---|---|---|
| **短链断链**（最严重） | KOL推广链接硬编码 `zhuxiang-jiu.com/r/{code}`，后端无 `/r/{code}` 跳转端点——站外流量根本进不来 | 智能短链系统（收口一切站外流量） |
| **无内容生产** | AI模块均为评分/风控/审核，无一具备生成能力；种草内容全靠人工 | AI内容工厂（大模型多平台内容生成） |
| **匿名流量丢失** | lead记录必须已有userId，未注册的纯点击无法落库——流量来了也看不见 | 匿名点击追踪（click_id体系，注册后身份归并） |
| **归因三套割裂** | traffic lead / KOL promoCode / promotion绑定关系互不相通，渠道效果说不清 | 统一归因中心（一次点击，全链归因） |
| **预算盲投** | 广告budget/dailyBudget纯存储无消耗控制；奖励预算不区分渠道效果 | ROI智能分配引擎（预算跟着转化走） |
| **无SEO** | 全库无sitemap/robots/关键词管理 | AI-SEO子系统（P1） |

### 1.2 一句话定位

**AI智能自动引流模块 = 站外流量的"总入口引擎"**：大模型按平台批量生成种草内容（小红书笔记/抖音脚本/朋友圈文案），经会员分销网络与KOL分发（复用现有两套码体系），落地智能短链（统一收口+匿名追踪），注册后自动归因到渠道与推广人，佣金走统一分润总账（复用AI智能管理模块），ROI数据反哺内容选题与渠道预算分配——形成"**内容→分发→落地→归因→结算→优化**"的增长飞轮。

### 1.3 核心概念模型（增长飞轮）

```
   ┌──────────────────────────────────────────────────┐
   │  ① AI内容工厂（选题→多平台内容生成→合规审核）        │
   │        ↓ 分发（零成本渠道优先）                      │
   │  ② 分发网络（会员矩阵码/KOL/官方账号）               │
   │        ↓ 站外点击                                   │
   │  ③ 智能短链 /r/{code}（收口+UTM+AB落地页）          │
   │        ↓ 匿名追踪（click_id）                       │
   │  ④ 统一归因（点击→注册→下单，全渠道一张表）           │
   │        ↓ 注册/下单                                   │
   │  ⑤ 分润结算（复用统一总账/佣金体系）                  │
   │        ↓ ROI数据回流                                 │
   │  ⑥ 智能优化（渠道预算再分配+内容选题再生成）──→ 回①  │
   └──────────────────────────────────────────────────┘
```

---

## 2. 成熟模型对标与创新

### 2.1 对标模型

| 成熟模型 | 借鉴点 | 本模块应用 |
|---|---|---|
| Growth Loop（GrowthHackers/Andreessen） | 增长是闭环不是漏斗：产出→投入→产出 | 六环飞轮，ROI数据直接驱动下一轮内容选题 |
| Jasper / Copy.ai | 大模型批量生成营销文案，人只做审核 | AI内容工厂：一次选题生成多平台变体，人工/规则审核后分发 |
| Shein/Temu 社交裂变 | 用户即渠道，分享即收益 | 分发复用现有会员矩阵（ZXBJ码两级奖励）与推广员（P码），不新建分销体系 |
| Bitly + UTM/GA4 | 短链收口+参数归因 | 智能短链携带渠道/内容/推广人三元参数，点击即归因 |
| Temu/TikTok Ads 智能投放 | 预算向高ROI渠道自动倾斜 | ROI分配引擎：把奖励预算当"推广预算"，按渠道边际ROI再分配 |
| Ahrefs/SurferSEO | 关键词→内容→排名 | AI-SEO：关键词库+AI文章生成+站内TDK建议（P1） |
| Branch/Adjust 移动归因 | click_id 匿名指纹+注册归并 | 匿名点击追踪，注册时身份归并（P0按短链click_id简化实现） |

### 2.2 创新点

1. **"预算即奖励"的ROI闭环**（业内少有）：不设独立广告预算——promotion两级奖励、KOL佣金、推广员佣金本身就是推广费；本模块把它们按**渠道真实ROI**动态调整（如小红书ROI高→矩阵奖励额度上调、抖音低→下调），实现"零买量预算下的智能投放"。
2. **一次选题、全平台变体**：大模型按"竹酒文化/场景/工艺"选题生成时，同步产出小红书笔记、抖音15s脚本、朋友圈短文案、SEO长文四种形态——内容边际成本趋零，全平台矩阵覆盖。
3. **归因三合一**：打通现有割裂的 traffic lead / KOL码 / promotion绑定——任何一次站外点击在统一归因表可查，渠道效果从"说不清"变"一张表"。

---

## 3. 与现有模块的关系（不重复建设）

| 现有模块 | 关系 | 复用方式 |
|---|---|---|
| traffic（推广员/KOL/lead） | **上下游对接** | 短链生成时绑定 promoterId/influencerId；注册归因回调写 traffic lead（补齐其匿名盲区） |
| promotion（会员矩阵码） | **分发通道** | AI内容分发时携带会员ZXBJ码（复制即带码文案）；绑定关系作归因源之一 |
| AI智能管理模块（role） | **结算对接** | 渠道分润统一走 profit_ledger（复用 record_external_settlement） |
| message（消息模板） | **触达对接（P2）** | 内容定时分发提醒经模板群发（当前存储模拟，接网关后即真实触达） |
| ad（站内广告位） | **不合并** | 站内展示与本模块站外获取互不干涉；预算控制思路可后移给ad |
| 24号合规监控 | **审核联动** | AI生成内容过合规词库（复用广告禁用词+敏感词），酒类文案强制健康警示 |

---

## 4. 六大子系统设计

### 4.1 子系统一：AI内容工厂

**流程**：选题录入（或AI从ROI数据选题）→ 大模型生成多平台变体 → 规则引擎合规审核 → 入内容库 → 分发。

- **选题库**（topics）：`{topicId, title, angle(文化/场景/工艺/优惠), keywords, source(manual/ai_roi), status}`
- **内容生成**：P0 规则引擎 B 级（模板+变量组合，预留大模型接口 `generate_content(topic, platform)`）；每选题产出四种平台变体：
  - 小红书笔记（标题+正文+标签+emoji排版）
  - 抖音15s口播脚本（钩子-卖点-行动指令三段式）
  - 朋友圈文案（50字内+短链）
  - SEO长文（关键词布局，P1）
- **合规审核**：复用广告禁用词体系（极限词/绝对化/医疗暗示）+ 酒类强制"过量饮酒有害健康"警示 + 18周岁提示；审核不过→重生成或人工改
- **内容库**（contents）：`{contentId, topicId, platform, body, hashtags, status(draft/approved/rejected/published), complianceScore}`

### 4.2 子系统二：智能短链系统（修复最核心断链）

- **短链端点**：`GET /r/{code}` —— 点击即302跳转落地页，落匿名点击记录；code 支持三种：
  - 会员矩阵码（ZXBJ-xxx）→ 落地注册页带邀请关系
  - KOL推广码（KOLxxx）→ 落地产品页带博主归因
  - 活动短码（本模块生成 A-xxxx）→ 落地活动页
- **UTM解析**：短链可携带 `?utm_source=&utm_medium=&utm_campaign=`，解析后与code归因合并
- **AB落地页**（P1）：同一短链按权重分流到不同落地页，转化率对比
- **点击记录**（clicks）：`{clickId, code, utmSource, utmMedium, utmCampaign, ip, userAgent, referer, landingPath, at}`——**匿名可落库**（click_id 体系），不要求注册

### 4.3 子系统三：匿名点击追踪与归因

- **匿名ID**：每次点击生成 click_id；注册时前端回传注册前最后点击的 click_id（或短链落地页种本地存储），后端完成"匿名→实名"归并
- **归并规则**：注册请求携带 clickId → 反查点击记录 → 得到渠道+推广人 → 写 traffic lead（状态 registered）+ promotion 绑定关系（若是ZXBJ码）——**一次点击，三套体系同时归因**
- **转化链**：点击（匿名）→ 注册（归并）→ 下单（`update_lead_status` 推进，本模块补路由暴露）

### 4.4 子系统四：统一归因中心

- **归因总表**（attributions）：`{clickId, code, channel, promoterId, influencerId, memberId, registeredAt, orderId, orderAmount, commission}`
- **数据来源**：短链点击表 + 注册归并 + 下单回调（traffic commission 已有钩子）
- **报表**：按渠道/平台/内容/推广人四个维度出 点击→注册→下单→GMV→佣金 漏斗与ROI
- 本表是 ROI 引擎与内容选题的数据底座

### 4.5 子系统五：ROI智能分配引擎（低成本核心）

- **渠道ROI** = 渠道归因GMV ÷ 该渠道奖励支出（矩阵奖励+佣金+KOL分成）
- **再分配**：周期性扫描（对齐 AI智能管理模块 sweep 风格）——ROI高于均值的渠道，其矩阵奖励额度/佣金系数上调（+10%~20%封顶）；持续低于阈值的下调或内容停发
- **预算上限**：月度奖励总预算池（可配），分配调整在池内此消彼长（不新增总成本）
- **内容联动**：ROI高的渠道/选题角度进入AI选题库 source=ai_roi（数据回流）

### 4.6 子系统六：AI-SEO（P1）

- 关键词库（竹香型白酒/婚宴用酒/山东特产酒等）+ AI长文生成 + sitemap/robots 输出 + 站内TDK建议
- 搜索流量经短链体系归因（自然搜索作为 channel=seo）

---

## 5. 数据模型（新表）

| 表 | 关键字段 | 说明 |
|---|---|---|
| attract_topics | topicId/title/angle/keywords/source(manual/ai_roi)/status | 选题库 |
| attract_contents | contentId/topicId/platform/body/hashtags/complianceScore/status(pending/approved/rejected/published) | 内容库 |
| attract_short_links | code(A-xxxx)/targetType(promotion/influencer/activity)/targetId/landingPath/utmDefault/active | 活动短码（ZXBJ/KOL码复用现有，不重发） |
| attract_clicks | clickId/code/utmSource/utmMedium/utmCampaign/ip/userAgent/referer/landingPath/at | 匿名点击（核心增量） |
| attract_attributions | clickId/code/channel/promoterId/influencerId/memberId/registeredAt/orderId/orderAmount/commission | 统一归因总表 |
| attract_channel_budgets | channel/monthlyPool/currentRate(奖励系数)/roi/score/history | ROI分配账本 |

存储沿用现有模式（内存dict/Redis Hash，键前缀 `zhuxiang:`，写操作 `lock:` 分布式锁）。

---

## 6. API设计（P0 初版 22 端点）

**内容工厂（admin，7）**：
- `POST /api/attract/topic` 选题录入　`GET /api/attract/topics`
- `POST /api/attract/content/generate` AI生成多平台变体（传topicId）
- `GET /api/attract/contents`（按平台/状态筛选）　`GET /api/attract/contents/{id}`
- `POST /api/attract/contents/{id}/review` 合规审核
- `POST /api/attract/contents/{id}/publish` 发布（绑定分发渠道与码）

**短链与追踪（公开，4）**：
- `GET /r/{code}` 短链跳转（302+落匿名点击，**无鉴权**）
- `POST /api/attract/short-link` 活动短码创建（admin）
- `GET /api/attract/clicks` 点击流查询（admin）
- `POST /api/attract/attach` 注册归因上报（前端注册时携带clickId，公开）

**归因与报表（admin，4）**：
- `GET /api/attract/attributions` 归因总表（四维筛选）
- `GET /api/attract/report/funnel` 漏斗报表（点击→注册→下单→GMV）
- `GET /api/attract/report/channel` 渠道ROI报表
- `GET /api/attract/report/content` 内容效果报表

**ROI引擎（admin，3）**：
- `POST /api/attract/roi/rebalance` 渠道预算再分配（周期sweep）
- `GET /api/attract/roi/budgets` 分配账本查询
- `GET /api/attract/roi/suggest-topics` AI选题建议（数据回流）

**联动（4）**：
- `POST /api/attract/lead/{lead_id}/status` 补齐 traffic lead 状态推进路由（修复空白#6）
- `GET /api/attract/seo/sitemap`（P1占位）　`GET /api/attract/seo/robots`（P1占位）　`POST /api/attract/seo/keywords`（P1占位）

---

## 7. 测试与验收

- 短链闭环E2E：生成内容→发活动短码→模拟站外点击（302+匿名落库）→注册带clickId→归因三合一（lead+绑定+归因表）→模拟下单→漏斗报表全链断言
- 合规：AI生成内容命中禁用词→拒绝/重生成；酒类文案必带健康警示
- ROI：两渠道造数（一高一低）→ rebalance 后高ROI渠道奖励系数上调、低下调、总池不变
- 幂等/并发：短码创建唯一、点击落库无锁竞态、注册归并幂等

## 8. 开发分期

| 阶段 | 内容 |
|---|---|
| P0 | 短链系统（/r/{code}+匿名点击）+ 注册归因三合一 + AI内容工厂（规则引擎）+ 归因总表与漏斗/渠道报表 + ROI再分配引擎 | ✅ 已实现（2026-08-29，36项E2E+promotion 45/traffic 48回归通过） |
| P1 | AI-SEO（关键词/长文/sitemap/robots）+ AB落地页 + message分发提醒 + provider抽象 | ✅ 已实现（2026-08-29，累计51项E2E；大模型API接入留 generate_content_bodies_v2 单点） |
| P2 | 裂变活动插件（海报/任务宝）、广告预算控制（反哺ad模块）、自然搜索归因 |

---

## 附：前置决策项

| 编号 | 内容 | 状态 |
|---|---|---|
| D-10 | 短链跳转 `/r/{code}` 302目标：注册页（优先拉新）vs 按码类型分流（ZXBJ→注册页/KOL→产品页/活动→活动页） | ✅ 已确认：**按码类型分流**（2026-08-29，landing_for_code_type 落地） |
| D-11 | P0内容生成实现：规则引擎B级（模板组合，立即可用）vs 直接口径预留大模型接口 | ✅ 已确认：**规则引擎B级 + generate_content_bodies 抽象点**（后续接大模型仅替换该单方法，2026-08-29） |
| D-12 | ROI再分配的作用对象：promotion矩阵奖励额度 / traffic佣金系数 / 两者兼有 | ✅ 已确认：**两者兼有**（currentRate 系数同时作用于两套奖励，统一池内此消彼长，2026-08-29） |

> P0 实现说明（2026-08-29，36项E2E通过）：
> - 短链 `/r/{code}` 为公开302端点（无鉴权），落地页自动追加 `clickId` 参数供注册回传；三类码分流落地；UTM优先于码默认渠道；
> - 合规审核：100 - 禁用词×30 - 缺警示×35 - 缺年龄提示×35，<70 不可通过（四平台模板均内置警示+年龄提示，生成即达标）；
> - ROI均值含全部渠道（样本不足渠道ROI计0），避免单渠道时 roi==avg 永不调整的数学死锁；
> - 途中修复：抖音/朋友圈模板补年龄提示（原65分不达标）、朋友圈{link}占位符补全。
