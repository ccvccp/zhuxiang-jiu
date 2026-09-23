"""复核清洗备份: 103 条 asr_failed 轮的真实性判定

按 audioMeta.sizeBytes/durationSec 与时间窗,
区分真实用户语音轮与 fake-audio 测试轮。
"""
import json

with open("/opt/zhuxiang/asr_failed_test_rounds_"
          "20260923T132311Z.json", encoding="utf-8") as f:
    rounds = json.load(f)

big = 0
small = 0
print(f"n={len(rounds)}")
for r in rounds:
    d = r["data"]
    meta = json.loads(d.get("audioMeta") or "{}")
    size = meta.get("sizeBytes") or 0
    dur = meta.get("durationSec")
    if size and size >= 1000:
        big += 1
        tag = "REAL_AUDIO"
    else:
        small += 1
        tag = "small/none"
    print(f"{str(d.get('createdAt'))[:19]} "
          f"raw='{str(d.get('rawText'))[:8]}' "
          f"size={size} dur={dur} {tag}")
print(f"\n真实音频轮(>=1KB): {big}, "
      f"小/无音频: {small}")
