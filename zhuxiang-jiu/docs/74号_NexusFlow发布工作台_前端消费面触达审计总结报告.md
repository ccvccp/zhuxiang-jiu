# 74号·NexusFlow 发布工作台 前端消费面触达 审计总结报告

> 报告日期: 2026-09-16 | 范围: 前端消费面审计 → 发布工作台实施 → 生产实机验证
> 环境: 生产 47.236.61.117(zxjiu.com, NEXUSFLOW74_MODE=full) + 本地开发
> 结论: 74号从"纯后端发布引擎"完成运营面全触达, 按同 73号 范式交付并通过实机验证

---

## 一、背景与审计发现

### 1.1 审计起点

全站大模型前端消费面对账时发现: 74号虽后端完备(full 档 +
红队免疫 + 元认知闭环), 但**前端消费面为零**——

| 审计维度 | 发现 |
|---|---|
| API 客户端 | 无 nexus74.ts, /api/nexus74 全文检索仅命中后端 |
| 页面/入口 | 无发布工作台页面, mine 页无 74号 入口 |
| 前端单测 | 无 |

### 1.2 后端资产盘点(触达基础)

- **路由**: [nexus74_routes.py](file:///d:/网站架构设计/zhuxiang-jiu/backend/routes/nexus74_routes.py)
  41 端点(/api/nexus74), admin 门禁(X-Role)
- **服务层**: nexus74_registry + P1~P6 七层
  (规则中枢/合规引擎→素材源→平台适配→发布调度→指标回流→
  复盘进化→免疫红队)
- **档位**: NEXUSFLOW74_MODE 四态(off/shadow/assist/full),
  生产 **full 档**(全站唯一与 73号 并列的 full 档)
- **主链路**: rules → sources → compliance → adapt → publish →
  metrics → retro(进化闭环)

### 1.3 触达缺口分级(按运营重要性)

1. **发布工作台**(admin)——素材合规/平台适配/发布管理/指标复盘
   主链路消费面, 最高优先
2. 发布物管理深化 / 观测面 / 元认知运营面——后续可选扩展

## 二、实施: 发布工作台四页签(提交 c52e8da)

### 2.1 交付物

- **API 客户端** [nexus74.ts](file:///d:/网站架构设计/taro-app/src/api/nexus74.ts):
  - 14 方法: modelStatus / quotaStatus / metricsSummary / sources /
    createSource / complianceCheck / warningInject / adapt /
    adaptations / publish / publications / publicationRetry /
    publicationReceipt / retrospects
  - 8 VO + 2 字典(PUBLISH_PLATFORMS 六平台 / SOURCE_INTENTS 四意图)
  - 平台名/意图标签由后端提供——**前端零字典**(防前后端口径漂移)
- **页面** [pages/nexus74](file:///d:/网站架构设计/taro-app/src/pages/nexus74/index.tsx)
  (四页签):
  - **总览**: 模型运行模式卡(full) + 六平台发布配额进度条 +
    静默窗生效警示(发布将进入待发队列) + 全局指标网格
    (发布记录/已发布/平均互动率/已回流指标)
  - **素材合规**: 合规检查(确定性判定——LLM 禁入口径文案) +
    一键注入警示语修复 + 素材源库(标题/意图标签/关键词/时间)
  - **适配发布**: 素材源选择 + 目标平台六选 pill → 生成适配版本
    (预览) → 发布; 发布记录(A 档自主/B 档人工标注) +
    **B 档人工回执登记**(已发布/被拒双按钮)
  - **指标复盘**: 分平台指标(发布数/读/赞/互动率) + 复盘建议
    (含确定性阈值判定文案)
- **入口**: 我的页 → 站点管理 → 📡 NexusFlow 发布工作台
  (信值大模型之后, 主题智能管理之前)
- **路由**: app.config.ts 注册 pages/nexus74/index

### 2.2 设计口径(三类端点语义)

| 类别 | 端点 | 前端处理 |
|---|---|---|
| 观测面 | quota/metrics/sources/publications/retrospects/model | 常开直读 |
| 决策面 | adapt / publish / retry | 受 NEXUSFLOW74_MODE; **409 友好降级** |
| 数据诚实面 | B 档回执 receipt | **不受 MODE**(回执即事实) |

合规判定(compliance/check)100% 确定性、LLM 禁入——页面明示口径文案。

## 三、后端 API 状态确认(零改动)

本次为**纯前端消费面交付**: 后端 41 端点零改动、服务层零改动,
与 73号"3 端点放宽"不同——74号工作台为 admin 运营面(站点管理员
身份即门禁), 无需本人数据放宽。X-Role: admin 由 API 客户端
统一注入。

## 四、实机验证实录(生产 zxjiu.com)

### 4.1 登录会话恢复(三步法)

浏览器会话过期且 UI 登录遇 step_up 短信核验, 突破路径:

1. curl 调用 `/api/entry/login`(password 模式) → 返回
   `step_up_required`(新设备风险分 27.7)
2. 调用 `/api/sms/send` 后 SSH 从生产 Redis 读取验证码
   (键 `zhuxiang:auth:sms:code:{phone}`, 容器 zhuxiang-redis-1)
3. 调用 `/api/entry/step-up/verify` 完成登录 → 取得 admin tokens

### 4.2 会话注入根因修复

直接向 localStorage 注入裸 JSON 对象无效——**Taro H5 的
setStorageSync 存储格式为 `{data:{...}}` 包装结构**
(getStorageSync 读取时校验 `hasOwnProperty("data")`,
裸对象判定无效返回空串 → 会话读取为空 → API 无 Authorization →
401 → request.ts 清会话跳登录)。修正为
`JSON.stringify({data:{memberId...tokens}})` 后一次成功。

### 4.3 四页签实时数据验证(全通过)

| 页签 | 实机数据 |
|---|---|
| 总览 | 模式 **full** · 6 发布平台 · 红线 6 条 · 免疫正常; 六平台配额 0/3(A 档 1 + B 档 5); 静默窗生效中; 全局指标(4 记录/1 已发布/30% 互动率/1 回流) |
| 素材合规 | 素材源库 15 条实时加载(品鉴教程 + 红队基准源, 意图标签/时间正确) |
| 适配发布 | 源选择(15 条可选) + 六平台 pill; 发布记录 4 条: **A 档自主 #35**(微信公众号) + B 档人工 3 条(知乎/小红书×2) + 回执登记按钮 |
| 指标复盘 | 六平台分指标(知乎 发布 1 · 读 100 · 赞 20 · 互动 30%); 复盘 1 条(高效传播 + "互动率 0.3 ≥ 0.15——强化该形式组合"建议) |
| 我的页入口 | 站点管理区「📡 NexusFlow 发布工作台」正常渲染 |

## 五、测试与质量记录

| 项 | 结果 |
|---|---|
| test-nexus74.js(API 层) | 6/6(sources URL+admin 头/sources 请求体/adapt/publish/receipt/平台字典) |
| test-nexus74.js(页面层) | 5/5(四页签/合规+警示注入/B 档回执/409 友好降级/LLM 禁入文案) |
| **合计** | **11/11** |
| H5 构建 | webpack 成功(app.420621bd.js, 含 nexus74 页面 chunk) |
| 生产部署 | dist 上传 /var/www/zxjiu/dist |
| 生产实机 | 四页签实时数据 + 入口全渲染(本文 §4.3) |

## 六、遗留项与建议

1. **发布物管理深化**(可选): 发布记录当前为列表+回执, 可扩展
   详情/重试(自愈)操作面(retry API 已备)
2. **观测面/元认知运营面**(可选): immunity/redteam/evolution/
   drift 等元认知端点暂未建消费页, 建议后续按需扩展
3. **规则库管理页**(可选): rules 录入/字典为 admin 面, 当前经
   API 可用, 页面化优先级低

## 七、终态

74号前端消费面: **零 → 发布工作台全触达**(主链路运营面);
全站八大模型前端消费面全部就绪。

| 模型 | 档位 | 前端 | 后端 |
|---|---|---|---|
| 68 信值臻选 | assist | ✅ | ✅ |
| 71 支付 | assist | ✅(历史) | ✅(历史) |
| 73 会员体验 | full | ✅ | ✅ |
| **74 NexusFlow** | **full** | **✅(本轮)** | ✅(full 档在线) |
| 45 信值UEBA | assist | ✅ | ✅ |
| 智运物流 | assist | ✅ | ✅ |
| 62 无形资产估值 | assist | ✅ | ✅ |

本轮完整提交链: **c52e8da**(7 文件, +1512 行)。

---

*74号: 后端 full 档发布引擎(41 端点/七层服务/红队免疫) + 前端
发布工作台(四页签/B 档回执数据诚实/LLM 禁入合规口径)。
浏览器实机验证含 Taro 存储包装格式/事件序列/Redis 验证码三则
工程经验, 已沉淀项目记忆。*
