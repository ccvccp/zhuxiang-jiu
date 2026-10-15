"""金刚区图标生成 v3: 限流自愈——定时探测恢复后慢速生成全套

机制: 占位图指纹(19a0b822ed)检测; 每 180s 探测一次(最多 20 轮),
恢复后逐枚生成(20s 间隔防再触发限流), 校验 8 枚 md5 全异。
"""
import hashlib
import time
import urllib.parse
import urllib.request

BASE = "https://trae-api-cn.mchost.guru/api/ide/v1/text_to_image"
PLACEHOLDER_MD5 = "19a0b822ed"
OUT = "images/icons"

STYLE = (", minimalist flat vector app icon, bamboo green color palette, "
         "soft cream white background, rounded simple shapes, clean minimal "
         "design, no text")
ICONS = {
    "kk-products": "Traditional Chinese green ceramic wine bottle with bamboo leaves",
    "kk-alliance": "Charming small shop storefront with striped awning",
    "kk-activity": "Gift box with ribbon bow and confetti, warm gold accent",
    "kk-trace":    "Magnifying glass over document with checkmark badge",
    "kk-member":   "VIP member card with crown emblem, warm gold accent",
    "kk-cart":     "Shopping cart filled with small gift boxes",
    "kk-service":  "Friendly customer service headset with microphone",
    "kk-news":     "Folded newspaper with small bamboo sprig",
}


def gen(prompt):
    url = (f"{BASE}?prompt={urllib.parse.quote(prompt)}"
           f"&image_size=square")
    with urllib.request.urlopen(url, timeout=120) as r:
        return r.read()


def md5(d):
    return hashlib.md5(d).hexdigest()[:10]


# 阶段 1: 探测恢复
recovered = False
for i in range(20):
    d = gen("flat icon of a bamboo leaf, green, white background")
    ok = md5(d) != PLACEHOLDER_MD5 and d[:3] == b"\xff\xd8\xff"
    print(f"[probe {i + 1}/20] {'RECOVERED' if ok else 'still-limited'}"
          f" md5={md5(d)}", flush=True)
    if ok:
        recovered = True
        break
    time.sleep(180)

if not recovered:
    print("ALL_PROBES_LIMITED——2 小时内未恢复, 退出")
    raise SystemExit(1)

# 阶段 2: 慢速生成全套
import os
os.makedirs(OUT, exist_ok=True)
seen = set()
for name, subject in ICONS.items():
    for attempt in range(3):
        d = gen(subject + STYLE)
        if md5(d) != PLACEHOLDER_MD5 and d[:3] == b"\xff\xd8\xff":
            break
        print(f"  [{name}] attempt{attempt + 1} 占位, 重试...", flush=True)
        time.sleep(60)
    else:
        print(f"  [{name}] FAILED 3 次", flush=True)
        continue
    with open(os.path.join(OUT, name + ".jpg"), "wb") as f:
        f.write(d)
    seen.add(md5(d))
    print(f"[ok] {name}: {len(d)}B md5={md5(d)}", flush=True)
    time.sleep(20)

print(f"DONE distinct={len(seen)}/8", flush=True)
if len(seen) < 8:
    raise SystemExit(2)
