# 72号·AI智能自动引流大模型 上线部署与 shadow 转段留档

> 部署日期: 2026-09-14 | 生产环境: https://zxjiu.com（47.236.61.117, Docker compose）
> 范围: P4 短链记忆 + P5 热点卡位 + P6 元认知与治理 三期上线 + shadow 影子期开启
> 服务器路径: /opt/zhuxiang（.env + docker-compose）/opt/zhuxiang/zhuxiang-jiu（代码）

## 一、部署清单

| 步骤 | 内容 | 结果 |
|---|---|---|
| 1 代码同步 | 7 文件 scp（registry/p4/p5/p6 service + repo + routes + ai_learning）| md5 哈希校验一致 ✓ |
| 2 环境变量 | `.env` 备份为 `.env.bak-attract72-off` → `ATTRACT72_MODE=shadow` | ✓ |
| 3 容器重建 | `docker compose up -d --build backend` ×3（含两轮生产修复） | Up healthy ✓ |
| 4 三层自验 | 容器 `printenv ATTRACT72_MODE=shadow` / 本地 health 200 / model-status mode=shadow | ✓ |
| 5 生产修复 | Redis 序列化三处（见下）本地修复→回归→重部署 | ✓ |
| 6 公网验证 | 观测面 + shadow 决策面 8/8（见下） | ✓ |

## 二、shadow 起点留档

| 项 | 值 |
|---|---|
| 起点时间 | 2026-09-14 13:07 (UTC+8) |
| 运行模式 | ATTRACT72_MODE=shadow（决策面开·落档不执行；观测面常开） |
| 影子期口径 | ≥7 天，评估签核后转 assist（71号惯例） |
| 生产红队 | 首轮 4/4 全防御（runId=1, mode=shadow, 2026-09-14 13:07:42）——full 转段核验在案 |

**回滚命令**：
```bash
sed -i 's/^ATTRACT72_MODE=.*/ATTRACT72_MODE=off/' /opt/zhuxiang/.env
cd /opt/zhuxiang && docker compose up -d backend
# .env 原始备份: /opt/zhuxiang/.env.bak-attract72-off
```

**紧急制动**：`.env` 置 `ATTRACT72_KILL=1` + 重建（数据面+环境变量双保险，观测面保留）
**解冻通道**：`ATTRACT72_IMMUNITY=1` + `POST /api/attract72/meta/unfreeze`（人工专属双保险）
**运行时切档**（免容器重建）: `POST /api/attract72/mode/{target}?confirm=true`（admin+confirm）

## 三、生产实测发现并修复（Redis 序列化三处——本地 asyncio 不暴露）

| # | 缺陷 | 现象 | 修复 |
|---|---|---|---|
| ① | `_list` 通配域碰撞：`next_id("health")` 的 seq 键落在 `attract72:health:*` 通配内 | evolution/log 500（`'int' object is not iterable`） | 非 dict 解析值跳过（fail-soft 防御） |
| ② | `avgDwell`/`windowBaseClicks`/`thresholds` 未入五清单 | Redis 读回字符串——老客回访 decide_landing `str>=float` TypeError 隐患 | 三字段入清单（float/int/JSON dict） |
| ③ | `_FLOAT_FIELDS` 误含 `potential`（决策记录为四维 dict） | hotspot/decide 500（`float(dict)`） | 移除误项+注释铁律（potential 走 JSON dict 轨道） |

修复提交: `dfd5895`（本地回归 P1-P6 283 断言 + attract v1.0 71/71 + ruff 全绿后重部署）

## 四、公网验证矩阵（shadow 档, 8/8 全过）

| # | 端点 | 结果 |
|---|---|---|
| 1 | POST /landing/dynamic（无指纹） | 200 variant=default（v1.0 兼容兜底） |
| 2 | POST /memory/visit → GET /memory/{fp} | 200 clicks=1 avgDwell=180.0（float 正常） |
| 3 | POST /landing/dynamic（老客指纹） | 200 variant=benefit_first（高参与直显权益） |
| 4 | GET /hotspot/opportunities | 200 n=3 top=极端天气自救指南 total=0.72 verdict=chase（雷达只读消费） |
| 5 | GET /meta/health?refresh=1 | 200 verdict=healthy（insufficient=3 小样本不判定） |
| 6 | GET /redteam | 200 runs=1 4/4 防御 allDefended=True |
| 7 | GET /model/status | 200 mode=shadow kill=false frozen=false |
| 8 | POST /hotspot/decide（雷达 22） | 200 decisionId=3 verdict=chase executed=**False**（shadow 落档不执行语义实证） |
| 附 | POST /mode/full（未确认） | 409 转段须二次确认（防误触） |

## 五、生产数据留痕说明

- 卡位决策 decisionId=3（radar:22, chase, 落档不执行）保留为 shadow 期实证
- 红队 RT scratch 前缀留痕（RT-72-01-a/b 记忆、subject 9901 画像）——隔离命名，不污染真实数据
- 验证用指纹 prod-verify-001 已清理

## 六、转段路径（后续）

```
shadow（当前, ≥7天）
  → assist（评估签核: 影子期留痕质量 + 护栏零告警）
  → full（须红队四向量全防御核验 + L1 白名单内）
```

assist 转段命令（届时）：
```bash
sed -i 's/^ATTRACT72_MODE=.*/ATTRACT72_MODE=assist/' /opt/zhuxiang/.env
cd /opt/zhuxiang && docker compose up -d backend
```
