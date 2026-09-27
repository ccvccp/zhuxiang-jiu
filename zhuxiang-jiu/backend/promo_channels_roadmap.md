# 36 号发布平台接入路线图（四平台调研 · 2026-09-26）

背景：五平台发布通道中微博已接入（token 获取中，协议实测校准），
小红书（2026-09-26）与抖音图文（2026-09-27）RPA 通道已落地
（第五/六节 SOP），其余平台经官方文档与社区实证调研，结论如下。

---

## 一、四平台结论速览

| 平台 | 官方发布 API | 资质门槛 | 可行路径 | 优先级 |
|---|---|---|---|---|
| 抖音 | ❌ 无简单文本发布 API；视频发布需开放平台能力 | API 需**企业主体**（营业执照+对公认证）；网页图文个人号即可 | **RPA 图文（已落地）**：creator.douyin.com 网页版发图文（个人号）；企业资质 → open.douyin.com 视频发布（代码端点已预留）为升级项 | 短期已通(RPA) / 中期(API 升级) |
| 小红书 | ❌ 官方 API **无发笔记能力**（企业资质也只开放广告投放/商品数据/商家运营；Content Publishing 面向品牌大客户合作制，5 步商务流程） | 企业/商家自研资质也不解决发布 | **创作者中心网页版 RPA**（creator.xiaohongshu.com 发图文笔记，个人号即可，浏览器自动化） | 短期最优 |
| 微信朋友圈 | ❌ 个人朋友圈无官方 API | — | **无合规路径**（WTAPI 类为微信协议逆向 RPA，封号+法律风险，禁用） | 放弃 |
| 微信视频号 | ❌ 官方明确无视频发布 API（开放能力仅橱窗/留资/直播数据） | — | 视频号助手网页版 RPA（channels.weixin.qq.com）技术上可行，**但需视频内容生产线**（36 号当前为图文短文案） | 远期 |

## 二、关键洞察

1. **合规 API 矩阵实际很窄**：微博（已就绪）+ 抖音（企业资质后）。
   其余平台官方均未开放第三方内容发布。
2. **RPA 是小红书的现实最优解**：官方无 API 但创作者中心网页版
   可发图文笔记——与 36 号内容形态（标题+正文+hashtag）完全
   匹配；业界主流方案（xhs-skill 类），风险=平台反自动化检测
   （需低频+拟人化，配合既有日上限 5 条闸门天然合规）。
3. **抖音网页图文解锁 RPA 路线（2026-09-27）**：creator.
   douyin.com 个人号网页版支持图文上传——与小红书同范式，
   免企业资质、免视频生产线；开放平台视频 API 降级为资质
   就绪后的升级项（配凭证自动切真实轨）。
4. **朋友圈放弃**：无合规路径，逆向协议不可用于生产。
5. **代码侧现状**：四平台端点/鉴权风格已实现（promo_channel_
   service.py），`PROMO_CHANNEL_{X}_URL` 可覆盖——抖音资质就绪
   后零代码改动接入；小红书 RPA 需新增 RPA 通道实现类（browser
   自动化发布 → 统一回执结构）。

## 三、推荐路线（您做选择题）

### 短期（0 成本，现有资源）
- **微博**：token 获取完成后灰度真发（进行中，断点见
  weibo_access_token_guide.md）
- **小红书 RPA**：个人号 + 创作者中心 + 浏览器自动化发布通道
  ——若确认执行，我实现 `RPAChannelService`（browser_use
  agent 驱动发布+统一回执），并挂接 36 号发布队列
- **抖音 RPA**（2026-09-27 已落地）：个人号 + creator.
  douyin.com 图文发布，复用小红书 RPA 基建（RPA_PLATFORMS
  加 douyin，见第六节 SOP）

### 中期（需资质）
- **抖音开放平台 API（升级项）**：元禾生物科技主体若持有营业
  执照 → 开放平台企业认证 → 视频发布能力申请 → 配
  `PROMO_CHANNEL_DOUYIN_URL`（代码已就绪，配置凭证后自动从
  RPA 切真实 API 轨）；注意需**视频内容**，36 号当前图文文案
  需先建视频生产线
- **微信公众号（五平台外的增量建议）**：认证服务号有官方
  免费**群发 API**（素材上传+群发接口，完全合规）——内容形态
  匹配度最高的官方渠道，值得纳入发布矩阵

### 弃用
- 微信朋友圈（无合规路径）
- 任何协议逆向方案（WTAPI/ipad 协议类——封号与合规风险）

## 四、36 号当前发布矩阵终态

```
合规即时:  百度SEO收录(已通)
RPA 已落地: 小红书(图文, 第五节) + 抖音(图文, 第六节) + 微博(CLI桥, 第七节)
           + 视频号(视频产线RPA, 第八节, 2026-09-27)
资质后置:  微博企业认证建应用(方案C全自动升级) + 抖音开放平台视频API + 公众号(认证服务号, 建议新增)
永久放弃:  朋友圈
```

灰度闸门（日上限 5 + 强制人工 review + 合规过滤）对任何新通道
自动生效——新增通道只需实现 `publish_to_platform` 统一回执。

## 五、小红书 RPA 发布通道 SOP（已立项落地）

**架构**（promo_rpa_channel_service.py + channel 层分流）：
发布队列出队时小红书内容挂 `rpa_pending` 回执（不入
mock_fallback）→ `GET /api/promo/rpa/pending` 列待发布清单
→ 对话内 browser agent 执行创作者中心发布 →
`POST /api/promo/rpa/{contentId}/receipt` 登记回执
（成功=笔记 URL，失败=error 留痕保留重试）。

**操作规程**（对用户说「发布小红书待发内容」触发）：
1. 拉清单：`GET /api/promo/rpa/pending`（X-Role: admin）
2. 无内容 → 汇报"无待发布"结束
3. browser agent 打开 creator.xiaohongshu.com：
   - 未登录 → 移交用户扫码（首次一次，之后登录态持久）
   - 已登录 → 继续
4. 逐条发布：发布页填 **清单原文**（标题/正文/话题——
   RPA 层不改写文案，合规责任留在三审闸门），图文类型；
   **配图从清单 coverUrl 下载品牌卡片**（确定性 Pillow 渲染
   `GET /api/promo/rpa/{id}/cover.png`，竹绿主题+标题+要点+
   合规条——图文笔记强制配图的无人干预供给，2026-09-26
   立项；正文 emoji 卡面剥除、笔记正文不受影响），
   发布成功后从页面取笔记 URL。
   **发布按钮 UI 坑位（2026-09-27 实证）**：底部按钮栏位于
   闭合 shadow DOM 中默认不渲染——需先打开「定时发布」开关
   使按钮栏渲染出现，再切回「立即发布」（按钮栏保持可见且
   文案变"发布"）后点击发布；窄视口遮挡控件时用事件注入
   操作开关与按钮
5. 逐条登记回执（成功 noteUrl / 失败 error；
   `register_rpa_receipt.py <cid> <url> <noteId>` 平台通用）
6. 汇报发布结果（清单→成功 URL/失败原因）

**笔记 URL 验证注意（2026-09-27 实证）**：小红书网页版对
未登录/无 xsec_token 的裸 explore 链接一律拦截
（error 300031「当前笔记暂时无法浏览」，所有笔记同策略非
单篇问题）——线上验证需登录态或经个人主页官方入口带
token 访问，或直接用 App 查看确认。

**风控合规**：
- 只有通过强制人工 review 的内容才可能进入清单（未过审
  内容在闸门处已被拦）
- 日上限 5 条由队列闸门保证——RPA 单次会话最多消费当日余量
- 低频 + browser agent 拟人操作，规避平台反自动化检测
- 铁律：不做协议逆向；发布文本与过审内容逐字一致

## 六、抖音 RPA 发布通道 SOP（2026-09-27 落地）

**架构**（复用小红书 RPA 基建，`RPA_PLATFORMS` 加 douyin）：
发布队列出队时抖音内容同样挂 `rpa_pending` 回执 →
`GET /api/promo/rpa/pending`（多平台清单，`platform` 字段区分）
→ 对话内 browser agent 打开 creator.douyin.com 发布图文 →
`POST /api/promo/rpa/{contentId}/receipt` 登记回执（成功=
作品 URL，失败=error 留痕保留重试）。封面复用
`/api/promo-cover/{id}.png`（1080×1440 竖版 3:4，抖音图文
同规格，按内容渲染与平台无关）。

**操作规程**（对用户说「发布抖音待发内容」触发）：
1. 拉清单：`GET /api/promo/rpa/pending`（X-Role: admin），
   取 `platform=douyin` 条目
2. 无内容 → 汇报"无待发布"结束
3. browser agent 打开 creator.douyin.com 图文发布页：
   - 未登录 → 移交用户扫码（首次一次，之后登录态持久）
   - 已登录 → 继续
4. 逐条发布：上传封面（清单 coverUrl 下载品牌卡片）→ 填
   标题/正文/话题（**清单原文，RPA 层不改写文案**，合规
   责任留在三审闸门）→ 发布 → 从页面取作品 URL
5. 逐条登记回执（成功 noteUrl / 失败 error）
6. 汇报发布结果（清单→成功 URL/失败原因）

**风控合规**：同第五节（强制人工 review + 日上限 5 +
低频拟人化；文案红线同三审闸门：无"华南理工"、专利未获
授权前不标注"国家发明专利"、无医疗功效表述）。

**与开放平台 API 的关系**：RPA 为现实主通道；元禾生物
企业资质就绪后可申请开放平台视频发布 API，配置
`PROMO_CHANNEL_DOUYIN_KEY/URL` 后自动切真实 API 轨（代码
端点已预留），届时视频内容生产线为前提。

## 七、微博 CLI 桥 RPA 发布通道 SOP（2026-09-27 落地）

**架构**：微博并入 RPA_PLATFORMS（无 key → rpa_pending），但
执行形态与前两平台不同——非浏览器自动化，走**本机官方
weibo-cli**（@weibo-ai/weibo-cli，npm 发行）。CLI 网关 token
由其 keychain 自管轮换（直连 c.api.weibo.com 不可行，21332
实证）。发布主体：微博账号「姜大漂亮姐姐」（UID
7200614644，正式服务 23000C 余额，写限 60 次/小时）。

**本机运行环境**（发布机 = 开发机）：
- 便携 node v20：`d:\网站架构设计\nodejs\node-v20.18.2-win-x64`
- CLI 凭据：keychain，`USERPROFILE` 重定向至
  `d:\网站架构设计\.weibo-home`（沙箱内可写）
- 每次调用前置：`$env:Path="...node-v20...;$env:Path"`；
  `$env:USERPROFILE="d:\网站架构设计\.weibo-home"`

**操作规程**（对用户说「发布微博待发内容」触发）：
1. 拉清单：`GET /api/promo/rpa/pending`（X-Role: admin），
   取 `platform=weibo` 条目
2. 无内容 → 汇报"无待发布"结束
3. 封面下载：`https://zxjiu.com/api/promo-cover/{id}.png`
   → 本地临时文件
4. 发布（图文，共 20C）：
   - `weibo statuses upload_pic --pic <本地文件>` → 取 pic_id（5C）
   - `weibo statuses upload_url_text --pic_id <pic_id>
     --status "<清单原文>" --mblog_statement 1`（15C）→ 取 idstr
   - **`--mblog_statement 1` 必带**（AI 生成内容平台合规打标，
     36 号内容为 GLM/rule 生成；缺失责任自负——平台原文）
   - 纯文本降级：`weibo statuses update --status "<文案>"
     --mblog_statement 1`（15C）
5. 回执登记：`register_rpa_receipt.py <cid>
   https://m.weibo.cn/status/<idstr> <idstr>`（m.weibo.cn
   接受十进制 id，免 base62 换算）
6. 汇报发布结果

**风控合规**：同第五节（强制人工 review + 日上限 5）；
AI 打标为微博侧硬要求；`visible=0` 默认公开。

**前向兼容（方案 C 升级路径）**：元禾企业认证通过后建
网页应用配 `PROMO_CHANNEL_WEIBO_KEY`（长命 OAuth token）→
key 存在时自动切经典 share.json API 轨（代码已就绪），
RPA 分支天然让位。认证材料：公司名称/营业执照号/法人
身份证正反面/营业执照副本（控制台 identity/edit 提交）。

## 八、视频号视频生产线 RPA 发布通道 SOP（2026-09-27 立项）

**架构**：视频号无官方视频发布 API（开放能力仅橱窗/留资/
直播数据）——上游**视频产线** + 下游**网页版 RPA** 两段式：
- 产线（开发机跑）：`build_promo_video.py <contentId>` →
  生产拉内容 → 四页品牌卡渲染（封面/卖点/行动/合规，
  1080×1440 竖版，PromoCoverService 同源色板字体+IP 角标）
  → 本机便携 ffmpeg（`d:\网站架构设计\ffmpeg\`，
  `FFMPEG_PATH` 可覆盖）xfade 合成 16.2s 无声轮播
  （4.5s/页 + 0.6s 淡入淡出，yuv420p/faststart）
- 发布（触发式）：wechat_channels 入 RPA_PLATFORMS（无 key
  → rpa_pending），对用户说「**发布视频号待发内容**」→
  channels.weixin.qq.com 上传 mp4 + 清单原文文案 → 发布 →
  `register_rpa_receipt.py` 登记回执（登录形态见下方首发
  实证——自动化浏览器被微信风控拦，当前为用户手动发布）

**操作规程**：
1. 拉清单取 platform=wechat_channels 条目
2. 无内容 → 「继续」跑内容周期生成 wechat_channels 平台内容
   过审入队（规则模板：图文短句+封面文案口径）
3. `python build_promo_video.py <cid>`（backend 目录）产出
   `promo_videos/promo_video_<cid>.mp4`（页卡+视频一并产出）
4. browser agent 发布（上传视频文件用分块 base64→File→
   DataTransfer 注入或本机临时 http 服务供浏览器拉取——
   agent 端既有先例技术）
5. 回执登记（成功作品 URL / 失败 error 留痕重试）

**登录坑位与首发实证（2026-09-27）**：微信对自动化浏览器
（CDP）扫码登录风控拦截——手机端确认成功（收到登录通知），
网页端不落登录态、二维码循环刷新死锁；同账号用户自有
Chrome 秒登录。首发由用户在自己浏览器手动完成（对话供给
mp4 路径 + 逐字文案 + 表单指引：短标题用过审标题截取、
合集首条跳过、AI 声明若表单提供须勾选），回执照常登记闭环。
后续 RPA 化前置条件 = 自动化浏览器登录态移植（cookie 导入
profile 或非 CDP 浏览器方案）。作品 URL 形态：
`https://weixin.qq.com/sph/<id>`（#39 = AonC54eLb）。

**风控合规**：同第五节；页4 合规页强制健康警示三行；AI 生成
内容若平台提供打标选项须勾选。

**升级路径**：视频产线产物同时适配抖音（视频形态更长效），
后续「发布抖音待发内容」可复用 mp4 走视频发布页。
