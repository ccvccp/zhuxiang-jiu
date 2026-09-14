# 37号·同盟大模型 shadow 短窗回放验证报告

> 验证时间：2026-09-15 04:52-04:53（UTC+8） | 环境：生产 zxjiu.com（47.236.61.117）
> 验证目的：shadow 短窗回放——验证运行时切档机制、决策面放行语义、观测面常开铁律、留痕链路完整性（治理层升级建议项之二，机制验证口径提前执行；数据积累期的长期 shadow 观察仍按 1-2 周节奏另行安排）

## 一、执行链

| # | 动作 | 结果 |
|---|------|------|
| 1 | 基线采集 `GET /mode` | mode=assist, source=env, paused=False, checkCount=2 |
| 2 | 切档 `POST /mode/override?mode=shadow` | mode=**shadow**, source=runtime_override, envMode=assist |
| 3 | 决策面探测 `POST /apply`（测试会员） | 放行——返回业务错误「会员不存在」而非模式拒绝（shadow=决策放行语义与 assist 一致，差异仅留痕） |
| 4 | 观测面探测 `GET /merchants` / `GET /whitepaper` | 双 200（商户 8 家 / 白皮书四章节）——观测面常开铁律不受切档影响 |
| 5 | 护栏实测 `POST /mode/guard/auto` | checkCount 2→3, breached=False, pausedNow=False, 三指标 0（样本门生效） |
| 6 | 容器日志核验（SSH） | `04:52:15 alliance37_override=shadow by=admin` 留痕在案 |
| 7 | 回档 `POST /mode/override?mode=`（空串清除） | mode=assist, source=env（回落环境变量） |
| 8 | 终态核验 `GET /mode` | mode=assist, source=env, paused=False, checkCount=3 |
| 9 | 清除日志核验 | `04:52:48 alliance37_override=清除 by=admin` 留痕在案 |

## 二、验证结论

| 验证项 | 结论 |
|--------|------|
| 运行时切档（免容器重建） | ✅ shadow 往返 33 秒完成，双操作均日志留痕 |
| 决策面 shadow 语义 | ✅ 放行（业务校验正常执行，非模式拒绝） |
| 观测面常开 | ✅ 商户/白皮书 200 不受切档影响 |
| 护栏留痕链路 | ✅ checkCount 递增、指标留痕、无恶化不暂停 |
| 状态回退完整性 | ✅ override 清除后回落 env 来源，终态与基线一致（assist/env/未暂停） |
| 审计链 | ✅ 切档/清除双操作时间戳+操作者在容器日志可查；决策时刻可通过 mode 状态史与日志时间轴关联溯源 |

## 三、审计口径说明

shadow 期决策的溯源方式：容器日志中 override 切档/清除的时间戳构成 shadow 窗口边界（本轮回放窗口 04:52:15 → 04:52:48），该窗口内的决策面调用即 shadow 期决策，可与业务日志按时间轴关联审计。

## 四、遗留与建议

- 长期 shadow 观察窗口（1-2 周节奏）按原建议另行安排，当前 assist 档继续运行
- 订单/评价数据积累过样本门（结算≥5/评价≥5）后，护栏实测三指标将自动生效
- 白皮书年度终稿发布为 admin 人工审批动作（AI 仅展示，发布责任人工）
