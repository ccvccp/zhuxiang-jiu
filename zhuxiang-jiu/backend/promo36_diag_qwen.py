"""调用千问 max 联合检测 36号 AI 智能推广工作状况

输入: 生产取证(硬编码)——调度器/数据存量/mock 回执/
LLM 轨迹/SEO 推送冲突
输出: /tmp/promo36_diag_report.md
"""
import json
import urllib.request

key = ""
with open("/opt/zhuxiang/.env", encoding="utf-8") as f:
    for line in f:
        if line.startswith("DASHSCOPE_API_KEY="):
            key = line.split("=", 1)[1].strip()
            break
assert key, "DASHSCOPE_API_KEY not found"

ARCH = """# 36号 AI 智能推广模块 · 设计与取证上下文

## 设计链路
热点雷达扫 5 平台热榜(baidu/douyin/weibo/zhihu/xiaohongshu)
→ 四维评分(0.4热度+0.2速度+0.3品牌相关+0.1持续)+风险词
一票否决+48h 指纹去重 → 决策三档(≥70 auto_engage/
50-70 manual_queue/<50 pass) → GLM Agent 四步链(热点分析/
受众匹配/生成/自查, glm-5.3→glm-4-flash→规则模板三级降级)
→ 三审闸门(规则硬拒/AI审60分线/人工review) → 挂 attract
归因短码 A-xxx → 入发布队列(黄金时段+日上限20+冷却2条/48h)
→ 发布(5 平台) → 效果经 attract 点击/注册/下单回流 →
Hedge/Bandit 进化层调雷达品类权重

## 生产配置(2026-09-26 实查)
- PROMO_RADAR_AUTO=on(15min 调度)/PROMO_PUBLISH_AUTO=on
  (5min)/PROMO_EVOLUTION_AUTO=off
- PROMO_CHANNEL_MODE=mock(五平台 KEY 已配但模式未开 real)
- HOTSPOT_*_API_KEY/URL 全空——热点走 mock 固定主题池
- BAIDU_PUSH 通道已配置(唯一真实对外路径)"""

EVIDENCE = """# 生产取证(2026-09-26 redis 全量+日志)

## 调度器活性
- 雷达/发布调度器启动日志正常(900s/300s); 72h 内 promo
  相关日志仅 5 条, 最新扫描 promo_radar_scan
  scanned=25 new=0 discarded=0 skipped=25(mock 池 48h
  指纹去重窗内全跳过)

## 数据存量
- 热点 200 条: 5 平台×40, 标题样本「中秋团圆宴白酒清单
  火了」「国风音乐节门票秒空」「某地地震最新救援进展」
  「周末露营野餐攻略」——全部 mock 固定池, 无真实热点
- 决策 160 条: auto_engage 115 / manual_queue 5 /
  pass 40; 最近决策 09-25(调度持续运转)
- 内容 11 条: published 7 / approved 2 / pending 2;
  标题高度重复(同一中秋主题); 合规分全 100, 含健康警示
- 内容短码链接 11 条(A- 码 09-11~09-16 生成)——attract
  侧 197 短链的来源之一
- 进化层: evo_log 11 / evo_bandit 1 / cat_stats /
  style_stats——在零真实流量下已产生学习记录

## 发布真实性(核心证据)
- 最新 published 内容回执: {"mode": "mock",
  "platform": "weibo", "publishId": "PUB-weibo-5",
  "exposureEstimate": 1483500}——**mock 回执+曝光量
  148 万为确定性模拟数字**, 内容从未发到任何平台
- agentTrace: step1Analysis/step2Audience/
  step3Generate/step4SelfCheck 全部 = "rule"——
  **LLM 四步链从未启用**, 11 条内容全部规则模板生成
  (三级降级直接落底)
- 唯一真实对外: 百度 SEO 收录推送 10 次(1 次失败),
  推送 URL 形态 = sitemap.xml + /r/A-xxx 短链

## 效果侧关联证据(同日 72 号引流联合检测结论)
- attract 197 短链 1667 次点击: 99.88% 为搜索引擎爬虫
  (PetalBot 1328 等, referer=sitemap), 真人点击≈2 次
- converted=0 / registered=0——**推广内容的真实转化
  为零**; 平台报表口径(report_platform 的 clicks/gmv/
  gmvPerClick)在当前数据下无真实流量支撑

## 修复联动冲突(今日 P0 修复误伤)
- 上午 P0 修复(72 号检测衍生): sitemap 摘除短链 +
  robots.txt 加 Disallow: /r/——但 36 号百度推送的
  URL 恰是 /r/A-xxx: 主动推送(收录信号)与 robots
  禁抓(拒抓指令)冲突, **36 号唯一真实对外通道被
  robots 规则压制"""

PRE_DIAG = """# 人工预诊结论(待你复核确认/否定/补充)

1. 【空转定性】36 号在生产处于"全 mock 自动空转":
   调度器活着(雷达 15min/发布 5min)但雷达扫固定池、
   发布留 mock 回执、曝光数字(148 万)为模板模拟值
   ——对外真实触达=0, 仅百度推送 10 次有真实动作
2. 【名实问题】"AI 智能推广"的 LLM 四步链从未启用
   (agentTrace 全 rule)——内容为确定性模板; mock 热点
   +规则生成+mock 发布=完整仿真的"推广数字工厂",
   但不产生真实推广
3. 【冲突误伤】robots Disallow: /r/ 压制了百度推送
   通道——P0 修复(防爬虫刷点击)与 36 号 SEO 策略
   (推短链)方向相反, 需要精细方案(如 User-agent:
   Baiduspider 专属放行)而非一刀切
4. 【数据风险】进化层(Bandit/品类权重)已在零真实
   流量下产生学习记录——mock 数据喂养的权重会污染
   未来真实决策; exposureEstimate 148 万类模拟数字
   若入运营报表会造成虚假繁荣
5. 【链路成熟度】代码侧链路完备(雷达→决策→内容→
   审核→发布→回流→进化), 但每一环的前置依赖
   (真实热榜 API/平台发布凭证/LLM key/真实流量)
   均未满足——是"管道建好无水"而非"管道破损\""""

PROMPT = ARCH + "\n\n" + EVIDENCE + "\n\n" + PRE_DIAG + """

---

# 你的任务: 资深增长营销系统专家做 36 号推广效果联合检测

输出:
1. 【总裁定】36 号是否真正正常工作? 实现推广了吗?
   一句话可执行结论(给站长)
2. 【证据链复核】预诊 5 条逐条确认/否定/修正
   (引用取证数据), 指出遗漏盲区
3. 【空转地图】各环节(雷达/决策/内容/发布/回流/
   进化)标注: 真实运转/mock 空转/未启用, 各配证据
4. 【修复优先级】P0/P1/P2: 每项改法+预期收益+风险;
   特别回答: (a) robots/百度推送冲突的最优解;
   (b) 前置依赖(热榜API/平台凭证/LLM)哪些值得
   投入、哪些可以放弃(用确定性替代); (c) 进化层
   在 mock 数据下的污染如何止损
5. 【效果预期】按你建议的 P0 修复后, 什么信号出现
   才算"36 号真正开始工作"? 给可观测的验收判据
   (如: 真实热点占比/LLM 轨迹启用率/真实点击>0 等)

要求: 引用取证数据; 判断到具体模块; 不泛泛而谈;
中文输出。"""

req_body = {
    "model": "",
    "messages": [
        {"role": "system", "content":
            "你是资深增长营销系统架构专家, 精通内容营销"
            "自动化、SEO/百度收录机制、灰度发布治理。基于"
            "生产取证数据做严谨判定, 不臆测。"},
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
              f"tokens={usage.get('total_tokens')} =====")
        print()
        print(text)
        with open("/tmp/promo36_diag_report.md", "w",
                  encoding="utf-8") as f:
            f.write(
                "# 36号 AI 智能推广联合检测报告"
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
