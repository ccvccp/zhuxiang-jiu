# 77号·竹鉴 BambooVerify 大模型 full 转段交付总结

> 文档版本：v1.2 · 2026-09-19（v1.1 补充代码变更明细；v1.2 补充第七章「前端三面接入」——C端质检查询页生产上线/B端治理看板/三页互链，提交 f7c62cf）
> 转段动作：ZJIAN_MODE off → shadow → assist → full（单会话全周期：方向裁定 → 开发 → 上线 → 四档灰度贯通）+ 前端三面接入（C 端可达性闭环）
> 设计依据：D:\竹奕酒的资料 双检测报告工程化裁剪（ZZ26SW1489303A 52%vol 型 / ZZ26SW1489404B 42%vol 型，山东中质华检测试检验有限公司，判定依据 Q/SRQ 0001S-2023 / GB 2760-2024 / GB 7718-2025，签发 2026-06-25）
> 方向授权：用户授权自主设计（D 盘零新增文档源裁定）
> 验证脚本：backend/prod_zjian_gradation_verify.py（四档自适应零破坏矩阵）

---

## 一、转段总览

| 维度 | 数据 |
|---|---|
| 转段方向 | off → shadow → assist → **full**（四档范式最高档，单会话贯通） |
| 开放域 | L1 自主域白名单：auto_patrol（护栏自主巡检，每 10 次决策节流触发） |
| 保留域 | 报告典藏锚定（检测数据人工录入——原文事实源）/ resume——full 档亦人工（铁律） |
| 范式依据 | 73/74/75/76 四档先例（本模块为第五位 full 档） |
| 生产状态 | zxjiu.com 公网 mode=full(env) · 容器 healthy |
| 验证结论 | 本地 59/59 · 回归 41/41 + 132/132 · 生产 off 12/12 → shadow 14/14 → assist 14/14 → **full 17/17** |
| 全站意义 | 十八大模型第五位 full 档；竹香酒产品域三闭环齐备（75 工艺 / 76 内容 / **77 品质引证**） |

---

## 二、方向裁定与素材审计

用户授权自主设计后对 D 盘素材审计：

| 素材 | 审计结论 |
|---|---|
| ZZ26SW1489303A / ZZ26SW1489404B PDF | **实为检测报告**（山东中质华检委托检测，非专利/企标文书）——4 页可提取 2136 字符，含封面/声明/检测结论/15 项指标明细 |
| 竹奕酒电子版.pdf（101 字符）/ 竹香酒.pdf（33 字符） | 纯扫描件，文本不可提取（OCR 超工程范围） |
| D:\文档 两文件 | 历史需求文档（信值系参考，已由 45/68 号落地） |

**裁定**：基于双检测报告可提取全量数据，开发**质检典藏引证大模型**——全站引证域空缺（75 号答"工艺是什么"，77 号答"检测数据是什么、报告哪里写"），三域闭环。

## 三、工程化设计（纯确定性，LLM 禁入）

| 组件 | 设计 |
|---|---|
| 质检典藏 | 双规格报告全量结构化：元数据（机构/判定依据/签发/检测期）+ 15 项指标明细（实测值/技术要求/单项判定/检测方法 GB 5009 系） |
| 指标域检索 | 15 项指标关键词域确定性路由 + 安全域聚合（"安全性"→8 项有害物质汇总）+ 规格消歧（52/42 型显式或双型并列） |
| 质检问答 | 引证式应答：指标值 + 技术要求 + 单项判定 + 检测方法 + **报告编号引证**（技术断言必含引证——75 号 L3 溯源同源精神） |
| 合规拦截 | 医疗功效断言（治病/疗效/降血压）与夸大宣传（最好/唯一/绝对安全）——检测数据不得医用（判定依据红线） |
| 规格比对 | 双型全 15 项指标对照表（52 型 51.3%vol/总酸 0.84/总酯 1.40 vs 42 型 41.7%vol/0.78/1.10） |
| 四档灰度 | ZJIAN_MODE(off/shadow/assist/full) + L1 自主域 auto_patrol + 护栏（漏网口径铁律） |

## 四、代码架构与变更明细（9 文件）

| 文件 | 职责 |
|---|---|
| repositories/zjian_repository.py | 报告典藏 + 问答留痕 + 统计仓储 |
| services/zjian_mode_service.py | 四档灰度 + L1 自主域 + 护栏三指标 |
| services/zjian_service.py | 典藏种子（双报告 15 项全量）+ 检索路由 + 引证问答 + 比对 |
| services/zjian_scorer.py | bamboo_verify 评分器（batch51 四因子） |
| routes/zjian_routes.py | 11 端点（观测 6 GET 白名单 + 决策 verify/compare + 管理 anchor/override/guard/resume） |
| ai_learning_service.py / main.py / routes/__init__.py / auth_middleware.py | 注册入册 + /api/zjian/ 公开白名单 |
| test_zjian.py + prod_zjian_gradation_verify.py | 59 项本地 + 四档生产矩阵 |

### 4.1 services/zjian_service.py——核心服务（486 行）

| 组件 | 位置 | 内容 |
|---|---|---|
| `_METRIC_TPL` | L40 | 15 项指标模板（名称/单位/技术要求/检测方法 GB 5009 系/未检出定量限）——双报告通用字段 |
| `REPORTS` | L120 | 双规格典藏种子：ZZ26SW1489303A 52%vol 型（酒精度 51.3/总酸 0.84/总酯 1.40/固形物 0.23）+ ZZ26SW1489404B 42%vol 型（41.7/0.78/1.10/0.21）——含机构/判定依据/签发/检测期元数据 |
| `METRIC_KEYWORDS` | L189 | 15 项指标关键词域（酒精度/甲醇/氰化物/铅/防腐剂×2/甜味剂×2/二氧化硫/标签/锰/总酸/总酯/固形物/杂醇油）——**规格数字 52/42 不入指标域**（消歧专用，防"42 型甲醇"误命中酒精度） |
| `SAFETY_KEYS` | L206 | 安全域聚合 8 项（甲醇/氰化物/铅/苯甲酸/山梨酸/糖精钠/甜蜜素/二氧化硫）——"安全性"一词触发 |
| `BLOCK_PATTERNS` | L211 | 合规拦截正则：medical（治病/疗效/降血压/包治）+ exaggerate（最好/第一/唯一/绝对安全/零风险）——检测数据不得医用 |
| `_match_metrics` | L246 | 问题→指标键检索（保序去重）+ 安全域聚合重排 |
| `_match_spec` | L262 | 规格消歧：含"52"→单 52 型 / 含"42"→单 42 型 / 缺省双型并列 |
| `verify_chat` | L283 | 决策面主链：合规拦截 → 指标检索 → 引证应答（值+要求+判定+方法+报告号）→ 留痕 |
| `_render_answer` | L385 | 确定性渲染：`[规格] 指标: 实测 X(技术要求 Y, 符合); 检测方法 Z; 报告 W` |
| `compare` | L410 | 双规格 15 项对照表 |

### 4.2 services/zjian_mode_service.py——四档灰度（75/76 同源）

MODE_VALUES 四档封闭（L1 自主域 auto_patrol / 每 10 次决策节流巡检）+ 护栏三指标（aggregate_guard_metrics 聚合 guard stats → 漏网口径：bench_leak/cite_miss/out_context 均为放行后失格计数——结构恒零防线）+ status_view 公示（fullAutonomy/neverAutonomous）。

### 4.3 routes/zjian_routes.py——11 端点

| 区 | 端点 | 位置 |
|---|---|---|
| 观测 6 GET（白名单） | reports / reports/{rid} / metrics / catalog / asks / mode | L116-150 |
| 决策 2 POST（@_decision 门控） | verify 质检问答 / compare 规格比对 | L159+ |
| 管理 4 POST | **reports/anchor（典藏锚定——field 仅限 conclusion/signDate，metrics 拒绝 409）** / mode/override / mode/guard / mode/resume | 管理区 |

`_decision` 装饰器（L55）：off 409 门控 + 三档 zjianMode 留痕 + full 档 auto_patrol 触发（异常只告警不阻断）。

### 4.4 评分器与注册

- zjian_scorer.py：bamboo_verify 四因子（引证覆盖 0.30 / 指标准确 0.30 / 拦截有效 0.20 / 检索命中 0.20）→ observe/optimize/urgent 三级
- ai_learning_service.py 三处入册：SCORER_REGISTRY（batch51）/ DECISION_THRESHOLDS / default_weights 分支
- auth_middleware.py：PUBLIC_GET_PREFIXES 加 `/api/zjian/`（GET only——典藏公示游客可查，POST 决策面 JWT+门控）

**护栏三指标（漏网口径铁律）**：判定失真率 0.10（应答与典藏不符）/ 引证错失率 0.05（放行技术应答缺引证——恒零防线）/ 断章率 0.05（引证偏离问题域——恒零防线），恶化 >3% 自动 guard_pause。

**永不自主**：报告典藏锚定（检测数据线上不可修订——anchor 端点仅限 conclusion/signDate 留痕，metrics 拒绝 409）· 护栏恢复 resume。

## 五、灰度全周期验证矩阵（生产）

| 档位 | 关键验证 | 结果 |
|---|---|---|
| off | 观测面 6 GET（游客白名单）/ 决策面 2 POST → 409 / 管理面边界 | **12/12** |
| shadow | 引证应答（52 型酒精度 51.3 + 报告号引证 + synapseMode 留痕）/ 医疗拦截 / 安全汇总 16 项 / 比对 15 行 | **14/14** |
| assist | 同 shadow | **14/14** |
| **full** | + 自主域公示（auto_patrol/10）/ 永不自主红线 / **auto_patrol 实证**（10 次决策 checkCount 自增） | **17/17** |

## 六、过程修复沉淀

| 问题 | 修复 |
|---|---|
| 规格消歧词污染指标域（"42 型甲醇"误命中酒精度——42 在 alcohol 关键词表） | 52/42 移出指标域，仅在 _match_spec 消歧 |
| auth_middleware.py scp 错放 backend 根（同错二犯） | 部署清单固化：core/ 子目录必须显式单列 |
| 容器 build 后 12 秒启动不足（Connection refused） | 就绪等待循环（health 探针 ×30×2s）替代固定 sleep |

---

## 七、前端三面接入（C 端可达性闭环 · 提交 f7c62cf · 6 文件 +1073 行）

### 7.1 C 端 · zjian.html——质检查询页（**已上线 https://zxjiu.com/zjian.html**）

自包含单文件（零 Taro 依赖，nginx try_files 直 serve，备份 `dist.bak-zjianfront`）。主题：**品质青 #14665B × 签章红 #B5453C**（检测报告权威感——区别于 75 竹香绿 / 76 织智蓝金）。

| 面 | 能力 | 实现要点 |
|---|---|---|
| 游客面 | 双检测报告卡（规格/机构/签发/酒精度实测/未检出项计数）+ 15 项指标目录表 | 公开白名单 GET（零鉴权） |
| 登录面 | 质检查询（5 快捷 chip：52型酒精度/42型总酸/甲醇/安全性/标签） | `POST /api/zjian/verify` + 精简 JWT 链（401→refresh→retry 自愈，等价验证 4/4） |
| 引证应答卡 | 规格 pill + 指标明细表（实测/技术要求/判定/检测方法）+ 报告引证徽标 + 灰度留痕 | 技术断言必含报告引证的前端呈现 |
| 拦截友好化 | 医疗/夸大断言 → 合规话术卡（"检测数据仅证明产品符合食品安全标准…"） | guardrailsTriggered 分支 |
| 规格比对 | 双规格 15 行对照表（签章红按钮触发） | `POST /api/zjian/compare` |

### 7.2 B 端 · zjian-dashboard.html + js/zjian-dashboard.js

六区块全公开观测面只读（零鉴权零管理面写操作）：①灰度态与护栏（四档 pill/decisionSeq/checkCount）②典藏统计 ③指标命中分布 ④问答留痕 ⑤双报告典藏 ⑥指标目录（15 项）。script 引用加 `?v=1` 防缓存。

### 7.3 三页互链（产品域三闭环前端齐备）

zyh.html（75 工艺）/ synapse.html（76 内容）/ zjian.html（77 品质）页脚互链——竹香酒产品域三闭环前端完整贯通（`?v=` 绕缓存公网验证）。

### 7.4 前端验证矩阵

| 环境 | 链路 | 结果 |
|---|---|---|
| 本地（8001 后端） | 游客 2 报告卡 + 15 行目录 | ✓ |
| 本地 | 引证应答（52 型 pill + 表格 + 报告引证 + 灰度 full 留痕） | ✓ |
| 本地 | 比对 16 行（表头+15）/ 医疗拦截合规话术 | ✓ |
| 本地 | 看板六区块（FULL pill/统计/留痕/典藏） | ✓ |
| 本地 | 401 自愈等价验证（verify_zjian_front_auth.py） | ✓ 4/4 |
| **生产公网** | 游客 2 卡 15 行 / 登录查询"报告引证: ZZ26SW1489303A · 灰度 full" | ✓ |
| 公网互链 | zyh/synapse 页脚竹鉴入口 | ✓ |

---

## 八、运营机制与回滚

**full 期节奏**：决策流量每 10 次自主巡检（zjian_auto_patrol 留痕）· 人工兜底 POST /api/zjian/mode/guard · 观测 GET /api/zjian/metrics · 典藏核对走部署链（人工显式）。

**回滚路径**：

```bash
sed -i 's/^ZJIAN_MODE=.*/ZJIAN_MODE=off/' /opt/zhuxiang/.env
cd /opt/zhuxiang && docker compose up -d backend
```

## 九、转段终态

| 项 | 终态 |
|---|---|
| 灰度档位 | **full（env 直配，无 override）** |
| 决策面 | verify 质检问答 / compare 规格比对放行 + zjianMode=full 留痕 |
| 自主域 | auto_patrol 节流巡检运行 |
| 红线 | LLM 禁入 · 典藏锚定永不自主 · 检测数据不作医疗/夸大宣传 |
| 评分器 | bamboo_verify（batch51）在册 46号学习总线 |
| **前端** | **zjian.html 已上线（zxjiu.com/zjian.html）**——游客报告典藏浏览 + 登录引证查询 + 15 行比对；B 端治理看板齐备；三页互链（75/76/77 产品域三闭环前端贯通） |
| 全站位次 | 十八模型 full 档第五位（73/74/75/76/**77**） |

**结论**：77号竹鉴 BambooVerify 完成从素材审计、方向裁定、开发、四档灰度贯通（full 17/17）到**前端三面接入**的全周期——后端 11 端点 full 档生产运行，C 端质检查询页上线（游客零鉴权典藏浏览 + 登录引证式查询 401 自愈 + 拦截友好化，公网验证全通），B 端治理看板与三页互链齐备。竹香酒产品域三闭环（75 工艺 / 76 内容 / 77 品质）**前后端完整贯通**；全站十八大模型 full 档序列 73/74/75/76/77。
