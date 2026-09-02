# 41号·直发通道接入真实 API 凭证待办清单

> 目标：将平台直发三轨兜底（当前 mock 确定性直发）切换为真实合作代驾平台 API。
> 前置现状：`DRIDE_CHANNEL_MODE` 三态通道已就绪（mock / real / mock_fallback），
> `_platform_real` 已按冻结契约实现（[ride_dispatch_service.py](../backend/services/ride_dispatch_service.py)），
> **只缺凭证与回调鉴权，零结构改动即可上线**。

---

## 一、合作平台选定与凭证获取（外部依赖，关键路径）

- [ ] **1.1 选定合作平台**：滴滴代驾开放平台 / e代驾开放平台 / 其他持有代驾资质的聚合平台（注意：部分平台仅对企业主体开放，需营业执照与道路运输经营许可）
- [ ] **1.2 完成企业入驻**：提交竹香酒业主体资质（营业执照/食品经营许可/道路运输相关备案，按平台要求）
- [ ] **1.3 获取接入凭证**：
  - `PARTNER_APP_ID`（应用 ID）
  - `PARTNER_APP_SECRET`（密钥，进密钥管理，**不入代码库**）
  - `PARTNER_CALLBACK_TOKEN`（回调签名令牌，若平台支持）
- [ ] **1.4 确认商务条款**：分佣比例、结算周期（T+1/T+7）、对账单获取方式（API 拉取 / 邮件 / 后台导出）——影响第 41号日结对账的 `channelBills` 来源
- [ ] **1.5 确认平台运力城市**：泰安是否在服务城市列表（种子司机坐标以泰安为锚点，若平台无泰安运力需换城验证）

## 二、环境变量与配置（Docker 部署侧）

- [x] **2.1 新增环境变量**（docker-compose.yml backend 服务 + `.env`，**`.env` 不入库**）：
  ```yaml
  environment:
    DRIDE_CHANNEL_MODE: real            # 终态; 灰度期用 mock_fallback
    DRIDE_PARTNER_URL: https://open.example.com/v1
    DRIDE_PARTNER_APP_ID: ${DRIDE_PARTNER_APP_ID}
    DRIDE_PARTNER_APP_SECRET: ${DRIDE_PARTNER_APP_SECRET}
    DRIDE_PARTNER_CALLBACK_TOKEN: ${DRIDE_PARTNER_CALLBACK_TOKEN}
  ```
  ✅ 已完成：docker-compose.yml 已加 6 项 DRIDE 环境变量脚手架（`${VAR:-}` 默认空，mock 模式零影响）
- [ ] **2.2 注意常量时机**：`DRIDE_CHANNEL_MODE` / `DRIDE_PARTNER_URL` 在模块导入时读取（ride_repository.py L144-153），**修改需重建容器**（`docker compose -p zhuxiang-jiu up -d --build backend`），不支持运行时热切
- [ ] **2.3 沙箱/生产隔离**：dev 用 `mock_fallback`（真实轨失败自动回退 mock 并标记 `platformChannel=mock_fallback`），生产切 `real`（fail-hard，失败抛错不拒单转人工告警）

## 三、直发契约对接（代码侧，预计改动小）

- [x] **3.1 核对请求契约**：当前 `_platform_real` 发送（已按冻结契约）：
  ```
  POST {DRIDE_PARTNER_URL}/dispatch
  {rideId, pickup:{lat,lng,address}, dropoff:{lat,lng,address},
   couponValue, estimatedKm}
  ```
  ✅ 代码侧已完成并测试（契约字段断言见 test_ride_real_channel.py）；剩余：按平台实际文档适配字段名（如平台要求 `order_id`/`depart_lat` 等驼峰转下划线）
- [x] **3.2 核对响应映射**：平台响应 → `{accepted, partnerOrderId, driver:{name, phone, plateNo, rating}, etaSeconds}`；`accepted=false` 走 `no_driver` 分支（券退回留痕，已实现并测试）
- [x] **3.3 补充平台鉴权头**（按平台规范二选一或组合）：
  ✅ 已完成：`_partner_auth_headers()` 双风格——APP_ID+APP_SECRET → HMAC-SHA256 签名头（X-App-Id/X-Timestamp/X-Nonce/X-Signature）；仅 TOKEN → `Authorization: Bearer`；均未配置 → 裸跑
- [x] **3.4 超时与重试对齐**：✅ 已完成：timeout=10s + 传输层错误重试 1 次（`X-Request-Id: rideId` 幂等键；HTTP 4xx/5xx 业务响应不重试）
- [x] **3.5 计价口径确认**：✅ 代码侧已完成留痕：直发响应捕获平台报价（字段名兼容 `quotedAmount`/`totalFee`/`estimatedFee`）→ 行程 `channelQuotedAmount` → 结算单同名字段落库（本站计价 vs 平台报价差异溯源，供对账 `amount_mismatch` 审计）；剩余：拿到真实平台后核对计价承担方

## 四、回调安全加固（当前开放端点，上线前必做）

- [x] **4.1 回调签名校验**：✅ 已完成（`ride_routes.py` `_verify_partner_callback`）：配置 `DRIDE_PARTNER_CALLBACK_TOKEN` 后双通道校验——`X-Partner-Token` 静态令牌 或 `X-Partner-Signature` HMAC-SHA256(原始请求体)；未配置则放行（mock/联调兼容口径，生产 real 模式前必须配置）
- [x] **4.2 回调幂等复核**：✅ 已复核：同 `partnerOrderId`+`event` 重复回调依赖状态机 409 拒绝（test_ride_real_channel.py 验证重复事件不产生脏数据）
- [x] **4.3 回调事件补全**：✅ 已完成：`driver_arrived`（司机到达）事件映射 `driver_arriving`（幂等，重复到达不报错）；PARTNER_EVENTS 扩至五事件，其余平台特有事件维持 409 拒绝（显式忽略需真实平台文档确认后按需加）
- [ ] **4.4 回调网络准入**：生产环境将回调端点限制为平台出口 IP（nginx/网关层）

## 五、测试与灰度验证（Mock-first 三步走）

- [x] **5.1 宿主机专项**：✅ 已完成（`test_ride_real_channel.py`，24 项）：本地 mock 平台服务器（ThreadingHTTPServer 记录请求头/体）验证 real 轨全链路/HMAC 与 Bearer 鉴权头/X-Request-Id 契约/首连失败重试/accepted=false 券退回/fail-hard/回调签名四分支/`driver_arrived` 事件映射/平台报价留痕（行程 + 结算单 `channelQuotedAmount`）。另：实机验收脚本已加"三态通道标记"断言（mock 模式验证 `platformChannel=mock`，灰度切 mock_fallback/real 后自动覆盖真实轨标记验证），实机 106 项两跑全绿
- [ ] **5.2 实机灰度（mock_fallback）**：容器置 `DRIDE_CHANNEL_MODE=mock_fallback` + 真实 `DRIDE_PARTNER_URL`，跑 `verify_ride_live.py`——真实轨成功则 `platformChannel=real`，失败自动回退 mock 且标记 `mock_fallback`，105 项断言全绿
- [ ] **5.3 实机全量（real）**：切 `real` 模式重跑验收脚本（郊区 11km 叫单走真实平台派单→回调→结算链路）
- [ ] **5.4 对账单真实化**：`channelBills` 从平台 API 拉取（或后台导出转 JSON），替换当前"按本站镜像"Mock 口径；验证四类差异检测在真实数据上的表现
- [ ] **5.5 计价回写验证**：真实回调 `trace.actualKm` 参与本站计价（已实现），与平台账单金额比对，超容差记录 `amount_mismatch`

## 六、上线切换与回退预案

- [ ] **6.1 切换顺序**：mock（现状）→ mock_fallback（灰度 ≥48h 观察日志）→ real（全量）
- [ ] **6.2 监控观测点**（日志关键字）：
  - `ride_escalated_platform ... channel=real`（直发成功率）
  - `ride_platform_real_failed`（真实轨失败，mock_fallback 触发源）
  - `ride_no_driver`（平台拒单，全轨无运力）
  - `ride_mileage_anomaly`（里程差异，疑似计价口径分歧）
- [ ] **6.3 回退预案**：出问题改 `DRIDE_CHANNEL_MODE=mock` 重建容器即回退（≤5 分钟），业务零中断（券/行程/结算结构不变）
- [ ] **6.4 文档同步**：完成后更新本清单为勾选状态 + [交付总结](AI智能代驾模块41_交付总结.md) 第八节移除该项

---

## 附：代码触点速查（预计总改动量：~100 行）

| 文件 | 改动 | 规模 | 状态 |
|------|------|------|------|
| `services/ride_dispatch_service.py` | `_partner_auth_headers()` 双风格鉴权 + 重试 + 幂等键 + 报价捕获 + driver_arrived | ~75 行 | ✅ 已完成 |
| `routes/ride_routes.py` | `_verify_partner_callback` 回调签名校验 | ~30 行 | ✅ 已完成 |
| `repositories/ride_repository.py` | 凭证常量 + driver_arrived 事件 + 空浮点反序列化修复 | ~15 行 | ✅ 已完成 |
| `docker-compose.yml` + `.env` | 6 个新环境变量脚手架 | 配置 | ✅ 已完成 |
| `test_ride_real_channel.py` | real 轨全分支测试（本地 mock 平台服务器） | ~300 行 | ✅ 24/24 |
| `verify_ride_live.py` | 三态通道标记断言（灰度自动覆盖） | ~6 行 | ✅ 106/106 两跑 |

**当前状态**：代码侧全部就绪（约 420 行，含测试），**只欠凭证**——完成第一节入驻拿到凭证后，仅需在 `.env` 填入 6 个变量 + 按清单 5.2-5.3 灰度验证即可上线。清单剩余未勾选项（1.x 凭证/4.4 网关/5.2-5.5 灰度/6.x 切换）全部依赖外部凭证或真实环境。

*清单更新于代码侧落地后，配套：[设计文档](AI智能代驾模块41_设计文档.md) §2.3 平台直发契约 · [交付总结](AI智能代驾模块41_交付总结.md) §八 · [real 轨测试](../backend/test_ride_real_channel.py)*
