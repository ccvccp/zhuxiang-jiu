"""73号(sv) 生产 shadow 灰度验收(容器管道 Bearer——46+1 口径)

验收面:
    1 观测面 scripts 常开
    2 shadow 全链实跑(生产 ffmpeg 渲染 mp4)
    3 幂等二跑(reused)
    4 mp4 产物容器内实存
    5 无 Bearer 裸头 401(46+1 语义)
"""
import json
import subprocess
import urllib.error
import urllib.request

CONTAINER = "zhuxiang-backend-1"
BASE = "http://localhost:8000"
RESULTS = []


def rec(name, ok, detail=""):
    RESULTS.append(ok)
    print(("  OK " if ok else "  FAIL ") + name
          + ("" if ok else " — " + str(detail)[:150]))


def admin_token():
    pipe = (
        "import asyncio\n"
        "async def m():\n"
        "    from services.auth_service import AuthService\n"
        "    try:\n"
        "        r = await AuthService().register(\n"
        "            phone='13800000731',\n"
        "            password='test123456',\n"
        "            role='admin')\n"
        "    except ValueError:\n"
        "        r = await AuthService().login(\n"
        "            '13800000731', 'test123456')\n"
        "    print(r.get('accessToken', ''))\n"
        "asyncio.run(m())\n"
    )
    out = subprocess.run(
        ["docker", "exec", CONTAINER, "python", "-c", pipe],
        capture_output=True, text=True, timeout=60)
    lines = [l for l in (out.stdout or "").splitlines()
             if l.strip()]
    assert lines, "token 获取失败: " + (out.stderr or "")[:200]
    return lines[-1].strip()


H = {"Authorization": "Bearer " + admin_token(),
     "Content-Type": "application/json"}


def call(method, path, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers=headers or H)
    try:
        r = urllib.request.urlopen(req, timeout=180)
        return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"raw": raw[:100].decode("utf-8", "replace")}


print("== 73号(sv) 生产 shadow 灰度验收 ==")

# 1 观测面
s, b = call("GET", "/api/sv73/scripts")
rows = b.get("data") or []
rec("1 观测面 scripts 200", s == 200, s)

# 5 无 Bearer 裸头 401(46+1)
s5, _ = call("GET", "/api/sv73/scripts",
             headers={"X-Role": "admin"})
rec("5 裸头 401(46+1 语义)", s5 == 401, s5)

# 2 shadow 全链
HOTSPOT = {
    "fingerprint": "fp-prod-shadow-001",
    "platform": "douyin",
    "title": "国庆家宴白酒怎么选",
    "score": 85,
}
s, b = call("POST", "/api/sv73/pipeline/run", {
    "hotspot": HOTSPOT, "category": "auto",
    "template": "fast"})
d = b.get("data") or {}
rec("2 pipeline 200", s == 200, s)
rec("2 mode=shadow", d.get("mode") == "shadow", d.get("mode"))
track = (d.get("storyboard") or {}).get("track")
print("    track =", track, "(LLM 轨或 rule)")
rec("2 匹配引擎接入",
    (d.get("match") or {}).get("topSeries") in (
        "礼盒", "经典"))
render = d.get("render") or {}
video = render.get("video") or ""
rec("2 渲染产物回传", bool(video), video)
rec("2 时长注册表", render.get("durationSeconds") == 15.6,
    render.get("durationSeconds"))
rec("2 content=pending(三审闸门不动)",
    (d.get("content") or {}).get("status") == "pending")
rec("2 shadow 留痕不占归因",
    ((d.get("content") or {}).get("sv73") or {}).get(
        "shadow") is True)
rec("2 shortCode 空(shadow)",
    (d.get("content") or {}).get("shortCode") == "")

# 3 幂等二跑
s, b = call("POST", "/api/sv73/pipeline/run", {
    "hotspot": HOTSPOT, "category": "auto",
    "template": "fast"})
d2 = b.get("data") or {}
rec("3 幂等 reused", d2.get("reused") is True)
rec("3 contentId 一致",
    (d2.get("content") or {}).get("contentId")
    == (d.get("content") or {}).get("contentId"))

# 4 mp4 容器内实存
if video:
    out = subprocess.run(
        ["docker", "exec", CONTAINER, "sh", "-c",
         "ls -la '" + video + "' 2>&1 | awk '{print $5}'"],
        capture_output=True, text=True, timeout=30)
    size = (out.stdout or "").strip()
    rec("4 mp4 实存(size>0)",
        size.isdigit() and int(size) > 100000, size)
    out2 = subprocess.run(
        ["docker", "exec", CONTAINER, "sh", "-c",
         "ffprobe -v error -show_entries format=duration "
         "-of csv=p=0 '" + video + "' 2>&1"],
        capture_output=True, text=True, timeout=30)
    dur = (out2.stdout or "").strip()
    try:
        rec("4 mp4 时长≈15.6s",
            abs(float(dur) - 15.6) < 0.7, dur)
    except ValueError:
        rec("4 mp4 时长解析", False, dur)

print("-" * 46)
ok = sum(RESULTS)
print(f"总计: {ok} 通过 / {len(RESULTS) - ok} 失败")
