# 43号·P5-2 Redis 告警接入消息通道 实施计划 v1.0

> 配套：[43号P5_收口增强实施计划.md](43号P5_收口增强实施计划.md) §三
> 定位：P4-4 的 Redis 体检告警（大 key / rate 泄漏 / 内存水位 / 碎片率）当前仅**面板手动体检时可见**——无人主动查看则告警等于不存在。P5-2 把告警从"被动可见"变成"主动触达"：体检发现 P1 级风险 → 管理员站内信自动送达。
> 调研结论（2026-09-03）：
> 1. `message_service.send_message` 已支持 `CATEGORY_SECURITY` 分类，且该分类在 **MANDATORY_CATEGORIES（不可退订强制投递）**——安全告警天然绕过订阅/静默时段/频率四重调控，无需任何白名单改造；
> 2. 调度挂钩点为 `security_scheduler.run_scheduled_security_tasks()`（P4-2 已建,追加 ④ 步骤即可,`SECURITY_SCHEDULER_MODE=on` 总开关复用）；
> 3. 管理员收件人经 `MemberRepository.list_all()` 过滤 `role == "admin"`（auth 模块角色字段,无现成 list_admins——服务层过滤,内存/Redis 双模式通用）；
> 4. `RedisHealthService.collect()` 返回 `alerts: [{level, rule, message}]`（level ∈ critical/warn/info）——P5-2 只触达 critical+warn（info 级碎片率提示仅面板可见,防收件箱噪声）。

---

## 一、交付物总览

| 编号 | 内容 | 文件 | 性质 |
|------|------|------|------|
| P5-2a | 告警发送服务(采集→过滤→去重→触达) | `services/security_alert_service.py` | 新建 |
| P5-2b | 调度器日度轨集成(④ 步骤+统计留痕) | `services/security_scheduler.py` | 修改 |
| P5-2c | 手动轨端点+面板一键发送按钮 | `routes/security_routes.py` + `js/security-dashboard.js` | 修改 |
| P5-2d | 专项测试+回归+实机+提交 | `test_security_p5_2.py` + `verify_security_p5_2_live.py` | 新建 |

**无新增环境变量**：调度轨由既有 `SECURITY_SCHEDULER_MODE` 门控（默认 off,开启即日度体检+告警）；去重窗口固定 24h（计划既定口径,不做配置面）。

---

## 二、P5-2a：告警发送服务

### 服务结构（新建 `services/security_alert_service.py`）

```python
class SecurityAlertService:
    """Redis 体检告警 → 管理员站内信(P5-2)"""

    # 触达级别门槛: info 仅面板可见(防噪声), critical/warn 才发消息
    ALERT_LEVELS = ("critical", "warn")
    # 同规则去重窗口(秒)——防日度体检重复轰炸
    DEDUPE_TTL = 86400

    async def notify_redis_alerts(self, force: bool = False) -> dict:
        """体检 → 过滤 → 去重 → 管理员触达

        Args:
            force: True 跳过 24h 去重(手动轨演练/通道验证用)

        Returns:
            {alerts, eligible, sent, deduped, admins,
             failed, collectedAt}
        """
        # ① 采集(复用 P4-4, 异常 fail-soft 返回错误标记)
        report = await RedisHealthService().collect()
        alerts = report.get("alerts") or []

        # ② 级别过滤: critical/warn 触达, info 不发
        eligible = [a for a in alerts
                    if a.get("level") in self.ALERT_LEVELS]

        # ③ 规则级 24h 去重(force 跳过)
        fresh = []
        deduped = 0
        for a in eligible:
            if not force and not await self._claim(a["rule"]):
                deduped += 1
                continue
            fresh.append(a)

        # ④ 管理员触达(逐一发送, 单人失败不阻断)
        admins = await self._list_admin_ids()
        sent = failed = 0
        if fresh and admins:
            title, content = self._compose(fresh)
            for admin_id in admins:
                try:
                    await MessageService().send_message(
                        admin_id, CHANNEL_INMAIL, title, content,
                        category=CATEGORY_SECURITY,
                        priority=PRIORITY_P1)
                    sent += 1
                except Exception as exc:
                    failed += 1
                    logger.warning("security_alert_send_failed "
                                   "admin=%s: %s", admin_id, exc)
        return {...}

    async def _claim(self, rule: str) -> bool:
        """同规则 24h 内第二次调用返回 False(去重锁)

        Redis: SETNX security43:alert:dedupe:{rule_hash} TTL 86400
        内存: bucket _security43_alert_dedupe {rule: expiry_ts}
        """

    async def _list_admin_ids(self) -> list[int]:
        """会员表 role=admin 的全部会员 ID(运行时查询)"""

    def _compose(self, alerts: list) -> tuple[str, str]:
        """N 条告警 → 单封站内信(标题聚合+逐条明细+处置建议)"""
        # title: "[安全运维] Redis 体检告警(2 项)"
        # content: 逐条 [级别][规则] 消息 + 处置建议 + 采集时间
        #          + "面板⑦区「Redis 实况体检」可查看详情"
```

### 关键设计决策

| 决策 | 口径 | 理由 |
|------|------|------|
| **聚合发送** | 一轮体检 N 条告警 → **单封**站内信(逐条列明) | 管理员收 N 封 vs 1 封：防轰炸优先 |
| **规则级去重** | 同 `rule`(如"单键 >100KB")24h 一条 | 大 key 昨天未处理今天还在 → 是**提醒不是新事件**；处理掉后次日再出现则重新触达(锁过期) |
| **CATEGORY_SECURITY** | 强制投递分类 | 调研确认在 MANDATORY_CATEGORIES——绕过退订/静默/频率,安全告警必须送达 |
| **P1 优先级** | `priority=PRIORITY_P1` | 对齐消息模块口径(高优先,非 P0 紧急——P0 留给全站级故障) |
| **单人失败不阻断** | 逐 admin try/except | 三管理员中一人订阅状态异常不影响其余两人收到 |
| **无告警零发送** | eligible 为空直接返回 | 不发"一切正常"骚扰信(面板体检可见健康态) |

---

## 三、P5-2b：调度器日度轨集成

### 挂钩点：`run_scheduled_security_tasks()` 追加 ④ 步骤

```python
    # ④ Redis 日度体检+告警触达(P5-2): 调度器开启即自动巡检,
    #    P1 级风险站内信直达管理员(24h 规则级去重防重复)
    try:
        from services.security_alert_service import (
            SecurityAlertService,
        )
        alert = await SecurityAlertService().notify_redis_alerts()
        result["alerts"] = {
            "eligible": alert.get("eligible", 0),
            "sent": alert.get("sent", 0),
            "deduped": alert.get("deduped", 0),
        }
    except Exception as exc:
        logger.warning("security_scheduler_alert_failed: %s", exc)
        result["errors"].append(f"alert:{exc}")
```

- 统计留痕扩展：`save_scheduler_stats` 增加 `lastAlerts` 字段（面板/日报可观察告警触达趋势）
- **体检结果与告警分离**：collect() 全量结果不落库（开销大），仅告警计数留痕——口径与 P4-4"体检不进自动刷新"一致（调度轨是低频日度，非 30s 刷新）

---

## 四、P5-2c：手动轨端点 + 面板联动

### 端点（`routes/security_routes.py`,第 30 个端点）

```python
@router.post("/admin/redis/alert/test")
async def admin_redis_alert_test(
    force: bool = Query(False, description="跳过 24h 去重"
                        "(演练/通道验证)"),
    x_role: str = Header(default="", alias="X-Role"),
):
    """手动轨: 立即 Redis 体检并发送告警站内信(通道演练)"""
    _require_admin(x_role)
    # → SecurityAlertService().notify_redis_alerts(force=force)
```

### 面板⑦区联动（`js/security-dashboard.js`）

`renderRedisHealth` 渲染告警条时，若存在 critical/warn 级告警，追加按钮：

```javascript
// 告警条区域尾部:
// [发送站内信] → POST /admin/redis/alert/test
// showInfo('已发送: N 名管理员 / 去重 M 条')
```

- 按钮仅在有 P1 级告警时出现（无告警不显示——与"无告警零发送"口径一致）
- force=false 默认（面板日常操作走去重；演练需求经 API 直调）

---

## 五、测试与验收

### 专项测试（`test_security_p5_2.py`,预计 24 项）

| 分组 | 覆盖 |
|------|------|
| 服务单元 | 无告警零发送 / critical+warn 触达 / info 过滤不发 / 聚合单封(标题含条数) / 内容含规则+消息 / P1 优先级+SECURITY 分类落库 |
| 去重 | 同规则 24h 第二次 deduped / 不同规则各自独立 / force 跳过去重 / 去重锁过期后重新触达 |
| 管理员触达 | 多管理员逐一发送 / 无管理员零发送 / 单人 send 异常不阻断其余 / 管理员过滤正确(member 角色不收) |
| 调度轨 | run_scheduled_security_tasks 含 alerts 统计 / 调度异常不阻断基线重建 / lastAlerts 留痕 |
| HTTP 层 | 缺 Role 403 / force 参数 / 返回结构(sent/deduped/admins) |

### 实机验收（`verify_security_p5_2_live.py`,预计 12 项）

1. 正常业务零影响（健康检查+业务流量）
2. 制造大 key(>100KB Hash)→ 手动轨端点 → 返回 sent≥1
3. **管理员站内信收件箱可见**（经消息模块 API 查 CATEGORY_SECURITY 最新消息,标题含"Redis 体检告警"）
4. 消息内容含规则与处置建议
5. 24h 内重复触发 → deduped=1 sent=0（去重生效）
6. force=true → 跳过去重重新发送
7. 清理大 key → 再体检 → 无告警零发送
8. 调度轨：容器内手动执行单轮 `run_scheduled_security_tasks()` → 统计含 lastAlerts
9. 面板⑦区按钮渲染（浏览器实测:有告警时"发送站内信"按钮可见,点击后 info 提示）
10. 全程业务回归正常

### 回归范围

- security 全系列 14 套（13 既有 + P5-1 新增）
- **message 模块测试回归**（`test_message*.py`——告警走 send_message,须确认零影响）

---

## 六、关键风险与对策

| 风险 | 对策 |
|------|------|
| 收件箱轰炸(日度体检天天告警) | ①规则级 24h 去重 ②仅 critical/warn 触达 ③聚合单封——三重抑制 |
| 管理员列表为空(无人收到) | 返回 admins 计数；实机验收断言 sent≥1；运维口径:管理员角色经 auth 模块既有端点配置 |
| send_message 被防骚扰拦截 | 调研已确认 CATEGORY_SECURITY 强制投递(MANDATORY_CATEGORIES)——零改造直通 |
| collect() 执行开销(KEYS/SLOWLOG) | 仅两处触发:日度调度轨(SECURITY_SCHEDULER_MODE=on)+手动端点；不进 30s 自动刷新(与 P4-4 口径一致) |
| 调度轨告警失败影响基线重建 | ④ 步骤独立 try/except,errors 留痕不中断(①②③ 步骤既有范式) |

---

## 七、里程碑

| 阶段 | 交付 | 验收门槛 |
|------|------|---------|
| P5-2a | security_alert_service | 专项全绿(服务单元+去重+触达) |
| P5-2b | 调度器 ④ 集成 | 调度轨专项+lastAlerts 留痕 |
| P5-2c | 端点+面板按钮 | HTTP 层专项+浏览器实测 |
| P5-2d | 收官 | 专项+security/message 回归零新增+实机 10+ 项全绿+提交推送 |

---

*P5-2 计划(2026-09-03):纯集成层交付——不新建检测能力(P4-4 已有)、不新建消息通道(消息模块已有),只建"告警→触达"桥接层;三重防骚扰设计(级别过滤+规则去重+聚合单封)保证运维噪声可控。*
