# 81号 HRM·EMA 开启前状态检查清单

> 2026-10-01 编制 | 适用: `HRM81_EMA` off→on 放量决策
> 惯例对齐: 79/80号 E2/E3 放量指南(前提→检查→操作→验证→回退→观察)
> 代码依据: hrm81_service.py P2 §二(采样/基线/只紧不松) + §7.4 实录

## 〇、当前基线状态(2026-10-01 编制时点)

- EMA 三层已上线, kill switch `HRM81_EMA=off`(采样自动积累中)
- 采样起步: 2026-10-01 16:30(UTC 08:30)首条——**预计 2026-10-08 起
  各小时桶陆续满足 7 日门槛(valid)**
- `HRM81_MODE=shadow`(闸门恒放行只留痕)——EMA 开启在 shadow 下
  **零拦截风险**(只改判定线, 不改放行语义)

## 一、开启前提(三项全满足)

| # | 前提 | 判据 |
|---|---|---|
| 1 | 数据积累 ≥7 天 | 24 个小时桶基线全部 `valid=true`(桶内样本覆盖 ≥7 个不同日) |
| 2 | 双记观察通过 | 近 7 天 decisions 留痕 `emaWould` 评估结论明确(见 §二-4 判据) |
| 3 | shadow 期零误伤 | decisions 留痕无异常(水位全 green 或 amber 有因可查) |

## 二、逐项检查(六项, 每项含命令/期望/异常处置)

检查执行方式(容器内 python, 规避 shell 引号剥离):

```powershell
# 逐项检查脚本模板: 写入本地 chk.py 后管道执行
Get-Content .\chk.py -Raw | ssh root@47.236.61.117 "docker exec -i zhuxiang-backend-1 python -"
```

### 1. 采样数量与连续性

```python
import asyncio
from services.hrm81_service import list_water_samples

async def main():
    s = await list_water_samples()
    days = {str(x["ts"])[:10] for x in s}
    print("samples:", len(s), "days:", len(days), sorted(days))

asyncio.run(main())
```

- **期望**: samples ≈ 24×积累天数(±, 重启/停摆可缺), days ≥ 7
- **异常处置**: 缺整天(重启致决策轮停摆)→ 补等 1-2 天再查;
  samples 为 0 → 查 `docker logs zhuxiang-backend-1 | grep hrm81`

### 2. 24 小时桶基线覆盖率(核心前提)

```python
import asyncio
from services.hrm81_service import _build_ema_baselines, list_water_samples

async def main():
    b = _build_ema_baselines(await list_water_samples())
    invalid = {h: v for h, v in b.items() if not v["valid"]}
    print("buckets:", len(b), "invalid:", invalid)

asyncio.run(main())
```

- **期望**: buckets=24 且 invalid 为空(全 valid)
- **异常处置**: 个别桶 invalid(该时段恰逢长期重启)→ 可接受先开,
  该时段走静态轨(冷启动兜底设计); 大面积 invalid → 继续积累

### 3. 基线值合理性(防异常样本污染)

```python
import asyncio
from services.hrm81_service import _build_ema_baselines, list_water_samples

async def main():
    b = _build_ema_baselines(await list_water_samples())
    for h in sorted(b):
        print(h, b[h]["memEMA"], "MB  load", b[h]["loadEMA"])

asyncio.run(main())
```

- **期望**: 各桶 memEMA 在 300-900MB 量级(与该机常态一致);
  动态 amber 线(EMA×0.65)落在 200-585MB 区间
- **异常处置**: 某桶 memEMA 异常低(<300, 如演练压力期样本混入)
  → 检查该桶样本; 必要时清样本重来:
  `docker exec zhuxiang-backend-1 python -c "..."`(del Redis key
  `zhuxiang:hrm81:water:samples` 后重新积累 7 日)

### 4. emaWould 双记回看(开启决策的核心依据)

```python
import asyncio
from services.hrm81_service import decision_history

async def main():
    rows = await decision_history(50)
    would = [r for r in rows if r.get("emaWould")]
    print("rounds:", len(rows), "emaWould:", len(would))
    for r in would[:10]:
        print(r["decidedAt"][:16], r["level"], "->", r["emaWould"])

asyncio.run(main())
```

- **判据**:
  - `emaWould` 占比 <5% 且无 red 级 → **安全开启**(动态轨很少收紧)
  - 占比 5-30% amber → **可开启**(批任务偶发错峰符合设计意图),
    开启后重点观察 §四-3
  - 占比 >30% 或出现 red → **暂缓**: 动态线贴近日常水位——先核查
    基线(§二-3)是否被拉低, 或评估调 ratio(改 hrm81_service 常量
    MEM_EMA_AMBER_RATIO 0.65→0.55 后重新观察)
- **说明**: emaWould 只在 EMA off 且基线有效时记录——它就是
  "开了会怎样"的预演数据, 无需推测

### 5. 环境与调度器健康

```python
import asyncio, os
from services import hrm81_service as hrm
from services.hrm81_scheduler import scheduler_running

async def main():
    print("MODE:", os.environ.get("HRM81_MODE"),
          "EMA:", os.environ.get("HRM81_EMA"),
          "AUTO:", os.environ.get("HRM81_AUTO"))
    print("scheduler_running:", scheduler_running())
    rows = await hrm.decision_history(3)
    print("latest_decision:", rows[0]["decidedAt"][:19] if rows else "-")

asyncio.run(main())
```

- **期望**: `MODE=shadow EMA=off AUTO=on`; scheduler_running=True;
  最新留痕时间距现在 <10 分钟(300s 周期)
- **异常处置**: 留痕停摆 → `docker logs` 查 hrm81_scheduler 异常

### 6. 回退路径演练就绪(开启前确认, 不实际执行)

- EMA 级回退: `.env` `HRM81_EMA=on`→`off` + `docker compose up -d
  backend`——**基线样本数据保留**(kill switch 只停判定, 采样照常)
- 闸门级回退: `HRM81_MODE=shadow`→`off`(或等熔断: red 连续 2 轮
  自动回 shadow+P0 告警, 内建无需人工)

## 三、开启操作(两步, 前提全满足后)

```bash
# 生产 /opt/zhuxiang/.env
sed -i 's/^HRM81_EMA=off/HRM81_EMA=on/' /opt/zhuxiang/.env
grep '^HRM81_' /opt/zhuxiang/.env   # 确认 MODE=shadow EMA=on AUTO=on
cd /opt/zhuxiang && docker compose up -d backend
```

> 推荐节奏: 先在 **shadow 下开 EMA**(零拦截风险, 观察判定线变化
> 1-3 天)→ 确认 tracks/level 合理 → 再评估 HRM81_MODE=on(闸门
> 真实拦截届时才受动态线影响)。两开关解耦, 逐级放量。

## 四、开启后验证(四项)

| # | 验证 | 命令要点 | 期望 |
|---|---|---|---|
| 1 | 开关生效 | §二-5 脚本 | `EMA: on` |
| 2 | status 观测面 | `ema.enabled=true`, samples 持续增长 | GET /api/hrm81/status(admin) |
| 3 | tracks 变化 | decisions 新留痕 `tracks` | 若水位低于动态线出现 `"mem":"ema"`; 全 static 也正常(水位高于动态线) |
| 4 | emaWould 消失 | decisions 新留痕 | 无 `emaWould` 字段(on 后即实际判定, 无需预演) |

## 五、应急速查

| 症状 | 处置 |
|---|---|
| 动态线误收紧(批任务频繁 deferred 且水位实属正常) | `.env` EMA→off 重启(§二-6); 留痕核查该时段基线 |
| 某时段基线异常(演练/迁移样本污染) | 清样本 key 重新积累(§二-3); 该时段临时走静态 |
| red 连续 2 轮 | 熔断自动回 shadow+P0 告警(内建), 人工核查主机资源 |
| 全量紧急回退 | `.env` MODE→off + EMA→off 一次重启 |

## 六、观察记录表(开启前每日一行)

| 日期 | samples | 桶 valid | emaWould 占比 | 异常 | 结论 |
|---|---|---|---|---|---|
| 10-01 | 1 | 0/24 | - | - | 采样起步 |
| ... | | | | | |

---

*关联文档: 81号 P2 开发计划(§7.4 EMA 实录/§7.5 Tier3 收官) ·
81号 P1 方案(§六) · E2/E3 放量指南(同惯例范式)*
