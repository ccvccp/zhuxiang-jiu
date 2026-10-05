#!/bin/bash
echo "=== 近 10 分钟 episodes/me 与 growth access ==="
docker logs zhuxiang-backend-1 --since 10m 2>&1 | grep -E "episodes/me|growth/|unlocks" | tail -15
echo "=== 近 10 分钟 5xx/4xx ==="
docker logs zhuxiang-backend-1 --since 10m 2>&1 | grep -E " 4[0-9]{2} | 5[0-9]{2} " | grep -vE "collect|static" | tail -8
echo "=== B token 剩余有效期 ==="
JWT_B=$(cat /opt/zhuxiang/deploy_tmp/dp4/jwt_b.txt)
python3 - "$JWT_B" <<'EOF'
import sys, base64, json, time
t = sys.argv[1].split('.')[1]
t += '=' * (-len(t) % 4)
p = json.loads(base64.urlsafe_b64decode(t))
print('exp:', p['exp'], 'now:', int(time.time()), 'left_s:', p['exp']-int(time.time()))
EOF
