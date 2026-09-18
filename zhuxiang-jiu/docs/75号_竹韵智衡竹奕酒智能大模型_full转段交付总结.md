# 75号·竹韵·智衡竹奕酒智能大模型 full 转段交付总结

> 文档版本：v1.1 · 2026-09-18（v1.1 补充第四章「代码变更明细」——4 文件变更点/行号/核心逻辑/兼容性影响面）
> 转段动作：ZYH_MODE assist → full（四档灰度范式升档 + L1 自主域开放）
> 前置文档：75号 SDD V3.0（工艺宪法/守门三层/DTDAE 范式）· 全周期交付（off→shadow→assist）
> 关联提交：8aaad87（feat(zyh): 75号升full档——四档灰度范式+L1自主域auto_patrol）
> 验证脚本：backend/prod_zyh_gradation_verify.py（四档自适应零破坏矩阵）

---

## 一、转段总览

| 维度 | 数据 |
|---|---|
| 转段时间 | 2026-09-18 21:50（Asia/Shanghai） |
| 转段方向 | assist → full（四档范式最高档） |
| 开放域 | L1 自主域白名单：auto_patrol（护栏自主巡检） |
| 保留域 | 知识 upsert / 守门规则变更 / resume / cache clear——full 档亦人工（铁律） |
| 范式依据 | 73/74 四档先例（member73 L1_AUTONOMY_DOMAINS / nexus74"A 档 auto 仅 full"） |
| 生产状态 | zxjiu.com 公网 mode=full(env) · 容器 healthy |
| 验证结论 | 本地 77/77 全绿 · 生产 23/23 全绿 |
| 全站意义 | 十六模型中第三位 full 档（73 会员体验 / 74 NexusFlow / 75 竹韵·智衡） |

---

## 二、full 档范式资格裁定（73/74 先例横向对齐）

| 模型 | 模式域 | full 语义 | 裁定 |
|---|---|---|---|
| 73号 会员体验 | off/shadow/assist/full | 低风险触达域自主（hint_render / silence_rule / form_ranking） | 已 full 运行 |
| 74号 NexusFlow | off/shadow/assist/full | 仅 A 档低风险域自主（B 档平台操作永远人工） | 已 full 运行 |
| **75号 竹韵·智衡** | **off/shadow/assist/full（本轮升格）** | **护栏自主巡检 auto_patrol（决策面每 10 次调用节流触发）** | **本轮执行** |
| 其余十三模型 | off/shadow/assist（三态） | 无 full 档 | assist 即终态 |

**75号 full 语义裁定**：75号决策面（chat 问答 / stress-test 推演 / probe debate 辩题）均为生成入口，在 assist 期已完全生效；full 档的差异化自主权落于**护栏巡检自动化**——替代人工定期 `POST /api/zyh/mode/guard`，形成"决策流量自驱动护栏巡检"的闭环。

---

## 三、转段执行链

```
范式调研: 73/74 full 档实现审计
  member73_registry: MODE_VALUES 四档封闭 + L1_AUTONOMY_DOMAINS 白名单
  nexus74_p3: "auto 自主仅 full 档(A 档低风险域)" 越权前置范式
  结论: full ≠ 决策面再增强, full = 低风险域自主权
    ↓
代码升格(4 文件, +194/-21):
  1) zyh_mode_service.py: MODE_VALUES 四档 + L1_AUTONOMY_DOMAINS
     = ("auto_patrol",) + AUTO_PATROL_EVERY=10 + 决策计数
     decisionSeq + note_decision_and_maybe_patrol() 节流巡检
  2) zyh_routes.py: _decision 装饰器 full 档决策后触发自主巡检
     (异常只告警留痕, 不阻断决策响应) + 切档描述四档化
  3) test_zyh.py: 67→77 项(新增 10 项 full 档矩阵)
  4) prod_zyh_gradation_verify.py: 三档→四档自适应 + full 专属实证
    ↓
本地验证: 77/77 全绿 + Ruff lint 零告警(仓库 lint-only 约定)
    ↓
部署: scp 两代码文件 → /opt/zhuxiang/zhuxiang-jiu/backend/
      提交 8aaad87 推送 origin/master
    ↓
切档: sed ZYH_MODE=assist→full /opt/zhuxiang/.env
      docker compose up -d --build backend(容器重建 healthy)
    ↓
生产验证: 容器内注入 e2e_auth_helper + verify 脚本
          23/23 全绿(mode=full(env))
```

---

## 四、代码变更明细（4 文件 · +194/-21 · 提交 8aaad87）

### 4.1 services/zyh_mode_service.py —— 四档升格核心

| 变更点 | 位置 | 内容 |
|---|---|---|
| 模式域升格 | L65 | `MODE_VALUES = ("off", "shadow", "assist", "full")`——三态升四档封闭（`_valid_mode` 消费同一元组，非法值回落 off） |
| L1 自主域 | L68-70 | `L1_AUTONOMY_DOMAINS = ("auto_patrol",)`——封闭白名单，注释载明永不扩容铁律（知识条目/守门规则/resume/cache clear 永不入列） |
| 节流常量 | L72 | `AUTO_PATROL_EVERY = 10`——决策面每 N 次调用巡检一次 |
| 状态字段 | L135 | `_state()` 初始 dict 新增 `"decisionSeq": 0`——决策调用累计计数（存于 mode_state 单键 JSON，与 override/paused/metrics 同址） |
| 自主巡检方法 | L175-203 | 新增 `note_decision_and_maybe_patrol() -> dict \| None`（核心新增，见下） |
| 灰度总览公示 | L355-393 | `status_view()` 新增两个公示键（见下） |
| 模块 docstring | L1-30 | 三态改写为四档语义说明 + L1 自主域定义 + 永不自主铁律清单 |

**新增方法核心逻辑**（L175-203，节选）：

```python
async def note_decision_and_maybe_patrol(self) -> dict | None:
    state = await self.current_mode()
    if state["mode"] != "full":      # 前置档位检查:
        return None                  #   非 full 档零副作用(不计数)
    st = await self._state()
    seq = int(st.get("decisionSeq") or 0) + 1   # 决策计数 +1
    st["decisionSeq"] = seq
    await self._save_state(st)
    if seq % AUTO_PATROL_EVERY != 0: # 节流: 非 10 倍数不巡检
        return None
    result = await self.patrol()     # 复用既有护栏巡检
    result["autoPatrol"] = True      # 自主巡检留痕标记
    result["decisionSeq"] = seq
    logger.info("zyh_auto_patrol seq=%s breaches=%s", ...)
    return result
```

设计要点：
1. **档位前置**——非 full 档在计数前即返回，assist/shadow 期 decisionSeq 不增长（人工巡检范式不受污染）
2. **巡检零新造**——`patrol()` 为既有护栏链（`aggregate_guard_metrics()` 确定性聚合 guard/cache 统计 → 三指标 → 恶化 >3% 自动 guard_pause），自主巡检不新增任何判定路径
3. **小样本保护继承**——分母 <10 指标留痕不判恶化（护栏既有行为，自主触发同样受保护）
4. **恶化即熔断**——自主巡检发现恶化仍走自动暂停（等效 off），恢复须人工 resume（永不自主铁律）

**status_view() 新增公示键**（L355-393）：

| 键 | 内容 |
|---|---|
| `fullAutonomy` | `{"domains": ["auto_patrol"], "autoPatrolEvery": 10, "decisionSeq": <n>, "note": "full 档低风险自主域(封闭白名单); assist 期巡检须人工 POST /mode/guard"}` |
| `neverAutonomous` | 四条红线文案：knowledge upsert(单一事实源)/守门规则变更/护栏 resume(人工留痕)/cache clear(管理面)——full 档亦永不自主 |

### 4.2 routes/zyh_routes.py —— 决策装饰器接入

| 变更点 | 位置 | 内容 |
|---|---|---|
| `_decision` 装饰器 | L72-109 | full 档决策响应后触发 `note_decision_and_maybe_patrol()`（L99-107） |
| 巡检异常隔离 | L101-107 | `except Exception: logger.warning("zyh_auto_patrol_failed", exc_info=True)`——巡检异常只告警留痕，**不阻断决策响应**（自主域故障不伤决策面可用性） |
| `ModeOverrideRequest` | L137-141 | 描述更新 `off/shadow/assist` → `off/shadow/assist/full`（运行时切档接口同步支持 full） |
| 端点区块标题 | L68-70 | "三态灰度" → "四档灰度(全站范式·73/74 同源)" |
| 文件头 docstring | L4 | 融合优化描述同步四档 |

**装饰器执行序**（语义时点，L85-108）：

```
1. require_decision_mode()   → off 409 门控(鉴权 401/403 由
                                JWT 中间件先行——铁律不变)
2. await fn(*args, **kwargs) → 业务执行
3. zyhMode 留痕注入          → shadow/assist/full 三档均注入
4. full 档专属               → note_decision_and_maybe_patrol()
                                (try/except 包裹, 只告警)
```

### 4.3 test_zyh.py —— 67 → 77 项

`test_mode()` 新增 8 项（L275-318）：

| 断言项 | 验证内容 |
|---|---|
| 四档封闭 | `MODE_VALUES == ("off", "shadow", "assist", "full")` |
| full 放行 | `require_decision_mode()` 返回 mode=full |
| assist 档不自主巡检 | `note_decision_and_maybe_patrol()` 返回 None（零副作用） |
| 节流前 9 次不巡检 | 连续 9 次调用全返回 None |
| 第 10 次自主巡检 | 返回 `autoPatrol=True` 且 `guard.checkCount` 恰好 +1（对照巡检前后快照） |
| 自主域白名单封闭 | `L1_AUTONOMY_DOMAINS == {"auto_patrol"}` |
| 永不自主红线公示 | `"resume" in neverAutonomous` |
| 非法档拒绝 | `set_override("super")` → ValueError |

`test_http()` 更新 1 项 + 新增 2 项：

| 断言项 | 验证内容 |
|---|---|
| HTTP mode(四档公示)（更新） | modeValues 四值 + `fullAutonomy.domains` 含 auto_patrol |
| HTTP chat full 放行（新增） | env=full 下 chat 200 + `zyhMode=full` 留痕 |
| HTTP full 自主巡检计数留痕（新增） | 10 次决策调用后 `decisionSeq >= 10` |

### 4.4 prod_zyh_gradation_verify.py —— 四档自适应 + full 矩阵

| 变更点 | 位置 | 内容 |
|---|---|---|
| 灰度态断言 | L56-58 | 合法档位集合扩为 `("off", "shadow", "assist", "full")` |
| 观测面 mode 断言 | L75-77 | `modeValues == ["off", "shadow", "assist", "full"]` |
| **full 专属验证块** | L172-198 | `if mode == "full":` 分支——三项新增（见"五、验证矩阵"加粗行） |
| 零破坏设计 | L182-192 | auto_patrol 实证用同 query 重复调用（语义缓存命中）——**零知识写入、零守门计数污染**，checkCount 快照对照 before/after |

full 专属块实证逻辑：记录 `guard.checkCount` 快照 → 10 次同 query chat（决策计数 +10，必跨节流边界触发至少一次自主巡检）→ 复查 checkCount ≥ before+1。

### 4.5 兼容性与影响面

| 面 | 影响 |
|---|---|
| 既有三档行为 | 零变化——off 409 / shadow 留痕 / assist 决策生效语义原样（`MODE_VALUES[1:]` 留痕判断天然含 full） |
| 护栏/守门/缓存 | 零改动——`patrol()`/`guard_check()`/守门三层/知识内核未触碰 |
| 存储结构 | mode_state 单键 JSON 向后兼容——旧 state 无 decisionSeq 键时 `int(st.get("decisionSeq") or 0)` 缺省 0，无需迁移 |
| 前端/其他模块 | 零耦合——75号观测面响应为超集扩展（新增键），无破坏性字段变更 |

---

## 五、full 期语义验证矩阵（生产 23/23）

| 验证面 | 端点/方法 | 结果 |
|---|---|---|
| 健康探针 | GET /api/decision/health | 200 ✓ |
| 灰度态 | GET /api/zyh/mode | **mode=full(env)** ✓ |
| 观测面 8 GET | knowledge/graph/rules/stats/cache/qa/单条/mode | 全 200（公开白名单，永不关停）✓ |
| 四档公示 | GET /api/zyh/mode | modeValues=[off,shadow,assist,**full**] ✓ |
| 决策面放行 | POST /api/zyh/chat | 200 + **zyhMode=full 留痕** ✓ |
| 守门 L1 | 旧工艺表述 | 拦截（guardLayer=1）✓ |
| 守门 L2 | 医疗断言 | 拦截 ✓ |
| 实体消歧 | 竹筒酒竞品维 | resolvedCompetitor+resolvedAroma ✓ |
| 语义缓存 | 同 query 二次请求 | cacheHit=True ✓ |
| 韧性推演 | material_moisture 量化 | "+12%" ✓ |
| 辩题生成 | probe/debate | total=2 ✓ |
| **full 自主域公示** | GET /api/zyh/mode | **fullAutonomy.domains=[auto_patrol] · autoPatrolEvery=10** ✓ |
| **永不自主红线公示** | GET /api/zyh/mode | neverAutonomous 含 resume/知识/守门/cache clear ✓ |
| **auto_patrol 实证** | 10 次决策调用 | **checkCount 自增（10 次决策必跨节流边界，全缓存命中零知识写入）** ✓ |
| 护栏巡检 | POST /api/zyh/mode/guard（只读） | 200 · 无恶化 · 无暂停 ✓ |
| 管理面边界 | 无 JWT 调 mode/override | 401（鉴权优先于门控）✓ |
| 统计健康 | GET /api/zyh/stats | totalRequests 有计数 ✓ |

---

## 六、L1 自主域语义（自主域边界）

| 域 | 自主语义 | 风险级 |
|---|---|---|
| auto_patrol | 护栏自主巡检：决策面每 10 次调用节流触发一次 patrol（确定性聚合+阈值比较，LLM 禁入），替代人工定期巡检 | 低（观测+熔断方向） |

**永不自主铁律**（full 档亦人工）：

| 动作 | 理由 |
|---|---|
| 知识条目 upsert | 单一事实源——专利 ZZ26SW1489303A/企标 ZZ26SW1489404B 人工锚定 |
| 守门规则变更（L1/L2/L3） | 工艺宪法防线，规则域永远显式 |
| 护栏恢复 resume | 人工决策留痕——自动暂停的解除不可自动化 |
| 缓存清空 cache/clear | 管理面 X-Role 权限域 |

**配套保险（不变项）**：护栏三指标（工艺误述率 0.10 / 等同混淆率 0.10 / 溯源缺失率 0.05）恶化 >3% 自动 guard_pause（等效 off）· 小样本保护（分母 <10 指标留痕不判恶化）· 恢复须人工 resume · 观测面（知识内核事实锚点）永不关停。

---

## 七、运营机制

**full 期巡检节奏**：

1. **自主（新增）**：决策流量每 10 次自动触发护栏巡检（`zyh_auto_patrol` 日志留痕：seq/breaches），生产无需人工定期调 guard
2. **人工兜底**：`POST /api/zyh/mode/guard`（admin）随时可手动巡检——与自主巡检并存不冲突
3. **持续观测**：`GET /api/zyh/stats` 守门拦截分布 + `GET /api/zyh/mode` 的 guard.checkCount（巡检留痕计数，上限 50 条滚动）
4. **暂停响应**：若 auto_patrol 发现恶化即自动 guard_pause（决策面 409），按 pausedReason 排查后 `POST /api/zyh/mode/resume` 人工恢复

**回滚路径**：

```bash
# 回 off(紧急静默——决策面关闭, 观测面保留):
sed -i 's/^ZYH_MODE=.*/ZYH_MODE=off/' /opt/zhuxiang/.env
cd /opt/zhuxiang && docker compose up -d backend

# 回 assist(推荐——保留决策生效, 收回自主巡检):
sed -i 's/^ZYH_MODE=.*/ZYH_MODE=assist/' /opt/zhuxiang/.env
cd /opt/zhuxiang && docker compose up -d backend

# 免重建快速切档(运行时 override, 留痕):
curl -X POST https://zxjiu.com/api/zyh/mode/override \
  -H "Authorization: Bearer <admin JWT>" -H "X-Role: admin" \
  -H "Content-Type: application/json" -d '{"mode":"assist"}'
```

---

## 八、转段终态

| 项 | 终态 |
|---|---|
| 灰度档位 | **full（env 直配，无 override）** |
| 决策面 | chat / stress-test / probe debate 放行 + zyhMode=full 留痕 |
| 自主域 | auto_patrol 节流巡检运行（每 10 次决策 1 巡） |
| 红线 | LLM 禁入守门与护栏（全确定性）· 守门三层随知识查询常开 |
| 评分器 | zhuyun_cognition（batch49）在册 46号学习总线 |
| 全站位次 | 十六模型 full 档第三位（73/74/75），其余 assist 运行 |

**结论**：75号竹韵·智衡 full 转段完成，四档灰度范式（off/shadow/assist/full）与 73/74 全站对齐；自主域严格收敛于低风险巡检动作（auto_patrol），知识内核/守门规则/护栏恢复四条永不自主红线全部保留人工显式性；本地 77/77 + 生产 23/23 双全绿验证通过，模块转入 full 档自主生产期。
