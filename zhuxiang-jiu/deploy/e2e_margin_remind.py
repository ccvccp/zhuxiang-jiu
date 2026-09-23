"""保证金提醒真实运维通道 E2E(容器内执行)

快照→临时改 locked(20天后到期,30档)→remind 两轮
(验证下发+幂等)→finally 还原 settled 原值→后续
外部跑 audit 复平。真实短信此前已实证(bizId),
本 E2E 验证运维链(档位/幂等/留痕/无损还原)。
"""
import asyncio
import copy
import json
from datetime import datetime, timedelta

from repositories.citystore_repository import (
    CityStoreRepository,
    MARGIN_STATUS_LOCKED,
    MARGIN_STATUS_SETTLED,
)
from services.citystore_service import CityStoreService


def brief(r) -> str:
    return json.dumps(
        r, ensure_ascii=False,
        default=str)[:500]


async def main() -> None:
    repo = CityStoreRepository()
    svc = CityStoreService()
    settled = await repo.list_all_margins(
        MARGIN_STATUS_SETTLED)
    if not settled:
        print("E2E-ABORT no-settled-margin")
        return
    m = settled[0]
    orig = copy.deepcopy(m)
    print("target:", m.get("marginNo"),
          m.get("storeCode"),
          "amount=", m.get("amount"))
    today = datetime.utcnow().date()
    m["status"] = MARGIN_STATUS_LOCKED
    m["startDate"] = (
        today - timedelta(days=345)).isoformat()
    m["endDate"] = (
        today + timedelta(days=20)).isoformat()
    m.pop("remindedSteps", None)
    await repo.save_margin(m)
    try:
        r1 = await svc.run_margin_reminder_round()
        print("round1:", brief(r1))
        r2 = await svc.run_margin_reminder_round()
        print("round2:", brief(r2))
    finally:
        await repo.save_margin(orig)
        print("restored:", orig.get("marginNo"),
              orig.get("status"))
    after = await repo.get_margin(
        orig.get("marginNo"))
    print("verify-restored:",
          after.get("status")
          == MARGIN_STATUS_SETTLED,
          "endDate=", after.get("endDate"))


asyncio.run(main())
