"""金刚区图标生成 v4: 过夜长窗(滚动配额窗口恢复探测)

v3 实证: 20 轮×3min 限流未恢复 + 午夜非日界重置 → 配额按滚动
窗口(首烧 ~22:45, 预计次日同时段恢复)。本任务 15min/探 × 最长
22h 覆盖恢复点; 恢复后慢速生成 8 枚(20s 间隔)并校验全异。
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
    url = f"{BASE}?prompt={urllib.parse.quote(prompt)}&image_size=square"
    with urllib.request.urlopen(url, timeout=120) as r:
        return r.read()


def md5(d):
    return hashlib.md5(d).hexdigest()[:10]


recovered = False
for i in range(88):   # 15min × 88 ≈ 22h
    try:
        d = gen("flat icon of a bamboo leaf, green, white background")
        ok = md5(d) != PLACEHOLDER_MD5 and d[:3] == b"\xff\xd8\xff"
    except Exception:
        ok = False
    print(f"[probe {i + 1}/88] {'RECOVERED' if ok else 'limited'}",
          flush=True)
    if ok:
        recovered = True
        break
    time.sleep(900)

if not recovered:
    print("ALL_PROBES_LIMITED——22h 未恢复", flush=True)
    raise SystemExit(1)

import os
os.makedirs(OUT, exist_ok=True)
seen = set()
for name, subject in ICONS.items():
    for attempt in range(3):
        d = gen(subject + STYLE)
        if md5(d) != PLACEHOLDER_MD5 and d[:3] == b"\xff\xd8\xff":
            break
        print(f"  [{name}] attempt{attempt + 1} 占位重试", flush=True)
        time.sleep(60)
    else:
        print(f"  [{name}] FAILED", flush=True)
        continue
    with open(os.path.join(OUT, name + ".jpg"), "wb") as f:
        f.write(d)
    seen.add(md5(d))
    print(f"[ok] {name}: {len(d)}B md5={md5(d)}", flush=True)
    time.sleep(20)

print(f"DONE distinct={len(seen)}/8", flush=True)
raise SystemExit(0 if len(seen) == 8 else 2)
