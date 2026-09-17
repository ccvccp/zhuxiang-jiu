"""生产执行: traceprod 批次全生产链路实证(报告遗留观察项3)

链路: 建工人→超管授三环节权限+签责任书→建批次→7工段顺序打卡
(含2质检关卡)→生成生命码→绑码→放行→验证(链式哈希/健康度/公开
脱敏溯源)→差集清理(四域键, seq保留)。

执行依据: 生命码管理模块验收报告遗留观察——traceprod 生产批次
链路完整度 0, 走全生产链路可实证承接能力。
"""
import asyncio
import json
import os

os.environ["LOCK_MODE"] = "redis"

import redis

from repositories.member_repository import MemberRepository
from services.perm_service import PermService
from services.trace_prod_service import TraceProdService
from services.trace_service import TraceService

BATCH = "ZX52-2026X02-LT01"
SUPER = 3  # 生产站点管理员(role=admin, 实证扫描)

WORKERS = [
    ("19900000001", "张师傅LT", "production.operate"),
    ("19900000002", "李库管LT", "storage.operate"),
    ("19900000003", "王司机LT", "logistics.operate"),
]


async def main():
    r = redis.Redis(host="redis", port=6379, decode_responses=True)
    patterns = ("zhuxiang:traceprod:*", "zhuxiang:perm:*",
                "zhuxiang:member:*", "zhuxiang:trace:*")

    def snap():
        s = set()
        for p in patterns:
            s |= set(r.keys(p))
        return s

    before = snap()

    member_repo = MemberRepository()
    perm_svc = PermService()
    svc = TraceProdService()
    trace_svc = TraceService()

    # 1 三工人 + 授权 + 签责任书
    ids = {}
    for phone, nick, code in WORKERS:
        m = await member_repo.create({
            "phone": phone, "password": "Lt@12345", "nickname": nick,
            "avatar": "", "gender": 1, "level": 1, "growth_value": 0,
            "points": 0, "status": 1, "reg_source": "phone",
            "role": "member",
        })
        ids[code.split(".")[0]] = m["id"]
        g = await perm_svc.assign_grant(SUPER, m["id"], code)
        await perm_svc.sign_duty(m["id"], g["grantId"])
    brewer = ids["production"]
    storer = ids["storage"]
    shipper = ids["logistics"]
    print(f"[1] workers: brewer={brewer} storer={storer} shipper={shipper}")

    # 2 创建批次
    batch = await svc.create_batch(brewer, BATCH, 52, 100)
    print(f"[2] batch create: {batch['batchNo']} status={batch['status']}")

    # 3 七工段顺序打卡
    punches = [
        (brewer, "STG-BREW", {"窖池号": "LT-1号", "酒度": "52.2"}, ""),
        (storer, "STG-STOR", {"容器号": "LT-T01"}, ""),
        (brewer, "STG-BLEND", {"酒度": "52.0"}, "调配检测合格"),
        (storer, "STG-FILL", {"灌装线": "LT线", "实际灌装量": "100"}, ""),
        (storer, "STG-PACK", {"装箱规格": "6"}, "标签包装合格"),
        (storer, "STG-WARE", {"库位": "LT-A01"}, ""),
        (shipper, "STG-OUT", {"运单号": "LT-SF01"}, ""),
    ]
    for who, stage, params, qc in punches:
        p = await svc.punch(who, stage, BATCH, params=params,
                            qc_conclusion=qc)
        print(f"[3] punch {stage}: result={p['result']} "
              f"anomalies={p['anomalies']}")

    # 4 生成生命码 + 绑码
    gen = await trace_svc.generate_life_codes(
        "ZX52-2026X02", BATCH, 2, product_name="竹奕·竹香小坛LT",
        product_abv=52, product_volume="500ml")
    codes = [l["lifeCode"] for l in gen["lifeCodes"]]
    bind = await svc.bind_life_codes(storer, BATCH, codes)
    print(f"[4] bind: {len(bind['lifeCodes'])} codes")

    # 5 放行
    released = await svc.release_batch(shipper, BATCH)
    print(f"[5] release: status={released['status']}")

    # 6 验证
    from repositories.trace_prod_repository import TraceProdRepository
    repo = TraceProdRepository()
    vc = await repo.verify_chain(BATCH)
    print(f"[6] chain valid={vc['valid']} checked={vc['checked']}")
    health = await svc.trace_health(BATCH)
    print(f"[6] health: {json.dumps(health, ensure_ascii=False)[:300]}")
    pub = await svc.public_trace(BATCH)
    print(f"[6] public trace: {len(pub['timeline'])} timeline nodes")

    # 7 差集清理(seq 保留)
    after = snap()
    new_keys = [k for k in (after - before) if not k.endswith(":seq")]
    for k in new_keys:
        r.delete(k)
    residual = snap() - before
    print(f"[7] cleaned {len(new_keys)} keys, residual={sorted(residual)}")
    print("PROD FULLCHAIN DONE")


asyncio.run(main())
