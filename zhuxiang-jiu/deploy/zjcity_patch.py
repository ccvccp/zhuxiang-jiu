#!/usr/bin/env python3
"""市级网店开店城市选择补丁 v3: 省市二级联动 multiSelector + PC 交互增强

v1(34145d9): 单列平铺344项 → 省市二级联动 multiSelector
v2: 修复"在电脑上进行操作未实现联动滑动"
  实测根因: 联动逻辑本身在PC完全正常(拖拽/点击均联动),坏的只是交互入口——
  ① taro-picker-group 无 wheel 监听 → PC滚轮完全无效(用户第一本能)
  ② 拖拽链无指针捕获 → 拖出列区域即中断
  ③ onColumnChange 收到 null/NaN 会清空第二列(健壮性隐患)
v2 修复:
  - MutationObserver 监听弹层 taro-picker-group 出现即挂 wheel 监听
    (每 tick 滚一项; handleMoving→height 由 Stencil 异步渲染回 prop,
     handleMoveEnd 须延时 60ms 调用——同步三连会读到旧值被 snap 归位,实测教训)
  - mousedown 时 setPointerCapture(拖出列区域不中断)
  - onColumnChange/onChange 数值防御(isFinite 校验)
  - zjq=zjw(), 以 var 声明形式调用(v2 首发裸调用 zjw(), 在 minified var
    声明列表中非法致 chunk 解析失败市店页白屏——实测教训)
v3: 修复 v2 同名文件缓存钉住问题——语法修复版复用了坏文件名 3456.zjcity2.js,
  immutable 缓存(30天)钉住坏文件访问过白屏版的用户无法自愈 → 全链换名 zjcity3

三步法(锚点替换 + chunk 改名绕 immutable 缓存):
  1) 3456.1656538d.js 锚点替换 → 3456.zjcity3.js
  2) app.a401fix.js 映射 3456:"1656538d" → "zjcity3" → app.zjcity3.js
  3) index.html 引用 app.zjcity2.js → app.zjcity3.js
"""
import os
import shutil

DIST = "/var/www/zxjiu/dist"
TAG = "zjcity3"

# ---------- 文件级备份(回滚=恢复3文件) ----------
bakdir = DIST + ".bak-zjcity3"
os.makedirs(bakdir, exist_ok=True)
for f in ("index.html", "js/app.zjcity2.js", "js/3456.zjcity2.js"):
    srcf = os.path.join(DIST, f)
    dstf = os.path.join(bakdir, f)
    os.makedirs(os.path.dirname(dstf), exist_ok=True)
    if os.path.exists(srcf):
        shutil.copy2(srcf, dstf)
print("[1] file backup ->", bakdir)

# ---------- 补丁 3456 chunk(从原始 1656538d 重新打,含 v1+v2) ----------
src = DIST + "/js/3456.1656538d.js"
js = open(src, encoding="utf-8").read()

A1 = ("ee=(0,o.useState)(-1),ne=(0,r.Z)(ee,2),"
      "te=ne[0],ae=ne[1],")
A1_NEW = (A1 +
          # --- v1: 省索引 state + 辅助函数 ---
          "zjP=(0,o.useState)(0),zjV=(0,r.Z)(zjP,2),"
          "zjpi=zjV[0],zjps=zjV[1],"
          "zjf=function(){var s={},p=[];(X||[])"
          ".forEach(function(e){e&&e.provinceName&&"
          "!s[e.provinceName]&&(s[e.provinceName]=1,"
          "p.push(e.provinceName))});return p},"
          "zjg=function(pn){return (X||[])"
          ".filter(function(e){return e&&"
          "e.provinceName===pn})},"
          # --- v2: PC 交互增强 ---
          # MutationObserver: 弹层 taro-picker-group 动态出现即挂 wheel + 指针捕获
          "zjw=function(){if(window.__zjpc)return;"
          "window.__zjpc=1;"
          "var mo=new MutationObserver(function(){"
          'var gs=document.querySelectorAll("taro-picker-group");'
          "for(var k=0;k<gs.length;k++){"
          "var g=gs[k];if(g.__zjw)continue;g.__zjw=1;"
          "(function(g){"
          # wheel: 每 tick 滚一项(60ms 节流; handleMoveEnd 必须延时——
          # 同步紧接会因 Stencil 异步渲染回 prop 读到旧值被 snap 归位)
          'g.addEventListener("wheel",function(e){'
          "e.preventDefault();"
          "if(g.__zjbusy)return;g.__zjbusy=1;"
          "var s=e.deltaY>0?1:-1,y0=300,y1=y0-s*34;"
          "try{g.handleMoveStart(y0);"
          "g.handleMoving(y1);"
          "setTimeout(function(){"
          "try{g.handleMoveEnd(y1)}catch(_){}"
          "g.__zjbusy=0},60)}"
          "catch(_){g.__zjbusy=0}"
          '},{passive:false});'
          # 指针捕获: 拖拽移出列区域不中断
          'g.addEventListener("mousedown",function(e){'
          "try{g.setPointerCapture(e.pointerId)}"
          "catch(_){}})"
          "})(g)}});"
          "mo.observe(document.body||"
          "document.documentElement,"
          "{childList:true,subtree:true})},"
          # 以 var 声明形式调用(裸调用表达式 zjw(), 在 minified var
          # 声明列表中非法——v2 首发白屏事故实测教训)
          "zjq=zjw(),")

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
          # v2: 数值防御——null/NaN 不清空第二列
          '"onColumnChange":function(e)'
          '{var c=e&&e.detail&&e.detail.column,'
          'v=e&&e.detail&&e.detail.value;'
          'if(c===0&&typeof v==="number"&&'
          'isFinite(v)&&v>=0){zjps(v)}},'
          '"onChange":function onChange(e)'
          '{var v=(e.detail&&e.detail.value)||[0,0];'
          'var v0=Number(v[0]),v1=Number(v[1]);'
          'if(!isFinite(v0)||v0<0){v0=0}'
          'if(!isFinite(v1)||v1<0){v1=0}'
          'zjps(v0);'
          'var cs=zjg(zjf()[v0]||"");'
          'var sel=cs[v1]||cs[0];'
          'if(sel){var gi=(X||[]).indexOf(sel);'
          'gi>=0&&ae(gi)}},')

assert js.count(A1) == 1, "A1 锚点不唯一"
assert js.count(A2) == 1, "A2 锚点不唯一"
js = js.replace(A1, A1_NEW).replace(A2, A2_NEW)
assert "multiSelector" in js
assert "zjcity" not in js
assert "setPointerCapture" in js

dst = DIST + "/js/3456.zjcity3.js"
open(dst, "w", encoding="utf-8").write(js)
print("[2] 3456 patched ->", dst,
      len(open(src).read()), "->", len(js))

# ---------- 补丁 app chunk 映射(从原始 a401fix 打) ----------
asrc = DIST + "/js/app.a401fix.js"
ajs = open(asrc, encoding="utf-8").read()
MAP_OLD = '"3456":"1656538d"'
MAP_NEW = '"3456":"zjcity3"'
assert ajs.count(MAP_OLD) == 1, "app 映射锚点不唯一"
ajs = ajs.replace(MAP_OLD, MAP_NEW)
adst = DIST + "/js/app.zjcity3.js"
open(adst, "w", encoding="utf-8").write(ajs)
print("[3] app mapped ->", adst)

# ---------- index.html 引用 ----------
isrc = DIST + "/index.html"
idx = open(isrc, encoding="utf-8").read()
OLD_REF = "app.zjcity2.js"
assert OLD_REF in idx, "index 引用锚点缺失"
idx = idx.replace(OLD_REF, "app.zjcity3.js")
open(isrc, "w", encoding="utf-8").write(idx)
print("[4] index.html -> app.zjcity3.js")

print("ALL DONE v3")
