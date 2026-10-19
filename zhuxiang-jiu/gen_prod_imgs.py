# -*- coding: utf-8 -*-
# 生成 12 款中的 10 款商城商品本地品牌图(竹绿瓶+品名, 800x800)
from PIL import Image, ImageDraw, ImageFont
import sys
sys.stdout.reconfigure(encoding='utf-8')

F = r'C:\Windows\Fonts\msyhbd.ttc'   # 微软雅黑 Bold
FR = r'C:\Windows\Fonts\msyh.ttc'

GREEN_D = (53, 92, 68)     # 深竹绿
GREEN = (74, 124, 89)      # 竹绿
GOLD = (201, 169, 97)      # 金
CREAM = (244, 241, 230)    # 米白
INK = (53, 92, 68)

PRODUCTS = [
    # (文件名, 品名, 副标)
    ('zz-classic42.jpg',    '竹香经典', '42° · 500ml'),
    ('zz-classic45.jpg',    '竹香经典', '45° · 500ml'),
    ('zz-premium52.jpg',    '竹香尊享', '52° · 500ml'),
    ('zz-reserve53.jpg',    '竹香珍藏', '53° · 500ml'),
    ('zz-premium52-750.jpg','竹香尊享', '52° · 750ml'),
    ('zz-age10.jpg',        '年份陈酿', '53° · 10年'),
    ('zz-giftbox.jpg',      '竹香礼盒', '52° · 500ml×2'),
    ('zz-age20.jpg',        '二十年限量', '53° · 500ml'),
    ('zz-premium-gift.jpg', '尊享礼盒装', '52° · 双瓶'),
    ('zz-collection50.jpg', '竹香典藏', '50° · 750ml'),
]

def bottle(d, cx, top, bw, bh):
    """画酒瓶: 金盖+颈+深绿身+米白标"""
    neck_w, neck_h = int(bw*0.42), int(bh*0.22)
    cap_w, cap_h = int(bw*0.56), int(bh*0.07)
    # 盖
    d.rounded_rectangle([cx-cap_w//2, top, cx+cap_w//2, top+cap_h], radius=6, fill=GOLD)
    # 颈
    d.rectangle([cx-neck_w//2, top+cap_h, cx+neck_w//2, top+cap_h+neck_h], fill=GREEN_D)
    # 身
    d.rounded_rectangle([cx-bw//2, top+cap_h+neck_h, cx+bw//2, top+cap_h+neck_h+bh],
                        radius=int(bw*0.18), fill=GREEN_D)
    # 高光
    hl_w = max(6, bw//14)
    d.rounded_rectangle([cx-bw//2+int(bw*0.10), top+cap_h+neck_h+int(bh*0.08),
                         cx-bw//2+int(bw*0.10)+hl_w, top+cap_h+neck_h+int(bh*0.86)],
                        radius=hl_w//2, fill=(92, 138, 108))
    return top+cap_h+neck_h  # 瓶身顶

for fname, name, sub in PRODUCTS:
    img = Image.new('RGB', (800, 800), CREAM)
    d = ImageDraw.Draw(img)
    # 背景竹叶点缀
    d.ellipse([60, 90, 200, 150], fill=(232, 239, 233))
    d.ellipse([620, 120, 760, 180], fill=(232, 239, 233))
    # 酒瓶
    body_top = bottle(d, 400, 150, 190, 330)
    # 瓶身标签
    d.rounded_rectangle([330, body_top+70, 470, body_top+210], radius=10, fill=CREAM)
    f_name = ImageFont.truetype(F, 52)
    f_sub = ImageFont.truetype(FR, 30)
    f_brand = ImageFont.truetype(F, 34)
    w = d.textlength(name, font=f_name)
    d.text((400-w/2, body_top+100), name, font=f_name, fill=INK)
    w2 = d.textlength(sub, font=f_sub)
    d.text((400-w2/2, body_top+165), sub, font=f_sub, fill=GREEN)
    # 品牌字
    w3 = d.textlength('竹 奕', font=f_brand)
    d.text((400-w3/2, 640), '竹 奕', font=f_brand, fill=GOLD)
    f_slogan = ImageFont.truetype(FR, 26)
    s4 = '全竹发酵 · 竹香型首创'
    w4 = d.textlength(s4, font=f_slogan)
    d.text((400-w4/2, 700), s4, font=f_slogan, fill=(120, 138, 124))
    img.save(rf'd:\网站架构设计\zhuxiang-jiu\images\products\{fname}', quality=88)
    print('OK', fname)
print('DONE 10')
