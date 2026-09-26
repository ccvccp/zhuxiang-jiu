# 微博 access_token 获取步骤（36 号 P2 真发布最后一公里）

目标：拿到可调用 `POST https://api.weibo.com/2/statuses/share.json`
（发布一条微博）的 access_token，填入生产 `.env` 的
`PROMO_CHANNEL_WEIBO_KEY`——36 号灰度发布即从
`mock_fallback` 切为真实发布。

> 代码侧已全部就绪：share.json 端点与表单协议已实测校准
> （promo_channel_service.py:44-53，403 鉴权拦截证明协议正确），
> token 到位零代码改动。预计全程 20-40 分钟。

---

## 步骤 1 · 注册微博开放平台开发者（约 5 分钟）

1. 用**打算作为发布主体的微博账号**（建议官方/运营号）
   登录 https://open.weibo.com
2. 进入「微连接」→「立即接入」→ 完成开发者实名
   （个人开发者即可，无需企业认证）
3. 实名审核通常即时或几分钟内通过

> ⚠️ 重要：微博授权后 **token 属于授权者账号**——用哪个
> 微博号授权，36 号就会用哪个号发微博。请用官方运营号操作。

## 步骤 2 · 创建应用，拿 App Key / Secret（约 5 分钟）

1. 「微连接」→「创建应用」→ 选择 **网页应用（Web）**
2. 填写：
   - 应用名称：如「竹香酒官方推广」（随意，审核前仅自见）
   - 应用地址：`https://zxjiu.com`
   - 回调地址：`https://zxjiu.com/weibo-callback`
     （占位即可，OAuth 换 token 需与之一致，无需真实存在页面）
3. 创建后进入「应用信息」→ 记下：
   - **App Key**（即 client_id）
   - **App Secret**（即 client_secret）

> 未提交审核的应用有"测试用户"限制：仅应用创建者本人的
> UID 在白名单内可授权成功——**对灰度正好够用**（发布主体
> 就是创建者账号）。若日后要多账号运营再走应用审核。

## 步骤 3 · 浏览器授权，拿 code（约 2 分钟）

浏览器直接打开（替换 YOUR_APP_KEY）：

```
https://api.weibo.com/oauth2/authorize?client_id=YOUR_APP_KEY&response_type=code&redirect_uri=https%3A%2F%2Fzxjiu.com%2Fweibo-callback
```

1. 页面显示授权确认 → 点「授权」
2. 浏览器跳转到 `https://zxjiu.com/weibo-callback?code=XXXXXXXX`
   （页面 404 无所谓，**只取地址栏里的 code 参数值**）
3. code 有效期约 10 分钟，立即进行步骤 4

## 步骤 4 · code 换 access_token（curl/浏览器均可）

在生产服务器执行（或任何有外网的机器）：

```bash
curl -X POST https://api.weibo.com/oauth2/access_token \
  -d "client_id=YOUR_APP_KEY" \
  -d "client_secret=YOUR_APP_SECRET" \
  -d "grant_type=authorization_code" \
  -d "code=步骤3拿到的CODE" \
  -d "redirect_uri=https://zxjiu.com/weibo-callback"
```

成功响应：

```json
{"access_token": "2.00xxxxxx_xxxxxxxx",
 "remind_in": 157679999, "expires_in": 157680000,
 "uid": "1234567890", "isRealName": "true"}
```

记下：
- **access_token**（填 .env 用）
- **expires_in**（秒；个人 token 通常约 5 年有效
  ——微博现行个人授权长期 token；若返回较短有效期，
  过期后需重新走步骤 3-4）
- uid（发布主体的微博 UID）

## 步骤 5 · 验证 token：发一条测试微博

```bash
curl -X POST https://api.weibo.com/2/statuses/share.json \
  -d "access_token=你的TOKEN" \
  --data-urlencode "status=竹香酒发布通道测试(可删除) 🎋"
```

- 成功：返回 `{"idstr": "...", "text": "..."}`——**该条微博
  已真实出现在发布主体账号上**（可去微博 App 删除）
- 失败对照：
  - `21301 auth by Null spi!` → token 无效/过期，回步骤 3
  - `21332 authentication data error` → 应用信息不匹配
  - `test users over limit` → 非测试用户授权（未审核应用
    限制，确认授权账号=应用创建者）

## 步骤 6 · 填入生产并生效

```bash
# 生产服务器
sed -i 's/^PROMO_CHANNEL_WEIBO_KEY=.*/PROMO_CHANNEL_WEIBO_KEY=你的TOKEN/' /opt/zhuxiang/.env
cd /opt/zhuxiang && docker compose up -d --build backend
```

验证（容器内）：

```bash
docker exec zhuxiang-backend-1 python -c \
  "from services.promo_channel_service import PromoChannelService; print(PromoChannelService().channel_status()[3])"
# 预期 weibo 行: keyConfigured=True, effectiveMode=real
```

之后 36 号发布队列中的内容（人工 review 通过的）将
**真实发布到微博**。

---

## 安全与回滚

- **token 即发布凭证**：勿入 git/日志；.env 已在服务器
  权限保护内
- **回滚**（出现异常内容/风控）：

```bash
# 秒级退回 mock(不发布)
sed -i 's/^PROMO_CHANNEL_MODE=.*/PROMO_CHANNEL_MODE=mock/' /opt/zhuxiang/.env
cd /opt/zhuxiang && docker compose up -d --build backend
```

- **内容防线不变**：灰度三闸门（日上限 5 + 强制人工
  review 101 阈值 + 合规词过滤）与 token 无关，持续生效
- **token 过期观测**：发布回执 `error` 字段会带微博真实
  报错（promo_channel_real_failed 日志），见错续期即可

## 附：36 号接入点速查

| 项 | 值 |
|---|---|
| .env 变量 | `PROMO_CHANNEL_WEIBO_KEY` |
| 端点 | `https://api.weibo.com/2/statuses/share.json`（代码默认，无需配置 URL） |
| 鉴权风格 | 表单 `access_token` + `status`（≤1000 字） |
| 发布上限 | 单日全平台 5 条（PROMO_DAILY_CAP=5） |
| 审核状态 | 全量强制人工 review（PROMO_COMPLIANCE_PASS_SCORE=101） |
| 回退行为 | token 失效→`mock_fallback` 回执带真实报错，不炸主链 |
