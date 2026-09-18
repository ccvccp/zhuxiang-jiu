#!/usr/bin/env python3
"""市级网店开店城市选择补丁: 单列平铺 → 省市二级联动 multiSelector

三步法(锚点替换 + chunk 改名绕 immutable 缓存):
  1) 3456.1656538d.js 锚点替换 → 3456.zjcity.js
     - 注入省索引 state + 省列表/按省筛城辅助函数
     - Picker selector(344 项单列) → multiSelector(省/市两列联动)
  2) app.a401fix.js 映射 3456:"1656538d" → "zjcity" → app.zjcity.js
  3) index.html 引用 app.a401fix.js → app.zjcity.js
"""
import shutil

DIST = "/var/www/zxjiu/dist"

# ---------- 备份 ----------
shutil.copytree(DIST, DIST + ".bak-zjcity",
                dirs_exist_ok=True)
print("[1] backup -> dist.bak-zjcity")

# ---------- 补丁 3456 chunk ----------
src = DIST + "/js/3456.1656538d.js"
js = open(src, encoding="utf-8").read()

A1 = ("ee=(0,o.useState)(-1),ne=(0,r.Z)(ee,2),"
      "te=ne[0],ae=ne[1],")
A1_NEW = (A1 +
          "zjP=(0,o.useState)(0),zjV=(0,r.Z)(zjP,2),"
          "zjpi=zjV[0],zjps=zjV[1],"
          "zjf=function(){var s={},p=[];(X||[])"
          ".forEach(function(e){e&&e.provinceName&&"
          "!s[e.provinceName]&&(s[e.provinceName]=1,"
          "p.push(e.provinceName))});return p},"
          "zjg=function(pn){return (X||[])"
          ".filter(function(e){return e&&"
          "e.provinceName===pn})},")

A2 = ('(0,w.jsx)(c.cW,{"mode":"selector",'
      '"range":X.map(function(e){return"".concat('
      'e.provinceName," · ").concat(e.cityName)}),'
      '"value":te>=0?te:0,'
      '"onChange":function onChange(e)'
      '{return ae(Number(e.detail.value))},')
A2_NEW = ('(0,w.jsx)(c.cW,{"mode":"multiSelector",'
          '"range":[zjf(),zjg(zjf()[zjpi]||"")'
          '.map(function(e){return e.cityName})],'
          '"value":[zjpi,0],'
          '"onColumnChange":function(e)'
          '{e.detail.column===0&&'
          'zjps(Number(e.detail.value))},'
          '"onChange":function onChange(e)'
          '{var v=(e.detail&&e.detail.value)||[0,0];'
          'zjps(Number(v[0]));'
          'var cs=zjg(zjf()[Number(v[0])]||"");'
          'var sel=cs[Number(v[1])]||cs[0];'
          'if(sel){var gi=(X||[]).indexOf(sel);'
          'gi>=0&&ae(gi)}},')

assert js.count(A1) == 1, "A1 锚点不唯一"
assert js.count(A2) == 1, "A2 锚点不唯一"
js = js.replace(A1, A1_NEW).replace(A2, A2_NEW)
assert "multiSelector" in js
assert "zjcity" not in js

dst = DIST + "/js/3456.zjcity.js"
open(dst, "w", encoding="utf-8").write(js)
print("[2] 3456 patched ->", dst,
      len(open(src).read()), "->", len(js))

# ---------- 补丁 app chunk 映射 ----------
asrc = DIST + "/js/app.a401fix.js"
ajs = open(asrc, encoding="utf-8").read()
MAP_OLD = '"3456":"1656538d"'
MAP_NEW = '"3456":"zjcity"'
assert ajs.count(MAP_OLD) == 1, "app 映射锚点不唯一"
ajs = ajs.replace(MAP_OLD, MAP_NEW)
adst = DIST + "/js/app.zjcity.js"
open(adst, "w", encoding="utf-8").write(ajs)
print("[3] app mapped ->", adst)

# ---------- index.html 引用 ----------
isrc = DIST + "/index.html"
idx = open(isrc, encoding="utf-8").read()
assert idx.count("app.a401fix.js") == 1, \
    "index 引用锚点不唯一"
idx = idx.replace("app.a401fix.js", "app.zjcity.js")
open(isrc, "w", encoding="utf-8").write(idx)
print("[4] index.html -> app.zjcity.js")

print("ALL DONE")
