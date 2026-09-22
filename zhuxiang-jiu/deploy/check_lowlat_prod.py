"""v3 低延迟对话: 生产验收(语音页流水线特征/widget VER/index 引用)"""
import subprocess

HOST = "root@47.236.61.117"
CHECKS = [
    ("语音页: splitSpeech 分句切分",
     "curl -s https://zxjiu.com/xiaozhu-voice.html",
     "function splitSpeech"),
    ("语音页: pumpTts 队首接力",
     "curl -s https://zxjiu.com/xiaozhu-voice.html",
     "function pumpTts"),
    ("语音页: [LAT] 延迟埋点",
     "curl -s https://zxjiu.com/xiaozhu-voice.html",
     "[LAT] vad"),
    ("语音页: 语义感知 VAD(endsAsk)",
     "curl -s https://zxjiu.com/xiaozhu-voice.html",
     "endsAsk"),
    ("语音页: 过渡音让位(gapSrc)",
     "curl -s https://zxjiu.com/xiaozhu-voice.html",
     "ttsGapSrc"),
    ("语音页: 旧 VAD 固定 1200 已替换",
     "curl -s https://zxjiu.com/xiaozhu-voice.html",
     "endsAsk ? 600 : 1200"),
    ("widget: VER v=21(iframe 破缓存)",
     "curl -s 'https://zxjiu.com/js/voice-wake-widget.js?v=22'",
     'var VER = "v=21"'),
    ("index: 引用 ?v=22",
     "curl -s https://zxjiu.com/",
     "voice-wake-widget.js?v=22"),
]

ok = 0
for name, cmd, feat in CHECKS:
    r = subprocess.run(
        ["ssh", HOST, cmd + " | grep -c -F '" + feat + "'"],
        capture_output=True, text=True, timeout=60)
    n = r.stdout.strip()
    good = n not in ("0", "")
    if good:
        ok += 1
    mark = "PASS" if good else "FAIL"
    print(f"  [{mark}] {name} : {n}")

print(f"\n在线验收: {ok}/{len(CHECKS)}")
