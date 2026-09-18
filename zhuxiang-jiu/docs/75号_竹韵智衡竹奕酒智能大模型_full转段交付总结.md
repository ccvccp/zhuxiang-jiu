# 75号·竹韵·智衡竹奕酒智能大模型 full 转段交付总结

> 文档版本：v1.0 · 2026-09-18
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

## 四、full 期语义验证矩阵（生产 23/23）

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

## 五、L1 自主域语义（自主域边界）

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

## 六、运营机制

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

## 七、转段终态

| 项 | 终态 |
|---|---|
| 灰度档位 | **full（env 直配，无 override）** |
| 决策面 | chat / stress-test / probe debate 放行 + zyhMode=full 留痕 |
| 自主域 | auto_patrol 节流巡检运行（每 10 次决策 1 巡） |
| 红线 | LLM 禁入守门与护栏（全确定性）· 守门三层随知识查询常开 |
| 评分器 | zhuyun_cognition（batch49）在册 46号学习总线 |
| 全站位次 | 十六模型 full 档第三位（73/74/75），其余 assist 运行 |

**结论**：75号竹韵·智衡 full 转段完成，四档灰度范式（off/shadow/assist/full）与 73/74 全站对齐；自主域严格收敛于低风险巡检动作（auto_patrol），知识内核/守门规则/护栏恢复四条永不自主红线全部保留人工显式性；本地 77/77 + 生产 23/23 双全绿验证通过，模块转入 full 档自主生产期。
