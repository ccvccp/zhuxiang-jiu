# AI智能叫帮大模型（67号） 回滚执行手册

> 日期: 2026-09-16 | 适用版本: 提交 6f2d4c0(二代转段) / 7406c1b(验收报告)
> 生产环境: zxjiu.com(服务器 47.236.61.117, 部署目录 /opt/zhuxiang)
> 原则: **先模式回退(分钟级), 后代码回滚(小时级)**——模式回退覆盖 95% 场景

---

## 一、回滚决策矩阵(先判断回滚级别)

| 触发场景 | 回滚级别 | 预计耗时 |
|---|---|---|
| 决策面异常(发布/捐赠/传承/CSR 报错或误操作) | **一级: 模式回退** HELP_MODE=off | ~1 分钟 |
| 护栏误判持续暂停(小样本保护失效类) | **一级变体**: resume 或清 mode_state | ~1 分钟 |
| 前端页面异常(入口错乱/表单报错) | **二级: H5 回退**(dist.bak-jiaobang) | ~2 分钟 |
| 后端服务异常(容器崩溃/路由冲突/门控错死) | **三级: 完全代码回滚**(revert+scp+重建) | ~10 分钟 |

> 一级回退后, 决策面 7 端点返回 409(新增互助入口锁定),
> **存量履约流转(接单/开始/完成/取消/评价)与观测面不受任何影响**——
> 用户已发布互助的履约零中断, 大厅/类目/白皮书等全量常开。

---

## 二、一级回滚: 模式回退(HELP_MODE=off)

### 2.1 前置检查(30 秒)

```bash
# 本地终端执行(密钥已配置):
ssh root@47.236.61.117 "docker ps --filter name=zhuxiang-backend-1 --format '{{.Status}}'; grep -n 'HELP_MODE' /opt/zhuxiang/.env"
```

预期输出:
- `Up xx seconds/minutes (healthy)` —— 容器健康, 可执行回退;
- `98:HELP_MODE=assist` —— 当前模式行号(执行 sed 定位用)。

### 2.2 检查运行时 override(关键陷阱)

读取链为 **护栏暂停 > 运行时 override > env**——若曾设置 override,
仅改 env **不生效**:

```bash
ssh root@47.236.61.117 "docker exec zhuxiang-redis-1 redis-cli GET zhuxiang:help:mode_state"
```

- 输出 `"override":""` → 正常, 走 2.3;
- 输出含 `"override":"assist"`(或 shadow) → **先清 override**(二选一):
  - 接口法(留痕): `curl -X POST https://zxjiu.com/api/help/mode/override -H "X-Member-Id: 2" -H "X-Role: admin" -H "Content-Type: application/json" -d '{"mode":""}'`
  - 键删除法(直接): `docker exec zhuxiang-redis-1 redis-cli DEL zhuxiang:help:mode_state`

### 2.3 执行模式回退

```bash
ssh root@47.236.61.117 "sed -i 's/^HELP_MODE=.*/HELP_MODE=off/' /opt/zhuxiang/.env && cd /opt/zhuxiang && docker compose up -d backend"
```

> 注: 无代码改动不需 `--build`; 若 compose 提示配置变化重建正常。

### 2.4 回退验证(三项)

```bash
# ① 模式确认(公网, 期望 mode=off):
curl -s https://zxjiu.com/api/help/mode -H "X-Member-Id: 2" -H "X-Role: admin"

# ② 决策面关闭确认(期望 409 + detail 含 HELP_MODE):
curl -s -X POST https://zxjiu.com/api/help/orders -H "X-Member-Id: 2" \
  -H "Content-Type: application/json" \
  -d '{"mode":"public","category":"carry","title":"回滚验证","longitude":117.0,"latitude":36.6}'

# ③ 豁免面/观测面常开确认(期望非 409; 订单不存在 → 404 业务错即正常):
curl -s -X POST https://zxjiu.com/api/help/orders/99999/accept -H "X-Member-Id: 2"
curl -s https://zxjiu.com/api/help/categories
```

预期: ① `mode=off, source=env`; ② 409; ③ 404 与 200。

### 2.5 护栏暂停态的特殊处理

若回退原因是护栏 guard_pause(读取链最高优先级, **改 env 不会解除**):

```bash
# 人工恢复(留痕, 恢复后模式按 env/override 走):
curl -X POST https://zxjiu.com/api/help/mode/resume -H "X-Member-Id: 2" \
  -H "X-Role: admin" -H "Content-Type: application/json" -d '{"note":"回滚场景恢复"}'

# 或彻底清态(连暂停标记一起清):
ssh root@47.236.61.117 "docker exec zhuxiang-redis-1 redis-cli DEL zhuxiang:help:mode_state"
```

---

## 三、二级回滚: 前端 H5 回退

适用: 首页金刚区入口异常 / help 页表单渲染错误 / 决策面降级文案异常。

```bash
# 回退到叫帮上线前快照(备份目录 dist.bak-jiaobang):
ssh root@47.236.61.117 "cd /var/www/zxjiu && rm -rf dist && cp -r dist.bak-jiaobang dist && grep -o 'app\.[a-z0-9]*\.js' dist/index.html | head -1"
```

验证: 浏览器强制刷新(Ctrl+F5) https://zxjiu.com ——
- 首页金刚区**不再出现**"智能叫帮"入口;
- index.html 引用旧 chunk(非 app.dcd2d25d.js)。

> 后端不受影响——前端回退仅撤入口与降级文案, 门控在后端继续生效。

---

## 四、三级回滚: 完全代码回滚(git revert + scp)

适用: 后端服务异常(容器崩溃/路由冲突/门控装饰器缺陷)。
**执行前须先完成一级模式回退**(降低窗口期风险)。

### 4.1 本地代码回滚

```bash
cd d:\网站架构设计\zhuxiang-jiu
git revert --no-edit 6f2d4c0     # 生成反向提交(7 文件)
git log --oneline -1             # 确认 revert 提交生成
```

### 4.2 上传四个后端文件(分步验证退出码)

```bash
scp backend/services/help_mode_service.py root@47.236.61.117:/opt/zhuxiang/zhuxiang-jiu/backend/services/help_mode_service.py
scp backend/services/help_scheduler.py root@47.236.61.117:/opt/zhuxiang/zhuxiang-jiu/backend/services/help_scheduler.py
scp backend/routes/help_routes.py root@47.236.61.117:/opt/zhuxiang/zhuxiang-jiu/backend/routes/help_routes.py
scp backend/main.py root@47.236.61.117:/opt/zhuxiang/zhuxiang-jiu/backend/main.py
```

### 4.3 清理环境变量 + 容器重建

```bash
ssh root@47.236.61.117 "sed -i '/^HELP_MODE=/d' /opt/zhuxiang/.env && cd /opt/zhuxiang && docker compose up -d --build backend 2>&1 | tail -2"
```

### 4.4 残留清单(回滚后无害, 可选清理)

| 残留物 | 位置 | 影响 | 可选清理 |
|---|---|---|---|
| HELP_MODE env | 已在 4.3 删除 | 无 | — |
| mode_state 键 | Redis zhuxiang:help:mode_state | 无(旧版不读) | `redis-cli DEL zhuxiang:help:mode_state` |
| 巡检日志 | 容器日志 help_guard_* | 无 | 无需处理 |
| H5 新前端 | /var/www/zxjiu/dist | 无(旧后端无门控, 新前端降级文案不触发) | 按第三级 H5 回退处理 |

### 4.5 完全回滚验证

```bash
ssh root@47.236.61.117 "docker ps --filter name=zhuxiang-backend-1 --format '{{.Status}}'"
curl -s https://zxjiu.com/api/help/categories          # 200(基础功能恢复)
curl -s -X POST https://zxjiu.com/api/help/orders -H "X-Member-Id: 2" \
  -H "Content-Type: application/json" \
  -d '{"mode":"public","category":"carry","title":"回滚验证","longitude":117.0,"latitude":36.6}'
# 期望 200(回到无门控基线)而非 409
```

---

## 五、回滚后观察清单(30 分钟内)

| # | 项 | 正常 |
|---|---|---|
| 1 | 容器 | Up (healthy), RestartCount 不增 |
| 2 | 决策面(一级回退时) | 稳定 409, 无 5xx |
| 3 | 履约豁免面 | accept/complete/cancel 正常业务响应 |
| 4 | 大厅 | GET /api/help/orders 200 |
| 5 | 前端 | 发布页提交后显示降级提示(一级)或正常(三级) |
| 6 | 日志 | 无 help_mode/help_guard ERROR 刷屏 |

---

## 六、本会话其他改动回滚速查(2026-09-16 批次)

### 6.1 权限中心双中心(7d2e412)

```bash
# 本地: git revert 7d2e4c0 的升级提交 7d2e412 → scp 三文件 → 重建:
scp backend/repositories/perm_repository.py root@47.236.61.117:/opt/zhuxiang/zhuxiang-jiu/backend/repositories/
scp backend/services/perm_service.py root@47.236.61.117:/opt/zhuxiang/zhuxiang-jiu/backend/services/
scp backend/routes/perm_routes.py root@47.236.61.117:/opt/zhuxiang/zhuxiang-jiu/backend/routes/
ssh root@47.236.61.117 "cd /opt/zhuxiang && docker compose up -d --build backend"
```
残留: Redis 中 20 个网站中心节点(无害——旧版无 center 概念, 按环节展示)。
验证: 公网 GET /api/perm/nodes 回到 total 32。

### 6.2 62号登记入口(57539a2)

```bash
git revert 57539a2 → scp backend/services/av62_registry.py → 容器重建
# 前端: rm -rf dist && cp -r dist.bak-av62reg dist(或重构建)
```
残留: elementDetails 为增量观测键, 回滚无数据影响; 登记的资产数据保留。

### 6.3 钱包利率展示修复(707a9c9)

纯前端改动, 后端利率为业务方核定终态(**勿动**):
```bash
git revert 707a9c9 → 重构建 H5 → 上传替换
# 或直接: rm -rf dist && cp -r dist.bak-walletrate dist
```

### 6.4 H5 备份链(叠加注意)

| 备份目录 | 对应改动 | 时间序 |
|---|---|---|
| dist.bak-permdual | 权限中心双中心 | ① |
| dist.bak-walletrate | 钱包利率修复 | ② |
| dist.bak-av62reg | 62号登记入口 | ③ |
| dist.bak-jiaobang | 叫帮转段 | ④(最新) |

> 备份是**当时快照**——多模块叠加回滚须按时间倒序逐级恢复
> (如需回到②之前的态, 应恢复 dist.bak-permdual 而非 dist.bak-walletrate)。

---

## 七、恢复(回滚后重新上线)

问题修复并验证后, 重新启用 assist(先查后改):

```bash
# ① 查看当前状态:
ssh root@47.236.61.117 "grep -n 'HELP_MODE' /opt/zhuxiang/.env"

# ②a 若显示 HELP_MODE=off(一级回退遗留) → 改回:
ssh root@47.236.61.117 "sed -i 's/^HELP_MODE=.*/HELP_MODE=assist/' /opt/zhuxiang/.env"

# ②b 若无输出(三级回滚已删除) → 追加:
ssh root@47.236.61.117 "echo 'HELP_MODE=assist' >> /opt/zhuxiang/.env"

# ③ 重启后端:
ssh root@47.236.61.117 "cd /opt/zhuxiang && docker compose up -d backend"
```

验证: `curl -s https://zxjiu.com/api/help/mode -H "X-Member-Id: 2" -H "X-Role: admin"`
→ `mode=assist, source=env, paused=false`; 决策面发布测试单成功后**清理测试数据**
(`redis-cli DEL zhuxiang:help:order:{id}`, open/stats totalOrders 归位)。
