# 43号·AI智能安全管理模块 P6-3 enforce 就绪度自动化 详细实施计划 v1.0

> 配套：[P6 总计划](43号P6_聚合规模化与就绪度实施计划.md) §四 / [P6 全景收官总结报告](43号P6_全景收官总结报告.md) §八
> 定位：把 enforce 上线检查单从**操作指南 §六纯文字**（5 条人工核对）变成**一键就绪度评估端点 + 面板灯卡**——数据说话，不拍脑袋。**纯聚合层零新检测**（全部消费既有端点/服务留痕数据）。
> 铁律（总计划 §四已定）：**端点只评估不切换**——切换仍人工改 `SECURITY_ENFORCE_LEVEL=enforce`（防误切换，检查单第 6 条人工操作不可自动化）。

---

## 一、背景与目标

### 现状（操作指南 §六 enforce 上线检查单，纯人工）

```
1. observe 运行 ≥1 周，falsePositiveRate <10%
2. 待裁决事件积压已清零（pending=0）
3. 会员申诉通道畅通（已验证）
4. 健康检查路径在白名单（/api/decision/health 永久放行，Docker 探针不受影响）
5. 切换后 30 分钟内盯 blocks 数与正常业务 200 率
```

**痛点**：达标数据分散在四个端点（`/admin/reports/daily` 序列、`/admin/dashboard`、申诉队列、白名单代码常量）——切换 enforce 前需人工逐项翻查拼装判断。

**目标**：`GET /admin/enforce/readiness` 一键输出五检查（过/不过 + actual/required 实测值）+ 三信号达标汇总 + blockers 未过项清单 + `overall: ready|holding`；面板⑦区灯卡一屏可见。

**口径划分**：第 1-4 条为**切换前置项**（本端点自动化）；第 5 条为**切换后人工动作**（30 分钟盯盘——不自动化，端点 note 明示）。

---

## 二、数据源盘点（调研结论：全部既有，零新检测）

| 检查项 | 数据源（已核实） | 计算口径 | 门槛 |
|--------|----------------|---------|------|
| `observe_days` 灰度观察期 | [soc_report_service.py](../backend/services/soc_report_service.py) `daily_series(days=14).summary.activeDays` | 近 14 天中**有事件的天数**（事件按 `createdAt` 日期过滤；冷启动无流量不算观察期——双重保守，与误报率分母同源） | ≥ 7 天 |
| `false_positive_rate` 误报率 | 同上 `summary.falsePositiveRate` | `falsePositive / (confirmed + falsePositive)`（**已裁决分母**，对齐 dashboard 既有口径） | < 10%（0.10——检查单整体处置门槛，比 D5 单信号 <5% 宽松） |
| `pending_backlog` 待裁决积压 | [security_service.py](../backend/services/security_service.py) `stats().events.pending` | `verdict == "pending"` 事件计数 | = 0 |
| `appeal_channel` 申诉通道畅通 | `repo.list_appeals()` 动态调用 | 调用成功（存储可达 + 申诉数据模型在位）即 passed；异常（fail-soft 捕获）→ not passed | 畅通 |
| `health_whitelist` 健康检查白名单 | `security_service.GATEWAY_WHITELIST` 代码常量（L62-69） | 常量元组含 `/api/decision/health`（网关快道永久放行——Docker 探针不受 enforce 影响）；实机层另验真实 200 | 含 |

**三信号达标汇总**（signals——加分项不进 overall，与总计划口径一致）：

| 信号 | 数据源 | 输出字段 |
|------|--------|---------|
| D5 联动 | `SocReportService.d5_observation()`（P4-1 已含 criteria 三条件 + d5Enforce 实况） | `samples/falsePositiveRate/observeDays/criteria/recommendation/d5Enforce` |
| 威胁情报 | `ThreatIntelService.stats()` | `totalCidrs/matchMode/auto{enabled,degraded,degradedSources}` |
| GeoIP | `geoip_service.geo_available()` | `available`（mmdb 就位即 true） |
| AbuseIPDB | `abuseipdb_client` mode 实况 | `mode`（mock/real/mock_fallback——可观测加分项） |

---

## 三、实施方案（四阶段）

### P6-3a：服务层 `enforce_readiness_service.py`（新建，纯聚合）

```python
"""43号·P6-3 enforce 上线就绪度评估(纯聚合层零新检测)

数据源全部复用既有服务(§二盘点):
    - SocReportService.daily_series   → 观察天数/误报率
    - Security43Service.stats          → 待裁决积压/当前灰度态
    - repo.list_appeals                → 申诉通道动态探活
    - GATEWAY_WHITELIST                → 健康检查白名单(代码常量)
    - d5_observation/threatintel stats/geo_available/abuseipdb → 三信号

铁律: 只评估不切换(切换仍人工改 SECURITY_ENFORCE_LEVEL——
操作指南 §六检查单第 6 条人工操作不可自动化, 防误切换)。
"""

OBSERVE_WINDOW_DAYS = 14     # 观察窗口(面板日报序列同口径)
OBSERVE_MIN_DAYS = 7         # 检查单: observe ≥1 周
MAX_FALSE_POSITIVE = 0.10    # 检查单: 误报率 <10%


class EnforceReadinessService:
    """enforce 就绪度评估(五检查 + 三信号 + blockers)"""

    def __init__(self):
        from services.security_service import (
            Security43Service, GATEWAY_WHITELIST,
        )
        self._security = Security43Service()
        self._whitelist = GATEWAY_WHITELIST

    async def evaluate(self) -> dict:
        # ① 观察天数 + ② 误报率(同一次 daily_series, 14×daily_report
        #    聚合只算一遍——两检查共享)
        series = await SocReportService().daily_series(
            OBSERVE_WINDOW_DAYS)
        s = series["summary"]

        # ③ 积压 ④ 申诉探活 ⑤ 白名单
        stats = await self._security.stats()
        appeals_ok, appeals_detail = await self._probe_appeals()
        whitelist_ok = "/api/decision/health" in self._whitelist

        checks = [
            {"id": "observe_days", "name": "灰度观察期≥7天",
             "passed": s["activeDays"] >= OBSERVE_MIN_DAYS,
             "actual": f"{s['activeDays']}天",
             "required": f"≥{OBSERVE_MIN_DAYS}天",
             "detail": f"近{OBSERVE_WINDOW_DAYS}天中有事件的天数"},
            {"id": "false_positive_rate", "name": "误报率<10%",
             "passed": (s["falsePositiveRate"] is not None
                        and s["falsePositiveRate"]
                        < MAX_FALSE_POSITIVE),
             "actual": f"{s['falsePositiveRate']:.1%}",
             "required": "<10%",
             "detail": "分母=已裁决事件(confirmed+falsePositive)"},
            {"id": "pending_backlog", "name": "待裁决积压=0",
             "passed": stats["events"]["pending"] == 0,
             "actual": f"{stats['events']['pending']}件",
             "required": "=0",
             "detail": "GET /admin/events?verdict=pending"},
            {"id": "appeal_channel", "name": "申诉通道畅通",
             "passed": appeals_ok,
             "actual": appeals_detail, "required": "已验证",
             "detail": "POST/GET /api/security/appeals + "
                       "管理裁决队列读写探活"},
            {"id": "health_whitelist", "name": "健康检查白名单",
             "passed": whitelist_ok,
             "actual": f"{len(self._whitelist)}路径",
             "required": "含/api/decision/health",
             "detail": "网关快道永久放行(Docker 探针不受影响)"},
        ]
        blockers = [self._blocker_text(c) for c in checks
                    if not c["passed"]]
        return {
            "success": True,
            "overall": "ready" if not blockers else "holding",
            "enforceLevel": stats.get("enforceLevel"),
            "checkedAt": ts(),
            "checks": checks,
            "signals": await self._collect_signals(),
            "blockers": blockers,
            "note": "本端点只评估不切换——切换仍需人工改 "
                    "SECURITY_ENFORCE_LEVEL=enforce 并执行检查单"
                    "第5条(切换后30分钟盯 blocks 数与业务 200 率)",
        }

    async def _probe_appeals(self) -> tuple[bool, str]:
        """申诉通道动态探活(list 调用成功即畅通, fail-soft)"""
        try:
            appeals = await self._security.repo.list_appeals(
                limit=1)
            n = len(appeals)
            return True, (f"队列读写正常(已有{n}条)"
                          if n else "队列读写正常(空)")
        except Exception as exc:
            return False, f"探活异常: {exc}"[:120]

    def _blocker_text(self, c: dict) -> str:
        """未过检查项 → blockers 中文摘要"""
        mapping = {
            "observe_days": "观察期 {actual} 不足(需{required})",
            "false_positive_rate": "误报率 {actual} 未达标(需{required})",
            "pending_backlog": "待裁决积压 {actual} 未清零",
            "appeal_channel": "申诉通道探活失败: {actual}",
            "health_whitelist": "健康检查白名单缺失 "
                                "/api/decision/health",
        }
        return mapping[c["id"]].format(**c)
```

设计要点：
- **`daily_series` 只算一遍**——观察天数/误报率两检查共享同一次 14×daily_report 聚合（面板日报序列同量级开销，管理端按需点击可接受）
- **`_probe_appeals` fail-soft**——探活异常本身即"不畅通"信号（not passed + detail 含原因），不抛 500
- **blockers 与 checks 分离**——面板灯卡只读 blockers 摘要，详情展开看 checks

### P6-3b：路由第 34 端点

```python
# security_routes.py(learning 三连之后追加)

@router.get("/admin/enforce/readiness")
async def admin_enforce_readiness(
    x_role: str = Header(default="", alias="X-Role"),
):
    """enforce 上线就绪度评估(五检查+三信号+blockers, 只评估不切换)"""
    _require_admin(x_role)
    try:
        from services.enforce_readiness_service import (
            EnforceReadinessService,
        )
        return await EnforceReadinessService().evaluate()
    except Exception as e:
        raise _handle(e) from e
```

### P6-3c：前端⑦区就绪度灯卡

- [ai-security-dashboard.html](../ai-security-dashboard.html) ⑦区 D5 状态卡下方加卡位：`enforce 就绪度`（灯 + blockers 首条摘要 + "查看五检查"展开）
- [security-dashboard.js](../js/security-dashboard.js) `loadReportSeries()` 内链式追加 `loadEnforceReadiness()`（与 D5 状态卡同范式——不进 30s 自动刷新，日报序列按钮一并触发）：

```javascript
/* P6-3: enforce 就绪度卡(ready 绿灯/holding 黄灯+blockers,
 * 与 D5 状态卡同区, 日报序列按钮一并触发) */
async function loadEnforceReadiness() {
    var b = await fetchJson(
        api('/api/security/admin/enforce/readiness'),
        { headers: headers() }, 'enforce 就绪度');
    var ready = b.overall === 'ready';
    // 灯: ready=green / holding=yellow + blockers 首条
    // 展开: 五检查表(id/name/passed/actual/required) + 三信号摘要
    // 文案铁律: ready 仍提示"切换需人工改 SECURITY_ENFORCE_LEVEL"
}
```

- 灯卡文案：ready → `✓ ready（切换需人工操作）`；holding → `holding — ${blockers[0]}`

### P6-3d：操作指南 §六联动

[安全管理端操作指南.md](安全管理端操作指南.md) §六检查单下方补一行：

> **一键评估**：面板⑦区"enforce 就绪度"灯卡 / `GET /api/security/admin/enforce/readiness`（五检查+三信号+blockers；只评估不切换，人工核对为兜底）。

---

## 四、测试与验收

### 专项测试（`test_security_p6_3.py`，预计 22 项）

| 分组 | 覆盖 |
|------|------|
| 观察期检查（2） | 空 store → not passed（actual=0天）/ 造 7 个不同日期事件 → passed（actual=7天口径正确） |
| 误报率检查（3） | 造裁决数据 20% 误报 → not passed / 4% 误报 → passed / 零裁决 → not passed（分母为 0 无数据不算达标——保守口径） |
| 积压检查（2） | 造 pending 事件 → not passed（actual=N件）/ 全裁决 → passed |
| 申诉探活（2） | 正常 → passed（detail 含队列状态）/ mock `list_appeals` 抛异常 → not passed（fail-soft 不抛 500） |
| 白名单检查（1） | 常量含 `/api/decision/health` → passed |
| overall 判定（2） | 五检查全过 → ready / 任一不过 → holding |
| blockers（2） | 未过项中文摘要逐条匹配（观察期不足/误报率超标/积压未清零文案）/ 全过 → 空列表 |
| 三信号汇总（4） | signals.d5 含 criteria 三条件与 d5Enforce 实况 / signals.threatintel 含 totalCidrs 与 auto.degraded / signals.geo.available 布尔 / signals.abuseipdb.mode |
| 结构与铁律（3） | enforceLevel 反映当前灰度态（observe）/ note 含"只评估不切换"文案 / checkedAt 时间戳在位 |
| HTTP 层（3） | 缺 X-Role 403 / 200 结构（checks 五项/signals/blockers/overall）/ 服务层异常 → 500 包装（既有 `_handle` 口径） |

### 实机验收（`verify_security_p6_3_live.py`，预计 8 项）

1. 正常业务零影响（健康检查 + 商品列表 200）
2. 缺 Role 403
3. 真实容器就绪度输出（五检查结构完整 + actual 实测值合理 + 三信号在位）
4. 制造积压（docker exec 注入 1 条 pending 事件）→ holding + blockers 含"待裁决积压"
5. 裁决清积压（POST `/admin/events/{id}/decide` confirm）→ 该检查恢复 passed
6. `overall` 与 blockers 一致性（holding 时 blockers 非空 / ready 时空）
7. note 铁律文案（"只评估不切换"）+ enforceLevel=observe 实况
8. 业务回归（商品列表 200）

### 回归范围

security 全系列 20 套（零存量改动——本期待改文件：新建 1 服务 + 路由追加 1 端点 + 前端 2 文件 + 操作指南 1 行，**不触碰任何既有函数**，预期零回归风险）。

---

## 五、关键风险与对策

| 风险 | 对策 |
|------|------|
| 就绪度被误用为自动切换依据 | 端点只评估不切换（note 铁律文案）+ 面板 ready 态文案仍提示"切换需人工操作"——检查单第 6 条人工操作不可自动化 |
| 误报率计算口径分歧（全事件 vs 已裁决） | 沿用 dashboard 既有口径（已裁决分母）+ check.detail 明示分母——检查单原文同口径 |
| 零裁决数据误判 ready | 分母为 0 时 false_positive_rate 判 not passed（无数据 ≠ 达标，保守口径）；配合 activeDays ≥7 双重保守 |
| daily_series 14×daily_report 聚合开销 | 只在按钮点击时触发（不进 30s 自动刷新——日报序列/D5 状态卡同范式）；服务层两检查共享单次序列计算 |
| 申诉探活误报（存储瞬断） | fail-soft 捕获 → 该检查 not passed（探活失败本身就是"不畅通"信号），不影响其余检查输出 |
| 面板灯卡噪声（holding 常态化） | 冷启动期 observe_days 必然不足——灯卡黄灯 + blockers 首条是**预期态**（观察期未到的正常展示），文案不用警示红色 |

---

## 六、交付物与里程碑

| 阶段 | 内容 | 文件 | 类型 |
|------|------|------|------|
| P6-3a | 就绪度服务（五检查+三信号+blockers） | services/enforce_readiness_service.py | 新建 |
| P6-3b | 第 34 端点 | routes/security_routes.py | 追加 |
| P6-3c | ⑦区灯卡 | ai-security-dashboard.html + js/security-dashboard.js | 修改 |
| P6-3d | 操作指南 §六联动 | docs/安全管理端操作指南.md | 修改 |
| P6-3e | 专项 22 项 + 实机 8 项 + security 回归 20/20 + 提交推送 | test_security_p6_3.py + verify_security_p6_3_live.py | 新建 |

**验收门槛**：专项 22/22 + 实机 8/8（含制造积压→holding→清积压→恢复的完整 E2E）+ security 回归 20/20。

**统一口径**（P0-P6 惯例）：专项测试 + security 回归零新增失败 + Docker 实机验收 + 提交推送。

---

*P6-3 计划（2026-09-03）：enforce 切换从"翻文档逐项核对"变成"看一眼绿灯"——43号 P6 三方向的最后一块拼图（计划已就绪，纯聚合层半天级交付）。至此检查单五条中四条自动化（第 5 条切换后盯盘为人工动作明示不自动化），配合既有 fail-open 一键回退，observe→enforce 灰度切换的决策链与安全网全部闭合。*
