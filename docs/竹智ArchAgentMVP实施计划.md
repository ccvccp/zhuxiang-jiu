# 竹智·Arch-Agent MVP 实施计划（架构大脑·串联版）

> 依据：用户《竹智·Arch-Agent 规划方案》+ 投喂研判（2026-10-17）：四层"感知-决策-执行-进化"在本仓为**串联非新建**——guard 自动降档/灰度开闸/DPO×SelfPlay/观测看板均超配或范式有；K8s/Istio/区块链/WASM 不采信（单机栈+ICP 阻断+零流量期）。
> MVP 目标：**把散落在 85+ 模块的感知件（mode/stats/health 端点族）串成一个架构态势面**，输出统一快照 + 竹林生态隐喻态 + 观测页，为竹智后续决策联动（L3）打地基。

## 一、改造清单（4 文件）

### 1. 新增 `backend/services/arch_agent_service.py`（汇聚器，纯标准库 httpx? → 用内部直调避免 HTTP 自环）
- `ARCH_MODULES` 注册表：各模块 `{key, label, mode_getter, stats_path, health_path}`——mode_getter 直调各 mode_service.current_mode()（chat/agreement/alliance/attract72 等已确认存在），stats/health 走本机 HTTP 127.0.0.1:8000 best-effort
- `snapshot()`：并行采集 → 统一 `{collectedAt, modules: [{key, label, mode, source, health, degraded}], meta}`
- **竹林生态隐喻映射**（方案§五）：全绿=竹林微风/任一 paused=风雨交加/降级数=竹叶黄落数——纯呈现层文案函数，不碰逻辑
- Redis 留痕：`zhuxiang:arch:snapshot`（最新）+ `arch:snapshots`（LIST 滚动 50 条，观察期趋势）

### 2. 新增 `backend/routes/arch_routes.py`
- `GET /api/arch/observe`：公开观测面（对齐 zyh §公开 GET 范式——工艺科普游客可查口径，仅态势与隐喻态，无操作面）
- `GET /api/arch/status`：admin 详情（全模块 mode/paused/guard 留痕）
- `GET /api/arch/history`：快照滚动历史（admin，趋势）

### 3. 前端 `arch-dashboard.html`（+`js/arch-dashboard.js`）
- 对齐 zyh 观测页范式：态势总览（隐喻态+健康分）/模块矩阵（mode badge 六语义）/快照趋势/手动刷新+失败回落（`观测面暂不可达`）
- 换肤令牌合规（T3 基准，零硬编码色）

### 4. 观察期联动
- observe 巡检脚本追加 arch snapshot 采集一行；竹智快照即"架构域观察期报告"数据源

## 二、红线与不做

- **零改动既有模块**：只读汇聚，不碰任何 mode_service/guard 逻辑
- admin 操作面（override 联动）= L3 范围，MVP 只读
- 不做：K8s/IaC/Service Mesh/区块链/3D/流量预测（无数据）/ClickHouse

## 三、验证

- 单测：snapshot 采集降级（mock mode_getter 异常→degraded 不阻断）/隐喻映射三态/留痕滚动上限
- e2e：observe 公开可达/admin 401 边界/dashboard 渲染+回落
- 生产：随下次部署（或单独云助手），挂每日巡检

## 四、完成记录（2026-10-17）

- **实现**：arch_agent_service.py（24 模块注册表双形态懒加载/并行采集 fail-soft/竹林隐喻三态/Redis+内存双轨留痕滚动 50）+ arch_routes.py（observe 公开/status·history admin）+ main.py/__init__ 注册 + auth_middleware 公开白名单 + arch-dashboard.html/js（live+回落范式）。
- **验证**：observe 23/23 healthy"竹林微风"；status/history 403 边界正确（compat 剥伪造头）；e2e 8/8（live 渲染/矩阵/回落/errBar/零 pageerror）。
- **踩坑**：mode_service 存在类式与模块级函数式两种形态（zy/zk/zd/qr70 为函数式）——_load_service 双形态适配（先 import_module 整路径再类式拆解）。
- **后续**：生产部署（后端 3 文件+白名单+2 前端文件，随下次部署通道）；观察期巡检可追加 arch snapshot 采集。
