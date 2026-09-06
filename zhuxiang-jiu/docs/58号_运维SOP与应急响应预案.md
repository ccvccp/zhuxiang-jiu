# 58号·AI智能优化意图识别模块 运维 SOP 与应急响应预案

> 版本：v1.0（2026-09-06）｜适用代码基线：master ≥ `bc29531`（compose 四变量随 `176c539` 入册）
> 配套文档：[58号_上线部署操作手册.md](58号_上线部署操作手册.md)（部署/灰度/回滚三级预案）｜[58号_模块验收报告.md](58号_模块验收报告.md)
> 日志关键字：`ii58_` ｜ Redis 键前缀：`zhuxiang:ii58:` ｜ 管理面鉴权：`X-Role: admin`；会员面：`X-Member-Id`

---

## 一、开关矩阵（运维态速查）

| 开关 | 默认 | 开放面 | 运维语义 |
|------|------|--------|---------|
| `II58_MODE=off` | ✓ | 仅观测面 9 端点+终审/回流 | **零影响铁律**——决策/会员面全 409 |
| `II58_MODE=shadow` | | +评估留痕+采集流水线+阈值申请 | 观察学习期——会员反馈不开放 |
| `II58_MODE=assist` | | +显式反馈（会员面 X-Member-Id） | 辅助优化期——反馈飞轮开放 |
| `II58_LLM_MODE=on` | off | L2 低置信路由+合成建议（建议性） | LLM 仅建议不入库（共创 pending 范式）——建议最后开启 |
| `II58_LEARN_MODE=on` | off | T+1 六类真值信号回流调度 | 周期 `II58_LEARN_INTERVAL`（默认 86400s，下限 300） |

**不受任何开关影响**：语料终审 `corpus/{id}/review`（active 唯一出口）、标注终审 `labels/{id}/decide`（语料回流）、阈值终审模（calibrate 带 changeId）、回流 `feedback/collect`、全部 GET 观测面——off 态下这些是唯一的"人工兜底通道"。

---

## 二、日常操作 SOP

### 2.1 每日巡检（≤5 分钟，admin）

```bash
B=http://127.0.0.1:8000/api/ii58; H='X-Role: admin'

# ① 健康总览（四区：度量四指标+意图分布+语料+防御含宪法断言）
curl -sf -H "$H" $B/dashboard | python -m json.tool | head -40

# ② 标注队列（pending 深度+五来源分布）
curl -sf -H "$H" $B/labels | python -m json.tool | head -30

# ③ 阈值镜像健康（基线来源+pending 积压告警）
curl -sf -H "$H" $B/thresholds | python -m json.tool | head -25
```

**巡检判定口径**：

| 指标 | 正常 | 异常处置 |
|------|------|---------|
| 度量区 taskCompletionRate | 稳定（业务语料命中稳定） | 陡降→§四 E2（阈值漂移/语料污染排查） |
| 度量区 clarifyAcceptRate | 趋升或稳定 | 持续低位→§三 3.1（语料覆盖缺口） |
| 防御区 mirrorPendingAlert | false | true→§四 E4（阈值镜像积压） |
| 防御区 constitution.scorer33 | true | false→§四 E6（宪法破坏 P1） |
| labels pending | 持续清队 | 积压→§四 E5 |

### 2.2 每周任务

```bash
# ① 语料终审待审队列（pending 态——人工 review 清队）
curl -sf -H "$H" "$B/corpus?status=pending" | python -m json.tool | head -30

# ② 对抗样本覆盖度（gap 对建议构造——混淆域防御）
curl -sf -H "$H" $B/confusables | python -m json.tool

# ③ 澄清率趋势（语料覆盖缺口信号——高 clarify=覆盖不足）
curl -sf -H "$H" "$B/evaluations?state=clarify" | python -m json.tool | head -20

# ④ 容器日志周扫（异常快速定位）
docker logs zhuxiang-jiu-backend-1 2>&1 | grep "ii58_" | grep -E "WARN|ERROR" | tail -30
```

### 2.3 手动触发（不待调度周期）

```bash
# 一轮回流补标（第33档案池双写——幂等 evaluationId 1:1，重复执行零副作用）
curl -sf -X POST -H "$H" $B/feedback/collect

# 一轮调度任务（容器内——collect+scheduler_run 留痕）
docker exec zhuxiang-jiu-backend-1 python -c "import asyncio; \
  from services.ii58_scheduler import run_scheduled_tasks; \
  print(asyncio.run(run_scheduled_tasks()))"

# 一轮正样本挖掘（shadow/assist 态——48号 turns 纯读取）
curl -sf -X POST -H "$H" $B/mine/positive
```

### 2.4 全链演练 SOP（预生产/维护窗口）

```bash
# 挖掘→登记→终审→评估命中→反馈→裁决回流→阈值闭环（约 2 分钟）
curl -sf -X POST -H "$H" $B/mine/positive                              # turns→语料 active
curl -sf -X POST -H "$H" -H "Content-Type: application/json" \
  -d '{"intentId": "product.price_query", "text": "多少钱"}' \
  $B/corpus/ingest                                                       # pending
curl -sf -X POST -H "$H" -H "Content-Type: application/json" \
  -d '{"approve": true}' $B/corpus/<cid>/review                          # active
curl -sf -X POST -H "$H" -H "Content-Type: application/json" \
  -d '{"text": "多少钱"}' $B/evaluate                                   # resolved+槽位
curl -sf -X POST -H "X-Member-Id: 1" -H "Content-Type: application/json" \
  -d '{"evalId": <eid>, "text": "不对", "correctedIntentId": "product.new_query"}' \
  $B/feedback                                                            # assist 态
curl -sf -X POST -H "$H" -H "Content-Type: application/json" \
  -d '{"approve": true, "targetSampleType": "positive"}' \
  $B/labels/<lid>/decide                                                 # 语料回流 active
curl -sf -X POST -H "$H" $B/feedback/collect                             # 池双写
```

---

## 三、故障处置（常见问题速查）

### 3.1 功能面异常

| 现象 | 原因 | 处置 |
|------|------|------|
| evaluate/ingest/mine 409 "off" | II58_MODE=off | 非故障——零影响铁律；开启见部署手册 §五 |
| feedback 409 "assist" | off/shadow 态会员面未开放 | 非故障——反馈飞轮灰度语义 |
| ingest 409 "不在册/非法类型/混淆方" | 意图白名单外/类型四类外/非注册混淆对 | 非故障——封闭白名单防御（红队 RT-01） |
| evaluate clarify（不命中） | 语料库空/置信度<0.7 | 非故障——澄清优于错误执行铁律；补语料见 §2.3 挖掘 |
| evaluate boundaryIntercepted | guest 越权/deny 域 | 非故障——识别即合规（归因保留原始意图可查） |
| evaluate confirm_required | sensitive 沙箱意图 | 非故障——二次确认语义（48号 confirmToken 流） |
| calibrate 409 "已有待终审" | 镜像 pending 占用 | 先处置（终审模 approve/reject）再提交——队列纪律 |
| decide/review 409 "已裁决/pending" | 状态机不可重复 | 非故障——终审一次性裁决语义 |
| redteam 409 "shadow/assist" | off 态无攻击面 | 非故障——红队需决策面开放 |

### 3.2 fail-soft 边界（不阻断主链路的降级）

以下异常**降级留痕不阻断**（日志关键字 `ii58_`）：
- 47号 tier 读取异常（fail-soft 走 standard 基线——`ii58_tier_read_failed`）
- 主动学习入队异常（`ii58_auto_enqueue_failed`——评估主链不受影响）
- 44号池双写失败（poolFailed 计数——识别记录仍落库，下轮 collect 重试）
- 46号审批总线异常（`ii58_gov_settle_failed`——阈值终审本地镜像不阻断）
- 红队/挖掘 48号读取异常（`ii58_scan_turns_failed`——空态返回）
- 调度器单轮异常（scheduler_run 事件 errors 留痕——下轮继续）

### 3.3 专项排查命令

```bash
# 容器日志过滤（异常快速定位）
docker logs zhuxiang-jiu-backend-1 2>&1 | grep "ii58_" | grep -E "WARN|ERROR" | tail -30

# 第33档案池健康（44号学习域）
curl -sf -H "$H" $B/model/status | python -m json.tool | head -30

# 单条识别记录归因链追溯（compliance/slotSources/tier/阈值快照）
curl -sf -H "$H" $B/evaluations/<eid> | python -m json.tool

# 全链事件追溯
docker exec zhuxiang-jiu-redis-1 redis-cli LRANGE zhuxiang:ii58:event_all 0 20
```

---

## 四、应急响应预案（E1-E8）

### 事件分级

| 级 | 定义 | 响应时限 |
|----|------|---------|
| P1 | 数据泄漏/PII 暴露/宪法破坏 | 立即 |
| P2 | 业务面不可用/识别误导扩散/阈值漂移 | ≤30 分钟 |
| P3 | 劣化类（积压/指标异常） | 当日 |

### E1｜PII 泄漏嫌疑（P1——最高优先级）

**触发**：审计发现语料/反馈文本含明文 PII（mask_pii 漏检形态）。

1. **立即隔离**（秒级）：
   ```bash
   # II58_MODE 回退——决策/会员面 409（终审/回流通道保留）
   # .env: II58_MODE=off → docker compose -p zhuxiang-jiu up -d backend
   ```
2. **定位污染面**：
   ```bash
   curl -sf -H "$H" "$B/corpus" | python -m json.tool | grep -B2 -A5 "<关键词>"
   ```
3. **语料处置**：pending 态直接 review(approve=false) 驳回；active 态 Redis 直改 retired（`zhuxiang:ii58:ii58_corpus:{id}`）。
4. **根因**：48号 `mask_pii` 正则未覆盖的 PII 形态——登记缺陷+补充 `PII_PATTERNS`+全量复扫（预生产红队 RT-01 联动复验）。
5. **恢复**：根因闭环后按部署手册 §五 重新灰度。

### E2｜识别误导扩散/阈值漂移（P2）

**触发**：多用户"不是这个"反馈涌进 / collect calibrationAlert 触发 / dashboard trustGain 陡降。

1. **确认**：`GET /labels`（explicit_feedback 来源高优先积压）+`GET /dashboard` 度量区。
2. **阈值处置**（不停机）：
   ```bash
   # 高置信错误预警已提交 pending 收紧建议——人工终审裁决
   curl -sf -X POST -H "$H" -H "Content-Type: application/json" \
     -d '{"changeId": <cid>, "approve": true}' $B/threshold/calibrate
   ```
3. **语料处置**：误导语料经标注轨 decide(reject) 或 Redis 直改 retired；回流自动记 high_conf_error -0.8 负修正。
4. 若为**批量问题**（同类意图多发）→ §四 E3（语料投毒嫌疑）+L1 面级回退。

### E3｜语料投毒嫌疑（P2）

**触发**：ingest 异常来源登记激增 / confusables gap 对被恶意构造 / corpus bySource 分布异常。

1. **核查语料**：`GET /corpus`（source 字段分布——ops_register/label_reflow 异常占比）。
2. **可疑语料**：封闭白名单已拦（不在册意图/非法类型/伪造混淆方全 409——红队 RT-01 防御）；已入库可疑样本逐个 review 处置。
3. **系统性处置**：L1 回退 off + 人工复查全部 pending 语料。
4. **预防**：红队 RT-01/RT-06 复验（预生产）——注册表封闭+回流四类封闭。

### E4｜阈值镜像积压（P3）

**触发**：thresholds mirrorPendingAlert=true（校准申请 pending 滞留）。

1. 人工处置（终审模——不受开关影响）：
   ```bash
   curl -sf -X POST -H "$H" -H "Content-Type: application/json" \
     -d '{"changeId": <cid>, "approve": true, "reviewer": "ops"}' $B/threshold/calibrate
   ```
2. 积压根因：高置信错误预警自动提交频繁（E2 联动）——先治理识别质量；或人手不足——转增援。
3. 队列纪律保障：pending 占用时新申请 409 拒绝——不会叠加积压。

### E5｜标注队列积压（P3）

**触发**：labels pending 大量滞留（五来源：explicit/implicit×3/auto_ambiguity）。

1. 人工批量终审（`POST /labels/{id}/decide`——不受开关影响，off 亦可处理）。
2. 积压根因：auto_ambiguity 入队过量→语料覆盖不足（先跑 mine 补语料）；或人手不足→转增援（LLM 不进判定链——铁律）。

### E6｜护栏异常/宪法破坏（P3→P1——44号学习域联动）

**触发**：dashboard constitution.scorer33=false（P1 宪法破坏）或 44号 第33档案 champion 权重越界（P3）。

1. scorer33=false：立即排查 44号 SCORER_REGISTRY——33 档案口径被破坏（版本级故障→L3）。
2. 权重越界：`GET /model/status` 查明细——58号自身无权重写入路径（纯 submit_feedback 消费方），此事件提示**回流信号被投毒**（E3 联动）。
3. 极端时：46号冻结第33档案（46号审批总线）阻断学习。

### E7｜调度器异常（P3）

**触发**：scheduler_run 事件缺勤 / errors 非空。

1. 手动补跑：`POST /feedback/collect`（回流）+ docker exec run_scheduled_tasks（§2.3）。
2. 查事件留痕：
   ```bash
   docker exec zhuxiang-jiu-redis-1 redis-cli LRANGE zhuxiang:ii58:event_all 0 5
   ```
3. 调度器自身 fail-soft——单轮失败不影响下轮；连续失败→容器日志排查。

### E8｜数据损坏/误清理（P1/P2）

**触发**：Redis 数据异常 / 误跑验收脚本（清了生产键）。

1. **评估损失面**：验收脚本清理键清单（部署手册 §4.3）——`zhuxiang:ii58:*`（58号全量）+ `zhuxiang:ai46:*`（46号治理台账）+ `zhuxiang:voice48:*`（48号语音数据）+ `zhuxiang:ai_learning:*`（44号学习池）。
2. **58号数据重建**（ii58 七表全为增量——删除即回空图，不影响他模块）：
   ```bash
   docker exec zhuxiang-jiu-redis-1 redis-cli --scan --pattern "zhuxiang:ii58:*" | \
     xargs -r docker exec -i zhuxiang-jiu-redis-1 redis-cli DEL
   ```
   然后全链重建：mine→ingest→review→evaluate→feedback→decide→collect（§2.4 演练 SOP 即重建流程）。
3. **第33档案反馈**：不可恢复（真值历史）——池为空则重新积累；44号 champion 权重回默认。
4. **跨模块受损**：46号台账/48号语音数据——按对应模块手册处置。

---

## 五、数据治理

### 5.1 存储布局（Redis 键前缀 `zhuxiang:ii58:`）

| 表 | 内容 | 治理口径 |
|----|------|---------|
| ii58_intents | 意图动态扩展域 | 长期保留（白名单审计） |
| ii58_corpus | 语料（四类样本版本化） | rejected 保留不删（可追溯——铁律）；retired 旧版本保留 |
| ii58_evaluations | 识别记录（归因链+pooled 标记） | 长期保留（归因审计——无归因不计入有效服务） |
| ii58_feedback | 双通道反馈 | 永久保留（回流真值源） |
| ii58_labels | 标注队列 | approved/rejected 保留（回流审计） |
| ii58_thresholds | 阈值镜像（tier=baseline） | extra.history 版本化（近 5 条终审历史） |
| ii58_events | 全链事件 | 只追加（审计） |

### 5.2 红队痕迹处置

红队七向量注入的数据（伪造语料登记/攻击评估记录/RT 会员 9901 反馈）：投毒载荷被封闭白名单全拦（不落库）；RT 会员评估记录/反馈可按 `zhuxiang:ii58:ii58_feedback:*` 过滤 member_id 清理；红队语料种子 source=redteam 向量隔离（_cleanup 已 retired）；验收脚本每轮自动清理（键清单见部署手册 §4.3）。

### 5.3 备份

ii58 七表随 Redis appendonly 持久化；44号池/46号台账随既有模块策略。识别归因链+语料版本化为审计核心——恢复演练纳入年度计划。

---

## 六、SOP 周期任务总表

| 频率 | 任务 | 操作 |
|------|------|------|
| 每日 | 四区巡检+标注队列+阈值镜像 | §2.1（≤5 分钟） |
| 每日 | labels pending 积压检查 | §2.1 ② |
| 每周 | 语料终审清队+对抗覆盖度复核 | §2.2 ①② |
| 每周 | 澄清率趋势+容器日志周扫 | §2.2 ③④ |
| 每月 | 全链演练（§2.4）+红队复验（预生产） | RT-01~07 allDefended |
| 变更后 | 红队七向量复验（预生产） | `POST /redteam` |
| 半年 | 备份恢复演练（E8 流程） | §5.3 |

---

**结语**：58号运维日常三件事——巡检四区、清队终审、盯紧镜像；应急三铁律兜底——off 秒级回退、终审通道永不关闭、归因链全程可溯。**优化永不自动生效**在任何运维动作中不解除。
