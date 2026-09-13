# 74号·NexusFlow（智枢·流）AI智能全域发布大模型 full 转段交付总结

> 文档版本：v1.0 · 2026-09-13
> 转段动作：NEXUSFLOW74_MODE assist → full（A 档低风险自主域开放）
> 前置文档：《74号_NexusFlow智枢流_AI智能全域发布大模型_全链交付总结.md》（P1-P6 六期交付）
> 转段脚本：backend/scripts/nexus74_transfer.sh（status/shadow/assist/full/off/verify）

---

## 一、转段总览

| 维度 | 数据 |
|---|---|
| 转段时间 | 2026-09-13T12:26:24Z（UTC） |
| 转段方向 | assist → full（四档最高位） |
| 开放域 | **仅 A 档（微信公众号）低风险自主**——auto 发布直达适配器（compliance pass 前提） |
| 保留域 | **B 档五平台永远人工**（awaiting_manual+回执登记——平台操作显式性铁律，full 亦然） |
| 前置核验 | 红队四向量全防御（runId=4，**第三次生产红队**）+ 免疫 active + B 档铁律实证 |
| 生产状态 | zxjiu.com 公网 mode=full · 容器 healthy |
| 全站意义 | 与 73号 并列双 full 档——治理阶梯全站齐顶 |

---

## 二、full 档语义边界（规划 §七 四档定义）

| 域 | full 档语义 | 实证 |
|---|---|---|
| **A 档自主** | auto=True → autoPublished=True（门控放行）→ 直达适配器 | ✓ |
| **A 档诚实归因** | 无凭证 → failed(auth_expired) + tierAdvice + retriable=False——**不伪造成功** | ✓ |
| **B 档铁律** | auto=True → 仍 awaiting_manual + autoPublished=False（**full 不含 B 档自主**） | ✓ |
| 合规前置 | review_required 内容不可自主（compliance pass 才可 auto——P3 期断言，full 期沿用） | 设计继承 |
| 观测面 | 规则库/人格/矩阵/数据汇总/复盘/漂移/免疫常开（不受 MODE） | ✓ |

---

## 三、转段资格裁定（全站横向审计）

| 模型 | 模式域 | assist 后可转 | 裁定 |
|---|---|---|---|
| 68号 信值 | off/shadow/assist（三态） | 无 full 档 | assist 即终态 |
| 71号 支付端口 | off/shadow/assist/full | full=低风险参数自主 | **L1 评估模板要求 ≥28 天 assist + 八项前置**——未达，审慎驻留 |
| 73号 会员体验 | off/shadow/assist/full | full=L1 触达域自主 | 已 full（2026-09-13T11:55Z） |
| **74号 NexusFlow** | **off/shadow/assist/full** | **full=仅 A 档自主** | **本轮执行** |

---

## 四、转段执行链

```
前置核验③: 第三次生产红队
  RT-01 红线穿透   → defended ✓(合规前置拦截)
  RT-02 频次轰炸   → defended ✓(封顶熔断+伪造清理)
  RT-03 越权直发   → defended ✓(assist auto 拒绝
                     + full B 档仍人工)
  RT-04 回执伪造   → defended ✓(A 档+重复双拒绝)
  runId=4 allDefended=True → 放行
    ↓
转段脚本: bash nexus74_transfer.sh full
  前置核验四项(输出留档):
    1) assist 稳定期(治理注记: 按人工指令执行)
    2) B 档回执数据健康(rejected/throttled 占比)
    3) 红队四向量全防御(已执行 runId=4)
    4) B 档确认: full 不含 B 档自主
  免疫守卫: active ✓
    ↓
.env: NEXUSFLOW74_MODE=full → 容器重建(healthy)
    ↓
三层自验: 容器 env=full · 本地 health 200
          · 公网 mode=full ✓
```

---

## 五、full 期语义验证矩阵

| 验证面 | 方法 | 结果 |
|---|---|---|
| 模式公示 | GET /model/status | mode=full · kill=false · 免疫 active · wechat_mp=A 档 |
| **A 档自主开放** | 全链造数（合规 pass 源→微信适配→auto 发布） | `autoPublished=True`（assist 期 409 → full 期放行） |
| **诚实归因** | 同上（无凭证） | `failed · auth_expired · configure_credentials_or_switch_B · retriable=False` |
| **B 档铁律** | 小红书 auto=True 发布 | `awaiting_manual · autoPublished=False` |
| 零影响 | 71/68/73号+前端 | 71 assist · 73 full · 68 assist(paused=False) · 前端 200 |

---

## 六、A 档自主生效路径（凭证就绪三步）

1. **配置凭证**：`.env` 写入 `WECHAT_MP_APPID`/`WECHAT_MP_SECRET` 等 → `GET /healthz` 转 `ready`
2. **DRYRUN 演练**（可选）：`NEXUSFLOW74_WECHAT_DRYRUN=1` → 适配器返回 `wx-dryrun-N` 模拟成功（验证全链不落真实平台）
3. **生产直发**：auto=True 发布 → 微信素材+群发 API 真实执行 → published + externalId

**凭证未配置期间**：A 档 auto 自主=门控放行+诚实归因 failed（不伪造成功不空转——等待凭证的诚实驻留态）。

---

## 七、运营机制

**full 期巡检节奏**：
1. 每日：`POST /meta/drift`（三信号应空——publish_anomaly≥30/rejection_anomaly>0.3/review_anomaly>0.4）
2. 每周：`POST /redteam`（本日已建立 runId=1/2/4 三批次基线，四向量持续全防御）
3. 持续：`GET /healthz`（A 档凭证状态）+ `GET /metrics/summary`（B 档回执健康）+ `GET /quota/status`（封顶/静默窗）

**回滚路径**：
```bash
# 回 assist(推荐——B 档流程不变, A 档自主收回):
sed -i 's/^NEXUSFLOW74_MODE=.*/NEXUSFLOW74_MODE=assist/' /opt/zhuxiang/.env
cd /opt/zhuxiang && docker compose up -d backend
# 或一键 off(保险件恢复原件):
bash /opt/zhuxiang/zhuxiang-jiu/backend/scripts/nexus74_transfer.sh off
```

**转段履历**：off（09-13 10:44 部署）→ shadow（10:49）→ assist（11:30）→ **full（12:26）**——四档全走通，各档语义均生产实证。

---

## 八、全站模型终态（双 full 纪元）

| 模型 | 模式 | 阶梯位 | 自主域 |
|---|---|---|---|
| **73号 会员体验** | **full** | L1 白名单 | hint_render/silence_rule/form_ranking |
| **74号 NexusFlow** | **full** | A 档自主 | 微信直连 auto（待凭证生效） |
| 68号 信值 | assist | 三态终态 | —（护栏在线） |
| 71号 支付端口 | assist | L1 窗口期 | —（≥28 天评估） |

**治理体系终态**：观测面全域常开 · 双模型自主域开放（低风险触达 + A 档直连）· 人工确认位保留（B 档五平台 + 代办授权 + 46号审批链）· 免疫/护栏/红队三重保险在线——**确定性引擎承担 90% 策略执行，人工聚焦创意原点与平台操作确认**（源文档"人机协同"哲学的全站落地）。

---

## 九、后续观察项

1. **A 档凭证接入**：微信 APPID/SECRET 配置后 healthz→ready，A 档自主发布真实生效（DRYRUN 可先行演练）
2. **复盘数据积累**：B 档真实发布（人工平台操作→回执→指标）驱动 P6 单篇/分组复盘产出组合策略
3. **L1→L2 治理预留**：71号 L1 评估模板（≥28 天窗口）期满签核后，全站进入"三 full"治理纪元
4. **红队周检**：保持每周四向量复跑（三批次全防御基线已立）
