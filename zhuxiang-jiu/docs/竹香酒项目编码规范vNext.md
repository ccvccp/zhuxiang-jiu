# 竹香酒项目编码规范 vNext

> 版本: 2026-10-19 · 由代码库逆向提取 + 当日 24 轮线上修改史提炼
> 性质: 动态规范(含业务语义), 随演进持续修订; 新代码合入前逐条对照

## 0. 项目拓扑(一切规范的前提)

```
用户 ── zyjiu.com(海外 47.236.61.117, Linux/nginx)
   └── UA 分流: 手机→/index.html(商城首页) PC→/official.html(官网页)
用户 ── zxjiu.com(国内 ECS 8.147.66.237, Windows/nginx)
   └── /api → 本机 backend8147(nssm 常驻, 连本机 Redis, 与海外数据独立!)
静态: 两台各持 /var/www/zxjiu/dist 与 C:\www\dist 全量副本
后端: FastAPI(routes/services/repositories) + Redis(zhuxiang: 前缀)
SPA:  taro-app 打包产物 app.html(线上不可重建, 一律注入补丁)
```

**铁律**: 任何"一处修改"= 源码三副本(zhuxiang-jiu / taro-app/public / taro-app/dist)
+ 双服务器部署(海外 scp / ECS OSS+云助手) + 双 Redis 写入(海外 docker 与 ECS 本机是两个库!)
+ 双端验证。少一步即"双端漂移"(2026-10-19 全天事故的根源)。

## 1. 后端分层与命名

| 层 | 约定 | 示例 |
| --- | --- | --- |
| routes | `<域>_routes.py`, 只做参数校验/编排, 不写业务 | `product_routes.py` |
| services | 业务编排, 编号模块用 `<域><NN>_service.py` | `nexus74_p3_service.py` |
| repositories | 数据访问, 双态实现(内存/Redis) | `product_repository.py` |
| 存储选择 | `backend.py: is_redis_mode()` 动态判定, 禁止模块级冻结 | 见 backend.py NOTE |
| Key 构造 | 一律 `_k(entity, *parts)`, 禁止手拼 `"zhuxiang:"` | `_k("product", pid)` |
| 编号体系 | 业务模块带两位编号(73/74/84…), 环境开关 `<MOD>NN_MODE=full/shadow/assist/real` | `MEMBER73_MODE` |

## 2. 数据契约(血泪规范)

- **商品 product**: hash `zhuxiang:product:<ID>`; tags/scenes/attributes/images 为
  JSON 字符串字段; bool→1/0; 数字可字符串(由 `_deserialize_product` 还原)。
- **库存 inventory**: 独立 hash `zhuxiang:inventory:<ID>`(stock/reserved),
  绝不并入 product hash(InventoryRepository 注入)。
- **图片 URL 一律相对路径** `/images/products/x.jpg`——绝对域名在未备案侧
  HTTPS 被拦(2026-10-19 zxjiu.com 证书事故)。
- **改线上数据 = 改双端 Redis**(hset), 改代码 seed 只影响首次初始化;
  数据已存在时 seed 不生效, 必须 hset 同步。
- 新商品必须同时写: product hash(全字段) + inventory hash + 本地 seed(三处)。
- API 默认分页 pageSize=12, 验证全量一律 `page_size=50`。

## 3. 前端规范

- 静态站(zhuxiang-jiu/*.html + js/css): 改 js/css 必须**全站 v= 版本号升位**
  (nginx 30 天 immutable 缓存), 含无版本号引用; html 已 no-cache 免升位。
- **SPA(app.html)不可重建原则**: 线上 bundle 是历史构建产物(hash 文件名),
  修改走 app.html 注入补丁(样式 `<style>` + MutationObserver JS),
  已有先例: 官网按钮拦截器 v2 / 热销真图+详情 contain 修复。
- 注入补丁的 DOM 选择器用 `[class*="xxx"]` 通配(CSS module hash 类名逐版本变)。
- Taro H5 陷阱: Image 组件非 object-fit 实现, 而是
  `position:absolute+translate(-50%,-50%)+min-width/height 放大+溢出裁剪`;
  覆盖显示模式必须整组覆盖(static+transform:none+100%盒+object-fit)。
- SPA bundle 内字符串(如客服电话)可直接 sed 替换, 但引用必须加 `?v=` 破缓存。
- 首页(index/official)与 SPA 双入口的功能必须同源同图: 图片经
  `/api/product/list` 名称 token 匹配(matchMallImg), 兜底顺序: API 图→emoji→生成图。
- 交互统一: 商品卡"入车"写 localStorage(`zhuxiang_cart` / `allianceMall.cart`),
  卡片点击直达详情(SPA hash 路由 `#/pages/product-detail/index?id=` /
  同盟 `alliance-mall.html#shop=<mid>&pid=<pid>`)。

## 4. 图片与素材

- 外部图片生成 API 已退化为固定占位图——**禁止依赖**; 商品图用
  PIL 自绘(品牌规范: 米白底 CREAM(244,241,230)/深竹绿/金字)或实拍图处理。
- 实拍图处理: 逐行背景建模+软阈值抠图(参考 make_zx42_master.py),
  改字样须原位覆盖+采样原色重写, ASCII 放大校验字符完整后才可上线。
- 素材唯一入口: `d:\网站架构设计\商品设计素材\`(raw 实拍 / master 成品),
  文件名禁零宽字符与空格; 每次产出登记 `素材清单.md`。
- 图片缓存无 immutable, 但**新内容必须新文件名**(天然破缓存)。

## 5. 部署纪律(五步, 缺一不可)

1. 改本地源(三副本同步);
2. 打包: tgz 必须含目标相对路径前缀(如 `images/products/`), 解压落位才正确;
3. 海外 `scp` + 脚本(scp 脚本文件过去执行, 禁止内联 ssh 命令——PS 转义灾难);
   ECS: oss-put.ps1 上传 → 云助手 Send(记录真实 InvokeId, 禁止臆造);
4. 服务器本机改: 先备份(`*.bak-日期` / /tmp/*-bak-*), 后覆盖;
5. 验证: 本机 curl 带 `-H 'Host: <域>'`(不带 Host 落默认站点=误报 404),
   最终以**公网域名**为准。

## 6. 注释与"进化注释"

- 中文 docstring 讲三件事: 设计意图 / 兼容契约 / 为什么(历史背景)。
- **进化注释**(新增): 关键逻辑处标注踩坑来源, 格式
  `# [进化 2026-10-19] <结论>, 源自 <事故>: <一句教训>`,
  例: `# [进化 2026-10-19] 图片一律相对路径, 源自 zxjiu.com 证书事故: 绝对域名被拦`。
- 部署脚本头部注明用途+日期; 一次性脚本仍入 deploy/ 归档(过程产物 tgz 除外)。

## 7. 禁止事项

- 禁止在未取证双端状态前断言"已修复"(PC 海外/手机 ECS 数据源不同)。
- 禁止 PowerShell: 管道传 `tr -d "\r"`(反斜杠被吃→删字母)、here-string 缩进终止符、
  GBK 默认写文件(必须 `[Text.UTF8Encoding]::new($false)`)、`$var` 大小写混用。
- 云助手回传只认 Write-Host; python/外部命令输出先落文件再 Write-Host。
- 禁止把 AK/SK 写进任何文件(用户级环境变量 ALIYUN_AK_ID/SECRET)。
- git: 过程产物(报告/探针/tgz)入 .gitignore 不入库; commit 勿加 `2>&1`
  (pre-commit 钩子输出被重定向干扰判定)。
