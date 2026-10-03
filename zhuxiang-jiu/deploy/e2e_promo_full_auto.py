"""全自动发布链路·生产 E2E 验证 v2(2026-10-03 夜)

v2: 分段容错(单段失败不弃整链) + receipt 防御取值 +
    glm-5.3 轨验证(LLM_TIMEOUT_PROMO=120 后应不再回退 rule)。
"""
import subprocess
import sys

HOST = "root@47.236.61.117"

INNER = r'''
import asyncio, json, traceback
from datetime import datetime, UTC

async def main():
    out = {}
    try:
        from services.promo_service import PromoService
        from repositories.promo_repository import PROMO_LLM_MODEL
        svc = PromoService()
        out["llmModel"] = PROMO_LLM_MODEL

        # 0. 最新 engaged 热点
        from repositories.backend import get_redis_client
        c = await get_redis_client()
        dec_keys = [k for k in await c.keys(
            "zhuxiang:promo:promo_decisions:*")
            if not k.endswith(":seq")]
        engaged = []
        for k in dec_keys[-80:]:
            raw = await c.get(k)
            if not raw:
                continue
            d = json.loads(raw if isinstance(raw, str)
                           else raw.decode())
            if d.get("decision") == "auto_engage":
                engaged.append(d)
        engaged.sort(key=lambda d: d.get("decisionId", 0),
                     reverse=True)
        hs_id = engaged[0]["hotspotId"]
        out["hotspotId"] = hs_id

        # 1. [断点1] 生成(冷却闸容错: 逐热点尝试至成功)
        contents = None
        hs_id = None
        for d in engaged[:5]:
            try:
                cand = await svc.generate_contents(
                    d["hotspotId"],
                    platforms=("xiaohongshu",))
                if cand:
                    contents = cand
                    hs_id = d["hotspotId"]
                    out["hotspotId"] = hs_id
                    break
            except ValueError as ve:
                out.setdefault("cooldownSkipped", []).append(
                    f"{d['hotspotId']}:{str(ve)[:30]}")
        if not contents:
            raise ValueError(
                "前 5 个 engaged 热点均在冷却期——无可用热点")
        ct = contents[0]
        cid = ct["contentId"]
        out.update({
            "contentId": cid,
            "status": ct.get("status"),
            "complianceScore": ct.get("complianceScore"),
            "requiresManualReview": ct.get(
                "requiresManualReview"),
            "shortCode": ct.get("shortCode"),
            "title": (ct.get("title") or "")[:36],
        })
        tr = ct.get("agentTrace")
        if isinstance(tr, dict):
            steps = tr.get("steps") or tr.get("chain") or []
            tracks = [s.get("track") for s in steps
                      if isinstance(s, dict)]
            out["agentTracks"] = tracks or tr.get("tracks") or []
        else:
            out["agentTracks"] = []

        # 2. [断点2] 人工审(HITL)
        rv = await svc.review_content(
            cid, approved=True, reviewer="E2E验证")
        out["reviewStatus"] = rv.get("status")

        # 3. [断点3] 入队(now)
        pq = await svc.publish_content(
            cid, publish_at=datetime.now(UTC).isoformat())
        out["queueStatus"] = pq.get("status")

        # 4. 出队发布
        receipts = await svc.process_publish_queue()
        pick = None
        for r in receipts:
            if r.get("contentId") in (cid, str(cid)):
                pick = r
                break
        if pick is None and receipts:
            pick = receipts[0]
        if pick:
            out["receipt"] = {
                "contentId": pick.get("contentId"),
                "mode": pick.get("mode"),
                "error": (pick.get("error") or "")[:70],
                "status": pick.get("status"),
            }

        # 5. RPA 待发布 + SEO 计数
        try:
            from services.promo_rpa_channel_service import (
                PromoRpaChannelService,
            )
            pending = await PromoRpaChannelService()\
                .list_pending()
            out["rpaPending"] = len(pending)
        except Exception as e:
            out["rpaPendingErr"] = str(e)[:80]
        seo = await c.keys(
            "zhuxiang:promo:promo_seo_pushes:*")
        out["seoPushCount"] = len(
            [k for k in seo if not k.endswith(":seq")])
    except Exception:
        out["fatal"] = traceback.format_exc()[-400:]

    print("@@BEGIN@@")
    print(json.dumps(out, ensure_ascii=False, default=str))
    print("@@END@@")

asyncio.run(main())
'''


def main():
    import base64
    b64 = base64.b64encode(INNER.encode()).decode()
    cmd = (f"echo {b64} | base64 -d | "
           f"docker exec -i zhuxiang-backend-1 python -")
    r = subprocess.run(["ssh", HOST, cmd],
                       capture_output=True, text=True,
                       timeout=600)
    print(r.stdout)
    if "llm_chat_failed" in (r.stderr or ""):
        print("[!] 仍有 LLM 超时回退:",
              r.stderr.count("llm_chat_failed"), "次")
    if "@@BEGIN@@" not in r.stdout:
        print("[stderr]", (r.stderr or "")[:400])
        raise SystemExit(1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
