# 智图·AI智能地图大模型 交付总结

> 一句话: 位置地图模块升级为「全域角色时空服务中枢」——从"坐标查询工具"到"业务意图翻译器+资源匹配引擎"
> 日期: 2026-09-12 · 全链叠加式(既有 location 13 端点零改动)
> 生产部署: 2026-09-12 已上线 zxjiu.com(基线 c816fc1+1fe638d, 验收报告见《智图AI智能地图大模型_部署验收报告》)
> 地图底座: 百度地图 JS API(测试期, AK 经 TARO_APP_BAIDU_MAP_AK 注入; 未配置诚实降级列表视图)

---

## 一、交付全景(四引擎)

| 阶段 | 引擎 | 核心能力 | 服务文件 |
|---|---|---|---|
| P0 | 意图引擎 | 12 类业务本体(确定性词表)/复合意图解析("吃饭+买酒"→dining∧retail)/六角色画像/复合时空搜索(AND 匹配+距离0.4×评分0.25×余量0.2×营业0.15) | zt_intent_service.py |
| P1 | 资源聚合 | 全业务 POI 底座(11 种子: 旗舰店/体验馆/餐饮×2/城市仓×2/月台×3/服务点×2, 含实时状态)/配置化 POI 注册(零代码)/月台预约(最短排队分配建议)/多仓匹配(库存过滤+运费测算 8+0.6/km)/履约看板 | zt_resource_service.py |
| P2 | 全域调度 | 态势一张图(POI 分层+运力)/异常四检测器(排队≥4/座位<15%/库存<50%/渠道失败≥2)→预案工单(责任岗位+ack/dismissed 闭环)/代理驾驶舱(热力+合规风险)/风险雷达(三域分级) | zt_command_service.py |
| P3 | 进化闭环 | 行为四阶段留痕(搜索→下单→核销→评价)/选址沙盘(人口0.3+竞争0.3+密度0.25+成本0.15 确定性加权, 收益/风险量化)/时空绩效画像/反馈负样本回流 | zt_evolution_service.py |

## 二、宪法对齐(三铁律实证)

1. **LLM 禁入决策链**: 意图解析为本体词表确定性分词(测试断言"同输入同输出"); 匹配/排序/沙盘全部确定性公式, 响应体带 formula 字段。
2. **建议书模式**: 月台分配/工单处置/选址决策响应体均带 disposition, 永不自动执行。
3. **进化可控**: 驳回/拒绝均记负样本留痕, 决策权在人工。

## 三、端点清单(22 新增, 前缀 /api/map-ai)

- **P0 意图(5)**: POST intent/parse · GET intent/ontology · POST intent/search · GET intent/roles · POST intent/behavior-hints
- **P1 资源(7)**: GET poi/nearby · GET pois · POST poi/register · POST supplier/dock-booking · GET supplier/dock-status · POST b2b/warehouse-match · GET b2b/fulfillment-board
- **P2 调度(6)**: GET command/situation · POST command/scan · GET command/tickets · POST command/tickets/{id}/ack · GET agent/cockpit · GET management/radar
- **P3 进化(4)**: POST evolution/behavior · GET evolution/behaviors · POST evolution/sandbox · GET evolution/performance(+POST/GET evolution/feedback×2 合计 6 路由)

## 四、验证结果

| 项 | 结果 |
|---|---|
| 智图专项(test_zt_map_ai.py) | **54/54 全绿**(服务+HTTP+宪法铁律+location 回归) |
| 既有 location 回归 | 58/58 全绿(零改动实证) |
| 智运/物流回归 | 85/85 + 22/22 全绿(跨模块无影响) |
| ruff lint | 全部通过 |
| 前端 H5 构建 | webpack 5.78 编译成功 |

**实测亮点**:
- 复合搜索: "找附近能吃饭还能买酒的地方"→3 POI(D-101/D-102/E-001), AND 匹配+四因子降序+确定性(同输入同输出)
- 月台预约: 排队最短 K-003 分配, 建议书模式; 满时段 409
- 多仓匹配: 库存过滤(W-002 550<800 剔除)+成本测算; 全仓不足 409
- 异常四检测器: 种子数据全覆盖(排队/满座/缺货/物流失败), 责任岗位+预案+ack/驳回负样本闭环
- 沙盘: 春熙路商圈评分显著高于无人区(区位敏感性), 建议书模式

## 五、前端交付

- zt.ts: 18 方法(意图 5/资源 6/调度 6/进化 5)+BAIDU_MAP_AK 导出
- 智图工作台四页签(意图/资源/调度/进化): 地图底座(AK 有则动态加载百度 JS API 标注 11 POI; 无则诚实降级列表+距离)
- 首页金刚区新增「智图地图」入口

## 六、已知边界与后续

- 百度地图 AK 未配置: 前端走列表降级(诚实不伪造); 生产接入时设 TARO_APP_BAIDU_MAP_AK 即恢复
- 沙盘收益测算为线性代理口径: 真实人流/租金数据接入后仅换因子常量
- 驾驶舱窜货/价格异常: 需风控模块数据联动(已留注释)
- POI 实时状态(库存/座位/排队)为种子静态: 接入业务系统实时数据后替换数据源即可
