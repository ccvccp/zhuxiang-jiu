"""生成唤醒引擎拼音容错表(GB2312 一级字库 → 无声调音节分组紧凑 JSON)

用途: voice-wake-widget.js 自定义唤醒词同音容错——唤醒词与 ASR
转写各转拼音序列("-"连接), 音节级包含匹配。
用法: python -B gen_pinyin_table.py [输出.js]
"""
import json
import sys

from pypinyin import lazy_pinyin

# GB2312 一级字库: 区 16-55(3755 常用汉字), 区位码枚举
chars = []
for qu in range(16, 56):
    for wei in range(1, 95):
        try:
            b = bytes([160 + qu, 160 + wei])
            ch = b.decode("gb2312")
        except (ValueError, UnicodeDecodeError):
            continue
        if ch:
            chars.append(ch)

groups: dict[str, list[str]] = {}
skipped = 0
for ch in chars:
    py = lazy_pinyin(ch, errors="default")
    s = py[0] if py else ""
    # 非拉丁音节(生僻/符号)跳过
    if not s or not s.isascii() or not s.isalpha():
        skipped += 1
        continue
    groups.setdefault(s, [])
    if ch not in groups[s]:
        groups[s].append(ch)

table = {k: "".join(v) for k, v in sorted(groups.items())}
out = json.dumps(table, ensure_ascii=False, separators=(",", ":"))

total_chars = sum(len(v) for v in groups.values())
print(f"字库: {len(chars)} 字(跳过 {skipped}), 音节: {len(groups)}, "
      f"收录: {total_chars}, JSON: {len(out.encode('utf-8'))} bytes")

if len(sys.argv) > 1:
    with open(sys.argv[1], "w", encoding="utf-8") as f:
        f.write(out)
    print(f"written -> {sys.argv[1]}")
