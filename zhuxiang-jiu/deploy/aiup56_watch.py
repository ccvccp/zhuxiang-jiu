#!/usr/bin/env python3
"""56号·受控自治日常观测巡检(aiup56_watch)

部署: 服务器 /opt/zhuxiang/obs/aiup56_watch.py(容器外,
      docker exec 调容器内服务层——零新增镜像依赖)
cron: 每小时(crontab -l 见 aiup56_watch 条目)
日志: /opt/zhuxiang/obs/aiup56_watch.log(变化时追加)

语义:
    - 漏斗九态分布 + 与上次快照 diff(无变化静默)
    - 自主审批/交付/验证失败事件捕捉(高亮 ⚠)
    - fail-soft: 任何异常记一行错误不中断
"""

import json
import subprocess
import sys
from datetime import datetime, timezone, timedelta

CONTAINER = "zhuxiang-backend-1"
OBS_DIR = "/opt/zhuxiang/obs"
LOG = f"{OBS_DIR}/aiup56_watch.log"
LAST = f"{OBS_DIR}/aiup56_last.json"

CST = timezone(timedelta(hours=8))


def _py_script() -> str:
    return (
        "import asyncio,json\n"
        "async def main():\n"
        "    from services.aiup56_dashboard_service"
        " import Aiup56DashboardService\n"
        "    from repositories.aiup56_repository"
        " import Aiup56Repository\n"
        "    d = await Aiup56DashboardService().build()\n"
        "    fun = (d.get('zones') or {}).get('funnel') or {}\n"
        "    repo = Aiup56Repository()\n"
        "    events = await repo.list_events(limit=200)\n"
        "    watched = [e for e in (events or []) if"
        " str(e.get('eventType')) in"
        " ('auto_review','deliver','deliver_verify_failed',"
        "'rollback','approve','reject')]\n"
        "    print(json.dumps({'funnel': fun,"
        " 'watched': len(watched),"
        " 'lastEvent': watched[0] if watched else None},"
        " ensure_ascii=False, default=str))\n"
        "asyncio.run(main())\n"
    )


def collect() -> dict | None:
    try:
        r = subprocess.run(
            ["docker", "exec", CONTAINER, "python", "-c",
             _py_script()],
            capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            _log(f"ERROR collect rc={r.returncode} "
                 f"{r.stderr.strip()[:200]}")
            return None
        return json.loads(r.stdout.strip().splitlines()[-1])
    except Exception as exc:            # noqa: BLE001
        _log(f"ERROR collect {exc}")
        return None


def _log(line: str) -> None:
    now = datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"[{now}] {line}\n")


def main() -> int:
    snap = collect()
    if snap is None:
        return 1
    funnel = snap.get("funnel") or {}
    by = funnel.get("byStatus") or {}
    try:
        prev = json.load(open(LAST, encoding="utf-8"))
    except Exception:                   # noqa: BLE001
        prev = None

    cur = {
        "total": funnel.get("total"),
        "byStatus": by,
        "watched": snap.get("watched"),
        "lastEventId": (snap.get("lastEvent") or {}
                        ).get("eventId"),
    }

    if prev != cur:
        # 有变化 → 记日志(首跑也记一次基线)
        flag = ""
        if (prev is not None
                and (by.get("delivered") or 0) >
                ((prev.get("byStatus") or {})
                 .get("delivered") or 0)):
            flag = " 🎉新交付"
        if (prev is not None
                and (cur.get("watched") or 0) >
                (prev.get("watched") or 0)):
            flag += " ⚠新审批/交付事件(见漏斗)"
        _log(
            f"漏斗 total={cur['total']} "
            f"approved={by.get('approved')} "
            f"delivered={by.get('delivered')} "
            f"rolled_back={by.get('rolled_back')} "
            f"待审audited={by.get('audited')}{flag}")
        if snap.get("lastEvent"):
            le = snap["lastEvent"]
            _log(f"  最近事件 {le.get('eventType')} "
                 f"proposal={le.get('proposalId')} "
                 f"{str(le.get('detail'))[:160]}")
        json.dump(cur, open(LAST, "w",
                            encoding="utf-8"),
                  ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
