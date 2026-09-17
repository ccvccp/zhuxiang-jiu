"""生产 E2E: AI智能中枢(35号)全链实证(14 端点)

七条链:
    A 入口链: panel(角色chips) → health(聚合绿灯)
    B 能力链(admin): capabilities(注册表查询自动回填) →
      toggle上下架+切回
    C 输入链: intent(规则轨分类"这瓶酒多少钱") → asr
      (无LLM key结构化降级) → asr限流留痕
    D 治理链(admin): ops/intents(意图分布) → ops/overview
      (总览) → learning/retrigger(全量重跑) → ops/usage
      (LLM用量) → learning/approvals(空清单)
    E 审批负测: approve未知404 / reject未知404
    F 媒体链: media/voice + media/image(落盘) → 文件清理
    负测: 治理无头403

清理: hub域差集(capabilities/intent_stats/asr用量等新键)
    + media文件删除; E2E全程零业务副作用.
"""
import base64
import glob
import json
import os

import httpx
import redis

BASE = "http://127.0.0.1:8000"
U = 1
M1 = {"X-Member-Id": str(U)}
A = {"X-Role": "admin"}

c = httpx.Client(base_url=BASE, timeout=60)
r = redis.Redis(host="redis", port=6379, decode_responses=True)

before = set(r.keys("zhuxiang:hub:*"))
print(f"[inv] hub keys before: {len(before)}")

# ============================================================
# A 入口链
# ============================================================

resp = c.get("/api/hub/panel", headers=M1)
d = resp.json()
chips = d.get("chips") or d.get("capabilities") or []
print(f"[A1] panel: HTTP {resp.status_code} role={d.get('role')} "
      f"chips={len(chips)} keys={sorted(d.keys())[:7]}")

resp = c.get("/api/hub/health")
d = resp.json()
print(f"[A2] health: HTTP {resp.status_code} "
      f"{json.dumps(d, ensure_ascii=False)[:130]}")

# ============================================================
# B 能力链(admin)
# ============================================================

resp = c.get("/api/hub/capabilities", headers=A)
caps = resp.json().get("capabilities") or []
first = (caps or [{}])[0]
cap_id = first.get("id")
print(f"[B1] capabilities: HTTP {resp.status_code} count={len(caps)} "
      f"first={cap_id}({first.get('name')})")

resp = c.post(f"/api/hub/capabilities/{cap_id}/toggle", headers=A,
              json={"enabled": False})
d = resp.json()
print(f"[B2] toggle off: HTTP {resp.status_code} "
      f"enabled={(d.get('capability') or d).get('enabled')}")
resp = c.post(f"/api/hub/capabilities/{cap_id}/toggle", headers=A,
              json={"enabled": True})
d = resp.json()
print(f"[B3] toggle on: HTTP {resp.status_code} "
      f"enabled={(d.get('capability') or d).get('enabled')} (切回)")

# ============================================================
# C 输入链
# ============================================================

resp = c.post("/api/hub/input/intent", headers=M1,
              json={"text": "这瓶酒多少钱"})
d = resp.json()
print(f"[C1] intent: HTTP {resp.status_code} "
      f"intent={d.get('intent')} route={d.get('route') or d.get('module')} "
      f"count={d.get('count')}")

resp = c.post("/api/hub/asr", headers=M1, json={
    "audio_b64": base64.b64encode(b"fake-audio-bytes").decode(),
    "fmt": "webm"})
d = resp.json()
print(f"[C2] asr(no llm key): HTTP {resp.status_code} "
      f"success={d.get('success')} "
      f"fallback={str(d.get('fallback_hint', d.get('error', '')))[:50]}")

# ============================================================
# D 治理链(admin)
# ============================================================

resp = c.get("/api/hub/ops/intents", headers=A)
d = resp.json()
print(f"[D1] intents: HTTP {resp.status_code} "
      f"{json.dumps(d.get('distribution') or d, ensure_ascii=False)[:110]}")

resp = c.get("/api/hub/ops/overview", headers=A)
d = resp.json()
print(f"[D2] overview: HTTP {resp.status_code} "
      f"matrix={len(d.get('capabilityMatrix') or [])} "
      f"intent7d={json.dumps(d.get('intentDistribution7d') or {}, ensure_ascii=False)[:60]}")

resp = c.post("/api/hub/ops/learning/retrigger", headers=A, json={})
d = resp.json()
print(f"[D3] retrigger: HTTP {resp.status_code} total={d.get('total')} "
      f"learned={d.get('learned')} skipped={d.get('skipped')}")

resp = c.get("/api/hub/ops/usage", headers=A)
d = resp.json()
print(f"[D4] usage: HTTP {resp.status_code} "
      f"keys={sorted(d.keys())[:8]} "
      f"totals={json.dumps((d.get('totals') or {}), ensure_ascii=False)[:80]}")

resp = c.get("/api/hub/ops/learning/approvals", headers=A)
d = resp.json()
print(f"[D5] approvals: HTTP {resp.status_code} "
      f"pending={len(d.get('pending') or d.get('challengers') or [])}")

# ============================================================
# E 审批负测
# ============================================================

resp = c.post("/api/hub/ops/learning/approve/lt-not-exist", headers=A)
print(f"[E1] approve unknown: HTTP {resp.status_code} (expect 404)")

resp = c.post("/api/hub/ops/learning/reject/lt-not-exist", headers=A,
              json={"reason": "LT"})
print(f"[E2] reject unknown: HTTP {resp.status_code} (expect 404)")

resp = c.get("/api/hub/ops/overview")
print(f"[E3] no auth: HTTP {resp.status_code} (expect 403)")

# ============================================================
# F 媒体链(上传+清理)
# ============================================================

voice_b64 = base64.b64encode(b"LT-fake-webm-audio").decode()
resp = c.post("/api/hub/media/voice", headers=M1,
              json={"data_b64": voice_b64, "fmt": "webm"})
d = resp.json()
voice_url = (d.get("data") or d).get("url") or d.get("url")
print(f"[F1] media voice: HTTP {resp.status_code} "
      f"url={voice_url} success={(d.get('data') or d).get('success')}")

img_b64 = base64.b64encode(b"LT-fake-jpeg").decode()
resp = c.post("/api/hub/media/image", headers=M1,
              json={"data_b64": img_b64, "fmt": "jpg"})
d = resp.json()
print(f"[F2] media image: HTTP {resp.status_code} "
      f"url={(d.get('data') or d).get('url') or d.get('url')}")

# ============================================================
# 清理(差集 + media文件)
# ============================================================

removed = 0
for k in set(r.keys("zhuxiang:hub:*")) - before:
    if k.endswith(":seq"):
        continue
    r.delete(k)
    removed += 1

# media 文件清理(容器内 media 目录 LT 内容)
media_files = []
for pattern in ("/app/media/voice/*", "/app/media/image/*",
                "media/voice/*", "media/image/*"):
    media_files.extend(glob.glob(pattern))
for p in media_files:
    try:
        with open(p, "rb") as f:
            if b"LT-" in f.read(64):
                os.unlink(p)
                removed += 1
    except OSError:
        pass

residual = [k for k in set(r.keys("zhuxiang:hub:*")) - before]
print(f"[clean] redis_removed={removed - 0} residual={residual} "
      f"media_scanned={len(media_files)}")
print("HUB E2E DONE")
