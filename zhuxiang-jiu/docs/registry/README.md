# 竹盾资产图谱 registry

> 78号·竹盾（网站开发增量管理 Agent）的资产底座
> 方案: [../78号_竹盾增量管理Agent_创新规划方案.md](../78号_竹盾增量管理Agent_创新规划方案.md) · 任务列表: [../78号_竹盾增量管理Agent_开发任务列表.md](../78号_竹盾增量管理Agent_开发任务列表.md)
> 建立: 2026-10-20（任务 T0.1）

## 目录用途

| 条目 | 内容 | 状态 | 维护方式 |
| --- | --- | --- | --- |
| L0-端点清单.md | backend 全部路由（方法/路径/函数/行号/鉴权面/门控标注） | ✅ T0.2 | 新增路由后重扫或手工补录；P1 起由 `zhudun index` 自动重建 |
| L0-模块清单.md | backend services/schedulers/models + 全部 MODE 环境开关（附回滚值与两通道回滚模板） | ✅ T0.3 | 同上 |
| L0-页面清单.md | 三源页面资产（仓库根 html/js/css + taro-app/public + deploy 直部署）+ nginx 路由 + dist 实际对照 | ✅ T0.4 | 同上；dist 数据为云助手取证快照，刷新经 deploy/aliyun-ecs-rpc.ps1 |
| 红线标记.md | 六大红线（支付回调/鉴权/模型门控/Redis/语音/部署）+ 行为资产五条，每条附代码位置 | ✅ T0.5 | **lead 维护**，变更须专项流程 |
| registry.json | 机器可读索引（2612 端点+480 开关+675 模块+页面+红线文件面，zhudun impact 命中判定用） | ✅ T0.6 | `tools/build_registry_json.py` 从 MD 生成，禁手工编辑 |
| reports/ | 影响报告归档：`影响报告-YYYYMMDD-HHmm.md`，与 PR 一一对应 | 骨架已建 | 每次 `zhudun impact` 产出后归档 |

## 维护规则

1. **MD 为源、JSON 由脚本生成**——手工改动只改 MD；registry.json 由脚本从 MD 重新生成，禁止直接编辑 JSON
2. 代码与清单同步：新增/修改路由、模块、页面后同步对应清单条目（P1 起 pre-commit 钩子提示）
3. 白名单口径唯一来源：`backend/core/auth_middleware.py`（PUBLIC_EXACT / PUBLIC_GET_PREFIXES / PUBLIC_GET_PATTERNS），端点清单标注与本文件对齐
4. 本目录**全部入 git**（影响报告为长期资产，区别于一次性探针报告——见进化检查清单 F2）
5. 临时生成器：`tools/scan_endpoints.py`（端点层）+ `tools/scan_modules.py`（模块与开关层）+ `tools/scan_pages.py`（页面层）——P1 由 `zhudun index` 替代后移除
