"""调用千问 max 联合检测 72号 AI 智能引流工作状况

输入: 生产取证(硬编码于脚本——redis 13 表存量/v1.0 流量
法证/model_status/日志零活动)
输出: /tmp/attract72_diag_report.md 结构化检测报告
模型: qwen3-max -> qwen-max -> qwen-plus 逐候选回退
"""
import json
import urllib.request

# 从生产 .env 取 DashScope key
key = ""
with open("/opt/zhuxiang/.env", encoding="utf-8") as f:
    for line in f:
        if line.startswith("DASHSCOPE_API_KEY="):
            key = line.split("=", 1)[1].strip()
            break
assert key, "DASHSCOPE_API_KEY not found"

ARCH = """# 72号 AI 智能自动引流大模型 · 系统设计与取证上下文

## 设计架构(代码侧调研实证)
- P1 感知跃迁: 渠道人格画像(品鉴/分享/优惠/新晋四型+信任分)、
  意图快照(点击→词表密度)、外部信号总线
- P2 因果认知: 反事实对照推理、定律结晶(→46号审批→57号知识库)、
  定律生命周期衰减
- P3 预算预判: 72h 渠道预分配、偏差>15% 重博弈、探索基金 5%-15%
- P4 短链记忆: ctx 编解码、动态落地页变体(default/trust_first/
  benefit_first)、跨会话指纹记忆、反作弊隔离
- P5 热点卡位: 雷达事件×四维势能 → chase/observe/reject 裁决
- P6 元认知治理: 健康度三指标(越界自动冻结)、红队四向量、
  沙箱实验、进化日志

## 引流链路设计意图
v1.0 attract 模块(/r/{code} 短链跳转 → 302 落地页带 clickId →
/api/attract/attach 注册归并 → /api/attract/attach-order 下单
回写)之上, 72 号叠加: P1 同步画像 → P4 动态落地页+设备记忆 →
终点=注册归并+下单回写(转化漏斗归因)

## 关键代码事实
- 灰度: ATTRACT72_MODE 四档(生产 env=assist), ATTRACT72_KILL
  秒级制动(当前 False)
- 零 LLM 调用(全模块铁律"LLM 禁入判定链")——自然语言查询为
  关键词路由+模板插值 engine="deterministic-template"
- 无专属调度器(main.py 调度器清单无 attract72)
- 前端零引用(frontend 无任何 attract72 调用)
- v1.0 attract_routes.py 后端代码零引用 attract72(grep 实证)
- 40 个端点全部需 X-Role: admin, 无公开观测面"""

EVIDENCE = """# 生产取证(2026-09-26 容器内直调+redis 全扫)

## 72 号侧(13 表存量)
- personas 12 / intent_snapshots 0 / external_signals 5
- causal_insights 7 / growth_laws 0 / budget_forecasts 1
- device_memory 2 / landing_variants 2 / hotspot_decisions 3
- experiments 1 / meta_health 4 / redteam 2 / mode_state 0
- model_status: mode=assist, kill=False, frozen=False,
  健康检查最后一次 2026-09-14 verdict=healthy 但
  insufficient=[diversity, match, mape](样本量<5 最低线)
- 红队最后一次 2026-09-14: 4/4 防御全过(RT-01 刷量注入/
  RT-02 归因投毒/RT-03 博弈操纵/RT-04 人格漂移), 攻击样本
  clickId=999999/999997/999996——均为自测痕迹
- docker logs 72h: attract72 相关日志 0 条; 当前容器实例
  全量 grep 也是 0 条

## v1.0 attract 侧(流量法证)
- 短链 197 条(全 A-开头活动码, createdAt 分布 09-15/09-20/
  09-21 持续新建), 落地页全为 /pages/activity/index
- 点击 1667 次: 唯一 IP 数=1(172.18.0.1 Docker 网桥——nginx
  反代后端取的 request.client.host, 真实访客 IP 丢失)
- UA 分布: PetalBot(华为花瓣搜索爬虫) 1328 + AppleWebKit
  爬虫 199 + SERankingBacklink 爬虫 138 = 1665(99.88%);
  真人浏览器 UA 仅 2 次(Windows)
- referer: https://zyjiu.com/sitemap.xml——短链 URL 被写入
  sitemap, 搜索引擎爬虫轮番抓取
- converted=0, registered=0(1667 点击零转化零注册)
- utmSource/utmMedium 全空
- /api/attract/attach 与 attach-order: redis 归因类 key
  (*attribution*/*lead*/*attach*) 全部 0 条——注册归并
  链路从未被真实调用
- traffic 博主侧: influencer 3 个、influencer_code 102 条
- promo 侧: hotspots 92 / decisions 73 / contents 5

## 时间线
- 72 号全部数据痕迹(personas/insights/红队/健康检查)
  停留在 2026-09-14 及之前——12 天零活动
- v1.0 短链 09-15 起持续新建(09-21 最新), 点击 at 最新
  09-23(爬虫)"""

PRE_DIAG = """# 人工预诊结论(待你复核确认/否定/补充)

1. 【断链定性】72 号是"孤岛系统": v1.0 后端零调用+前端零引用+
   无调度器——设计的引流增强链路(P1 画像同步/P4 动态落地页)
   从未接入真实流量; 现有 12 画像/7 洞察/2 记忆全部为 09-14
   前测试数据
2. 【流量定性】v1.0 的 1667 次"点击"99.88% 是搜索引擎爬虫抓
   sitemap 里的短链——真实引流效果≈零(真人点击仅 ~2 次且
   零注册零转化, attach 归并端点从未被调用)
3. 【采集缺陷】点击 IP 全为 172.18.0.1(nginx 反代未传
   X-Forwarded-For)——即使将来有真人流量, 反作弊(P4 指纹
   隔离)与归因质量也会因 IP 维度退化
4. 【名实问题】"AI 智能引流"实为确定性公式系统(零 LLM),
   "智能"体现在反事实推理/势能打分等算法层而非大模型层
5. 【健康度悖论】model_status verdict=healthy 但
   insufficient 三指标样本不足——健康是"没数据所以没病",
   不是"验证过真的健康"
6. 【潜在风险】短链进 sitemap 被爬虫狂刷点击计数, 会污染
   v1.0 归因数据底座与 72 号未来 P1 画像素材(若接入)"""

PROMPT = ARCH + "\n\n" + EVIDENCE + "\n\n" + PRE_DIAG + """

---

# 你的任务: 资深增长系统架构专家做引流效果联合检测

输出:
1. 【总裁定】72 号是否真正正常工作? 是否实现引流? 用一句话
   给出可执行的结论(给站长看的判定)
2. 【证据链复核】逐条确认/否定/修正人工预诊 6 条(引用取证
   数据), 指出我遗漏的证据盲区(若有)
3. 【断点地图】从"流量进站"到"注册转化"画出实际断点位置
   (哪个环节断了/哪个环节在空转), 标注每个断点的证据
4. 【修复优先级】P0/P1/P2 三级: 每项给改法要点+预期收益+
   风险; 特别回答——72 号六层架构(P1-P6)哪些层在当前
   流量规模下是超前建设(可冻结), 哪些是真实堵点(必须先通)
5. 【效果预期】若只修 P0, 预计多久能拿到第一份真实引流
   转化数据; 什么条件下才值得把 ATTRACT72_MODE 升到 full

要求: 直接引用取证数据; 判断到具体模块; 不泛泛而谈;
中文输出。"""

req_body = {
    "model": "",
    "messages": [
        {"role": "system", "content":
            "你是资深增长系统/Growth Hacking 架构专家, 精通"
            "引流归因链路、反作弊风控、灰度发布治理。基于生产"
            "取证数据做严谨判定, 不臆测。"},
        {"role": "user", "content": PROMPT},
    ],
    "temperature": 0.3,
    "max_tokens": 8192,
}
url = ("https://dashscope.aliyuncs.com/compatible-mode/v1"
       "/chat/completions")
candidates = ["qwen3-max", "qwen-max", "qwen-plus"]
last_err = ""
for m in candidates:
    req_body["model"] = m
    r = urllib.request.Request(
        url, data=json.dumps(req_body).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(r, timeout=300) as resp:
            out = json.loads(resp.read().decode("utf-8"))
        text = out["choices"][0]["message"]["content"]
        usage = out.get("usage", {})
        print(f"===== model={m} ok "
              f"tokens={usage.get('total_tokens')} =====\n")
        print(text)
        with open("/tmp/attract72_diag_report.md", "w",
                  encoding="utf-8") as f:
            f.write(
                "# 72号 AI 智能引流联合检测报告"
                f"(千问 {m}, 2026-09-26 生产取证)\n\n")
            f.write(text)
        break
    except Exception as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "ignore")[:300]
        except Exception:
            pass
        last_err = f"{m}: {e} {body}"
        print(f"[fallback] {last_err}")
else:
    raise SystemExit("all candidates failed: " + last_err)
