#!/bin/bash
TA=$(cat /opt/zhuxiang/deploy_tmp/dp4/jwt_a.txt)
TB=$(cat /opt/zhuxiang/deploy_tmp/dp4/jwt_b.txt)
TB2=$(cat /opt/zhuxiang/deploy_tmp/dp4/jwt_b2.txt)
cat > /tmp/repro_auth.py <<EOF
import asyncio

async def m():
    from services.auth_service import AuthService
    for name, t in (('B_old', "$TB"), ('B_new', "$TB2"), ('A', "$TA")):
        try:
            mem = await AuthService().get_current_member(t)
            print(name, 'OK memberId=', mem['memberId'])
        except Exception as e:
            print(name, 'FAIL:', type(e).__name__, str(e)[:150])

asyncio.run(m())
EOF
docker cp /tmp/repro_auth.py zhuxiang-backend-1:/tmp/repro_auth.py
docker exec -w /app zhuxiang-backend-1 python /tmp/repro_auth.py
