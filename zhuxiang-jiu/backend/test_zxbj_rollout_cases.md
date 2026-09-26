# ZXBJ 推广码投放测试用例（72 号引流链路 · 投放验收）

生成时间：2026-09-26（P0/P1/落地修复收官后验收基线）
适用环境：生产 zxjiu.com（ssh root@47.236.61.117）
测试账号：推广人 member1（13800000001）/ member3（13800000002，admin）；
新注册号建议用 `139xxx` 段可辨识测试号。

> 背景：ZXBJ 码（会员矩阵码）经 `/r/ZXBJ-xxx` 直达链接投放，
> 302 落地 `/?clickId&v72#/pages/login/index`（注册页子视图），
> 注册 Referer 自动归因 → promotion 矩阵绑定 + traffic lead +
> 72 号记忆 → 下单自动回写。本用例验收**投放全链路**。

---

## A. 码准备（领取与幂等）

**A1 领取专属推广码**
- 前置：member1 登录态（X-Member-Id: 1）
- 步骤：`POST /api/promotion/code/claim` body `{"channel": "direct"}`
- 预期：`{"success": true, "code": "ZXBJ-xxxxxx", "channel": "direct",
  "shareTip": "直达链接带推广码 ZXBJ-xxx 注册即绑定", "reclaimed": false}`
- 记录 code 为后续用例的 **{CODE}**

**A2 同渠道幂等**
- 重复 claim 同 channel → `reclaimed: true` 且 code 不变

**A3 多渠道独立**
- claim channel=wechat_miniprogram → 新 code（与 A1 不同），
  shareTip 为小程序文案（码文本形态）

**A4 我的推广码列表**
- `GET /api/promotion/my/codes` → 两码在列

## B. 直达链接投放链路（核心路径）

**B1 点击落库 + 真实 IP**
- 步骤：浏览器无痕窗口访问 `https://zxjiu.com/r/{CODE}`
- 预期：302 一次重定向，最终 URL 形如
  `https://zxjiu.com/?clickId={N}&v72=trust_first#/pages/login/index`
- 验证：
  ```
  docker exec zhuxiang-redis-1 redis-cli GET zhuxiang:attract:click:{N}
  ```
  → `"code": "ZXBJ-xxx"`, `"codeType": "promotion"`,
  `"ip": "<真实公网IP，非 172.18.0.1>`, `"promoterId": 1`

**B2 注册落地正确性（路由修复验收）**
- 最终页面为 **login 页的注册子视图**（手机号/昵称/密码表单），
  而非商城首页 tabbar
- 新指纹首访 v72=trust_first → 顶部绿色横幅
  "🛡 品牌直供 · 官方正品"

**B3 72 号三连落库**
- 验证：
  ```
  docker exec zhuxiang-redis-1 redis-cli --scan --pattern 'zhuxiang:attract72:snapshot:{N}'
  docker exec zhuxiang-redis-1 redis-cli GET zhuxiang:attract72:memory:<UA指纹>
  ```
  → 意图快照存在；设备记忆 clicksTotal≥1

## C. 注册归因闭环（本链路灵魂）

**C1 新会员注册自动归因**
- 步骤：B1 落地页上完成注册（新手机号 139xxxx）
- 预期：注册成功 + 归因四件套：
  ```
  # 归因表(302 的 clickId 归属新 memberId)
  redis-cli GET zhuxiang:attract:attr:{N}     → memberId=新ID, registeredAt 非空
  # promotion 矩阵绑定(新人 24h 窗口: status=valid 计业绩+触发上级奖励)
  日志: docker logs zhuxiang-backend-1 | grep -E 'promotion|relation' | tail
  # traffic lead(promoterId 有值: registered 态)
  # 72 号记忆
  redis-cli GET zhuxiang:attract72:memory:<UA指纹> → "registered": 1
  # 归并日志
  docker logs zhuxiang-backend-1 | grep attract_auto_attached
  ```

**C2 推广人业绩可见**
- `GET /api/promotion/my/codes`（member1）→ 该码 boundCount +1

## D. 下单转化回写

**D1 归因会员下单自动回写**
- 步骤：C1 新会员登录商城，任意商品下单
- 预期：
  ```
  redis-cli GET zhuxiang:attract:attr:{N}
  → "orderId": "<订单号>", "orderAmount": <实付金额>
  日志: attract_auto_order member={新ID} order={订单号} click={N}
  72 号: memory "conversions": 1
  ```

**D2 幂等保护**
- 同会员重复下单（第二单）→ 归因 orderId 不被覆盖
  （日志 attract_auto_order_skip: 已回写订单——单归因单订单）

## E. 防御用例

**E1 老会员经码落地注册**（业务正确性）
- 已注册 >24h 的老会员点 `/r/{CODE}` 后在落地页登录
  （不再注册）→ 无新归因；若走绑定则 relation status=invalid
  不计业绩不奖励（attach bind_skip 日志属正常）

**E2 跨会员归因冲突**
- 同一 clickId 的归因已属会员 A，会员 B 的注册请求伪造
  Referer 同 clickId → 409 语义
  （日志 attract_auto_attach_skip: 点击已归并至其他会员）

**E3 幽灵 clickId**
- 手工访问 `/?clickId=99999999#/pages/login/index` 后注册
  → 注册成功但无归因（fail-soft 日志 attract_auto_attach_skip:
  点击不存在）

**E4 重复注册归并幂等**
- 已归并会员再次带同 clickId Referer 注册请求 →
  幂等返回既有归因，不炸不重复

## F. 反作弊与制动

**F1 同指纹高频点击隔离**（72 号 P4）
- 同 UA 短时间 >FINGERPRINT_RATE_LIMIT 次点击
  → memory `"isolated": 1` + 隔离窗内变体降级 default
  （日志 attract72_p4_fingerprint_isolated）

**F2 KILL 秒级制动**
- 临时 `.env` 置 `ATTRACT72_KILL=1` + rebuild →
  点击/注册/下单主链全部正常，但 72 号三连零写入
  （归因 v1.0 侧不受 KILL 影响，照常工作）
- **测毕必须恢复 KILL=**（.env 原值空）并 rebuild

## G. 码文本投放形态对照（小程序/社媒）

**G1 输码注册**
- 用户在小程序注册时手输推广码 {CODE} 绑定
  → promotion relation 成立（不产生 /r/ 点击流——
  此形态无 clickId 归因属**设计内**，观测上走 promotion 侧）

**G2 直达链接为投放入口主推**
- 分享文案 direct 渠道即直达链接形态；其余渠道为码文本。
  两种形态矩阵关系等价，**引流漏斗观测（72 号）仅直达链接形态可采**

## 验收红线（全链一图）

```
领码(claim) → /r/ZXBJ-xxx 点击(真实IP+P1快照+P4变体)
  → 302 注册落地(hash路由+v72横幅) → 注册(归因四件套+72记忆)
  → 下单(自动回写 orderId/金额+conversions) → 推广人业绩/佣金
```

任一环节数据缺失即该用例 FAIL；全过 = ZXBJ 码可正式投放。

## 测试数据清理提示

- 测试注册的 139xxxx 会员与 relation/奖励留痕建议运营侧标记
  （生产无删除端点——设计内审计留痕）；冒烟归因记录
  （如 ORD-SMOKE-*）可辨识勿复用其 clickId。
