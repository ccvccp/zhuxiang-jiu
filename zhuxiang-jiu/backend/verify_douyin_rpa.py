"""36号·抖音 RPA 通道生产 E2E 验证(2026-09-27 部署)

链路: member3 登录(Bearer) → 通道状态(mode=real) → 雷达扫描 →
engaged 热点(无则取最高分可跟进项手动 decide) → 生成抖音内容
(GLM Agent) → 人工过审 → 入队(立即到期) → 出队 → rpa_pending
清单含 douyin 条目 + 封面公开可达。

残留说明: 验证内容保持 rpa_pending(供首次「发布抖音待发内容」
真实消费); 不登记回执、不删数据。

运行: python3 verify_douyin_rpa.py (生产服务器本机)
"""
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = "http://127.0.0.1:8000"
PASS = 0
FAIL = 0


def call(method, path, token=None, body=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data,
                                  headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"detail": str(e)}


def record(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  OK {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} -- {detail}")


def finish():
    print("=" * 56)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return 1 if FAIL else 0


def main():
    # 1. 登录
    st, body = call("POST", "/api/auth/login",
                    body={"phone": "13800000002",
                          "password": "test123456"})
    token = body.get("accessToken") \
        or (body.get("data") or {}).get("accessToken", "")
    record("member3 登录取 Bearer", st == 200 and bool(token),
           f"st={st} {str(body)[:150]}")
    if not token:
        return finish()

    # 2. 通道状态
    st, body = call("GET", "/api/promo/channels/status", token)
    rows = body.get("data") or []
    mode = rows[0].get("mode") if rows else ""
    douyin = next((r for r in rows
                   if r.get("platform") == "douyin"), {})
    record("通道总模式 real", mode == "real", f"mode={mode}")
    record("douyin 通道行存在", bool(douyin), f"rows={rows}")

    # 3. rpa_pending 基线
    st, body = call("GET", "/api/promo/rpa/pending", token)
    before = body.get("data") or []
    print(f"  .. rpa_pending 基线: {len(before)} 条 "
          f"{[r.get('platform') for r in before]}")

    # 4. 雷达扫描(冷却期 409 容忍——调度器 15min 同源动作)
    st, body = call("POST", "/api/promo/radar/scan", token)
    record("雷达扫描(409=冷却期, 容忍)", st in (200, 409),
           f"st={st} {str(body)[:150]}")

    # 5. engaged 热点(无则取可跟进项手动 decide)
    st, body = call("GET", "/api/promo/radar/hotspots?status=engaged",
                     token)
    hotspots = body.get("data") or []
    if not hotspots:
        st2, body2 = call("GET", "/api/promo/radar/hotspots", token)
        all_hs = body2.get("data") or []
        cand = next((h for h in all_hs if h.get("status")
                     not in ("passed", "discarded", "engaged")), None)
        if cand:
            st3, body3 = call(
                "POST",
                f"/api/promo/decisions/{cand['hotspotId']}/decide",
                token, {"engage": True, "note": "douyin-verify"})
            record(f"手动跟进热点 {cand.get('hotspotId')}",
                   st3 == 200, f"st={st3} {str(body3)[:150]}")
            if st3 == 200:
                hotspots = [cand]
    record("存在 engaged 热点", bool(hotspots), "无可用热点")
    if not hotspots:
        return finish()

    # 6. 生成抖音内容(GLM Agent + 三审闸门)——遍历 engaged
    #    热点直到找到冷却未满额者(上限2条/48h per fingerprint)
    c, cid = None, None
    cooldown_hits = []
    for hs in hotspots[:6]:
        st, body = call("POST", "/api/promo/contents/generate", token,
                        {"hotspotId": hs["hotspotId"],
                         "platforms": ["douyin"]})
        if st == 200:
            contents = body.get("data") or []
            if isinstance(contents, dict):
                contents = (contents.get("contents")
                            or contents.get("items") or [contents])
            if contents:
                c, cid = contents[0], contents[0].get("contentId")
                break
        detail = str(body.get("error") or body)[:120]
        if "冷却" in detail:
            cooldown_hits.append(hs["hotspotId"])
        else:
            print(f"  .. 热点 #{hs['hotspotId']} 生成失败: {detail}")
    if c is None and cooldown_hits:
        print(f"  .. 全部 engaged 热点冷却满额(2条/48h): "
              f"{cooldown_hits}")
    record("生成 douyin 内容", c is not None,
           f"tried={len(hotspots[:6])} cooldown={cooldown_hits}")
    if c is None:
        return finish()
    print(f"  .. 内容 #{cid} 《{c.get('title', '')}》 "
          f"compliance={c.get('complianceScore')}")
    print(f"     body: {str(c.get('body', ''))[:120]}")

    # 7. 人工过审(HITL)
    st, body = call("POST", f"/api/promo/contents/{cid}/review", token,
                    {"approved": True, "reviewer": "douyin-verify"})
    record("人工过审", st == 200, f"st={st} {str(body)[:200]}")
    if st != 200:
        return finish()

    # 8. 入队(1 分钟前 → 立即到期)
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    st, body = call("POST",
                    f"/api/promo/contents/{cid}/publish", token,
                    {"publishAt": past.isoformat()})
    record("入发布队列(立即到期)", st == 200,
           f"st={st} {str(body)[:200]}")
    if st != 200:
        print("  .. 入队失败(409=单日上限则属闸门正常拦截)")
        return finish()

    # 9. 出队(调度器 300s 同源动作, 手动触发)
    st, body = call("POST", "/api/promo/publish/process", token)
    published = body.get("data") or []
    mine = [p for p in published if p.get("contentId") == cid]
    record("出队处理", st == 200 and bool(mine),
           f"st={st} n={len(published)}")
    if mine:
        r = mine[0].get("receipt") or {}
        record("douyin 回执 mode=rpa_pending",
               r.get("mode") == "rpa_pending", f"receipt={r}")

    # 10. rpa_pending 清单含 douyin + 字段完整 + 封面可达
    st, body = call("GET", "/api/promo/rpa/pending", token)
    after = body.get("data") or []
    dy = [r for r in after if r.get("platform") == "douyin"]
    record("清单含 douyin 条目", bool(dy),
           f"total={len(after)} dy={len(dy)}")
    if dy:
        row = dy[0]
        record("字段完整(title/body/hashtags/coverUrl)",
               bool(row.get("title")) and bool(row.get("body"))
               and bool(row.get("hashtags"))
               and bool(row.get("coverUrl")), f"row={row}")
        st, body = call("GET", row["coverUrl"])
        record("封面端点公开可达", st == 200, f"st={st}")

    print(f"\n内容 #{cid} 保持 rpa_pending —— 首次「发布抖音待发内容」"
          "即可真实消费(creator.douyin.com 需扫码登录一次)")
    return finish()


if __name__ == "__main__":
    sys.exit(main())
