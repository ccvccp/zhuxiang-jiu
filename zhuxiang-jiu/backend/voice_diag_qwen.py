"""调用千问 max 对小竹语音全链路做全面诊断复查

输入: /tmp/voice_diag.json(24h 六维统计+日志样本)
输出: 结构化诊断报告(分维度根因+分级优化方案)
模型: qwen3-max -> qwen-max -> qwen-plus 逐候选回退
"""
import json
import os
import urllib.request

# 从生产 .env 取 DashScope key(ssh shell 无容器 env)
key = ""
with open("/opt/zhuxiang/.env", encoding="utf-8") as f:
    for line in f:
        if line.startswith("DASHSCOPE_API_KEY="):
            key = line.split("=", 1)[1].strip()
            break
assert key, "DASHSCOPE_API_KEY not found in /opt/zhuxiang/.env"

with open("/tmp/voice_diag.json", encoding="utf-8") as f:
    diag = json.load(f)

ARCH = """# 小竹语音精灵系统全貌(诊断上下文)

## 架构
- 双轨麦克风: 主站 widget KWS 轨(常驻待命, VAD 能量门限起段,
  双唤醒词「小竹小竹/你好小竹」, 短文本≤6字拼音弱匹配) + 语音
  面板轨(iframe 全功能页: 免提连续对话 VAD 0.8s 断句 / tap 录音)
- 四态状态机: S0 待命(呼吸动画)→S1 唤醒(「叮-咚」beep+「我在，
  请吩咐」应答, widget 主页面 AudioContext 播)→S2 倾听(8s 免唤醒
  窗)→S3 响应(播报期 re-arm 识别供 barge-in 打断)→5s 续问窗→关窗
- member 级 5 分钟免唤醒窗(_LAST_WAKE_AT, 三路点亮: 服务端
  detect_wake / ws finish 含小竹 / REST body wake=1)

## 连接层
- 浏览器→后端: V2 预热连接池(auth.ver=2→armed 入池, 25s ping
  穿 nginx 60s 空闲超时, 段后保活回池复用)
- 后端→百炼: 一段一连接(百炼掐线规则不透明, 连接寿命不可依赖)
  +SSLContext TLS ticket 复用+段间预建+段内重试一次
- v=76 新上线自愈: visibilitychange 回前台三探活(ctx resume/
  麦克风流 track ended/池连接重建)+preWs onclose 前台指数退避重建

## ASR 前端链
- 浏览器 AudioContext 48kHz ScriptProcessor→线性重采样 16k→
  PCM16 base64 WS 推流
- VAD: RMS 门限起段(TH_ON 0.009)/止段(TH_OFF 0.006), ring
  buffer 2.5s 回补词头, 收段等待 0.9s(换气窗口)
- 增益: 双向平滑增益(限速1.6x/帧, 目标rms 0.06)+±0.95 软压缩
  (X5 无视 autoGainControl 约束, 采集层 AGC 削顶的应对)
- 百炼模型: qwen-audio-3.0-asr-flash 流式, 服务端注入唤醒词
  vocabulary 热词(权重5)
- 空转写健康轮二打: final 空且源rms≥0.006(健康)→同段缓存音频
  自动重识别一次(百炼偶发抖动)

## TTS
- 切句流式合成(逐句 fetch 百炼 TTS)+整句回退+断连单次重试(400ms)
+preheat 首句预合成
- 播报: 面板/主页面双 AudioContext 播放, ctx 永不销毁只复用
  (X5 无手势新建 ctx 为 suspended 态)
- 时延实测: 合成 p50 1305ms/p90 1817ms(每句), 流式开播 60-110ms

## 意图
- rule 轨: 26 指令(wine.verify 真伪/wine.craft 工艺/
  wine.recommend 推荐/wine.reviews 评价/product.new 换一款等),
  正则 pattern 表直达, 实测 48~151ms
- LLM 轨: 兜底闲聊/复杂意图(智谱 glm-5.3)
- 门控: 三态灰度(XIAOZHU_MODE off/shadow/assist), 免提闲聊
  门控三层(非 tap 轮+无商品语境→静默; 含商品语境+非意图动词
  开头+近3轮无真指令→静默), 频次熔断(分钟 10 段封顶)

## barge-in(播报打断)
- 播报期 re-arm 识别持续推流→partial 回流→文本判定: 非回声
  ≥3字或「小竹」≥2字或纠正词(不对/不是/别说了/闭嘴/停/等等)
  →切断合成+finish 收段提交
- 回声判定: 切句+子序列+LCS≥0.75+中文数字归一(四十二→42)
  +12s 时间窗自录检测

## 已知物理边界(X5 内核/微信)
- 无 AEC: TTS 外放直达麦克风 rms 0.15+ 与人声不可分——能量
  版打断不可行, 文本版是定版
- 息屏/切后台冻结 JS 杀 WS(1006)+audio 图停+可能回收麦克风流
- 同源 token 双 iframe 并发刷新互吊销(xz.refAt 20s 锁已防)
- 外放视频声盖人声(环境干扰非 bug)"""

KNOWN_ISSUES = """# 人工预诊信号(待你复核确认/否定)

1. TTS bad_response 失败率 30%(11/36): 日志见 ct=application/json
   len=98 intact=False 连续出现, 同一句「500ml」经典进阶·醇」
   重复合成两遍; llm_tts_stream_failed(回退整句): Remote end
   closed connection without response
2. ASR 轮 62%(23/37)为环境声长转写(≥20字, 用户外放时政视频
   「桃子观察」全文被转写)——KWS 轨只检测唤醒词却完整送百炼
   识别, 烧 ASR 额度
3. 0 轮 final 含「小竹」(wakeword_in_final=0)——但唤醒实际
   成功过(有 command_done)——唤醒词是否在 partial 命中后
   teardownSeg 致 final 未落? 或 vocabulary 热词未生效?
4. ws_asr_error: Cannot call "send" once a close message has
   been sent(2 次)——服务端往已关 socket send 未捕获
5. auth_failed 3 次/1006 2 次(recv=empty 建连期被杀)
6. TTS 合成单句 p50 1.3s——用户主诉「不能完整播报/不顺畅」
   的可能构成项"""

PROMPT = ARCH + "\n\n" + KNOWN_ISSUES + """

# 24h 生产统计(六维)
""" + json.dumps(diag["summary"], ensure_ascii=False, indent=1) + """

# 原始日志样本(WARNING/ERROR 全量 + 最近语音行)
""" + "\n".join(diag["warn_err_all"]) + """

---

# 你的任务: 资深语音交互系统专家做全面诊断复查

逐维度(ASR/连接/意图/TTS/唤醒/会话)输出:
1. 【诊断】每个问题: 日志证据→根因分析→置信度(高/中/低)
2. 【优化方案】分级: P0(立即修复-影响可用性)/P1(短期-体验
   或成本)/P2(长期-架构), 每项给: 改法要点+预期收益+风险
3. 【复核】人工预诊信号 6 条逐条确认/否定/存疑
4. 【复测建议】修复后验证清单

要求: 直接引用日志证据行; 根因到代码层(可指到模块名);
不要泛泛而谈; 中文输出。"""

req_body = {
    "model": "",
    "messages": [
        {"role": "system", "content":
         "你是资深语音交互系统架构专家, 精通 WebRTC/WebAudio/"
         "流式 ASR-TTS/移动端 X5 内核浏览器特性/阿里云百炼。"
         "基于日志证据做严谨诊断, 不臆测。"},
        {"role": "user", "content": PROMPT},
    ],
    "temperature": 0.3,
    "max_tokens": 8192,
}
url = ("https://dashscope.aliyuncs.com/compatible-mode/v1"
       "/chat/completions")
candidates = ["qwen3-max", "qwen-max", "qwen-plus", "qwen-turbo"]
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
        with open("/tmp/voice_diag_report.md", "w",
                  encoding="utf-8") as f:
            f.write(f"# 小竹语音全面诊断(千问 {m}, 24h 数据)\n\n")
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
