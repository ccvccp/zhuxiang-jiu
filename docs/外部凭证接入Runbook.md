# 外部凭证接入 Runbook

> **版本**：v1.0（2026-10-12）
> **适用**：《P2剩余项外部依赖接入设计稿》批次②③五域 Mock 通道已全部合入（专项 test_p2_batch23 56/56）——本手册是凭证就绪后的**零代码切换操作规程**。
> **总原则**：所有域默认 `mock`；每域独立三态 `mock / mock_fallback / real`；凭证只经环境变量注入（模板见 `backend/.env.example`），绝不落仓库/日志；切换只改 env 重启生效，不改代码。

---

## 〇、通用流程（每域相同）

```
1. 申请凭证(见各域"申请入口") → 拿到变量值
2. 注入环境变量(部署平台密钥管理 / .env)——先不切开关(保持 mock)
3. 自检: python check_external_creds.py
        → 目标域 readiness=ready(必需项+回调令牌全 OK)
4. 灰度: {MODULE}_MODE=mock_fallback(真实调用失败自动降级 mock 留痕)
        → 观察日志 fallback 留痕频率; 连续稳定(建议 ≥ 24h 无降级)
5. 切换: {MODULE}_MODE=real → 执行该域"real 冒烟验收"
6. 回滚(任一环节异常): 开关切回 mock_fallback 或 mock 即回滚,
   业务数据无损(mock 与 real 落数据结构一致, 仅来源字段不同)
```

**自检工具**：`python check_external_creds.py`（文本报告）／`--json`（供流水线）；只显示 已配置/缺失，不打印任何凭证值。

**验收基线**：全量 pytest（5 failed/1610 passed/192 skipped，失败为既有 Redis/阿里云短信环境依赖）在任何切换后不回退——mock 轨测试与 env 无关。

---

## 一、微信支付 V3 / 支付宝（payment 既有，`PAY60_CHANNEL_MODE`）

| 项 | 内容 |
|---|---|
| 申请入口 | 微信商户平台 pay.weixin.qq.com（API 证书+序列号）；支付宝开放平台 open.alipay.com（应用私钥/公钥） |
| 变量 | `PAY60_CHANNEL_KEY`（主凭证，real 总开关）、`PAY60_WECHAT_MCHID/APPID/SERIAL_NO/PRIVATE_KEY`、`PAY60_ALIPAY_APPID/PRIVATE_KEY`、可选 `PAY60_NOTIFY_URL`（默认 https://zyjiu.com/api/payment/callback/pay） |
| 私钥格式 | 内联 PEM 或挂载文件路径（`_env_or_file` 自动识别） |
| 回调验签 | real/mock_fallback 强制（微信平台证书 GCM 解密 / 支付宝 RSA2），伪造回调 401 |
| real 冒烟 | `POST /api/payment/pay(wechat)` → payParams 含 jsapi 五元组/h5 链接；`(alipay, wap)` → payUrl 跳转 openapi.alipay.com |
| 特别注意 | 真实扣款渠道——**只在生产/沙箱环境验证**；本地用微信沙箱商户号 |

## 二、花呗分期（payment，复用 `PAY60_CHANNEL_MODE`）

| 项 | 内容 |
|---|---|
| 前置 | 支付宝商户号已开通「花呗分期」产品（当面付/手机网站支付产品详情页签约） |
| 变量 | 同支付宝（`PAY60_ALIPAY_APPID/PRIVATE_KEY`）——花呗借道支付宝通道**无单独凭证** |
| 灰度即渠道 | 管理端渠道动态配置注册 `huabei` + toggle 即灰度（未注册前端不展示，代码合入零影响） |
| real 冒烟 | `POST /api/payment/pay(huabei, installments=6)` → payUrl 含 `enable_pay_installment`；分期费率/每期还款额由渠道返回透传（后端不自算） |
| 回调 | 走支付宝既有验签通道，无新增配置 |

## 三、京东白条（payment，复用 `PAY60_CHANNEL_MODE`）

| 项 | 内容 |
|---|---|
| 申请入口 | 京东金融商户后台（需企业资质 + Baitiao 支付产品签约） |
| 变量 | `PAY60_BAITIAO_MERCHANT`、`PAY60_BAITIAO_KEY` |
| 未签约影响 | 零——不注册渠道即不展示，Gateway 仅 channel=baitiao 时实例化 |
| real 冒烟 | `POST /api/payment/pay(baitiao, installments=3)` → payUrl 跳转京东收银台 |
| 回调 | real 落地时补京东回调验签分支（设计稿 §3.4 预留，接入时一并实施） |

## 四、高德地图 LBS（location，`LOC_AMAP_MODE`）

| 项 | 内容 |
|---|---|
| 申请入口 | 高德开放平台 lbs.amap.com → 创建应用 → **Key 类型必须选「Web 服务」**（非 JS API）；建议企业认证提额（个人 5000 次/日/接口） |
| 变量 | `AMAP_KEY`（必需）、`AMAP_SECRET`（可选：数字签名，配 IP 白名单时建议开启） |
| 灰度顺序 | ① geocode 回填（低风险）→ ② polygon 围栏（本地计算不受影响）→ ③ 驾车路网距离（**影响运费最后开**） |
| real 冒烟 | `POST /api/location/address/add`（不带坐标）→ 返回 `geocodeSource=amap` 且真实坐标/adcode |
| 降级语义 | 编码失败不阻断（地址保存保持无坐标现状，`geocodeError` 留痕）；配额消耗已有 30 天缓存（`loc:geocode:cache:*`） |
| 回滚 | 切回 mock：编码走确定性坐标（本地/测试语义稳定） |

## 五、物流电子面单 + 理赔（logistics，`LGS_CARRIER_MODE`）

| 项 | 内容 |
|---|---|
| 申请入口 | 快递鸟 api.kdniao.com / 快递100 商家版（一份 Key 覆盖 SF/JD/YT/DB 电子面单+轨迹推送）；先谈商务（电子面单需各物流商月结协议） |
| 变量 | `LGS_KDN_EBUSINESS_ID`、`LGS_KDN_APP_KEY`、`LGS_CALLBACK_TOKEN`（回调验签密钥，需在聚合平台推送配置侧同值设置） |
| ⚠️ 计费红线 | **电子面单 real 出单即产生真实物流费用**——`useEwaybill=true` 仅生产环境开；测试环境强制 mock |
| 回调验签 | real/mock_fallback 强制 `X-Carrier-Signature`（HMAC-SHA256）；无签名/错签名 401；轨迹推送地址配 `/api/logistics/callback/track` |
| real 冒烟 | `POST /api/logistics/order(useEwaybill=true)` → 真实运单号 + `ewaybillSource=channel`；理赔打款走 payment payout 审批链无需单独凭证 |
| 灰度顺序 | ① 轨迹订阅推送（只读）→ ② 面单真实下单（单向）→ ③ 实时运费（本地费率表降级已在） |

## 六、NFC 防伪（trace，`TRACE_ANTI_MODE`）

| 项 | 内容 |
|---|---|
| 前置 | NFC 芯片选型（如 NXP NTAG 系列）+ 标签厂商发码系统对接（生产侧 `bind` 环节调用；消费侧碰一碰验真**无外部调用**） |
| 变量 | `TRACE_NFC_VENDOR_URL`（厂商发码 API 地址）、`TRACE_NFC_API_KEY` |
| real 冒烟 | `POST /api/trace/nfc/bind`（admin，不传 nfcUid）→ 返回厂商真实 UID |
| 存量兼容 | mock 绑定的 `NFCMOCK*` 标签切 real 后仍可 verify（绑定关系存本地仓库，与网关无关） |

## 七、区块链存证（location，`CHAIN_EVIDENCE_MODE`）

| 项 | 内容 |
|---|---|
| 申请入口 | 联盟链开放服务选型：BSN / 蚁链 / 至信链（HTTP API + AK/SK，按条计费约 ¥0.05~0.5/条；**不自建节点**） |
| 变量 | `CHAIN_NODE_URL`、`CHAIN_AK`、`CHAIN_SECRET`、可选 `CHAIN_CONTRACT`（存证合约，缺省 evidence） |
| 上链口径 | 哈希上链、原文留库（链上无用户明文）；台账先行闭合，上链 best-effort 失败不阻断，pending 由 `chain_pending_scan` 惰性重试 |
| real 冒烟 | `POST /api/location/blockchain/evidence` → `chainVendor=chain`，`chainStatus` 推进 pending→onchain（`txHash/blockHeight` 落台账） |
| 复用扩展 | trace 生命码流转 / points 大额发放后续复用同一 `ChainGatewayService`（存证类型枚举扩展，不重复接链） |

## 八、短剧研判引擎真实数据源（drama85，`DRAMA85_SOURCE_MODE`）

| 项 | 内容 |
|---|---|
| 供给方（二选一） | A. 企业开通的第三方短剧数据服务 API（商务采购）；B. **自建合规采集 worker**（另行部署，只采公开元数据/评论/字幕——不绕登录、不破接口、不采隐私的红线在 worker 侧执行），输出标准 JSON 契约 |
| 契约 | `GET {DRAMA85_SOURCE_URL}?offset=&n=`，Header `X-Api-Key`（+可选 `X-Timestamp`/`X-Source-Signature` HMAC-SHA256）；响应 `{"dramas": [{title/platforms/episodes/durationSec/tags/metrics{plays,likes,comments,shares,completionRate}/commentsData[]/subtitleText(四段标注) 或 structure 直传}], "hasMore"}`——完整定义见 `services/drama85_source_gateway.py` 模块注释 |
| 变量 | `DRAMA85_SOURCE_URL`、`DRAMA85_SOURCE_API_KEY`、可选 `DRAMA85_SOURCE_SECRET`（与供给方同值）、`DRAMA85_SOURCE_QPS`（默认 1 频控） |
| **第三方格式适配** | 响应非契约格式时配 `DRAMA85_SOURCE_FIELD_MAP`（JSON）：`list`=剧集数组点路径（如 `$.data.rows`，必需）+ 逐字段映射（`title/plays→playCnt/commentsData→hotComments` 等条目字段名或点路径）。auto 判定：响应含 `dramas` 键契约直用，否则按映射转换（映射 JSON 非法/缺 list+title 配置类错误直接抛不重试）。评论兼容三形态：字符串列表/`[{content}]`/标准对象。三种主流风格预置模板见 `.env.example` 第六节（A 嵌套 `data.rows`+驼峰 / B 嵌套 `data.list`+下划线 / C 顶层数组+短别名） |
| real 冒烟 | `POST /api/drama85/collect`（admin）→ 报告 `sourceChannel=real`，池内新记录 `sourceMode=real` |
| **悟空 worker（P1 先行，已落地）** | 供给方 B 的第一实例：`python drama85_wukong_worker.py`——**三形态**：json/html（`probe --gen-map` 自动推导 `WUKONG_FIELD_MAP` 草稿并试采验证）+ **share（分享链接→单剧元数据，真实链实测打通，三级解析 10-13 深夜升级）**：`WUKONG_SHARE_URLS=分享链接列表`（`.env.example` ★ 块实测配置+探针验证命令；App 分享→复制链接→追加）→ 逐链三级解析（①**RENDER_DATA SSR 优先**：seoTDK 剧名/tomatoPlayetInfo.serialCount 总集数/videoDuration 单集时长/content 剧情简介→subtitleText/seriesId 去重，当前集指标 videoPlayCount·diggCount 与出品方仅探针预览——集级口径不入契约 metrics；②正文正则回退：title/共X集/已完结状态/og:url itemId；**红果页**（hainanyuyue.com/s/ 域）自动次级形态：JSON 剧名+第N集当前集下界+题材标签+vid 去重，platforms=hongguo；移动 UA，指标留通道①）→ `fetch`/`serve` 输出契约。**列表页实测配置 ×2（10-13，`.env.example` ★ 块复制即用）**：①短剧工程榜单页（**数据源抖音官方短剧日榜 TOP10**，robots 允许，XPATH 解析剧名/体裁/热度值，platforms=douyin）；②shortdramawiki `/en/new/`（13 平台 3.2 万出海剧库每日重扫，poster-card 解析剧名/集数/平台名——platforms 取条目值；同机多数据源=多 worker 实例各配 env）。**合规红线内置**：只 GET 公开页/robots Disallow 一律拒采（share 单链网络失败跳过留痕、robots 拒采整批上抛）/QPS 限速/T+1 缓存/评论不采。数值容错：`1.2万`/`3,400`/`72集`/`63 eps`/`71`（>1 按百分数）。专项 test_drama85_wukong 62/62 |
| **悟空探针（候选 URL 判定）** | `python probe_wukong.py --url <候选> [--url ...]`（`--demo` 零网络自检）：robots → GET → 结构摘要+映射草稿+试采 → 人读结论（可粘贴配置或排除结论）；`/is/`、`/short_play/` 悟空分享链与 `hainanyuyue.com` 红果分享链自动切 share 预览。**首轮实测（10-13）**：`www/m.wkbrowser.com` 为纯 App 下载落地页、无剧集数据——悟空短剧在 App 内，待从 App 分享链拿真实 H5 URL 后再探 |
| **红果 worker（P1，已落地）** | `python drama85_hongguo_worker.py`——红果漫剧分享落地页 SSR 采集（`_ROUTER_DATA` JSON）：主剧（`serial_count` 总集数/`category_list` 题材/`series_play_cnt` 播放/`like_count` 点赞）+ `recommend_series` 推荐位 ~12 条同款指标（**通道② 首个带真实播放/点赞指标的源**，一条分享链=1 主剧+动态轮换小榜单=新剧发现入口）。env `HONGGUO_SHARE_URLS/QPS/CACHE_TTL/PLATFORMS/INCLUDE_RECOMMEND/PORT`（8511，与悟空 8510 并行；`INCLUDE_RECOMMEND=0` 仅主剧）；CLI fetch/probe/serve。红线同款：robots 拒采上抛/单链失败跳过留痕/限速/T+1/**评论与收藏不采**。真实链 13 条契约全指标验证（牢狱蒙尘 ep=100 plays=1991万 + 12 推荐），专项 test_drama85_hongguo 30/30；红果 web 无公开列表页（域名不存在/番茄纯小说），推荐位即唯一合规批量入口 |
| 降级语义 | `mock_fallback`：真实源失败/超时自动降级确定性 mock 并落 `fallback/fallbackReason`（采集不中断）；`real` 失败 fail-hard |
| 网关能力 | 3 次退避重试 + QPS 频控 + 字段容错（缺 title 丢弃、completionRate 越界 clamp）+ structure 直传优先于字幕标注解析 |
| ⚠️ 合规红线 | **不做平台爬虫**：绕登录/破解接口抓取第三方平台数据违反平台规则——数据获取一律由合规供给方（A/B 两类）承担，85号只消费契约 |

### 预置映射模板选型三步（未选型先占位，选定后微调）

1. **拿样例**：向第三方索取一份真实分页响应样例（或用其文档的响应示例），确认剧集数组所在层级与字段命名风格；
2. **对号入座**：`.env.example` 第六节三份模板按主流风格预置——
   | 模板 | 数组路径 | 字段风格 | 适用形态 |
   |---|---|---|---|
   | A | `$.data.rows` | 驼峰（dramaName/playCnt/finishRate） | 电商化数据服务（蝉妈妈/灰豚类） |
   | B | `$.data.list` | 下划线（drama_name/play_count） | 国内开放平台/Python 系服务 |
   | C | `$.list` | 短别名（name/pv/cmt_num） | 轻量聚合接口 |
3. **微调**：复制对应模板行到 `.env` 去掉行首 `#`，按样例改字段名即可（嵌套字段支持点路径如 `stats.play_count`；缺字段直接删该键，适配器容错跳过）。改后跑 `POST /api/drama85/collect`（admin）看日志 `drama85_field_map_applied rows=N` 确认生效。

---

## 附：当前状态与快捷操作

```powershell
# 凭证就绪度自检(不打印值)
cd backend; python check_external_creds.py

# 单域灰度(示例: 高德)
$env:LOC_AMAP_MODE="mock_fallback"; python main.py   # 观察日志 amap_fallback
$env:LOC_AMAP_MODE="real";        python main.py     # 稳定后切换

# 一键回滚(任一域异常)
$env:LOC_AMAP_MODE="mock"         # 或对应 {MODULE}_MODE
```

**当前基线（2026-10-12）**：七域全部 mock（0/7 就绪）——本地/CI 零凭证全绿；五域 Mock 通道专项 test_p2_batch23 56/56、全量 pytest 1610 passed 与基线一致。凭证逐域到位后按上表独立接入，互不阻塞。
