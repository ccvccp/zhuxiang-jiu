# 43号·P5-4 AbuseIPDB 实时查询(单 IP 情报) 实施计划 v1.0

> 配套：[43号P5_收口增强实施计划.md](43号P5_收口增强实施计划.md) §五
> 定位：P4-3/P5-3 为**段级静态情报**（Firehol netset 全量替换）——单个 IP 不在已知恶意段时无实时评分能力。P5-4 引入 AbuseIPDB **单 IP 实时置信度**（confidenceScore 0-100），与 Firehol 形成**两级串联**：段级免费先行过滤已知恶意段，未命中者再花配额查实时评分——冷启动即可识别"不在任何已知段但正在作恶"的 IP。
> 外部依赖：AbuseIPDB 免费档 1000 次/天（注册 https://www.abuseipdb.com/ 获取 API key）——**无 key 不阻塞交付**（极验 v4 同口径：mock 轨全链路先行交付，key 到位 `.env` 配置即启用 real 轨）。
> 调研结论（2026-09-03）：
> 1. 三态客户端范式已确认（captcha_client.py 41号范式）：mock（确定性，测试友好）/ real（fail-hard）/ mock_fallback（传输故障回退）——P5-4 取 **mock_fallback 语义变体**：real 失败/超配额回退 mock 口径（fail-soft，不阻断网关）；
> 2. 联动挂载点为 `ThreatIntelService.apply_to_reputation` 尾部（Firehol 段命中优先返回，未命中再查 AbuseIPDB——两级串联天然省配额）；
> 3. 配额计数器/结果缓存存储复用 `security43:` 前缀（P5-2 去重锁 SETNX+TTL 同款范式，Redis INCR+当日 24:00 过期）；
> 4. 网关侧联动**不新增事件表**——`threatintel_hit` 事件 factors.detail 已含来源标识（P4-3 口径），AbuseIPDB 联动沿用同 action 留痕，`abuseipdb` 因子名单列。

---

## 一、交付物总览

| 编号 | 内容 | 文件 | 性质 |
|------|------|------|------|
| P5-4a | 三态客户端(校验/配额护栏/结果缓存) | `services/abuseipdb_client.py` | 新建 |
| P5-4b | 信誉联动两级串联(apply_to_reputation 扩展) | `services/threatintel_service.py` | 修改 |
| P5-4c | 管理端查询端点(实时+配额余量) | `routes/security_routes.py` | 修改 |
| P5-4d | 专项测试+回归+实机+提交 | `test_security_p5_4.py` + `verify_security_p5_4_live.py` | 新建 |

**新增环境变量**（docker-compose 暴露）：

| 变量 | 默认 | 说明 |
|------|------|------|
| `SECURITY_ABUSEIPDB_MODE` | mock | mock / real / mock_fallback |
| `SECURITY_ABUSEIPDB_KEY` | 空 | real 态必填（.env 注入） |
| `SECURITY_ABUSEIPDB_DAILY_LIMIT` | 900 | 配额红线（免费 1000 预留 100 手动余量） |

---

## 二、P5-4a：三态客户端

### 结构（新建 `services/abuseipdb_client.py`）

```python
ABUSEIPDB_MODES = ("mock", "real", "mock_fallback")
API_URL = "https://api.abuseipdb.com/api/v2/check"
CACHE_TTL = 86400          # 结果缓存 24h(当日重复 IP 零消耗)
DAILY_LIMIT_DEFAULT = 900   # 配额红线(免费 1000 预留余量)

# 信誉联动阈值(计划 §五④)
SCORE_BLOCK_TIER = 75       # ≥75 → 降档 31(suspicious, 同 Firehol 口径)
SCORE_LOW_TIER = 25        # 25-75 → 轻度扣分 -10


def abuseipdb_mode() -> str:
    """三态开关(默认 mock——无 key 全链路可测)"""


def _mock_score(ip: str) -> int:
    """确定性 mock 分数: IP 末段哈希 → 0/25/85 三档
    (覆盖联动三区间: <25 零影响 / 25-75 轻扣 / ≥75 降档)"""


async def check_ip(ip: str) -> dict:
    """单 IP 实时置信度(缓存→配额→查询→回退全链路)

    Returns:
        {score: int|None, source: "cache"|"real"|"mock"|
         "mock_fallback", quotaUsed, quotaRemaining, error?}
    """
    # ① 缓存命中(Redis: security43:abuseipdb:result:{ip} TTL 24h)
    #    → 零配额消耗直接返回
    # ② 配额护栏: INCR security43:abuseipdb:quota:{YYYY-MM-DD}
    #    (TTL 到当日 24:00) → 超过 DAILY_LIMIT 走 fallback
    # ③ real 查询: GET /api/v2/check?ip={ip}&maxAgeInDays=30
    #    Bearer key + httpx 重试 2 次(TransportError, captcha 范式)
    # ④ 结果写缓存 + 返回
    # ⑤ real 失败(网络/HTTP 非 200/超配额):
    #    mode=mock_fallback → 回退 _mock_score(source 标记)
    #    mode=real → 返回 score=None(不联动, 留 error)
```

### 关键设计决策

| 决策 | 口径 | 理由 |
|------|------|------|
| **缓存优先** | 查询前先查 Redis 缓存(24h TTL) | 同 IP 当日重复访问零消耗——网关场景"同 IP 反复请求"正是常态 |
| **配额红线 900** | 非 1000 满额 | 预留 100 次给管理端手动查询/排查,防自动链路烧尽配额 |
| **日切 TTL** | quota 键 TTL 到当日 24:00(非固定 24h) | 对齐 AbuseIPDB 按日重置口径(UTC),避免滚动窗口漂移 |
| **mock 确定性** | IP 末段哈希→0/25/85 | 三档精确覆盖联动三区间,测试可构造每个分支 |
| **real 失败不抛** | fallback/mock 或 score=None | 网关联动 fail-soft 铁律——外部 API 故障不阻断请求 |
| **real 态校验 key** | 启动/调用时无 key → ValueError | 同极验 real 态口径(配置错误显式暴露) |

---

## 三、P5-4b：信誉联动两级串联

### 挂载点：`apply_to_reputation` 尾部扩展

```python
async def apply_to_reputation(self, ip, reputation) -> dict:
    # ... 既有 Firehol 段命中逻辑(P4-3):
    #     命中 → 降档 31 + threatintel_hit 事件 → return(串联第一级,
    #     命中者优先, 不再花配额查 AbuseIPDB)
    hit = await self.check_ip(ip)
    if hit is not None:
        ...降档 31 + 留痕...
        return reputation        # ← 第一级出口

    # P5-4 第二级: Firehol 未命中 → AbuseIPDB 实时置信度
    # (仅异常不阻断; score=None 零影响)
    try:
        from services.abuseipdb_client import check_ip as ab_check
        r = await ab_check(ip)
        score = r.get("score")
        if score is None:
            return reputation                     # 查询失败零影响
        current = float(reputation.get("score") or 0)
        if score >= 75:
            # 高置信恶意 → 降档 31(同 Firehol 口径, 不直封)
            reputation["score"] = 31.0
            reputation["status"] = reputation_status(31.0)
            await self._record_hit_event(          # 复用留痕
                ip, reputation, factor_name="abuseipdb",
                detail=f"置信度{score}(实时, "
                       f"source={r.get('source')})")
            await self.repo.save_reputation(reputation)
        elif score >= 25:
            # 中置信 → 轻度扣分 -10(留痕 abuseipdb_low,
            # 可申诉)——不降档, 只记账
            if current > 70:                       # 防重复扣
                reputation["score"] = max(
                    31.0, current - 10.0)
                ...
        # score < 25 → 零影响
    except Exception as exc:
        logger.warning("security_abuseipdb_skip ip=%s: %s", ip, exc)
    return reputation
```

### 联动阈值口径（与 Firehol 对齐）

| confidenceScore | 动作 | 留痕 | 申诉 |
|-----------------|------|------|------|
| ≥75 | 降档 31（suspicious，**不直封**） | `threatintel_hit` 事件,因子 `abuseipdb` | 可申诉 |
| 25-75 | 轻度扣分 -10（下限 31 防误杀过深） | 因子 `abuseipdb_low` | 可申诉 |
| <25 | 零影响 | 无 | — |

**防重复扣分**：轻度扣分前检查当前分——已 ≤70（已被扣过/已降档）不再重复扣，与 Firehol"已降档不重复降"同口径。

---

## 四、P5-4c：管理端查询端点（第 32 个）

```python
@router.get("/admin/threatintel/abuseipdb/check")
async def admin_abuseipdb_check(
    ip: str = Query(..., description="待查询 IP"),
    refresh: bool = Query(False, description="跳过缓存强制查询"),
    x_role: str = Header(default="", alias="X-Role"),
):
    """单 IP AbuseIPDB 实时置信度(缓存/配额余量/来源)"""
    _require_admin(x_role)
    # → abuseipdb_client.check_ip(ip, force=refresh)
    # 返回: {score, source, quotaUsed, quotaRemaining, mode}
```

- 管理端手动查询**不占自动链路配额判断**（共用同一计数器——手动也计数，余量口径统一）
- `refresh=true` 跳过缓存（排查"缓存了旧分数"场景）

---

## 五、测试与验收

### 专项测试（`test_security_p5_4.py`，预计 30 项）

| 分组 | 覆盖 |
|------|------|
| mock 分数 | 三档确定性（末段哈希→0/25/85 可构造）/ 同 IP 稳定 / 不同档位分布 |
| 三态 | mock 直通 / real 无 key 拒启（ValueError）/ mock_fallback 回退（real 失败→mock 分数+source 标记）/ real 态失败返回 None |
| 配额护栏 | 计数递增 / 超 900 走 fallback / 日切键 TTL / 手动与自动共用计数 |
| 缓存 | 命中不耗配额 / TTL 24h / refresh=true 跳过缓存 / 缓存写入 |
| 信誉联动 | ≥75 降档 31+suspicious / 25-75 扣 10 / <25 零影响 / 防重复扣（已 70 不再扣）/ 已降档不重复 / 留痕因子名 / 事件可申诉 |
| 两级串联 | Firehol 命中→不查 AbuseIPDB（配额零消耗）/ Firehol 未命中→查询执行 |
| HTTP 层 | 缺 Role 403 / check 端点结构 / refresh 参数 |
| 回归 | 无 key（mock 默认）全链路 / 网关零影响（外部 API 故障 fail-soft） |

### 实机验收（`verify_security_p5_4_live.py`，预计 14 项）

1. 正常业务零影响
2. 容器默认 mock（mode 实况）
3. mock 全链路：构造三档 IP → 联动三区间（降档/轻扣/零影响）
4. 配额计数：容器内连续查询 → quotaUsed 递增
5. 缓存生效：同 IP 二次查询 source=cache + quota 不增
6. refresh=true 强制重查
7. 留痕验证：≥75 档 IP 事件流水含 abuseipdb 因子
8. 申诉通道回归（abuseipdb 联动事件可申诉）
9. 管理端端点鉴权+结构
10. Redis 键族回归（abuseipdb:result/quota 键入族统计）
11. Firehol 串联优先（导入段命中 IP → AbuseIPDB 配额不动）
12. **real 轨探测**（有 key 时：真实 API 冒烟；无 key：断言拒启/降级路径，不作为门槛）
13. 调度/网关全链路回归
14. 业务正常

### 回归范围

- security 全系列 16 套（15 既有 + P5-3 新增）

---

## 六、关键风险与对策

| 风险 | 对策 |
|------|------|
| 免费配额烧尽（1000/天） | 三重节省：①Firehol 段级前置过滤 ②24h 结果缓存 ③900 红线熔断走 fallback |
| 外部 API 故障阻断网关 | 联动挂 try/except fail-soft（外部依赖永不阻断网关——P4-3 同铁律） |
| 误杀（AbuseIPDB 评分偏激进） | ≥75 只降档 31 不直封（suspicious 区间）+ 申诉通道兜底 + 25-75 仅轻扣——三级强度递进 |
| 缓存脏数据（IP 被处置后仍查旧分） | TTL 24h 对齐情报日更周期 + refresh 手动刷新通道 |
| key 泄露 | key 走 .env 不入 git（极验同口径） |
| mock 分数与真实分布偏离 | mock 仅测试/兜底用——三档覆盖联动分支即可,不模拟真实分布 |

---

## 七、里程碑

| 阶段 | 交付 | 验收门槛 |
|------|------|---------|
| P5-4a | abuseipdb_client（三态+配额+缓存） | 专项三态/配额/缓存组全绿 |
| P5-4b | apply_to_reputation 两级串联 | 专项联动/串联组全绿 |
| P5-4c | check 端点（第 32 个） | HTTP 层专项 |
| P5-4d | 收官 | 专项 30 项+security 回归 16/16+实机 14 项+提交推送 |

---

## 八、外部待办（P5-4 启动前确认）

1. **AbuseIPDB 免费账号注册**（https://www.abuseipdb.com/ → API 标签页创建 key，1000 次/天）
2. key 写入 `.env`：`SECURITY_ABUSEIPDB_KEY=xxx` + `SECURITY_ABUSEIPDB_MODE=mock_fallback`
3. 无 key 不阻塞交付（mock 轨全链路先行——极验 v4 同口径）

---

*P5-4 计划（2026-09-03）：单 IP 情报是段级情报的精度补刀——两级串联（免费段级前置+付费单 IP 兜底）+ 三重配额节省（段过滤/缓存/红线）+ 三级强度递进（降档/轻扣/零影响），把外部依赖的成本与误杀风险同时压到最低。*
