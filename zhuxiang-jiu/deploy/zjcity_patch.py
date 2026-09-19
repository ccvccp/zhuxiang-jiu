#!/usr/bin/env python3
"""县（区）网店前端补丁 v4: 三级联动 + 身份证/签名表单 + 保证金提示 + 规则文案

版本链:
v1(34145d9): 单列平铺344项 → 省市二级联动 multiSelector
v2: PC 交互增强(wheel 滚轮 + setPointerCapture + 数值防御, zjq=zjw() 声明形式)
v3: 全链换名 zjcity3 修复 immutable 缓存钉住坏文件
v4(本次): 市级网店规则改造为县（区）网店
  - Picker 省市两列 → 省市区县三列联动(zjD 预载 districts/available 全量,
    Taro request 带鉴权; 市索引 zjbi + 选中区县 zjsel)
  - 表单: 营业执照号输入 → 身份证号(18位); 食品经营许可证号 → 身份证姓名;
    提交前新增保证金协议签名勾选(zjsig)
  - payload: businessLicense/foodLicense → idNumber/idName/signatureConfirm,
    city → district(三列选中区县对象)
  - 提交校验: 城市校验改区县; 新增签名勾选拦截
  - 文案: 市级网店→县（区）网店(分店定位/备案即分店备案/保证金/年任务5万)

三步法(锚点替换 + chunk 改名绕 immutable 缓存):
  1) 3456.1656538d.js 锚点替换 → 3456.zjcity6.js
  2) app.a401fix.js 映射 3456:"1656538d" → "zjcity5" → app.zjcity6.js
  3) index.html 引用 app.zjcity4.js → app.zjcity6.js
"""
import os
import shutil

DIST = "/var/www/zxjiu/dist"
TAG = "zjcity6"

# ---------- 文件级备份(回滚=恢复3文件) ----------
bakdir = DIST + ".bak-zjcity6"
os.makedirs(bakdir, exist_ok=True)
for f in ("index.html", "js/app.zjcity5.js", "js/3456.zjcity5.js"):
    srcf = os.path.join(DIST, f)
    dstf = os.path.join(bakdir, f)
    os.makedirs(os.path.dirname(dstf), exist_ok=True)
    if os.path.exists(srcf):
        shutil.copy2(srcf, dstf)
print("[1] file backup ->", bakdir)

# ---------- 补丁 3456 chunk(从原始 1656538d 重新打, 含 v1+v2+v4) ----------
src = DIST + "/js/3456.1656538d.js"
js = open(src, encoding="utf-8").read()

# ============ A0: 模块级注入(区县数据 + 加载器) ============
# 注意1: 必须在模块级——组件内有局部变量 _(useState setter)遮蔽模块级
# 请求模块 _, 组件内调用 (0,_.W) 会 TypeError(v4 首发实测教训)
# 注意2: 注入点 Z=function 在模块巨型 var 声明链中间——注入开头不得带
# var 关键字(逗号接续原链), 结尾用 ; 终结链后 var Z= 独立声明(v5 白屏教训)
# 注意3: 数据就绪后 window.__zjkick 通知组件重渲染(模块级对象不触发渲染)
A0 = "Z=function CityStorePage(){"
A0_NEW = (
    "zjD={},"
    "zjdf=function(cc){return zjD[cc]||[]},"
    "zjdl=function(){if(window.__zjdl)return;window.__zjdl=1;"
    "(0,_.W)({\"url\":\"/api/citystore/districts/available\"})"
    ".then(function(j){var ds=(j&&j.data&&j.data.districts)||[];"
    "ds.forEach(function(d){"
    "(zjD[d.cityCode]=zjD[d.cityCode]||[])"
    ".push({\"districtCode\":d.districtCode,"
    "\"districtName\":d.districtName,"
    "\"cityCode\":d.cityCode,\"cityName\":d.cityName,"
    "\"provinceCode\":d.provinceCode,"
    "\"provinceName\":d.provinceName})});"
    "window.__zjkick&&window.__zjkick()})"
    ".catch(function(){});};"
    "var Z=function CityStorePage(){")

# ============ A1: state 与辅助函数注入 ============
A1 = ("ee=(0,o.useState)(-1),ne=(0,r.Z)(ee,2),"
      "te=ne[0],ae=ne[1],")
A1_NEW = (A1 +
          # --- v1: 省索引 state + 省市辅助 ---
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
          'g.addEventListener("mousedown",function(e){'
          "try{g.setPointerCapture(e.pointerId)}"
          "catch(_){}})"
          "})(g)}});"
          "mo.observe(document.body||"
          "document.documentElement,"
          "{childList:true,subtree:true})},"
          # 以 var 声明形式调用(裸调用表达式在 minified var
          # 声明列表中非法——v2 首发白屏事故实测教训)
          "zjq=zjw(),"
          # --- v4: 市索引/选中区县/签名勾选 + 数据就绪 kick ---
          "zjB=(0,o.useState)(0),zjBI=(0,r.Z)(zjB,2),"
          "zjbi=zjBI[0],zjbs=zjBI[1],"
          "zjS=(0,o.useState)(null),zjSI=(0,r.Z)(zjS,2),"
          "zjsel=zjSI[0],zjssel=zjSI[1],"
          "zjG2=(0,o.useState)(!1),zjGI=(0,r.Z)(zjG2,2),"
          "zjsig=zjGI[0],zjsgs=zjGI[1],"
          # kick state(模块级 zjdl 数据就绪后 window.__zjkick 触发重渲染)
          "zjk=(0,o.useState)(0),zjKI=(0,r.Z)(zjk,2),zjkk=zjKI[1],"
          "zjkik=function(){window.__zjkick=function(){zjkk(Date.now())}},"
          # 触发 kick 挂载 + 区县数据加载(逗号表达式声明形式——
          # 组件内裸调用 zjkik() 在 var 声明链中非法, v5 白屏教训)
          "zjdlz=(zjkik(),zjdl()),")

# ============ A2: Picker 三列 multiSelector ============
A2 = ('(0,w.jsx)(c.cW,{"mode":"selector",'
      '"range":X.map(function(e){return"".concat('
      'e.provinceName," · ").concat(e.cityName)}),'
      '"value":te>=0?te:0,'
      '"onChange":function onChange(e)'
      '{return ae(Number(e.detail.value))},')
A2_NEW = ('(0,w.jsx)(c.cW,{"mode":"multiSelector",'
          # 三列: 省 | 市(名) | 区县(名)
          '"range":[zjf(),'
          'zjg(zjf()[zjpi]||"").map(function(e){return e.cityName}),'
          'zjdf((zjg(zjf()[zjpi]||"")[zjbi]||{}).cityCode||"")'
          '.map(function(e){return e.districtName})],'
          '"value":[zjpi,zjbi,0],'
          '"onColumnChange":function(e){'
          'var c=e&&e.detail&&e.detail.column,'
          'v=e&&e.detail&&e.detail.value;'
          'if(c===0&&typeof v==="number"&&isFinite(v)&&v>=0){'
          'zjps(v);zjbs(0)}'
          'else if(c===1&&typeof v==="number"&&isFinite(v)&&v>=0){'
          'zjbs(v)}},'
          # 确认: 三列值 → 选中区县对象(zjsel) + 同步城市索引 te
          '"onChange":function onChange(e){'
          'var v=(e.detail&&e.detail.value)||[0,0,0];'
          'var v0=Number(v[0]),v1=Number(v[1]),v2=Number(v[2]);'
          'if(!isFinite(v0)||v0<0){v0=0}'
          'if(!isFinite(v1)||v1<0){v1=0}'
          'if(!isFinite(v2)||v2<0){v2=0}'
          'zjps(v0);zjbs(v1);'
          'var cs=zjg(zjf()[v0]||"");'
          'var cy=cs[v1]||cs[0]||null;'
          'var dcs=zjdf(cy&&cy.cityCode||"");'
          'var d=dcs[v2]||dcs[0]||null;'
          'zjssel(d);'
          'if(cy&&d){var gi=(X||[]).indexOf(cy);gi>=0&&ae(gi)}},')

# ============ A3: Picker 显示文本 ============
A3 = ('(0,w.jsx)(c.xv,{"className":te>=0?u.pickerValue:u.pickerPlaceholder,'
      '"children":te>=0&&X[te]?"".concat(X[te].provinceName," ")'
      '.concat(X[te].cityName):"选择城市(一城一店, 先到先得)"}),')
A3_NEW = ('(0,w.jsx)(c.xv,{"className":zjsel?u.pickerValue:u.pickerPlaceholder,'
          '"children":zjsel?"".concat(zjsel.provinceName," ")'
          '.concat(zjsel.cityName," ").concat(zjsel.districtName):'
          '"选择区县(一区县一店, 先到先得)"}),')

# ============ A4: 提交校验 case1(城市→区县) ============
A4 = ('case 1:if(!(te<0)&&X[te]){n.n=2;break}'
      'return(0,l.CF)({"title":"请选择开店城市","icon":"none"}),n.a(2);')
A4_NEW = ('case 1:if(zjsel){n.n=2;break}'
          'return(0,l.CF)({"title":"请选择开店区县","icon":"none"}),n.a(2);')

# ============ A5: 校验 case3(营业执照→身份证) ============
A5 = ('case 3:if(ue.trim()){n.n=4;break}'
      'return(0,l.CF)({"title":"请输入营业执照号","icon":"none"}),n.a(2);')
A5_NEW = ('case 3:if(ue.trim()){n.n=4;break}'
          'return(0,l.CF)({"title":"请输入身份证号","icon":"none"}),n.a(2);')

# ============ A6: 校验 case4(食品证→姓名+签名勾选) ============
A6 = ('case 4:if(he.trim()){n.n=5;break}'
      'return(0,l.CF)({"title":"请输入食品经营许可证号","icon":"none"}),n.a(2);')
A6_NEW = ('case 4:if(!he.trim()){'
          'return(0,l.CF)({"title":"请输入身份证姓名","icon":"none"}),n.a(2)}'
          'if(!zjsig){'
          'return(0,l.CF)({"title":'
          '"请阅读并勾选保证金协议确认签名","icon":"none"}),n.a(2)}'
          'n.n=5;break;')

# ============ A7: payload(city+双证 → district+身份证+签名) ============
A7 = ('case 5:return k(!0),n.p=6,n.n=7,b({"memberLevel":I,'
      '"storeName":oe.trim(),"city":X[te],'
      '"businessLicense":ue.trim(),"foodLicense":he.trim()});')
A7_NEW = ('case 5:return k(!0),n.p=6,n.n=7,b({"memberLevel":I,'
          '"storeName":oe.trim(),"district":zjsel,'
          '"idNumber":ue.trim(),"idName":he.trim(),'
          '"signatureConfirm":zjsig});')

# ============ A7b: 成功提示文案 ============
A7B = '{"title":"申请已提交, 等待审核","icon":"none","duration":2500}'
A7B_NEW = ('{"title":"申请已提交, 等待平台确认开业","icon":"none",'
           '"duration":2500}')

# ============ A8: apply 请求 data 字段 ============
A8 = ('"cityCode":e.city.cityCode,"cityName":e.city.cityName,'
      '"provinceCode":e.city.provinceCode,"provinceName":e.city.provinceName,'
      '"businessLicense":e.businessLicense,"foodLicense":e.foodLicense,'
      '"taxRegNo":e.taxRegNo||""')
A8_NEW = ('"districtCode":e.district.districtCode,'
          '"districtName":e.district.districtName,'
          '"cityCode":e.district.cityCode,"cityName":e.district.cityName,'
          '"provinceCode":e.district.provinceCode,'
          '"provinceName":e.district.provinceName,'
          '"idName":e.idName,"idNumber":e.idNumber,'
          '"signatureConfirm":e.signatureConfirm')

# ============ A9/A10: 表单输入框 ============
A9 = ('"placeholder":"营业执照号","placeholderClass":u.placeholder,'
      '"maxlength":30')
A9_NEW = ('"placeholder":"身份证号(18位)","placeholderClass":u.placeholder,'
          '"maxlength":18')
A10 = ('"placeholder":"食品经营许可证号","placeholderClass":u.placeholder,'
       '"maxlength":30')
A10_NEW = ('"placeholder":"身份证姓名","placeholderClass":u.placeholder,'
           '"maxlength":30')

# ============ A11: 签名勾选行(sheetBtn 前插) ============
A11 = (',(0,w.jsx)(c.G7,{"className":u.sheetBtn,"onClick":fe,'
       '"children":Z?"提交中...":"提交申请"})')
A11_NEW = (',(0,w.jsxs)(c.G7,{"className":u.inputRow,'
           '"onClick":function(){zjsgs(!zjsig)},'
           '"children":['
           '(0,w.jsx)(c.xv,{"style":{"fontSize":"36rpx",'
           '"color":zjsig?"#2a9d5c":"#bbb","marginRight":"16rpx"},'
           '"children":zjsig?"☑":"○"}),'
           '(0,w.jsx)(c.xv,{"style":{"flex":"1","fontSize":"24rpx",'
           '"color":"#666","lineHeight":"1.5"},'
           '"children":"我已阅读并同意《县（区）网店合作开设与'
           '保证金协议》: 确认签名, 预存保证金¥1000(一年期不可提前取现, '
           '到期按年任务完成度退还)"})]}),'
           '(0,w.jsx)(c.G7,{"className":u.sheetBtn,"onClick":fe,'
           '"children":Z?"提交中...":"提交申请"})')

# ============ A12: sheetDesc ============
A12 = ('X.length>0?"".concat(X.length," 个城市可开(未被独占)")'
       ':"可用城市加载中..."')
A12_NEW = '"县（区）级开店 · 一区县一店 · 预存保证金¥1000"'

# ============ A14: toStore 详情映射字段 ============
A14 = ('"businessLicense":e.businessLicense||"",'
       '"foodLicense":e.foodLicense||"",'
       '"taxRegNo":e.taxRegNo||""')
A14_NEW = ('"idNumber":e.idNumber||"",'
           '"idName":e.idName||"",'
           '"districtName":e.districtName||""')

ANCHORS = [
    (A0, A0_NEW, "A0"),
    (A1, A1_NEW, "A1"), (A2, A2_NEW, "A2"), (A3, A3_NEW, "A3"),
    (A4, A4_NEW, "A4"), (A5, A5_NEW, "A5"), (A6, A6_NEW, "A6"),
    (A7, A7_NEW, "A7"), (A7B, A7B_NEW, "A7B"), (A8, A8_NEW, "A8"),
    (A9, A9_NEW, "A9"), (A10, A10_NEW, "A10"), (A11, A11_NEW, "A11"),
    (A12, A12_NEW, "A12"), (A14, A14_NEW, "A14"),
]
for old, new, name in ANCHORS:
    assert js.count(old) == 1, f"{name} 锚点不唯一: {js.count(old)}"
    js = js.replace(old, new)

# ============ A13: 规则文案(简单替换, 多处) ============
TEXTS = [
    ("市级网店 · 一城一店", "县（区）网店 · 一区县一店"),
    ("SVIP 专属权益 · 城市独占经营 · 月度考核享折扣",
     "SVIP 专属权益 · 区县独占经营 · 朋友圈口碑推广"),
    ("市店规则", "县（区）店规则"),
    ("· SVIP(L5) 专属, 一个城市仅一家网店(城市独占)",
     "· SVIP(L5) 专属, 一个区县仅一家网店(区县独占)"),
    ("· 凭营业执照 + 食品经营许可证申请, 平台审核后开业",
     "· 凭身份证 + 确认签名申请, 预存保证金¥1000后平台确认开业"),
    ("· 月度考核进货/销售双达标, 达标享次月更低折扣",
     "· 年进货任务¥5万, 月度达标享次月更低折扣(70/80/90折)"),
]
for old, new in TEXTS:
    assert js.count(old) >= 1, f"文案锚点缺失: {old[:20]}"
    js = js.replace(old, new)

# 保证金规则行(90 天冷静期行后插)
A13 = (',(0,w.jsx)(c.G7,{"className":u.noteLine,'
       '"children":"· 资格取消后 90 天冷静期内不可重新申请"})')
A13_NEW = A13 + (',(0,w.jsx)(c.G7,{"className":u.noteLine,'
                 '"children":"· 保证金¥1000一年期不可提前取现, '
                 '到期按年任务完成度退还"})'
                 ',(0,w.jsx)(c.G7,{"className":u.noteLine,'
                 '"children":"· 本站为总店, 网店为分店, '
                 '网站备案即为分店备案"})')
assert js.count(A13) == 1, "A13 锚点不唯一"
js = js.replace(A13, A13_NEW)

# ---------- 终检(全部替换完成后) ----------
for marker in ("multiSelector", "setPointerCapture",
               "districtCode", "signatureConfirm", "idNumber"):
    assert marker in js, f"marker {marker} 缺失"
assert "zjcity" not in js
assert "businessLicense" not in js, "旧字段残留"
assert "营业执照" not in js, "旧文案残留"
assert "食品经营许可" not in js, "旧文案残留2"

dst = DIST + "/js/3456.zjcity6.js"
open(dst, "w", encoding="utf-8").write(js)
print("[2] 3456 patched ->", dst,
      len(open(src).read()), "->", len(js))

# ---------- 补丁 app chunk 映射(从原始 a401fix 打) ----------
asrc = DIST + "/js/app.a401fix.js"
ajs = open(asrc, encoding="utf-8").read()
MAP_OLD = '"3456":"1656538d"'
MAP_NEW = '"3456":"zjcity6"'
assert ajs.count(MAP_OLD) == 1, "app 映射锚点不唯一"
ajs = ajs.replace(MAP_OLD, MAP_NEW)
adst = DIST + "/js/app.zjcity6.js"
open(adst, "w", encoding="utf-8").write(ajs)
print("[3] app mapped ->", adst)

# ---------- index.html 引用 ----------
isrc = DIST + "/index.html"
idx = open(isrc, encoding="utf-8").read()
OLD_REF = "app.zjcity5.js"
assert OLD_REF in idx, "index 引用锚点缺失"
idx = idx.replace(OLD_REF, "app.zjcity6.js")
open(isrc, "w", encoding="utf-8").write(idx)
print("[4] index.html -> app.zjcity6.js")

print("ALL DONE v6")
