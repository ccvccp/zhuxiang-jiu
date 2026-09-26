"""Qwen-3.8Max 语音系统健康复查(修复史+遗留风险模式)

背景: 容器 rebuild 清了 36h 日志(仅 2 样本), 日志驱动的
诊断无数据基础——改为"已知修复史+当前零告警状态+遗留
观察项"的风险预判复查, 输出下一步复测优先级。
"""
import json
import os
import urllib.request

key = os.environ.get("DASHSCOPE_API_KEY", "")
if not key:
    with open("/opt/zhuxiang/.env", encoding="utf-8") as f:
        for line in f:
            if line.startswith("DASHSCOPE_API_KEY="):
                key = line.split("=", 1)[1].strip()
                break

with open("/tmp/voice_diag.json", encoding="utf-8") as f:
    diag = json.load(f)

FIXED = """# v76~v86 修复史(本轮迭代全量)
- v76 连接自愈: 切后台 1006 后 visibilitychange 三探活(ctx/流/连接)
- v77 TTS: 短块合并(≤6字并前块)/块级400ms重试/错峰/KWS段长3s
- v78→v79: 串行链 X5 fetch 挂起 10s → 回退 forEach 并发+错峰800ms
- v80: 度数意向过滤(extract_abv+接近排序)/member窗内直接说
  (意图白名单弹面板直达首轮)
- v81: 商品定位前置(问价/加购 miss 明确回答, 推荐指名直入,
  LLM 轨全站事实注入防编造)
- v82: 影子状态机(SM 五态+7转换点+ILLEGAL留痕)/打断埋点
  (cut_after_ms)/PROCESSING 15s 看门狗/五色状态徽标
- v83: 查一款句式入 rule 轨/LLM 虚假执行守卫(已加→拦截)
- v84: 面板哑流 AHM 自愈(近零能量 3s → 硬重置 30s 退避)
- v85: 工具样例注入 LLM 分类/traceId 贯穿/会话偏好记忆注入
- v86: 语气词前置防御(不烧 LLM)/钩子 800ms 熔断/
  42度 UnboundLocalError 修复(ina 命中分支漏赋值——
  生产 42 度指令自 v80 起静默失败, 复测脚本首跑抓出)

# 当前状态
- 容器 rebuild 后零 WARNING/ERROR(新日志窗口)
- 回归: 9 套 300+ 项全绿; 42度专项 16 项全绿
- BOM: 容器内 0 个(两 xx64 文件双层叠加已剥净重建)

# 遗留观察项(未闭环)
1. 影子状态机观察期数据未收集(ILLEGAL 转换频率未知,
   收紧为硬守卫的时机未定)
2. 打断 SLA 基线未收(cut_after_ms 无实测数据,
   文档口径<200ms 受百炼 partial 回流约束)
3. 面板哑流自愈(v84)未经真机复验(部署后无测试流量)
4. 跨会话会员偏好记忆(LLM 轨)仅会话级, 跨 session 版
   需 repo 聚合查询(优化池)
5. LLM 轨拼接污染根因未深究(v83 pattern 兜住主路径,
   但 LLM 上下文注入的"竹海至尊您好"类拼离开场白
   的深层 prompt 问题未定位)
6. 面板 REST 轨空轮二打(弱音频轮)未做"""

PROMPT = """你是资深语音系统专家。基于修复史与遗留项做风险复查:

# 当前观测数据(新容器窗口, 数据少)
""" + json.dumps(diag.get("summary", {}),
                 ensure_ascii=False, indent=1) + """

""" + FIXED + """

# 任务
1. 【风险预判】遗留 6 项中, 哪些最可能在下次真机复测暴露?
   按 概率×影响 排序
2. 【复测优先级】给出真机复测的最小话术集(10 条内),
   每条标注目标遗留项
3. 【潜伏 bug 扫描】基于修复史模式(如 42 度 bug =
   verify 单路径漏分支), 指出 2~3 个可能存在的同类
   "单路径验证盲区"(如: 44 度跨款边界/指名+预算复合/
   member 窗边界 5 分钟整)——我会补进专项脚本
4. 【架构债】观察期内若收紧影子状态机为硬守卫, 预期
   需要处理哪些 ILLEGAL 转换路径?

中文输出, 直接引用修复史编号, 不泛泛而谈。"""

req = {
    "model": "qwen3-max",
    "messages": [
        {"role": "system", "content":
         "你是资深语音交互系统架构专家, 基于证据做严谨"
         "风险预判, 不臆测。"},
        {"role": "user", "content": PROMPT}],
    "temperature": 0.3, "max_tokens": 6144,
}
r = urllib.request.Request(
    "https://dashscope.aliyuncs.com/compatible-mode/v1"
    "/chat/completions",
    data=json.dumps(req).encode("utf-8"),
    headers={"Content-Type": "application/json",
             "Authorization": "Bearer " + key})
with urllib.request.urlopen(r, timeout=300) as resp:
    out = json.loads(resp.read().decode("utf-8"))
text = out["choices"][0]["message"]["content"]
print(text)
with open("/tmp/voice_risk_report.md", "w",
          encoding="utf-8") as f:
    f.write("# 语音系统风险复查(qwen3-max)\n\n" + text)
