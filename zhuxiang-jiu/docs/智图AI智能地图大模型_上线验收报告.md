# 智图·AI智能地图大模型 上线验收报告

> 日期: 2026-09-16 | 版本: **终版** | 验收范围: 大模型二代转段全链交付(三态灰度+护栏+控制面+自动巡检+62号同步桥接)
> 验收基线: 智图一代 24 端点(P0 意图/P1 资源/P2 调度/P3 进化, test_zt_map_ai 54 断言)+基础位置地图 13 端点(58 断言)
> 验收结论: **通过**——生产 assist 档运行, 全部验收项达标, 必办遗留项清零

---

## 一、验收对象与提交链

| 提交 | 内容 | 状态 |
|---|---|---|
| ec4c10e | 大模型二代转段(模式层+路由门控+控制面+自动巡检+专项测试+存量适配+交付文档, 含护栏口径修正与月台视图修正) | ✅ 已推送 |
| fb60c21 | 上线验收报告(初版, 本次刷新为终版——补全文档链) | ✅ 已推送 |
| d91d424 | 位置地图管理模块上线验收报告(模块整体三层结构终态, 与本报告配套) | ✅ 已推送 |

> 关联文档: 本报告聚焦智图大模型层(28 端点)验收; 模块整体
> (基础 13 端点+大模型 28 端点+一代 20号决策门)终态验收见
> 《位置地图管理模块_上线验收报告》(d91d424)。

生产部署物: 后端 4 文件(services/zt_map_mode_service.py、
services/zt_map_scheduler.py、routes/zt_routes.py、
main.py)scp 至 `/opt/zhuxiang/zhuxiang-jiu/backend/` +
容器重建 + `.env` 设 `ZT_MODE=assist`。

## 二、功能验收清单

### 2.1 大模型核心(全站第十二大模型标准)

| 验收项 | 验收标准 | 结果 |
|---|---|---|
| 三态灰度 ZT_MODE | off(默认)/shadow(智图观察期)/assist(智图辅助期) | ✅ |
| 读取链 | 护栏暂停 > 运行时 override > env > 默认 off | ✅ |
| 状态存储 | 整档 JSON 单键 `zhuxiang:zt:mode_state`(45号 P0 教训规避; 与 _ZtStore 表键空间互不匹配) | ✅ |
| 同步桥接 | 进程快照 `_G` + `legacy_current_mode()`(62号范式) | ✅ |
| 护栏三指标 | 建议驳回率 0.10 / 调度误报率 0.10 / 月台饱和率 0.60, 恶化>3% 自动 guard_pause | ✅ |
| 决策面门控 | 11 端点 @/_decision(off 409 + ztMode 标记) | ✅ |
| 控制面 | 4 端点(mode/override/guard/resume) | ✅ |
| 护栏自动巡检 | ZT_GUARD_AUTO 默认 on, 间隔 3600s, 共享表+fabric 合并视图确定性聚合 | ✅ |
| 鉴权优先 | 参数化装饰器(小竹/钱包/信用范式第四次零改动复用), 403 优先于 409 | ✅ |

### 2.2 三面口径(28 端点)

| 面 | 端点 | 口径 |
|---|---|---|
| 决策面(11 POST) | intent/parse+search+behavior-hints / poi/register / supplier/dock-booking / b2b/warehouse-match / command/scan+tickets/ack / evolution/behavior+sandbox+feedback | off 409, shadow/assist 放行+ztMode 标记 |
| 观测面(13 GET) | ontology/roles/poi nearby+pois/dock-status/fulfillment-board/situation/tickets/cockpit/radar/behaviors/performance/feedbacks | 永不关停 |
| 控制面(4) | GET /mode、POST /mode/override、POST /mode/guard(缺省实时聚合)、POST /mode/resume | admin 门禁 |

### 2.3 宪法红线(智图域)

> **叠加式升级铁律**: 既有位置地图 13 端点(/api/location/*)零改动。

| 红线 | 口径 |
|---|---|
| 既有 location 13 端点 | mode 只挡智图 AI 决策面——实机实证 |
| 观测面 13 GET | 永不关停(全域查询/看板/雷达常开) |
| 建议书模式 | 月台分配/仓推荐/沙盘选址均为建议书制(人工确认生效)不受 mode 影响 |
| 一代 20号位置地图 AI 决策门 | delivery_point(ai_enforcement_longtail)独立 AI_ENFORCE_MODE, 不受 ZT_MODE 影响 |
| LLM 禁入 | 护栏=阈值比较, 全确定性 |

## 三、质量验收记录

| 测试套 | 结果 |
|---|---|
| test_zt_map_mode.py 专项 | **53/53** |
| test_zt_map_ai.py 存量(头部补 ZT_MODE=assist) | **54/54** |
| test_location_routes.py 存量(零改动) | **58/58** |
| **小计** | **165/165** |
| ruff lint | All checks passed |

### 3.1 专项断言分布(53 项)

三态语义 5 + 读取链 5 + override 1 + 护栏 7(基线不暂停/
三指标逐一恶化暂停/留痕/resume/未暂停拒绝) + 序列化往返 4 +
同步桥接 5(快照干净回落/override/暂停/清除) + 护栏巡检 6
(默认 on/开关可关/间隔可调/非法间隔回落/冷启动不误判/
**pending 草稿不误判(人工确认制口径铁律)**/误报率聚合恶化
暂停) + HTTP 决策面 off **11/11**(特征串法) + 鉴权优先 1
(admin 403) + 观测面 3 + shadow/assist 放行+标记 2 +
控制面 9(形状/admin 门禁/override/非法 409/清除回落/
未暂停 resume 409/guard 显式恶化暂停/resume 恢复) +
端点计数 2(≥28 map-ai + ≥12 location 下限断言——
PUT/DELETE 共享 address/{id} 路径)。

### 3.2 存量零破坏实证

- 智图 54 断言(复合意图/本体/六角色/POI/月台/多仓/态势/
  异常扫描/ack 闭环/驾驶舱/风险雷达/沙盘/绩效/反馈/
  宪法铁律)头部补 `ZT_MODE=assist` 后业务断言零改动全绿
- location 58 断言零改动全绿——13 端点不受门控影响
  (叠加式铁律实证)

## 四、生产实机验收(zxjiu.com)

### 4.1 三层自验

- **容器**: zhuxiang-backend-1 Up (healthy)
- **env**: ZT_MODE=assist
- **日志**: `zt_guard_loop_started interval=3600s` +
  首轮 `zt_guard_patrol_ok`(三指标 0)

### 4.2 实机七项(全过)

| # | 项 | 结果 |
|---|---|---|
| 1 | 公网 mode 总览 | 200 + assist(env 源) + guard.checkCount=1 + 三面口径完整公示 |
| 2 | 观测面 roles/situation/radar | 200 |
| 3 | 既有 location 13 端点 | 200(nearby 实测)——叠加式铁律实证 |
| 4 | 决策面放行 parse | 200 + 复合意图(吃饭买酒)解析 + **ztMode: "assist"** 标记 |
| 5 | 运行时切档 shadow | 200(免容器重建) → 清除回落 env assist |
| 6 | 护栏实时聚合 | 200 + 月台视图(docks:3/capacity:9/peakTrucks:0) + 三指标 0 不误暂停 |
| 7 | 测试数据零残留 | 9-11 上线验证残留(zt_tickets 8 条+zt_feedbacks 1 条+zt_dock_bookings 1 条)已清理, mode_state 冷启动重建 |

## 五、验收中发现并解决的问题(工程记录)

### 5.1 护栏口径修正实录(部署实证驱动——本模块核心工程洞察)

- **现象**: 首版三指标含「工单积压率(pending/总工单)」,
  生产部署后巡检首轮即 guard_pause(0.875: 9-11 智图上线
  验证残留 8 工单全 pending)
- **根因**(业务洞察): 智图工单为《处置预案草稿》人工
  确认制——**pending 是业务常态而非恶化信号**, 无人 ack 的
  草稿永远 pending, 原口径会「业务未使用→护栏自锁」
- **修正**: 三指标改为 建议驳回率/调度误报率(分母只含
  已处置 acked+dismissed)/月台饱和率; 新增专项断言
  「pending 草稿不误判(人工确认制)」实证口径铁律
- **处置**: 生产残留清理 + 修正后首轮巡检 OK(三指标 0)
- **沉淀**: 建议书制模块的「待确认草稿」不可作护栏恶化指标

### 5.2 月台底座视图修正(实机验证驱动)

- **现象**: 公网 guard 聚合返回 docks:0 但 peakTrucks:2——
  月台数 0 与预约 2 车矛盾
- **根因**: POI 种子在代码内(ZT_POI_SEEDS, fabric 层虚拟
  合并视图), **不落 zt_pois 表**——直读共享表数月台得 0
  (实际 3)
- **修正**: 聚合源改用 `ZtFabricService.list_pois(
  poi_type="dock")` 合并视图(种子+注册表), 修正后
  docks:3/capacity:9/peakTrucks:0 实证正确
- **沉淀**: 聚合口径必须对齐业务读取视图——代码内种子/
  虚拟数据源不能直读底层表

### 5.3 全局中间件响应形状(测试修正实录)

- HTTPException 409 经全局中间件转为
  `{"success": false, "error": ...}`——特征串断言首版
  只查 detail 键误判, 修正为 **detail/error 双键取值**

## 六、回滚与运维

- **回滚命令**:
  `sed -i 's/^ZT_MODE=.*/ZT_MODE=off/' /opt/zhuxiang/.env && docker compose up -d backend`
- **运行时切档**(免容器重建):
  `POST /api/map-ai/mode/override {"mode":"shadow"}`
- **护栏暂停恢复**: `POST /api/map-ai/mode/resume`(人工留痕)
- **巡检开关**: ZT_GUARD_AUTO=off 可关(默认 on——
  护栏是保护机制)
- **巡检间隔**: ZT_GUARD_INTERVAL(默认 3600s, 最小 60s)
- **off 态语义**: 智图决策 11 端点(意图/注册/预约/匹配/
  扫描/处置/留痕/沙盘/反馈)409; 观测面 13 端点与既有
  location 13 端点零影响

## 七、遗留项

| 项 | 状态 | 说明 |
|---|---|---|
| 大模型化核心 | ✅ 本次交付 | 三态+护栏+控制面+标记+自动巡检 |
| 存量适配 | ✅ 头部补 env | 54+58 断言全绿 |
| 护栏口径 | ✅ 部署实证修正 | pending 草稿不进误报分母 |
| 月台视图聚合 | ✅ 实机验证修正 | fabric 合并视图 |
| 前端消费面 | 可选 | 智图 H5 消费面(按需) |

**必办遗留项清零**(仅剩按需可选项)。

## 八、验收结论

智图·AI智能地图大模型二代转段通过上线验收:

1. **功能完备**: 大模型标准九项+叠加式升级铁律(既有
   location 13 端点零改动)+参数化双头鉴权(第四次零改动复用)
   +建议书制红线保留
2. **质量达标**: 专项 53+存量 112=165 断言全绿、实机
   七项全过、ruff 全绿
3. **生产实证**: assist 档运行、护栏巡检自动运行
   (checkCount 递增、首轮 OK)、运行时切档、
   月台合并视图聚合正确、零测试残留
4. **零破坏**: 智图一代 54 断言(业务断言零改动)+
   location 58 断言(零改动)全绿
5. **工程沉淀**: 建议书制护栏口径铁律(待确认草稿不作
   恶化指标)+虚拟数据源聚合视图原则(种子不可直读表)+
   全局中间件 detail/error 双键断言法
6. **验收后修正闭环**: 护栏口径与月台视图两处修正均为
   生产部署/实机验证实证驱动, 修正后专项断言扩充实证,
   165 断言复跑全绿

全站十二大模型终态:
68信值/71支付/73会员体验/74NexusFlow/45信值UEBA/智运物流/
62无形资产估值/65网店及商品/小竹智能语音/钱包盈利/信用管理/
**智图AI智能地图**——73/74 为 full 档, 其余 assist 档运行。
位置地图管理模块以「基础 13 端点 + 智图大模型 28 端点」双层
架构收官, 智图为全站大模型化工程的第十二位成员, 零必办
遗留项。
