"""v77 上线验证: 容器内 split_speech 短块合并 live 冒烟"""
import services.joyvoice_service as j

cases = [
    ("介绍完了。理性饮酒，满上。", "全短块合并"),
    ("好的——我为您推荐竹奕·竹香经典 52度 500ml，您看这款怎么样？", "首块保护"),
    ("已加「竹奕·竹香便携 42° 250ml」×1，¥88，清单1件（竹香便携×1）。还要吗？", "短块并入"),
]
for text, name in cases:
    parts = j.split_speech(text)
    short = [p for p in parts[1:] if len(p.strip()) <= 6]
    print(f"[{name}] blocks={len(parts)} "
          f"short_tail={short} ok={not short}")
    for p in parts:
        print("   |", p)
