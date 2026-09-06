# 57号·AI智能知识库模块 运维 SOP 与应急响应预案

> 版本：v1.0（2026-09-06）｜适用代码基线：master ≥ `d1d4afd`（含 compose 三变量入册）
> 配套文档：[57号_上线部署操作手册.md](57号_上线部署操作手册.md)（部署/灰度/回滚三级预案）｜[57号_模块验收报告模板.md](57号_模块验收报告模板.md)
> 日志关键字：`kb57_` ｜ Redis 键前缀：`zhuxiang:kb57:` ｜ 管理面鉴权：`X-Role: admin`；会员面：`X-Member-Id`

---

## 一、开关矩阵（运维态速查）

| 开关 | 默认 | 开放面 | 运维语义 |
|------|------|--------|---------|
| `KB57_MODE=off` | ✓ | 仅观测面 11 端点+终审/召回+回流 | **零影响铁律**——决策/会员面全 409 |
| `KB57_MODE=shadow` | | +缺口诊断+采集+鉴别（quarantined 入库） | 观察学习期——种子不暴露 |
| `KB57_MODE=assist` | | +种子工坊+发布+feed/view/反馈/路径/触发 | 辅助生产期——种子暴露面 |
| `KB57_LEARN_MODE=on` | off | T+1 回流补标+有效期检查调度 | 周期 `KB57_LEARN_INTERVAL`（默认 86400s，下限 300） |

**不受任何开关影响**：终审 `review`（published 唯一出口）、召回 `recall`（人工下架通道）、回流 `collect`、全部 GET 观测面——off 态下这些是唯一的"人工兜底通道"。

---

## 二、日常操作 SOP

### 2.1 每日巡检（≤5 分钟，admin）

```bash
B=http://127.0.0.1:8000/api/kb57; H='X-Role: admin'

# ① 健康总览（四区齐备+度量五指标）
curl -sf -H "$H" $B/dashboard | python -m json.tool | head -40

# ② 回流健康度（六类信号分布+池提交数）
curl -sf -H "$H" $B/feedback/stats

# ③ 缺口积压（open/collecting 态数量与优先级）
curl -sf -H "$H" $B/gaps | python -m json.tool | head -30
```

**巡检判定口径**：

| 指标 | 正常 | 异常处置 |
|------|------|---------|
| 度量区 coverageRate | ≥0.9 或空态 | 低→跑 gaps/scan 诊断 |
| 防御区 sourceConcentration.alert | false | true→§四 E3 |
| 防御区 guardrail.healthy | true | false→§四 E6 |
| 合规区 budgetHalts | 稳定低位 | 陡增→§四 E4 |
| gaps open 态 | 持续消化 | 积压→§三 3.1 采集流 |

### 2.2 每周任务

```bash
# ① 终审待审队列（human_load 因子输入——积压影响第32档案评分）
curl -sf -H "$H" "$B/seeds?status=sandbox" | python -m json.tool | head -30

# ② 隔离态资源积压复核（内容安全中风险/低可信度源待人工）
#    quarantined 资源无直接放行端点——按缺口重新鉴别或放弃
curl -sf -H "$H" $B/gaps?status=collecting | python -m json.tool

# ③ 会员学习异常排查（属主域——须带会员本人 X-Member-Id）
curl -sf -H "X-Member-Id: <mid>" $B/my/learning
```

### 2.3 手动触发（不待调度周期）

```bash
# 一轮回流补标（第32档案池双写——幂等 seedId 1:1，重复执行零副作用）
curl -sf -X POST -H "$H" $B/feedback/collect

# 一轮有效期健康检查（过期种子自动降权+触发更新）
docker exec zhuxiang-jiu-backend-1 python -c "import asyncio; \
  from services.kb57_seed_service import Kb57SeedService; \
  print(asyncio.run(Kb57SeedService().freshness_check()))"

# 一轮缺口诊断（shadow/assist 态——强信号环境返回 collect+gapId）
curl -sf -X POST -H "$H" $B/gaps/scan
```

### 2.4 全链演练 SOP（预生产/维护窗口）

```bash
# 诊断→采集→鉴别→锻造→终审→浏览→反馈→回流（约 2 分钟）
curl -sf -X POST -H "$H" $B/gaps/scan                       # gapId
curl -sf -X POST -H "$H" -H "Content-Type: application/json" \
  -d '{"gapId": <gid>}' $B/collect/run                       # resourceId (quarantined)
curl -sf -X POST -H "$H" $B/resources/<rid>/compliance      # verdict=passed+指纹
curl -sf -X POST -H "$H" -H "Content-Type: application/json" \
  -d '{"gapId": <gid>, "resourceId": <rid>}' $B/seeds/craft # seedId (sandbox)
curl -sf -X POST -H "$H" -H "Content-Type: application/json" \
  -d '{"reviewer": "ops", "approved": true}' \
  $B/seeds/<sid>/review                                      # published
curl -sf -H "X-Member-Id: 1" $B/seeds/<sid>/view             # 指纹校验+计量
curl -sf -X POST -H "X-Member-Id: 1" -H "Content-Type: application/json" \
  -d '{"kind": "positive"}' $B/seeds/<sid>/feedback
curl -sf -X POST -H "$H" $B/feedback/collect                # 回流入池
```

---

## 三、故障处置（常见问题速查）

### 3.1 功能面异常

| 现象 | 原因 | 处置 |
|------|------|------|
| gaps/scan 409 "off" | KB57_MODE=off | 非故障——零影响铁律；开启见部署手册 §五 |
| collect/run 409 "open/collecting" | 缺口已 resolved/ignored | 按状态机语义——新缺口走 gaps/scan |
| compliance 409 "quarantined" | 资源已鉴别过（状态机） | 查 `GET /compliance/{id}` 报告；重复鉴别架构上不存在 |
| compliance verdict=blocked | 版权关/内容安全关拦截 | 非故障——三关防御；换源或放弃该资源 |
| compliance verdict=halted | 缺口 budgetCap 满 或 49号系统账户不足 | §四 E4 |
| craft 409 "compliant/指纹" | 资源非合规态或无指纹 | 非故障——无指纹不入库铁律 |
| view 409 "指纹失效" | 种子指纹字段损坏 | 核查 Redis 数据；极端时重新鉴别+craft |
| view 409 "预算不足" | 会员当日 49号 预算耗尽 | 次日自动重置；紧急调隐私偏好（49号手册） |
| feed 409 "assist" | shadow 态会员面未开放 | 非故障——种子暴露面灰度语义 |
| paths/advance 409 "越权" | 非属主 X-Member-Id | 非故障——权限矩阵 |

### 3.2 fail-soft 边界（不阻断主链路的降级）

以下异常**降级留痕不阻断**（日志关键字 `kb57_`）：
- 单信号源采集异常（scan_signals skipped 列表——该侧不命中）
- 池双写失败（poolFailed 计数——种子标记仍落库，下轮补标重试）
- 45号补偿 deposit 失败（跳过计数——不阻断召回主链）
- 调度器单轮异常（scheduler_run 事件 errors 留痕——下轮继续）

### 3.3 专项排查命令

```bash
# 容器日志过滤（异常快速定位）
docker logs zhuxiang-jiu-backend-1 2>&1 | grep "kb57_" | grep -E "WARN|ERROR" | tail -30

# 第32档案池健康（44号学习域）
curl -sf -H "$H" $B/model/status | python -m json.tool | head -30

# 全链事件追溯（单缺口/资源维度）
docker exec zhuxiang-jiu-redis-1 redis-cli LRANGE zhuxiang:kb57:event_all 0 20
```

---

## 四、应急响应预案（E1-E8）

### 事件分级

| 级 | 定义 | 响应时限 |
|----|------|---------|
| P1 | 数据泄漏/PII 暴露/宪法破坏 | 立即 |
| P2 | 业务面不可用/误导种子扩散 | ≤30 分钟 |
| P3 | 劣化类（积压/指标异常） | 当日 |

### E1｜PII 泄漏嫌疑（P1——最高优先级）

**触发**：审计发现 published 种子含明文 PII / 隔离态资源被暴露。

1. **立即隔离**（秒级）：
   ```bash
   # KB57_MODE 回退——决策/会员面 409（终审/召回通道保留）
   # .env: KB57_MODE=off → docker compose -p zhuxiang-jiu up -d backend
   ```
2. **定位污染面**：
   ```bash
   curl -sf -H "$H" "$B/seeds" | python -m json.tool | grep -B2 -A5 "<关键词>"
   ```
3. **逐种子召回**（L2 单粒度——含受影响用户补偿）：
   ```bash
   curl -sf -X POST -H "$H" -H "Content-Type: application/json" \
     -d '{"reason": "PII 泄漏应急", "affectedMembers": [<mids>]}' \
     $B/seeds/<sid>/recall
   ```
4. **根因**：查该种子 compliance 报告（`GET /compliance/{id}`）——若隐私关漏检（正则未覆盖的 PII 形态），登记缺陷+补充 `PII_PATTERNS`+全量复扫（预生产红队 RT-03 复验）。
5. **恢复**：根因闭环后按部署手册 §五 重新灰度。

### E2｜误导种子扩散（P2）

**触发**：多用户负反馈涌进 / dashboard suggestRecall 预警 / 舆情上报。

1. **确认**：`GET /seeds/{id}` 看 negativeCount/positiveCount 比值（≥50% 负反馈）。
2. **召回+补偿**（不停机）：
   ```bash
   # 从 feedback 表取受影响会员（viewed 记录）
   curl -sf -X POST -H "$H" -H "Content-Type: application/json" \
     -d '{"reason": "内容误导", "affectedMembers": [<mids>]}' \
     $B/seeds/<sid>/recall
   ```
3. **验证**：45号 L2 存证（`trust45_events` 含 platform_conduct）；回流自动记 -0.8 负修正。
4. 若为**批量问题**（同类源多发）→ 源集中度告警联动（E3）+L1 面级回退。

### E3｜采集源投毒嫌疑（P2）

**触发**：dashboard sourceConcentration.alert=true（topRatio>0.8）/ 同源 blocked 激增。

1. **核查源**：`GET /sources`（内置 6 源+动态注册域）——确认 topSource 是否 admin 注册的动态源。
2. **可疑动态源**：该源后续采集自然被版权关按资源判定——已入库 blocked 资源不进入种子链；已发布种子逐个 review（E2 流程）。
3. **系统性处置**：L1 回退 off + 人工复查该源全部 seeds。
4. **预防**：红队 RT-01/RT-02 复验（预生产）——注册表封闭+版权关第一道阻断。

### E4｜预算熔断异常（P3）

**触发**：compliance verdict=halted 比例陡增 / budgetHalts 告警。

1. **区分口径**：
   - **缺口级**（budgetSpent≥budgetCap）：单缺口封顶——设计行为；需加额时人工调整该缺口 budgetCap（Redis 直改 `zhuxiang:kb57:kb57_gaps:{id}` 或放弃采集）。
   - **49号系统账户**（member 0）：日预算耗尽——次日自动重置。
2. **49号账户查询**：
   ```bash
   docker exec zhuxiang-jiu-redis-1 redis-cli HGETALL \
     zhuxiang:voice48:voice48_privacy_budget:0
   ```
3. 若为**异常消耗**（非业务量所致）：排查鉴别频次（`kb57_events` compliance 计数）——必要时 L1 回退阻断消耗。

### E5｜终审队列积压（P3）

**触发**：seeds sandbox/review 态大量滞留（human_load 因子拉低第32档案分）。

1. 人工批量终审（`POST /seeds/{id}/review`——不受开关影响，off 亦可处理）。
2. 积压根因：采集过量→收窄 `suggestedSources`；或人手不足→提高鉴别自动化置信度暂不可行（LLM 不进判定链——铁律），转增援。

### E6｜护栏异常（P3——44号学习域联动）

**触发**：dashboard guardrail.healthy=false（第32档案 champion 权重越 [0.5,2.0]）。

1. `GET /model/status` 查 champion/challenger 权重明细。
2. 44号学习中枢处置（44号手册）——57号自身无权重写入路径（纯 submit_feedback 消费方），此事件提示**回流信号被投毒**（见 E3 联动）。
3. 极端时：46号冻结第32档案（46号审批总线）阻断学习。

### E7｜调度器异常（P3）

**触发**：scheduler_run 事件缺勤 / errors 非空。

1. 手动补跑：`POST /feedback/collect`（回流）+ docker exec freshness（§2.3）。
2. 查事件留痕：
   ```bash
   docker exec zhuxiang-jiu-redis-1 redis-cli LRANGE zhuxiang:kb57:event_all 0 5
   ```
3. 调度器自身 fail-soft——单轮失败不影响下轮；连续失败→容器日志排查。

### E8｜数据损坏/误清理（P1/P2）

**触发**：Redis 数据异常 / 误跑验收脚本（清了生产键）。

1. **评估损失面**：验收脚本清理键清单（部署手册 §4.3）——`zhuxiang:kb57:*`（57号全量）+ `ai_learning:feedback:knowledge_orchestration`（第32档案反馈）+ `qr55:model_events*`（55号信号）+ 指定会员预算。
2. **57号数据重建**（kb57 九表全为增量——删除即回空图，不影响他模块）：
   ```bash
   docker exec zhuxiang-jiu-redis-1 redis-cli --scan --pattern "zhuxiang:kb57:*" | \
     xargs -r docker exec -i zhuxiang-jiu-redis-1 redis-cli DEL
   ```
   然后全链重建：gaps/scan→collect→compliance→craft→review（§2.4 演练 SOP 即重建流程）。
3. **第32档案反馈**：不可恢复（真值历史）——池为空则重新积累；44号 champion 权重回默认。
4. **跨模块受损**：55号模型事件/会员预算——按对应模块手册处置。

---

## 五、数据治理

### 5.1 存储布局（Redis 键前缀 `zhuxiang:kb57:`）

| 表 | 内容 | 治理口径 |
|----|------|---------|
| kb57_sources | 动态采集源注册 | 长期保留（白名单审计） |
| kb57_gaps | 缺口+信号快照 | resolved 保留（回流 gapRecurrence 溯源） |
| kb57_resources | 原始资源（隔离态） | blocked 按需清理；compliant 保留（指纹追溯） |
| kb57_compliance | 鉴别报告 | **永久保留**（合规审计——三关明细+指纹） |
| kb57_seeds | 种子（八态） | recalled/rejected 保留不删（可追溯——铁律） |
| kb57_feedback | 使用反馈（多 kind） | 永久保留（回流真值源） |
| kb57_events | 全链事件 | 只追加（审计） |
| kb57_paths / pushes | 学习路径/植入 | 随会员域治理 |

### 5.2 红队痕迹处置

红队七向量注入的数据（伪造信号事件/攻击资源/RT 会员 9901/9902 记录）：红队资源按三关自然拦截（blocked/quarantined 不进种子链）；RT 会员学习记录可按 `zhuxiang:kb57:kb57_feedback:*` 过滤 member_id 清理；验收脚本每轮自动清理（键清单见部署手册 §4.3）。

### 5.3 备份

kb57 九表随 Redis appendonly 持久化；44号池/45号档案随既有模块策略。种子合规指纹+鉴别报告为审计链核心——恢复演练纳入年度计划。

---

## 六、SOP 周期任务总表

| 频率 | 任务 | 操作 |
|------|------|------|
| 每日 | 四区巡检+回流健康度 | §2.1（≤5 分钟） |
| 每日 | gaps open 积压检查 | dashboard+gaps 端点 |
| 每周 | 终审队列清队+隔离态复核 | §2.2 |
| 每周 | 容器日志 kb57_ WARN/ERROR 扫描 | §3.3 |
| 每月 | 全链演练（§2.4）+护栏/集中度复核 | dashboard defense 区 |
| 上线前/变更后 | 红队七向量复验（预生产） | POST /redteam → allDefended |
| 半年 | 备份恢复演练+PII 复扫 | §5.3+E1 流程演练 |

---

**结语**：57号运维的日常是"巡检四区+清队终审"；应急的核心是**三铁律兜底**——隔离态永不暴露（E1 秒级回退无泄漏路径）、无指纹不入库（污染天然阻断）、预算全程封顶（E4 上限可控）。任何时候 off 回退不关闭终审/召回通道——人工下架能力永在线。
