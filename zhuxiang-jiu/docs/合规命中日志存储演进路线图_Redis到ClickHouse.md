# 合规命中日志存储演进路线图（Redis → ClickHouse）

> 版本：v1.0（2026-10-04）
> 定位：**前瞻设计沉淀，非当下施工项**——用户 ClickHouse 方案全文经架构校准后归档于此，绑定明确触发条件，条件成熟按图施工。

---

## 一、为什么现在不落地（三重错位存档）

| 维度 | ClickHouse 方案前提 | 项目现实（2026-10-04） |
|---|---|---|
| 数据源 | 从 MySQL 迁移 | 全站无 MySQL，gr_hit_log 为 Redis hash（每条一键 `zhuxiang:guardrail:gr_hit_log:{id}`） |
| 写入量级 | 单日千万级 | 小竹对话日均千级以下，命中为低频事件（上线首日 hits=0）；AsyncHitLogger 批量写入富余 4-5 个数量级 |
| 资源 | ReplicatedMergeTree 集群 + Kafka + Canal + ZK + Grafana | 单台 2C2G 轻量服务器（已跑 backend + Redis + nginx + 监控），CH 单节点最低 2-4GB 内存——硬上风险重演整机 OOM 事故 |

**当下已吸收的方案价值**（映射实现）：

| 方案设计 | 现实映射 | 状态 |
|---|---|---|
| MergeTree `TTL log_date + INTERVAL 90 DAY` | Redis 键过期 `GUARDRAIL_HIT_TTL_DAYS`（默认 180 天，0=永不过期），写入时 pipeline 附加 expire | ✅ 已上线 |
| 微批写入（攒批 10 万/3s） | AsyncHitLogger（2s/200 条 Redis pipeline）——同思想按现实量级缩放 | ✅ 已上线 |
| 丢弃背压（QueueFull） | 队列上限 10000 + 丢弃计数告警 | ✅ 已上线 |
| 游标分页 | 当前 `_scan` + limit 足够（量小）；列表 API 预留 `limit ≤500` | ⏸ 触发后改造 |

---

## 二、触发条件（何时启动迁移立项）

满足**任一**即启动评估（两项同时满足直接立项）：

1. **量级触发**：日均命中日志 > 10 万条，或单条查询（`/hits` 列表/overview 聚合）P95 > 500ms
2. **存储触发**：`zhuxiang:guardrail:gr_hit_log:*` 键总体内存 > 500MB（`redis-cli --memusage` 抽样估算）或 Redis 实例内存水位 > 70%
3. **分析触发**：Evolution Engine 周度 SFT/DPO 数据提取需要在亿级日志上做秒级 OLAP（多维 GROUP BY/趋势窗口）

> 监控挂钩：nexus74 日度巡检可周期记录 hit 数与 Redis 内存（建议随首月运营补进 overview 巡检留痕）。

---

## 三、目标架构（方案精简适配版）

### 3.1 部署形态演进（三步走）

```
阶段 0（现在）        阶段 1（触发后）           阶段 2（规模化）
─────────────       ─────────────────         ─────────────────
Redis hash          CH 单节点(MergeTree)       CH 集群(Replicated)
+ AsyncHitLogger    + AsyncHitLogger 双写      + Distributed 表
+ 键 TTL            + 按天对账                  + Kafka 削峰(可选)
```

- **阶段 1**：2C2G 换 4C8G 后部署 CH 单节点（无 ZK/Kafka/Canal——数据源是 Redis 不是 MySQL，**Canal/Debezium CDC 链路整体不需要**，双写由 AsyncHitLogger 加一个 CH writer 出口即可）
- **阶段 2**：多节点时升 ReplicatedMergeTree + Distributed；日写入过千万再考虑 Kafka Engine 削峰

### 3.2 表结构（单节点版 DDL，按用户方案裁剪）

```sql
CREATE TABLE zxjiu_guardrail.gr_hit_log
(
    trace_id    String,
    hit_time    DateTime64(3, 'Asia/Shanghai'),
    log_date    Date MATERIALIZED toDate(hit_time),
    member_id   String,
    session_id  String,
    direction   LowCardinality(String),      -- INPUT/OUTPUT
    category    LowCardinality(String),      -- 5 分类
    rule_word   String,
    rule_type   LowCardinality(String),      -- BLOCK/REPLACE
    original_text String CODEC(ZSTD(3)),     -- 已截断 500 字+脱敏
    processed_text String CODEC(ZSTD(3)),
    feedback_status LowCardinality(Int8),    -- 0/1/2(复核态)
    hit_day     Date
)
ENGINE = MergeTree
PARTITION BY toYYYYMMDD(log_date)
ORDER BY (category, log_date, hit_time)     -- 大盘查询模式: 分类过滤+时间窗
TTL log_date + INTERVAL 180 DAY
SETTINGS index_granularity = 8192;
```

设计要点（继承用户方案原则）：
- **反馈态的 Append-Only 处理**：打标不 UPDATE——追加 `feedback_status` 事件由 AsyncHitLogger 写入（或轻量 events 表 + `argMax` 聚合），查询时 argMax 取最新态（方案 B，生产推荐）
- **LowCardinality** 用于 direction/category/rule_type 三列（枚举压缩）
- **排序键** = 大盘查询模式（分类 Top10 / 误杀率按时间窗）——`(category, log_date, hit_time)`
- **无 tenant_id**：单租户站点裁剪；**无 oss_full_text_url**：文本已 500 字截断+脱敏，无需对象存储分离
- **去重**：AsyncHitLogger 队列单消费点天然幂等（内存队列不重复）；CH 侧可选 ReplacingMergeTree(hit_time)

### 3.3 写入链路（复用 AsyncHitLogger，无 Canal）

```
引擎命中 → AsyncHitLogger 队列(不变)
         → flush: 双写 Redis pipeline(在线详情/打标)
                  + CH insert(列式批量, JSONEachRow)
```

- 阶段 1 双写期间 Redis 为主读源；CH 连续 7 天对账通过（count/sum 抽样窗）后切读
- **游标分页**：`/hits` 列表切换时改造为 `(category, hit_time)` 复合游标（用户方案 1.2 实现可直接复用，tenant_id 裁剪）
- 打标写路径保留 Redis（低频小事务），CH 仅承担分析读

### 3.4 迁移 SOP（Redis → CH，简化版双写-对齐-切读）

1. **T0**：AsyncHitLogger 加 CH writer（feature flag `GUARDRAIL_CH_DSN`，空=关闭）
2. **存量搬运**：一次性脚本 scan Redis hit 键 → 微批 5 万条 insert CH（无需 DataX——数据源 Redis 非 MySQL，Python 直接搬运，量级万-十万级分钟完成）
3. **对账**：连续 7 天每日 count 抽样窗比对（CH 最终一致性，查询前容忍 5s 或低频 `FINAL`）
4. **切读**：`/hits` 与 overview 聚合按 flag 灰度 10%→50%→100% 切 CH
5. **收口**：Redis 侧 TTL 缩短为 7 天（近线详情+打标），历史分析全走 CH

---

## 四、监控（阶段 1 就绪清单）

- CH Prometheus endpoint（:9363 /metrics）接入现有 node-exporter 体系
- 必配 4 告警（沿用用户方案 PromQL）：RejectInserts、后台 Merge 队列、内存水位、消费延迟（双写队列）
- Grafana 导入官方 Dashboard ID 14192，微调 Parts 膨胀面板

---

## 五、决策记录

| 日期 | 决策 |
|---|---|
| 2026-10-04 | CH 方案校准为路线图（三重错位）；TTL 治理 + AsyncHitLogger 已吸收落地；Canal/Kafka/ZK 链路裁剪（无 MySQL 数据源）；触发条件如 §二 |
| 2026-10-04（二） | **Redis Stream 方案归档至阶段 1.5**：多实例 Consumer Group/XAUTOCLAIM/死信 Stream 代码模板归档（触发条件=多节点部署或 kill -9 丢日志窗口成为实际问题）；当下已吸收其重试语义——AsyncHitLogger flush 失败**批次回队重试**（替代丢弃，At-Least-Once 尽力） |
| 2026-10-04（二） | **Evolution Engine 聚合 SQL 模板 Redis 化落地**：误杀率日趋势/分类分布/僵尸规则检测 → `GET /api/guardrail/admin/analytics`；SFT/DPO 语料导出（Alpaca JSONL + 四类 PII 脱敏）→ `GET /api/guardrail/admin/sft-export`——数据闭环起点，不再等 CH |
| 2026-10-04（二） | **物化视图/ETL 脚本/聚合 SQL 原文归档**：AggregatingMergeTree 预聚合 DDL、clickhouse_connect 流式导出、四条 OLAP 模板（误杀率趋势/SFT 语料/僵尸规则/分钟突增告警）——阶段 1 CH 落地时直接复用 |
| 2026-10-04（二） | **LLM 拒答轨（Few-Shot 向量拒答 + BGE ONNX + Semantic Cache）方向性否定**：与本地拦截架构哲学冲突——DFA 拦截核心价值是 0 Token 成本+微秒延迟+话术运营可控；LLM 生成拒答引入 1s 延迟+Token 成本+组件复杂度（Qdrant/Embedding 服务/Redis Stack），而分类话术已由运营配置且即时稳定。**保留为可选探索项**：仅当用户反馈话术生硬且愿承担延迟时，可对特定分类做异步话术优化（离线批量，非实时拦截轨） |
