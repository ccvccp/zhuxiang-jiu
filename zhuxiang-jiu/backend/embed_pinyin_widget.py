"""把拼音容错表注入 voice-wake-widget.js(锚点替换, 幂等)

在「唤醒引擎」锚点前插入: PYZ 表(GB2312 一级字库, gen_pinyin_table.py
生成) + charToPy 反向索引 + toPinyinSeq 拼音序列转换。
"""
import io
import json
import sys

WIDGET = "../js/voice-wake-widget.js"
TABLE = sys.argv[1] if len(sys.argv) > 1 else "pinyin_table.json"

with io.open(TABLE, encoding="utf-8") as f:
    table = json.load(f)
pyz = json.dumps(table, ensure_ascii=False, separators=(",", ":"))

BLOCK = (
    "  /* ---------- 拼音容错表(GB2312 一级字库 3755 字 → 396 无声调音节,\n"
    "     gen_pinyin_table.py 生成; 自定义唤醒词同音容错: 唤醒词与\n"
    "     ASR 转写各转拼音序列做音节级包含匹配, 未收录字符原样保留) ---------- */\n"
    "  var PYZ = " + pyz + ";\n"
    "  var PY_MAP = null;\n"
    "  function charToPy(ch) {\n"
    "    if (!PY_MAP) {\n"
    "      PY_MAP = {};\n"
    "      for (var syl in PYZ) {\n"
    "        var str = PYZ[syl];\n"
    "        for (var i = 0; i < str.length; i++) { PY_MAP[str[i]] = syl; }\n"
    "      }\n"
    "    }\n"
    "    return PY_MAP[ch] || ch;\n"
    "  }\n"
    "  function toPinyinSeq(text) {\n"
    "    var out = [];\n"
    "    for (var i = 0; i < text.length; i++) { out.push(charToPy(text[i])); }\n"
    "    return out.join(\"-\");\n"
    "  }\n"
    "\n"
)

ANCHOR = "  /* ---------- 唤醒引擎(VAD + 云端流式 ASR) ---------- */"

src = io.open(WIDGET, encoding="utf-8").read()
if "var PYZ" in src:
    print("already-embedded")
    sys.exit(0)
assert ANCHOR in src, "anchor not found"
src = src.replace(ANCHOR, BLOCK + ANCHOR, 1)
io.open(WIDGET, "w", encoding="utf-8").write(src)
print(f"embedded: {len(pyz.encode('utf-8'))} bytes py-table")
