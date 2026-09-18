"""dist 401 自愈补丁(app 桥 + taro 拦截): 对生产 dist 两个 chunk 做锚点式精准修改

修改点:
  1. app.eebb43ae.js: refreshSession 定义后挂 window.__AUTH401__ 全局桥
     (返回 Promise<header|null>, 刷新成功给新 Bearer 头, 失败 null)
  2. taro.1560daa0.js: _request 响应分发处加 401 拦截——
     /api 前缀 + 非 auth/sms/entry + 未重试过 → 桥刷新 → 带 __r401 标记重发;
     桥不存在/刷新失败 → 原样透传(行为不变)

安全设计:
  - 防死循环: /api/auth/* /api/sms* /api/entry/* 排除 + __r401 单次重试标记
  - 桥缺失时(window.__AUTH401__ undefined)零影响原样透传——增量兼容
"""
import shutil
import sys

APP = "/var/www/zxjiu/dist/js/app.eebb43ae.js"
TARO = "/var/www/zxjiu/dist/js/taro.1560daa0.js"

# ============================================================
# 1. app chunk: 挂全局桥
# ============================================================
APP_ANCHOR = "function refreshSession(){return _refreshSession.apply(this,arguments)}"
APP_PATCH = (
    "function refreshSession(){return _refreshSession.apply(this,arguments)}"
    'window.__AUTH401__=function(C){'
    'return Promise.resolve().then(function(){'
    'return refreshSession()}).then(function(s){'
    'return s&&s.accessToken?{"Authorization":"Bearer "+s.accessToken}:null'
    "}).catch(function(){return null})};"
)

# ============================================================
# 2. taro chunk: _request 401 拦截
# ============================================================
# 2a. 保存原始 opts 引用(q), 供 401 重发用
TARO_ANCHOR_A = "l.credentials=f;var D=fetch(C,l).then(function(e){"
TARO_PATCH_A = "l.credentials=f;var q=e;var D=fetch(C,l).then(function(e){"

# 2b. 第二段 then(响应分发)替换为 401 自愈版
TARO_ANCHOR_B = "}).then(function(e){return u.data=e,(0,r.mf)(t)&&t(u),(0,r.mf)(n)&&n(u),u}).catch("
TARO_PATCH_B = (
    "}).then(function(e){"
    # 401 自愈: /api 前缀 + 非登录族端点 + 未重试过 + 桥存在
    "if(401===u.statusCode&&C&&0===C.indexOf(\"/api\")"
    "&&0!==C.indexOf(\"/api/auth/\")&&0!==C.indexOf(\"/api/sms\")"
    "&&0!==C.indexOf(\"/api/entry/\")&&q&&!q.__r401"
    '&&"function"==typeof window.__AUTH401__){'
    "var G=window.__AUTH401__(C);"
    'if(G&&"function"==typeof G.then)return G.then(function(h){'
    "if(h){q.__r401=1;"
    "q.header=Object.assign({},q.header||{},h);"
    "return _request(q)}"
    "u.data=e;(0,r.mf)(t)&&t(u);(0,r.mf)(n)&&n(u);return u})."
    "catch(function(){u.data=e;(0,r.mf)(t)&&t(u);(0,r.mf)(n)&&n(u);return u})"
    "}"
    "return u.data=e,(0,r.mf)(t)&&t(u),(0,r.mf)(n)&&n(u),u}).catch("
)


def patch(path, anchor, replacement, label):
    src = open(path, encoding="utf-8").read()
    n = src.count(anchor)
    if n != 1:
        print(f"[FAIL] {label}: 锚点出现 {n} 次(须唯一), 中止")
        sys.exit(1)
    open(path, "w", encoding="utf-8").write(src.replace(anchor, replacement))
    print(f"[OK] {label}: 锚点唯一, 已替换")


def main():
    # 备份(带 auth401 标记, 不覆盖既有备份)
    for p in (APP, TARO):
        bak = p + ".bak-auth401"
        try:
            open(bak, encoding="utf-8").read(16)
            print(f"[SKIP] 备份已存在: {bak}")
        except OSError:
            shutil.copy2(p, bak)
            print(f"[OK] 备份: {bak}")

    # 幂等: 若已打补丁(上次运行被误判中止), 先从备份恢复
    for p in (APP, TARO):
        src = open(p, encoding="utf-8").read()
        if "window.__AUTH401__" in src or "var q=e;var D=fetch" in src:
            shutil.copy2(p + ".bak-auth401", p)
            print(f"[OK] 检测到已打补丁, 已恢复原始: {p.split('/')[-1]}")

    patch(APP, APP_ANCHOR, APP_PATCH, "app 全局桥")
    patch(TARO, TARO_ANCHOR_A, TARO_PATCH_A, "taro 原始参数引用")
    patch(TARO, TARO_ANCHOR_B, TARO_PATCH_B, "taro 401 自愈拦截")

    # 语法完整性: 注入片段括号自配对(新增左右差值相等)
    for p, bak in ((APP, APP + ".bak-auth401"),
                   (TARO, TARO + ".bak-auth401")):
        a = open(bak, encoding="utf-8").read()
        b = open(p, encoding="utf-8").read()
        delta = {c: b.count(c) - a.count(c) for c in "(){}[]"}
        paired = (delta["("] == delta[")"] and delta["{"] == delta["}"]
                  and delta["["] == delta["]"])
        print(f"[CHECK] {p.split('/')[-1]} 括号差值: {delta} "
              f"({'自配对 OK' if paired else '不配对!'})")
        assert paired, "括号不配对!"
    print("\n补丁完成, 括号平衡校验通过")


if __name__ == "__main__":
    main()
