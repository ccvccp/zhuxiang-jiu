# 74号 NexusFlow 发布工作台运行检查与升级交付报告

> 检查日期：2026-10-04 | 第十一次检查范式（观测面补齐型）
> 检查对象：NexusFlow 智枢流·AI智能全域发布大模型（发布工作台）
> 关联模块：36号 promo 发布中心（RPA 通道联动）、40号 blogger 发布调度线

---

## 一、总体判定

**发布引擎本体健康（设计红线全部在位），但运营面停摆 3 周 + 观测面为零**——
属于「引擎完好、无人驾驶、且无仪表盘」形态。本轮补齐观测面（日度巡检），
运营面恢复列拍板项。

## 二、运行取证（生产实测 2026-10-04 08:50-09:20）

### 2.1 正常项

| 项 | 证据 |
|---|---|
| 模式档位 | `NEXUSFLOW74_MODE=full`（生产 .env） |
| 前端工作台 | nexus74 分包 chunk 8857 线上 200（24KB） |
| 数据层 | 14 表结构完好；repo 直调 0.3s（get_silence） |
| 静默窗 | 0-6+23 点配置在（2026-09-13 设置） |
| 发布状态机 | 六态数据诚实：pub35 微信 A 档 auth_expired 归因正确（不伪造成功） |
| 唯一真实发布 | pub 26 知乎 B 档 assist，回执 published（2026-09-13） |
| 复盘链 | retrospects:1 关联 pub 26（verdict effective，互动率 0.3） |
| 关联线 | 36号 PROMO_PUBLISH/RADAR_AUTO=on（daily 键活跃至 10-03）；40号 blogger 五调度全 on（work=1927 运行中） |

### 2.2 问题清单

| # | 问题 | 定性 |
|---|---|---|
| 1 | **供给断绝**：sources/publications 停在 2026-09-13 验收日，此后 21 天零增长 | 运营停摆（无素材投喂） |
| 2 | **观测面为零**：无任何巡检/健康调度，停摆 3 周无人知晓——本次检查才发现 | 本轮修复 ✓ |
| 3 | **A 档微信凭证未配置**：WECHAT_MP_APPID 空，full 档唯一自主发布通道 no_credentials | 待配置（需公众号管理员授权） |
| 4 | metrics/audits/learnings 零积累 | 根因同 #1（依赖发布量） |
| 5 | 32 条验收期 publications 已被人工清理（seq=36 存量 4） | 验收清理，非缺陷 |
| 6 | 重启后预热期 API 短暂阻塞（观测端点 20s+ 超时，恢复型） | 全站性隐患记录：启动即首轮多调度器 + LLM 同步调用阻塞事件循环（语音故障同源） |

### 2.3 检查中撤销的误判

- ~~transfer 脚本引用不存在的 /model/status~~——端点存在（P5 完整版，
  routes 1231 行；初查 grep 被 head_limit 截断致误判）
- ~~十四表全零~~——键模式含表空间前缀 `zhuxiang:nexus74:{table}:{id}`
  （attract72 冒号键教训二次规避成功）

## 三、修复升级交付

### 3.1 新增日度巡检调度器（观测面补齐）

- **`services/nexus74_scheduler.py`**（新建）：
  - 单轮巡检：11 表分布 / 发布六态分布 / **awaiting_manual 回执积压（>48h）** /
    A 档适配器健康摘要 / 模式一致性 / **供给新鲜度**（最新 source/publication 距今天数）
  - 范式平移（pocket/zy/zd/zk/qr70/trust45）：`NEXUS74_SCAN_AUTO` 默认 off、
    interval 下限 300s、**启动即首轮**、fail-soft、留痕 `zhuxiang:nexus74:daily_scan:last`
- **`main.py`**：启动挂载（调度器第 51 个）
- **`routes/nexus74_routes.py`**：新增 `GET /api/nexus74/admin/overview`
  （refresh=1 现场重扫 / 默认读缓存）
- **`.env`**：`NEXUS74_SCAN_AUTO=on`（与全站巡检线一致）
- 铁律不越：纯观测面，不改发布显式性/数据诚实/人工回执任何规则

### 3.2 存量测试自举修复（Bearer 迁移遗留）

- `test_nexus74_p1~p6.py` 六文件补 `AUTH_COMPAT_TRUST_HEADERS` setdefault
  （flashsale 范式，消除对 shell 前置依赖）

### 3.3 测试

- 新增 `test_nexus74_sched.py`：**16/16 全过**（开关/周期/生命周期 8 例 +
  真实数据巡检 8 例：表分布/六态/积压检出/mode/新鲜度/适配器健康/缓存/空库）
- 回归：`test_nexus74_p3.py` 48/48、`test_nexus74_p5.py` 39/39

### 3.4 生产部署验证（09:17）

```
nexus74_scan_scheduler started interval=86400s
nexus74_scan mode=full pubs=4
  status={'shadowed':1,'published':1,'failed':1,'awaiting_manual':1}
  backlog=1 source_age=20 pub_age=20
  adapters={wechat_mp:no_credentials, 五平台B档:ready}
```

- 启动即首轮 ✓（巡检值与手动取证完全吻合）
- 留痕键落库 974B ✓
- `/api/nexus74/admin/overview` 路由注册（openapi 确认）✓

## 四、遗留拍板项（不动代码，待决策）

| 项 | 说明 | 建议 |
|---|---|---|
| A 档微信凭证 | WECHAT_MP_APPID/SECRET 未配置，full 档自主通道空转 | 需公众号管理员授权后配置（healthz 即转 ready） |
| 供给恢复 | sources 投喂入口在工作台「素材合规」页签 | 运营动作：投喂素材 → P2 适配 → P3 发布 |
| 发布自动化 | 当前 API 驱动 + B 档人工回执（设计铁律） | 若需自动投喂可评估与 36号 promo 内容线打通（跨模块拍板） |
| 预热期阻塞 | 重启后多调度器并发 + LLM 同步调用拖慢 API 1-2 分钟 | 全站性优化项（LLM 调用线程池化），独立立项 |

## 五、结论

发布工作台引擎层验收质量高（数据诚实铁律实测有效），本轮补齐的日度巡检
使其具备「停摆即暴露」的自检能力；下一步价值释放取决于运营面（素材供给 +
A 档凭证），已列入拍板项。
