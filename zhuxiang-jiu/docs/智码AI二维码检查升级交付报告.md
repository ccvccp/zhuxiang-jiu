# 智码 AI 智能二维码大模型（70 号）检查升级交付报告

> 日期：2026-10-03（第四次四件套范式复制：zy → zd → zk → **zm**）
> commit：fe98adb ｜ 生产：zxjiu.com 已上线稳态
> 结论：**四要素缺三 + shadow 语义失真 → 四件套齐备 + 语义修正**，
> 智码达到全站统一范式并首次接入真实业务流（发货挂接）

---

## 一、检查结论（摸底诊断）

### 1.1 四要素核对

| 要素 | 升级前 | 证据 |
|---|---|---|
| ① 三态灰度 | ⚠️ **半有**（有三态 env 与决策面门控，但无 override 运行时切档；shadow 宣称"只留痕"实为**完整执行**——语义失真） | qr70_registry.current_mode 仅读 env；hub.generate/redeem 无 shadow 分支 |
| ② 调度器 | ❌ 无 | main.py 无 qr70 调度注册 |
| ③ 看板 | ❌ 零前端消费 | 64 端点无任何 HTML/JS 消费 |
| ④ 业务挂接 | ❌ 零外部消费 | 全站 import 扫描：qr70 服务仅被自身 9 期测试引用 |

### 1.2 特征性发现（区别于前三模型）

- **家底最厚**：六类码注册表 + 55 号签名基座 + 愉悦度引擎 + 免疫红队 + 9 期测试（p0~p8）——五层完整度高于 zy/zd/zk 升级时
- **独有的语义债**：registry docstring 宣称 shadow=影子期只留痕，实现却是 shadow≈assist 完整执行——若直接开 shadow 档会**产生真实码**，违背宣称
- **既有测试被 403 掩盖**：qr70_p0~p8 直跑不经 conftest，X-Role 被剥离全 403 crash——当日 AUTH-TEST-01 P0 止血只修了 3 个文件，本期补齐 9 个

## 二、升级内容（四件套）

### 2.1 ① 三态灰度增强（qr70_mode_service 新建）

- zk 范式平移：`current_mode/require_decision_mode/is_shadow/set_override`，读取链 **override > env > off**（Redis 键 `zhuxiang:qr70:mode_override`，切档留痕 qr70_events）
- **shadow 真语义落地**（本升级核心）：
  - generate：shadow 下不调 55 号签名、不写码实例表，返回 `dryRun=True` + `code_shadow_generated` 留痕
  - redeem：验签只读照做、状态机不变更（不同步过期/不迁移状态），`code_shadow_redeem` 留痕
- 路由门控 14 处改 async + override 感知；新增 `/mode`、`/mode/override`、`/scan/run` 三控制端点

### 2.2 ② 每日扫描调度器（qr70_scan_scheduler 新建）

- 扫描三件：愉悦度健康报告（含漂移检测）+ 免疫监控（分布恶化自动冻结）+ 码域分布快照（六类码 × 生命周期）
- 留痕 `daily_scan` 事件（逐项可解释）；双闸 `QR70_SCAN_AUTO × QR70_MODE != off`，间隔 86400s 启动即首轮

### 2.3 ③ 业务挂接（发货流——低频高价值点）

- `order_service.ship` 发货成功后自动附**发货交接码建议书**（`shipAssist`：code/nonce/版式标记/易混对）
- 三态联动：off 不附 / shadow 附 dryRun / assist 真码（55 号签名）
- 铁律：**fail-soft**（智码故障仅记录跳过原因，发货主流程零影响）+ 建议书永不自动执行（码供仓配扫码核销，不改订单状态）

### 2.4 ④ 八区块看板（qr70-dashboard.html + js 新建）

模式态总览 / 六类码分布 / 生命周期漏斗 / 码实例列表（脱敏）/ 全链事件流（含 daily_scan、shadow、切档留痕）/ 愉悦度观测 / 免疫红队 / 模式控制（运行时切档 + 立即扫描）。ES5、同源默认、401 汉化、宽松取值降级——七看板同款约定。**64 端点首次有前端消费**。

## 三、测试与验证

| 层 | 结果 |
|---|---|
| 升级专项 test_zhima_upgrade（服务层直调） | **17/17**：off 拒 / shadow 生成核销双 dryRun 留痕 / assist 真签名+once 核销+重放拒绝 / override 非法拒 / 调度留痕 / 挂接三态 / fail-soft |
| 存量回归 qr70_p0~p8（补自举后） | 8 期 0 失败；p5 仅 1 项**既有断言漂移**（33 号权限树"32 点"已演进，此前被 403 crash 掩盖，非本次引入，移交 perm 域确认） |
| 生产部署 | .env `QR70_MODE=assist` / `QR70_SCAN_AUTO=on`；build+up 成功 |
| 生产验证 | 调度器 started 86400s + **首轮 daily_scan 真实留痕**（joy=ok / immunity=stable）；Redis `zhuxiang:qr70:event:*` 出现；mode=assist（容器内实测）；看板 html/js 双 200 |

部署管线修正项：静态根为 `/var/www/zxjiu/dist`（非项目镜像目录）——已记入 deploy 脚本。

## 四、范式对照（四模型升级横览）

| 件 | 智启元 | 智单 | 智客 | **智码** |
|---|---|---|---|---|
| 灰度 | 新建 | 新建 | 新建 | **增强**（已有 env 版 + 补 override + 修 shadow 语义） |
| 调度 | 财务扫描 | 订单扫描 | 会员扫描 | **愉悦/免疫/码域扫描** |
| 挂接 | 拉取式 | 退款建议书 | 升级权益建议书 | **发货交接码建议书** |
| 看板 | 15 端点 | 20 端点 | 20 端点 | **64 端点** |
| 专项测试 | 10/10 | 10/10 | 10/10 | **17/17** |

## 五、遗留与建议

1. **p5 权限树断言漂移**（"32 点" vs 现双中心 28 点种子/8 域）——移交 perm 域确认后修正口径
2. 收货码挂接（confirm 时附 receiving 码）列为下期候选——本期发货挂接先行
3. 愉悦度端侧上报依赖前端接入（joy/report 公开）——C 端扫码页后续接入后样本积累启动
4. 观察节奏对齐三模型：10-10 一周回看调度产出质量

> 关联：docs/全站AI大模型交付总报告_20261003.md（v2 §三 三连升级篇——本报告为其第四例）
