#!/bin/bash
# DP3 邀请裂变·生产验收
echo "=== A. growth 端点路由守卫(无 token 应 401) ==="
curl -s -o /dev/null -w "invite_code_noauth=%{http_code}\n" -X POST https://zxjiu.com/api/v1/growth/invite-code
curl -s -o /dev/null -w "bind_noauth=%{http_code}\n" -X POST -H 'Content-Type: application/json' -d '{"invite_code":"zxtest"}' https://zxjiu.com/api/v1/growth/referrals/bind
curl -s -o /dev/null -w "invites_me_noauth=%{http_code}\n" https://zxjiu.com/api/v1/growth/invites/me

echo "=== B. 前端接线(drama.html 含邀请元素) ==="
curl -s -o /dev/null -w "drama_html=%{http_code}\n" https://zxjiu.com/drama.html
echo "invite_elements=$(curl -s https://zxjiu.com/drama.html | grep -c -E 'inviteBox|zx_inv|referrals/bind')"

echo "=== C. checkpoint invite 项(标题应来自阶梯表) ==="
EP=$(curl -s https://zxjiu.com/api/v1/dramas/drama_zhuxiang_01 | python3 -c "import sys,json;d=json.load(sys.stdin)['data'];free=d['free_episodes'];print([e['episode_id'] for e in d['episodes'] if e['episode_number']==free+1][0])")
echo "checkpoint_ep=$EP"
curl -s "https://zxjiu.com/api/v1/dramas/drama_zhuxiang_01/episodes/$EP/checkpoint" | python3 -c "import sys,json;m=json.load(sys.stdin)['data']['unlock_methods'][1];print('invite_item:',m['type'],'|',m['title'],'|',m['action_url'])"

echo "=== D. 容器启动与 DP3 导入 ==="
docker exec zhuxiang-backend-1 python -c "from services.drama76_growth_service import DramaGrowthService, REWARD_LADDERS; print('import_ok ladders=%d' % len(REWARD_LADDERS))"
docker logs zhuxiang-backend-1 --since 5m 2>&1 | grep -E "Started|drama76|ERROR" | tail -5
