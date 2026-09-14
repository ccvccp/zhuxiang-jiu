# 聊天模块全域升级 + 智能知识库训练模型 · 全链交付与 assist 转段总结

> 日期：2026-09-14 | 涉及模块：AI智能客服聊天（P0-P4 全域升级）、智能知识库训练模型（创新升级）
> 生产环境：https://zxjiu.com（47.236.61.117，Docker compose）

## 一、交付总览

| 模块 | 交付内容 | 提交 |
|---|---|---|
| 聊天模块全域升级 | P0 越权加固 + P1 增量轮询 + P2 客服工作台 + P3 三态灰度 + P4 性能修复 | `7b08d73`(后端) `fb3dd2c`(前端) |
| 智能知识库训练模型 | 改名 + 双师对抗-协同引擎 + 治理即奖励 + 平衡监控 | `0f7cad9` |
| 生产 H5 前端 | 客服工作台页 + 聊天轮询（本次部署） | dist 119 文件 |

## 二、聊天模块全域升级（P0-P4）

- **P0 安全**：`_authorize_session` 会话归属校验（会员仅自己 403/admin 任意），7 端点全覆盖
- **P1 实时**：`since_message_id` 增量轮询（3s，AI 态零开销）+ `POST /read` 已读标记
- **P2 工作台**：`pages/cs-workbench` 四页签（排队/我的会话/聊天窗/统计）+ 后端 cs 端点（queue/accept/reply）
- **P3 灰度**：`CHAT_LLM_MODE=off/shadow/assist` + 护栏（投诉/未解决率 >3% 自动降档）
- **P4 性能**：Redis `KEYS *` → session_index 索引集；调度异常 `dispatchError` 留痕
- 测试：专项 38/38 + 前端 13/13 + 回归 61/61+18/18

## 三、智能知识库训练模型（创新升级）

- **改名**：AI智能知识库训练模块 → 智能知识库训练模型（全链零残留）
- **双师引擎**（推理时 generation-judge 范式）：
  - 生成器双候选（稳健 0.2 + 探索 0.8），幻觉治理口径（仅依据编号资料）
  - 判别器四维加权：合规 40（**宪法域硬门槛，LLM 不可推翻**）+ 事实 30 + 结构 15 + 引用 15，<60 否决
  - 样本库：否决→负例库，双师一致高分(≥80)→黄金标准库（**仅为建议数据**，人工流转）
- **平衡监控**：否决率健康带 50%±10%，超带自动降档 off + 人工 resume
- 测试：专项 25/25 + 回归 133/133+38/38

## 四、知识库建设

| 阶段 | 内容 |
|---|---|
| 基础填充 | 双库 20 条（产品/政策/订单/活动/合规） |
| 缺口治理 | 4 条 open → 0（补 1 条老酒鉴估 + resolve 2 + ignore 1） |
| 变体补充 | 优惠/折扣 4 条 + 货到付款 1 条 + 粮食酒 1 条 |
| 文档入库 | 4 份 PDF（竹香酒/竹奕酒电子版 GLM-4V OCR + 两份检测报告）→ 10 条产品知识 |
| **终态** | **RAG 36 条 published（embedding 全覆盖）+ FAQ 兜底 20 条；命中率 85.5%（历史口径）/ 实测 12/12** |

## 五、生产部署与转段履历

### 5.1 转段时间线

| 时间 | 事件 |
|---|---|
| 08:23 | 聊天模块后端部署（4 文件），`CHAT_LLM_MODE=shadow` 影子期 |
| 08:43 | `LLM_API_KEY` 配置（glm-4-flash），容器重建 |
| 08:50 | 知识库双库填充 + 发布（20 条） |
| 08:55 | `KNOWLEDGE_EMBEDDING=on` + rebuild（embedding 20/20 回填） |
| 09:35 | 知识模型后端部署（5 文件），`DUAL_MODE=shadow` 影子期 |
| 10:05 | 双模块 **assist 转段**（`CHAT_LLM_MODE=assist` + `DUAL_MODE=assist`） |
| 10:12 | **H5 前端部署**（dist 119 文件原子替换） |

### 5.2 影子期实证记录

- **chat shadow**：`shadowLlm` 字段落库（rule 呈现 + LLM 留痕对比，synthesized 场景 glm-4-flash 真实合成带 [编号] 引用）
- **dual shadow**：双师全链路（生成器双候选 → 判别器四维 → 胜出 90.2 分 → 黄金标准 2 条落库）

### 5.3 assist 实证（转段后）

- chat：LLM 轨呈现，`shadowLlm` 字段消失（正确）
- dual：synthesized 胜出答案直接呈现；direct 场景正确返回原文
- rule 单轨零回归（0.9742 不变）

## 六、当前生产配置终态

```
CHAT_LLM_MODE=assist        # 聊天 AI: LLM 轨呈现
DUAL_MODE=assist            # 双师引擎: 胜出答案呈现
LLM_API_KEY=<已配置>         # glm-4-flash + glm-4v-flash + embedding-3
KNOWLEDGE_EMBEDDING=on      # 语义检索
```

## 七、回滚与运营速查

```bash
# 运行时秒级回滚(免容器重建)
curl -X POST -H 'X-Role: admin' -H 'Content-Type: application/json' \
  -d '{"mode":"shadow"}' https://zxjiu.com/api/chat/mode/override
curl -X POST -H 'X-Role: admin' -H 'Content-Type: application/json' \
  -d '{"mode":"shadow"}' https://zxjiu.com/api/knowledge/dual/mode/override

# 彻底回滚(.env + 重建, 保险原件 .env.bak-assist-pre)
cp /opt/zhuxiang/.env.bak-assist-pre /opt/zhuxiang/.env
cd /opt/zhuxiang && docker compose up -d backend

# H5 回滚(备份 /tmp/h5-backup-100907)
rm -rf /var/www/zxjiu/dist/js /var/www/zxjiu/dist/css
cp -a /tmp/h5-backup-100907/js /tmp/h5-backup-100907/css /var/www/zxjiu/dist/
cp /tmp/h5-backup-100907/index.html /var/www/zxjiu/dist/

# 观测面(永不关停)
curl -H 'X-Role: admin' https://zxjiu.com/api/chat/mode            # 聊天灰度态
curl -H 'X-Role: admin' https://zxjiu.com/api/knowledge/dual/stats  # 双师统计
curl -H 'X-Role: admin' https://zxjiu.com/api/knowledge/dual/samples?kind=golden  # 黄金标准
curl -H 'X-Role: admin' https://zxjiu.com/api/knowledge/stats       # 知识库命中率
```

**护栏机制**：chat 投诉/未解决率 >3% 自动降档；dual 否决率超 [40%,60%] 自动降档。恢复均须人工 resume 留痕。

## 八、验证矩阵汇总

| 套件 | 结果 |
|---|---|
| test_chat_upgrade.py | ✅ 38/38 |
| test-cs-workbench.js | ✅ 13/13 |
| test_knowledge_dual.py | ✅ 25/25 |
| test_knowledge_routes.py | ✅ 133/133 |
| test_chat_routes.py + triggers | ✅ 61/61 + 18/18 |
| 前端回归 5 套件 | ✅ 163/163 |
| ruff 全部改动文件 | ✅ 全绿 |
| 生产三层验证（env/health/公网） | ✅ 全通过 |
| 公网 H5 | ✅ 200，cs-workbench 落位 |

## 九、运营待办建议

1. **客服工作台使用**：admin 角色登录 → 首页金刚区「客服工作台」（🎧，仅 admin 可见）→ 排队接入
2. **黄金标准审查**：定期 `GET /dual/samples?kind=golden`，人工决定是否强化为正式条目
3. **知识缺口巡检**：`GET /api/knowledge/gaps`，按同范式补充（变体条目 > keywords > 阈值）
4. **护栏巡检**：`GET /chat/mode` 与 `/dual/mode` 确认非 paused 态

*文档版本：v1.0 · 2026-09-14*
