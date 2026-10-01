# v2-E3·Escrow 延迟结算——实施方案(可开工级)

> 版本: 2026-10-01 | 前置: E1(追回)/E2(设备闸)已上线 | 设计依据: v2 方案 §1.1
> 范围: traffic79 引进积分(300 分/人)的观察期延迟结算——防"僵尸号注册即套分"
> 状态: 实施方案(未实施), 按本文件步骤执行

---

## 一、目标与约束

**目标**: 引进积分从"注册即发"改为"7 天观察期后按活跃发放"——僵尸号(注册即弃)不产生积分成本。

**硬约束**:
- welcome80(100 新人礼)/share80(20 分享)保持即时发放(小额定性: 拉新体验优先)
- 全程 settings 开关控制(`referralEscrowEnabled` 默认 **False** 灰度)
- 不动 points 账户结构/前端展示(零前端改造)
- fail-soft: escrow 任何异常不阻断绑定主链路

## 二、关键选型: 三方案对比(本方案取 C)

| 方案 | 机制 | 优点 | 缺点 | 结论 |
|---|---|---|---|---|
| A 即时发+观察追回 | 注册即发 300, 7 天不活跃用 E1 追回 | 复用 E1, 会员体验即时 | 期间分可能已消费→欠口; 追回运营介入感强 | 弃 |
| B 真冻结 | 账户加 frozenPoints, 流水 freeze/unfreeze | 语义严格(docx 原意) | 改账户结构+流水状态机+前端展示, 改动面大 | 弃 |
| **C 延迟发放** | **注册时仅写 escrow 记录不发分; 7 天后活跃达标才 earn_points** | **零账户变更/零追回依赖/未发的分不存在追回问题** | 会员到账延迟 7 天(开关控制+站内信告知) | **取** |

## 三、详细设计

### 3.1 数据结构(2 项)

**points_escrows 表**(promotion_repository 承载, 键型对齐 v1 文档风格):

| 字段 | 类型 | 说明 |
|---|---|---|
| escrowId | int PK | 序列 `promotion:escrow:seq` |
| userId | string | 推荐人(受益人) |
| source | string | 固定 "traffic79"(v2-E3 仅此轨) |
| refId | string | `traffic79:{inviteeMemberId}`(与即时轨同源, 幂等域一致) |
| points | int | 待发额度(发放时快照, 支持档位调整后旧单按旧值) |
| state | string | **pending**(观察中) / **unlocked**(已发放) / **forfeited**(作废) |
| deadline | ISO8601 | 创建+escrowDays(默认 7 天) |
| createdAt / settledAt / reason | ISO8601/string | 结算时间与依据 |

- Redis: hash `promotion:escrow:{escrowId}`
- 索引: list `promotion:escrow_by_user:{userId}`(会员查"观察中奖励")
- 到期索引用**扫描过滤**(state=pending 且 deadline 到期)——量级: 日增 escrow 数(绑定量)可控, 每轮全量扫 pending 免 zset 双写一致性(74号巡检先例: 简单优先)

**settings 增量**(DEFAULT_SETTINGS + update_settings 白名单 + Request 模型):

| 字段 | 默认 | 说明 |
|---|---|---|
| referralEscrowEnabled | **False** | 总开关(灰度铁律) |
| escrowDays | 7 | 观察期天数(≥1) |
| escrowUnlockOrder | true | 首笔订单可解冻 |

### 3.2 发放路径改造(promotion_service._award_referral_points)

```
现行: settings 检查 → 幂等查重(traffic79:{invitee} 流水) → earn_points 300 → 站内信
改造: settings 检查 → 幂等查重(双域: points 流水 traffic79:{invitee}
       AND escrow 表同 refId pending/unlocked) →
  ├─ referralEscrowEnabled=False(默认): 现行路径不变(即时发+站内信)
  └─ =True: 写 escrow 记录(state=pending, points=当前 pointsPerReferral)
            + 站内信改文案"您推荐的会员已注册, +300 积分将在 7 天
              活跃观察期后到账"(fail-soft)
```

**幂等双保险**: escrow 写入在 bind_relation 的 relation 锁内(一人一绑) +
escrow 表 refId 查重——与 E1/即时轨同源幂等域, 开关切换不产生双发
(即时轨流水存在 → escrow 不建; escrow pending/unlocked 存在 → 不即时发)。

### 3.3 解冻调度器(services/growth80_escrow_scheduler.py 新建)

结构对齐 ibms_patrol_scheduler / order_timeout_scheduler 惯例:

```
环境开关: GROWTH_ESCROW_AUTO=off(默认)——与 referralEscrowEnabled 双闸
周期: ESCROW_INTERVAL=3600s(下限 300)
_scheduler_loop():
    while True:
        sleep(interval)
        await run_escrow_settlement()      # 扫描+结算, 异常 fail-soft
start_scheduler() / stop_scheduler() / scheduler_running()  # 惯例三件套
main.py startup 挂载(与 IBMS 调度器同段)
```

**run_escrow_settlement()**(独立可单测, 移植 74号 run_patrol 模式):

```
1. settings.referralEscrowEnabled=False → 跳过(双保险, 但已有 pending
   仍要结算——开关关闭只停新建, 存量照跑, 见 §3.5 回滚)
2. 扫描: escrow 全量 → state=pending 且 deadline ≤ now
3. 逐条(锁 promotion:escrow:{id}):
   达标判定(满足其一):
     a) 被引进人首笔订单: order_repo.get_by_member(invitee) 非空
        且状态非 cancelled/refunded
     b) 注册后回访: member.last_login_at > escrow.createdAt
        (摸底实证: 无 login_count 计数, 以"回访过"为僵尸号过滤器——
         僵尸号不会回来; login_count 精确计数列 P2 强化)
   → 达标: earn_points(推荐人, points, source=traffic79,
             refId=traffic79:{invitee}) + state=unlocked +
             站内信"+300 积分已到账(您推荐的会员已活跃)"(fail-soft)
   → 未达标: state=forfeited, reason="观察期满未活跃"(不发分不通知)
4. 熔断自检(每轮末尾):
   近 7 天 settled(unlocked+forfeited) 解冻率 =
   unlocked/(unlocked+forfeited);
   连续 3 轮(天) < 90% → update_settings(referralEscrowEnabled=False)
   + notify 管理员站内信"Escrow 解冻率熔断, 已自动回落即时发放"
   (复用 security_alert notify_growth_alerts 通道, extra 注明)
```

### 3.4 观测面

- `GET /api/promotion/my/funnel` 的 totals 增 `pendingPoints`(escrow
  pending 求和)——会员看"观察中 N 分"(service 层一处聚合, 前端 funnel
  页可选展示, 零改造也不影响)
- admin: `GET /api/promotion/admin/escrows?state=pending&memberId=`
  (列表+统计: pending/unlocked/forfeited 计数与解冻率)

### 3.5 回滚与边界

| 场景 | 动作 |
|---|---|
| 紧急回 v1 即时发放 | settings `referralEscrowEnabled=false`——新绑定即时发; **存量 pending 照常结算**(不弃单) |
| 调度器故障 | 调度器挂掉不影响 pending 数据; 手动 `POST /api/promotion/admin/escrows/settle`(重结算入口, 幂等) |
| 误作废申诉 | admin 手动补发工具已有(§3.6 grant/积分侧可经 earn)——escrow 单条 state 由 admin 端点可改 `POST .../escrows/{id}/force-unlock`(谨慎项) |

## 四、文件清单与改动量

| 文件 | 改动 | 量级 |
|---|---|---|
| repositories/promotion_repository.py | escrow CRUD 六方法 + settings 三字段 | ~120 行 |
| services/promotion_service.py | _award_referral_points 分叉 + escrow 查询面 + settings 白名单 | ~60 行 |
| services/growth80_escrow_scheduler.py | 新建(调度器+run_escrow_settlement+熔断) | ~150 行 |
| routes/promotion_routes.py | admin escrows 列表/手动结算/强制解冻 + Request 模型 + settings 字段 | ~70 行 |
| services/growth80_service.py | 无改(仅 traffic79 轨) | 0 |
| main.py | escrow 调度器 startup 挂载 | 3 行 |

## 五、单测用例(test_growth_v2_e3.py)

| # | 用例 | 断言要点 |
|---|---|---|
| T1 | 开关关(默认): 绑定→即时发 300(现行不变) | 现行回归零破坏 |
| T2 | 开关开: 绑定→不即时发/escrow pending 建立/站内信"观察期"文案 | 双域幂等: 流水无 traffic79 记录 |
| T3 | 幂等: 已有即时流水的 invitee 不再建 escrow(开关切换防双发) | 切换场景 |
| T4 | 结算达标(回访): last_login_at 晚于 createdAt → earn 300 + unlocked + 到账站内信 | monkeypatch member/时间 |
| T5 | 结算达标(首单): get_by_member 非空 → 同 T4 | mock order_repo |
| T6 | 结算未达标: 无回访无订单且 deadline 过 → forfeited, 不发分不通知 | |
| T7 | 结算幂等: 已 unlocked/forfeited 重跑跳过; 锁内单条并发安全 | |
| T8 | 熔断: 连续 3 轮解冻率 <90% → referralEscrowEnabled 自动 false + 告警调用(monkeypatch) | 状态计数 |
| T9 | 调度器: GROWTH_ESCROW_AUTO=off 静默; start/running 惯例 | 同 74号 T1 |

回归: test_growth80/traffic79/v2_e1/v2_e2 + promotion 47 全绿
(现行路径零破坏是 T1 的意义)。

## 六、部署与验收步骤

1. 本地: 单测 T1-T9 全绿 + 回归全绿 + main import
2. 生产: 备份(.bak-v2e3) → scp 六文件 → rebuild → healthy
3. 验收(容器内脚本, 测试号 1390000082x 段):
   a. 默认关确认(settings) + 绑定即时发(现行不变)
   b. 开 escrow → 新绑定 → 无即时分 + escrow pending + 站内信
   c. 造"回访"(改 member.last_login_at) → 手动触发结算端点 →
      300 到账 + unlocked
   d. 造不活跃(不改) → deadline 调过(测试参数 escrowDays=0 或直改
      deadline) → 结算 → forfeited
   e. **恢复 referralEscrowEnabled=False**(灰度默认, 验收留痕说明)
4. 文档回填: v2 方案 §七 E3 实录 + commit

## 七、风险清单

| 风险 | 缓解 |
|---|---|
| 会员感知"到账变慢"流失推荐动力 | 开关灰度放量+站内信文案明确"观察期"预期管理; 可按推荐人白名单先行 |
| 僵尸号 7 天内"回访登录一次"即解冻 | 回访仅是弱过滤器; E2 设备闸叠加(同设备多号不发分)——组合防御 |
| 调度器漏扫 | pending 扫描幂等+手动结算端点兜底; IBMS 巡检可加 escrow 积压检查项(后续) |
| 解冻率熔断误触发(小样本) | 阈值 90%+连续 3 天双条件; 熔断只降开关不删数据 |

---

*执行顺序建议: §3.1 存储 → §3.2 分叉 → §3.3 调度器 → §3.4 观测面 → 单测 → 部署。预计单次会话可完成(参照 E1/E2 实测节奏)。*
