# 40号·平台流量DV博主模块 完整交付总结 v1.0

> 交付周期：2026-09-02（单日完成 P0→P3 全部六期 + 两次实机验收 + 前端看板）
> 交付状态：**全部规划项落地收官**，14 个 commit，宿主机专项 356 项 + 实机验收 76 项全绿，全量回归全程 1189/0 零新增失败
> 定位回顾："借大V的流量，养自己的站"——AI 雷达持续侦测站外博主最新作品，全自动完成"理解作品 → 评估引流价值 → 合规制作跟随内容 → 挂专属短码发布"，站外私域流量精准导入本站

---

## 一、交付总览

| 分期 | commit | 交付内容 | 专项测试 |
|------|--------|----------|----------|
| P0 核心闭环 | `9f7f521` | 博主池/作品雷达/评分三档/跟随流水线/发布三限/归因闭环 | 101 项 |
| P0 实机验收 | `6a5c3bc` | Docker 容器 Redis 模式 52/52 | — |
| P1 学习闭环 | `703891f` | Hedge 回流/层2权重自进化/auto-paused 止损/沉淀窗口/learning 四端点 | 50 项 |
| P2a 防污染批 | `a73c7c9` | 点击质量门三层/连续奖励[-1,1]/fraud 止损/eta 覆盖 | 45 项 |
| P2b 进化批 | `621707a` | 探索三件套/效率调制/时间衰减/平台偏置/健康熔断 | 45 项 |
| P2 实机验收 | `ec2161b` | 全量口径 Docker 实机 76/76（P0+P1+P2a+P2b） | — |
| 前端看板 | `19b3858` | ai-blogger-dashboard 十区块，零 CORS/JS 报错 | 浏览器实测 |
| citation 存证 | `804c003` | EVIDENCE_TYPE_CITATION 第五类型/版权溯源留痕 | 合规 58 项 |
| P3a hub 注册 | `75def8b` | 第 9 能力 blogger.traffic/意图关键词/面板 chips | hub 125 项 |
| P3b 真实源 | `8cc6320` | proxy 轨适配器/令牌桶限速/熔断半开/契约归一 | 27 项 |
| P3c 账号矩阵 | `aab5451` | LRU 轮询/单账号日帽/限流冷却换号重试/封号 | 39 项 |
| P3d 评论区截流 | `8e3844a` | Mock 大V源/三因子评分/两段式共鸣回复/存活检查 | 49 项 |

**文件清单**（新增 12 个核心文件 + 改 8 个）：

```
backend/
  repositories/blogger_repository.py    数据层(池/作品/跟随/审计/偏置/账号/评论 七表)
  services/work_radar_service.py        作品雷达(Mock增量源+指纹去重+探索三件套)
  services/work_agent_service.py        四步 Agent(vision+三段式生成+搬运检测)
  services/blogger_service.py           核心编排(决策/三审/三限/归因/学习/进化/健康)
  services/blogger_scheduler.py         调度器(雷达/发布/学习+周维护)
  services/blogger_source_adapter.py    P3b 真实源代理适配器
  services/blogger_account_service.py   P3c 发布账号矩阵
  services/comment_intercept_service.py P3d 评论区截流
  routes/blogger_routes.py              33 端点
  test_blogger_p0/p1/p2/p2b/p3/p3c/p3d.py  专项测试七套
  verify_blogger_live.py                实机验收脚本
ai-blogger-dashboard.html + js/blogger-dashboard.js  前端看板
```

---

## 二、六期技术架构

### 2.1 P0 核心闭环（博主池 → 侦测 → 评分 → 跟随 → 发布 → 归因）

**一句话模型**："AI 盯博主 → 新作品秒级侦测 → 价值评分决策 → 合规跟随内容全自动生成 → 挂 KOL 码发布 → 点击注册归因 → 效果回流让 AI 越盯越准。"

- **博主池**：8 位种子博主（四平台×四领域×三档粉丝量级），权重分档（百万级 1.0/五十万+ 0.8/五万+ 0.6），领域准入门槛（酒/美食/礼品/生活），种子感知 ID 分配
- **作品雷达**：Mock-first 增量源（种子=平台+博主+日期+6h槽位，同槽位确定性可测去重）；SHA256 指纹去重（48h 窗口）；风险一票否决（洪水等 7 词直接 discarded 不进评分）
- **评分决策**：`BloggerWorkScorer`（**第 21 AI 学习档案，batch 7**）五因子——博主权重 0.25/品牌契合 0.25/作品热度 0.20/引流潜力 0.15/竞争密度 0.15；决策三档 ≥70 auto_follow / 50-70 manual_queue / <50 pass
- **跟随流水线**：四步 Agent（vision 封面理解→粉丝画像→三段式生成→出处自查），三级降级（glm-5.3→glm-4-flash→规则模板轨）产出永不中断；**合规跟随范式**——转述/致敬/引荐三段式 + @原作者 + 出处声明 + 5-gram 搬运检测（>40% 硬拒）；KOL 短码挂链（traffic KOL码→attract 活动码兜底）；出处声明区块链存证（**citation 专用类型**）
- **发布三限**：黄金时段（复用 36号 GOLDEN_WINDOWS）+ 单日上限 10 + 同博主冷却 1条/24h + 跟随间隔 4h 错峰；通道复用 36号三态回执（real/mock/mock_fallback）+ 百度 SEO 推送
- **归因闭环**：短码 → attract 点击 302+clickId → 注册三合一归并 → 下单回写 → 博主维度全口径（点击/注册/GMV）+ traffic KOL 体系归因合并

### 2.2 P1 学习闭环与 AI 权重自进化

**两层权重，两条进化通道**：

| 层 | 对象 | 引擎 | 机制 |
|----|------|------|------|
| 层1 因子权重 | 五因子全局权重 | 复用 ai_learning Hedge 第21档案 | clicks>0→correct±1 乘性更新+护栏+champion/challenger 晋升 |
| 层2 博主权重 | 池内个体 weight | 模块自有规则步进 | GMV+0.05/点击+0.02/零引流-0.05，clamp ±0.3 |

- 奖励语义：clicks>0 → auto_follow 决策正确；GMV 不进 Hedge（留层2，避免"有点击无转化"误判）
- **auto-paused 止损**：连续 3 条零引流出池（再罚一档）；恢复保留 weightAdjust（教训不白给）
- 24h 沉淀窗口批量回流（短内容流量 80% 集中在 24h 内）
- API：feedback/collect/run/status 四端点

### 2.3 P2a 点击质量门与连续奖励（防污染前提批）

- **三层质量门**：L1 同 IP 去重 → L2 /24 聚簇 >60% → quality×0.3 → L3 爬虫 UA/连点 <2s → quality×0.2；双命中 fraudSuspect；小样本豁免
- **连续奖励** reward∈[-1,1]：零引流 -0.1（弱惩罚）/爆款 +0.9/quality 折扣；引擎 `_feedback_reward` 缺省回退 ±1（**既有 21 档案零影响**）
- **fraud 止损**：fraudSuspect → reward 强制 -0.1 + 等效零引流 + fraudStreak+1；连续 2 次 fraud_suspect 出池
- 层2 正向步长 × clickQuality；eta 0.5→0.3 幂等覆盖（不覆盖 admin 调参）

### 2.4 P2b 进化批（探索/调制/校准/健康）

- **探索三件套**（破马太效应）：UCB 冷启动（新博主 3 作品保底置顶）/ ε=5% 低权重插队 / **缓刑复扫**（止损 7 天自动复扫，引流>0 自动 reactivate）
- **效率调制**：近 30d 引流效率池内分位 → 步长 ×1.5/×1.0/×0.6
- **时间衰减**：weightAdjust 每周向 0 回归 10%，近零归零——"可重返"闭环
- **平台校准偏置**：平台引流率差 ×λ=20 clamp ±8 分，`decide_work` 校准并重路由三档（68 分作品在高效平台可过线）
- **健康监控**：层2 震荡（7d 翻转≥3 → 冻结 14d 步长置0）/ 层1 退化（champion 对齐度连续 3 轮降 → 回滚默认权重）/ 污染熔断（fraud 占比>30% 暂停学习）
- 周维护调度（衰减+校准+巡检）；health/calibrate 两端点

### 2.5 P3 真实化与矩阵（四期）

- **P3a hub 注册**：第 9 能力 `blogger.traffic`（admin 域），7 条意图关键词（博主池/博主引流/KOL引流等），admin 面板 chips——全站 AI 助手自然语言触达
- **P3b 真实源 proxy 轨**：`BLOGGER_SOURCE_MODE=proxy` + `BLOGGER_{PLATFORM}_URL` 即插即用；统一契约与 mock 同构（雷达零改动）；令牌桶 QPS 限速 + 连续失败熔断半开 + Bearer 鉴权；一切异常回退 mock
- **P3c 账号矩阵**：LRU 轮询选号 + **第④限单账号日帽 3 条** + 限流 cooling 24h 换号重试 + 连续失败封号 banned + 跨日计数重置；无账号 mock 轨不阻断
- **P3d 评论区截流**（第二引流形态，不占发布三限）：Mock 大V源 → 三因子评分（评论热度 0.45/时效 0.35/品牌契合 0.20）→ 两段式共鸣回复（观点共鸣+软性提及+短码）→ 评论口径三审 → 账号矩阵发布 → **24h 存活检查**（被删→deleted+账号降权）→ 短码归因；单作品仅 1 条护栏

### 2.6 前端看板（十区块）

全景统计（池/作品/跟随/归因/三限）/博主池（权重进化字段+止损徽章）/作品流（**评分快照五因子条形图**+平台偏置）/跟随内容（三段式文案+回流指标）/待人工队列/雷达发布操作/学习闭环（因子权重条形图）/进化榜/健康三层视图/平台偏置——实机浏览器实测零 CORS/JS 报错。

---

## 三、验证体系

| 层级 | 规模 | 结果 |
|------|------|------|
| 宿主机专项（7 套） | 356 项 | 全绿 |
| 实机 Docker 验收（P0 口径） | 52 项 | 一次全绿 |
| 实机 Docker 验收（全量口径） | 76 项 | 全绿 |
| hub/decision 专项 | 125+58 项 | 全绿 |
| **全量回归 pytest** | **1189 项** | **14 个 commit 全程 0 新增失败** |

**实机验收覆盖**：种子池/CRUD/雷达指纹去重/风险否决/决策三档/三段式+KOL码+citation存证/三限逐一拦截（冷却/间隔/日帽）/归因闭环（302→注册→下单→GMV）/回流幂等/Hedge 实机学习/**质量门真实链路 L1 去重**/冷启动探测/健康三层视图/偏置 calibrate。

**过程发现并修复的真实缺陷**（均被测试体系捕获）：
1. 雷达指定 ID 扫描未过滤 paused 博主（P1 测试发现）
2. `_evolve_blogger_weight` 冻结分支 `reason` 未初始化（P2b 测试发现）
3. 账号表名与 `_ensure_store` 不一致导致 KeyError（P3c 测试发现）

---

## 四、环境变量与启用配置

### 默认行为（零配置 = Mock-first 全链路可跑）
全部功能默认 mock 轨：Mock 博主池/作品源/大V源、mock 发布回执、调度器默认 off。

### 真实化启用（资质就绪后免改代码）
```
# P3b 真实作品源(自建爬虫代理)
BLOGGER_SOURCE_MODE=proxy
BLOGGER_DOUYIN_URL=http://proxy:port/works      # +XIAOHONGSHU/WEIBO/WECHAT_CHANNELS
BLOGGER_DOUYIN_API_KEY=xxx                       # 可选 Bearer 鉴权
BLOGGER_SOURCE_QPS=1.0                           # 代理限速

# 发布真实化(复用 36号通道)
PROMO_CHANNEL_MODE=real
PROMO_CHANNEL_{PLATFORM}_KEY=xxx

# 后台调度(默认 off)
BLOGGER_RADAR_AUTO=on / BLOGGER_PUBLISH_AUTO=on / BLOGGER_LEARNING_AUTO=on
```

### 关键调参（均有保守默认）
`BLOGGER_DAILY_CAP=10` / `BLOGGER_FOLLOW_COOLDOWN_HOURS=24` / `BLOGGER_FOLLOW_GAP_HOURS=4` / `BLOGGER_ACCOUNT_DAILY_CAP=3` / `BLOGGER_FEEDBACK_SETTLE_HOURS=24` / `BLOGGER_PROBATION_DAYS=7` / `BLOGGER_EXPLORE_EPSILON=0.05` / `BLOGGER_ETA_OVERRIDE=0.3`

---

## 五、API 清单（33 端点）

| 域 | 端点 |
|----|------|
| 博主池(7) | POST/GET /pool · GET/PUT/DELETE /pool/{id} · POST /{id}/pause\|activate |
| 雷达侦测(4) | POST /radar/scan · GET /works · GET /works/{id} · POST /works/{id}/decide |
| 跟随流水线(5) | POST /works/{id}/manual-decide · POST /works/{id}/follow · GET /follows · POST /follows/{id}/review · GET /reviews/pending |
| 发布(2) | POST /follows/{id}/publish · POST /publish/run |
| 报表(2) | GET /report/overview · GET /report/blogger/{id} |
| 学习闭环(6) | POST /learning/feedback\|collect\|run\|calibrate · GET /learning/status\|health |
| 账号矩阵(6) | POST/GET /accounts · GET /accounts/overview · POST /{id}/activate\|ban · DELETE /{id} |
| 评论区截流(8) | POST /comments/scan\|generate · GET /comments · POST /{id}/review\|post\|survival · GET /comments/report · GET /{id}/attribution |

---

## 六、设计原则沉淀

1. **Mock-first 一以贯之**：所有外部依赖（平台 API/LLM/代理/账号）均有确定性 mock 轨，产出永不中断；真实化只加开关不改结构
2. **复用不合并**：36号通道/11号归因/37号存证/35号中枢/全局 ai_learning——七模块基建复用，40号只写"博主特有"的差分逻辑
3. **合规即架构**：三段式范式/搬运检测/出处存证/三审词库不是附加检查，是流水线的结构性环节
4. **学习分层**：全局语义（因子权重）交给 Hedge 引擎，个体语义（博主权重）用可解释规则步进——样本量决定工程选型
5. **护栏带边界**：所有进化量（adjust ±0.3/bias ±8 分/步长 0.05）有界可回滚，配健康熔断兜底
6. **测试即验收**：356 专项 + 76 实机 + 1189 全量，三个真实缺陷全部由自建测试体系在交付前捕获

---

## 七、后续演进方向（超出现有规划，供参考）

- **真实源协议冻结待接**：P3b 代理契约已冻结（`GET {endpoint}?account&cursor&limit` → `{works:[...]}`），代理侧按契约实现即插即用
- **评论区截流真实化**：评论发布回执当前 mock，真实评论 API 随平台资质接入（复用账号矩阵+存活检查框架）
- **P3d 学习回流**：评论归因数据可回流层2（评论质量作为账号维度信号），当前仅 failStreak 降权
- **跨模块选题去重**：40号博主跟随与 36号热点蹭点选题互查（设计文档风险表已预留口径）

---

*文档生成于 40号模块全周期收官（commit `8e3844a`），配套：[设计文档](AI智能平台流量DV博主模块40_设计文档.md) · [实机验收脚本](../backend/verify_blogger_live.py) · [前端看板](../ai-blogger-dashboard.html)*
