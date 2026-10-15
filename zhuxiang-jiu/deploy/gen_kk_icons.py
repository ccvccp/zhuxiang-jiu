"""金刚区图标生成 v2: JPEG 落盘(.jpg) + md5 差异校验"""
import hashlib
import os
import urllib.parse
import urllib.request

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
OUT = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "images", "icons")
os.makedirs(OUT, exist_ok=True)

for name, subject in ICONS.items():
    url = ("https://trae-api-cn.mchost.guru/api/ide/v1/text_to_image?prompt="
           + urllib.parse.quote(subject + STYLE) + "&image_size=square")
    dest = os.path.join(OUT, name + ".jpg")
    try:
        with urllib.request.urlopen(url, timeout=120) as r, open(dest, "wb") as f:
            f.write(r.read())
        data = open(dest, "rb").read()
        jpeg = data[:3] == b"\xff\xd8\xff"
        md5 = hashlib.md5(data).hexdigest()[:10]
        print(f"{name}: {len(data)}B jpeg={jpeg} md5={md5}")
    except Exception as e:
        print(f"{name}: ERR {e}")
