# 智图·AI智能地图大模型 升级上线交付总结

> 日期: 2026-09-16 | 模块: 位置地图管理(基础 13 端点 + 智图大模型 28 端点)
> 交付: 大模型二代转段(三态灰度 ZT_MODE + 护栏三指标 + 控制面 + 自动巡检 + 62号同步桥接)
> 结论: 生产 assist 档运行, 专项 53 + 存量 112 断言全绿, 零必办遗留项

---

## 一、升级背景与检查结论

**升级前状态**(2026-09-16 检查):

| 项 | 状态 |
|---|---|
| 基础位置地图(/api/location/*) | 13 端点在线, test_location_routes 58/58 全绿 |
| 智图大模型一代(/api/map-ai/*) | 24 端点在线(P0 意图 5/P1 资源 7/P2 调度 6/P3 进化 6), test_zt_map_ai 54/54 全绿 |
| 生产运行 | zxjiu.com 正常(intent/roles 200, location 200) |
| 一代 AI 决策门 | 20号位置地图 AI(delivery_point, ai_enforcement_longtail observe/enforce)独立运行 |
| **缺口** | **零三态灰度**: 无 ZT_MODE/护栏/控制面/运行时切档/决策门控 |

## 二、大模型二代转段内容(全站范式)

### 2.1 三态灰度 ZT_MODE(zt_map_mode_service.py 新增)

- **三态**: off(默认)/shadow(智图观察期)/assist(智图辅助期)
- **读取链**: 护栏暂停 > 运行时 override > env `ZT_MODE` > 默认 off
- **状态存储**: 整档 JSON 单键 `zhuxiang:zt:mode_state`(45号 P0 教训规避; 与 _ZtStore 表键空间 `zhuxiang:zt:{table}:{id}` 互不匹配)
- **同步桥接**(62号范式): 进程快照 `_G` + `legacy_current_mode()`
- **红线**: 既有 location 13 端点零改动; 观测面永不关停; 建议书模式(月台分配/仓推荐/沙盘选址人工确认制)不受 mode 影响; 一代 20号决策门独立 AI_ENFORCE_MODE

### 2.2 护栏三指标(智图时空调度语义, 恶化>3% 自动 guard_pause)

| 指标 | 口径 | 基线 | 恶化代理 |
|---|---|---|---|
| 建议驳回率 rejectRate | rejected / 总反馈 | 0.10 | AI 建议质量恶化 |
| 调度误报率 falseAlarmRate | dismissed / 已处置工单(acked+dismissed) | 0.10 | 异常扫描误报恶化 |
| 月台饱和率 dockSaturationRate | 峰值时段预约车数 / 月台总容量 | 0.60 | 月台运力饱和恶化 |

**口径铁律**(工程洞察): 智图工单为《处置预案草稿》(人工确认制), pending 为业务常态——已处置工单才进误报分母, 冷启动/未使用不误判(分母 0 取 0)。

### 2.3 三面口径(28 端点)

| 面 | 端点 | 口径 |
|---|---|---|
| 决策面(11 POST) | intent/parse+search+behavior-hints / poi/register / supplier/dock-booking / b2b/warehouse-match / command/scan+tickets/ack / evolution/behavior+sandbox+feedback | off 409 + ztMode 标记 |
| 观测面(13 GET) | ontology/roles/poi nearby+pois/dock-status/fulfillment-board/situation/tickets/cockpit/radar/behaviors/performance/feedbacks | 永不关停 |
| 控制面(4) | GET /mode、POST /mode/override、POST /mode/guard(缺省实时聚合)、POST /mode/resume | admin 门禁 |

### 2.4 自动巡检(zt_map_scheduler.py 新增 + main.py 挂载)

- ZT_GUARD_AUTO 默认 on, ZT_GUARD_INTERVAL 默认 3600s(最小 60s)
- 聚合源: zt_tickets/zt_feedbacks/zt_dock_bookings 整表 + **ZtFabricService 月台合并视图**
- run_guard_patrol 可独立调用(控制面 guard 缺省聚合)

### 2.5 鉴权优先(参数化装饰器, 小竹/钱包/信用范式第四次复用)

- @_decision(admin=True 默认): X-Role 非 admin 放行函数体触发 403(鉴权 403 优先于门控 409)
- 智图全部端点为管理端(X-Role: admin, 时空调度敏感域)

## 三、工程记录(实施中发现并解决)

### 3.1 护栏口径修正实录(部署实证驱动)

- **现象**: 首版三指标含「工单积压率(pending/总工单)」——生产部署后巡检首轮即 guard_pause(0.875: 9-11 智图上线验证残留 8 工单全 pending)
- **根因**(业务洞察): 智图工单为《处置预案草稿》人工确认制, **pending 是业务常态而非恶化信号**——无人 ack 的草稿永远 pending, 原口径会"业务未使用→护栏自锁"
- **修正**: 三指标改为 建议驳回率/调度误报率(分母只含已处置)/月台饱和率; 新增专项断言「pending 草稿不误判(人工确认制)」实证口径铁律
- **处置**: 生产 8 工单 + 1 反馈(9-11 验证残留)清理, 修正后首轮巡检 OK(三指标 0)

### 3.2 月台底座视图修正(实机验证驱动)

- **现象**: 公网 guard 聚合返回 docks:0 但 peakTrucks:2——月台数 0 与预约 2 车矛盾
- **根因**: POI 种子在代码内(ZT_POI_SEEDS, fabric 层虚拟合并), **不落 zt_pois 表**——直读共享表数月台得 0(实际 3)
- **修正**: 聚合源改用 `ZtFabricService.list_pois(poi_type="dock")` 合并视图(种子+注册表), 修正后 docks:3/capacity:9/peakTrucks:0 实证正确
- **沉淀**: 聚合口径必须对齐业务读取视图——代码内种子/虚拟数据源不能直读底层表

### 3.3 全局中间件响应形状(测试修正)

- HTTPException 409 经全局中间件转为 `{"success": false, "error": ...}`——特征串断言须双键(detail or error)取值

## 四、验证记录

| 测试套 | 结果 |
|---|---|
| test_zt_map_mode.py 专项 | **53/53** |
| test_zt_map_ai.py 存量(头部补 ZT_MODE=assist) | **54/54** |
| test_location_routes.py 存量(零改动) | **58/58** |
| **小计** | **165/165** |
| ruff lint | All checks passed |

### 4.1 专项断言分布(53 项)

三态语义 5 + 读取链 5 + override 1 + 护栏 7(基线不暂停/三指标逐一恶化/留痕/resume/未暂停拒绝) + 序列化往返 4 + 同步桥接 5(快照干净回落/override/暂停/清除) + 护栏巡检 6(默认 on/开关/间隔/非法间隔/冷启动/**pending 草稿不误判**/误报率聚合恶化暂停) + HTTP 决策面 off **11/11**(特征串法) + 鉴权优先 1(admin 403) + 观测面 3 + shadow/assist 放行+标记 2 + 控制面 9(形状/门禁/override/非法 409/清除/未暂停 resume 409/guard 显式恶化/resume) + 端点计数 2(≥28 map-ai + ≥12 location 下限断言, PUT/DELETE 共享路径)。

### 4.2 存量零破坏

智图 54 断言头部补 `ZT_MODE=assist` 后业务断言零改动全绿(仅头部补 env); location 58 断言零改动(13 端点不受门控影响实证)。

## 五、生产部署与实机验证(zxjiu.com)

### 5.1 部署物

后端 4 文件(services/zt_map_mode_service.py、services/zt_map_scheduler.py、routes/zt_routes.py、main.py) scp 至 `/opt/zhuxiang/zhuxiang-jiu/backend/` + 容器重建 + `.env` 设 `ZT_MODE=assist`。

### 5.2 三层自验

- **容器**: zhuxiang-backend-1 Up (healthy)
- **env**: ZT_MODE=assist
- **日志**: `zt_guard_loop_started interval=3600s` + 首轮 `zt_guard_patrol_ok`(三指标 0)

### 5.3 实机七项(全过)

| # | 项 | 结果 |
|---|---|---|
| 1 | 公网 mode 总览 | 200 + assist(env 源) + guard.checkCount=1 + 三面口径公示 |
| 2 | 观测面 roles/situation/radar | 200 |
| 3 | 既有 location 13 端点 | 200(nearby 实测)——叠加式铁律实证 |
| 4 | 决策面放行 parse | 200 + 复合意图(吃饭买酒)解析 + **ztMode: "assist"** |
| 5 | 运行时切档 shadow | 200(免容器重建) → 清除回落 env assist |
| 6 | 护栏实时聚合 | 200 + 月台视图(docks:3/capacity:9/peakTrucks:0) + 三指标 0 不误暂停 |
| 7 | 测试数据零残留 | zt_tickets 8 条 + zt_feedbacks 1 条 + zt_dock_bookings 1 条(9-11 验证残留)已清理, mode_state 冷启动重建 |

## 六、回滚与运维

- **回滚命令**:
  `sed -i 's/^ZT_MODE=.*/ZT_MODE=off/' /opt/zhuxiang/.env && docker compose up -d backend`
- **运行时切档**(免容器重建):
  `POST /api/map-ai/mode/override {"mode":"shadow"}`
- **护栏暂停恢复**: `POST /api/map-ai/mode/resume`(人工留痕)
- **巡检开关**: ZT_GUARD_AUTO=off 可关(默认 on——护栏是保护机制)
- **巡检间隔**: ZT_GUARD_INTERVAL(默认 3600s, 最小 60s)
- **off 态语义**: 智图决策 11 端点(意图/注册/预约/匹配/扫描/处置/留痕/沙盘/反馈)409; 观测面 13 端点与既有 location 13 端点零影响

## 七、全站终态

全站十二大模型(智图为第十二位):
68信值/71支付/73会员体验/74NexusFlow/45信值UEBA/智运物流/62无形资产估值/65网店及商品/小竹智能语音/钱包盈利/信用管理/**智图AI智能地图**——73/74 为 full 档, 其余 assist 档运行。位置地图管理模块以「基础 13 端点 + 智图大模型 28 端点」双层架构收官, 零必办遗留项。
