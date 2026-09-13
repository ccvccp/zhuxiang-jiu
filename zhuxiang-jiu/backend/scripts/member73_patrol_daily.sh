#!/bin/bash
# member73_patrol_daily.sh · 73号会员体验大模型 assist 期每日巡检
# 用法: bash member73_patrol_daily.sh
# 巡检七面(assist 期——71号 assist 监控范式):
#   ① 模型状态(mode=assist/kill=false/免疫 active)
#   ② 漂移三信号(空=正常; ≥1=跟进)
#   ③ 免疫看板(active/冻结事件零)
#   ④ moments 决策分布(present 渲染率/
#      reason 命中[静默窗/封顶/免疫]/总量)
#   ⑤ 进化日志异常(drift_detected/freeze/
#      unfreeze/negative_feedback 计数)
#   ⑥ 零影响回归(71号 assist/69号/前端)
#   ⑦ 容器健康(docker healthy/日志错误)
# 判定: 全绿=PASS; 有红=按处置路径跟进
#   - 冻结事件 → MEMBER73_IMMUNITY=1 授权流解冻
#   - 5xx/容器异常 → 热修或 bash member73_transfer.sh off
#   - 紧急 → MEMBER73_KILL=1(观测面保留)

set -u
BASE=https://zxjiu.com/api/member73
ADMIN="X-Role: admin"
PASS_CNT=0; RED_CNT=0

section() { echo; echo "=== $1 ==="; }
ok()  { PASS_CNT=$((PASS_CNT+1)); echo "  [绿] $1"; }
red() { RED_CNT=$((RED_CNT+1));  echo "  [红] $1"; }

section "① 模型状态"
STATUS=$(curl -s -m 15 -H "$ADMIN" "$BASE/model/status")
MODE=$(echo "$STATUS" | grep -o '"mode":"[a-z]*"' | grep -o '[a-z]*"$' | tr -d '"')
IMM=$(echo "$STATUS" | python3 -c "import json,sys; print(json.load(sys.stdin)['data']['immunity']['status'])" 2>/dev/null)
KILL=$(echo "$STATUS" | grep -o '"kill":[a-z]*' | grep -o ':[a-z]*' | tr -d ':')
[ "$MODE" = "assist" ] && ok "mode=assist" || red "mode=$MODE(期望 assist)"
[ "$KILL" = "false" ] && ok "kill=false" || red "KILL 静默中——立即人工介入"
[ "$IMM" = "active" ] && ok "免疫 active" || red "免疫=$IMM(冻结态——按处置路径跟进)"

section "② 漂移三信号"
DRIFT=$(curl -s -m 15 -X POST -H "$ADMIN" "$BASE/meta/drift")
SIGNALS=$(echo "$DRIFT" | python3 -c "import json,sys; print(json.load(sys.stdin)['data']['signals'])" 2>/dev/null)
RENDERED=$(echo "$DRIFT" | grep -o '"renderedTotal":[0-9]*' | grep -o '[0-9]*$')
RATE=$(echo "$DRIFT" | grep -o '"responseRate":[0-9.]*' | grep -o '[0-9.]*$')
[ "$SIGNALS" = "[]" ] && ok "三信号空(renderedTotal=$RENDERED responseRate=$RATE)" \
                     || red "漂移信号=$SIGNALS——评估免疫冻结前置"

section "③ 免疫看板"
IMMV=$(curl -s -m 15 -H "$ADMIN" "$BASE/immunity")
RT=$(echo "$IMMV" | grep -o '"redteamRuns":[0-9]*' | grep -o '[0-9]*$')
IMM_S=$(echo "$IMMV" | python3 -c "import json,sys; print(json.load(sys.stdin)['data']['status'])" 2>/dev/null)
[ "$IMM_S" = "active" ] && ok "免疫 active·冻结事件零(redteamRuns=$RT)" \
                     || red "免疫=$IMM_S(存在冻结——查 unfreeze 授权流)"

section "④ moments 决策分布"
MOMENTS=$(curl -s -m 15 -H "$ADMIN" "$BASE/mentor/moments?limit=500")
DIST=$(echo "$MOMENTS" | python3 -c "
import json,sys,collections
rows=json.load(sys.stdin)['data']
c=collections.Counter(r.get('decision') for r in rows)
rc=collections.Counter(r.get('reason') for r in rows)
rendered=sum(1 for r in rows if r.get('rendered'))
print('total={} present={} defer={} abandon={} rendered={} | reason: score={} silence={} cap={}'.format(
    len(rows), c.get('present',0), c.get('defer',0), c.get('abandon',0), rendered,
    rc.get('score',0), rc.get('silence_window',0), rc.get('daily_cap',0)))" 2>/dev/null || echo "parse_fail")
if [ "$DIST" != "parse_fail" ]; then
  ok "$DIST"
else
  red "moments 解析失败——查容器日志"
fi

section "⑤ 进化日志异常扫描"
EVO=$(curl -s -m 15 -H "$ADMIN" "$BASE/evolution/log?limit=500")
EVODIST=$(echo "$EVO" | python3 -c "
import json,sys,collections
rows=json.load(sys.stdin)['data']
c=collections.Counter(r.get('kind') for r in rows)
anom=c.get('drift_detected',0)+c.get('freeze',0)+c.get('unfreeze',0)
print('form_learning={} negative_feedback={} drift={} freeze={} unfreeze={} redteam={}'.format(
    c.get('form_learning',0), c.get('negative_feedback',0),
    c.get('drift_detected',0), c.get('freeze',0), c.get('unfreeze',0), c.get('redteam',0)))
exit(1 if anom>0 else 0)" 2>/dev/null)
EVORC=$?
if [ $EVORC -eq 0 ]; then ok "无漂移/冻结/解冻异常: $EVODIST"
else red "异常事件留痕: $EVODIST——逐条核对 /evolution/log"; fi

section "⑥ 零影响回归"
P71=$(curl -s -m 15 -H "$ADMIN" https://zxjiu.com/api/pay71/model/status | grep -o '"mode":"[a-z]*"' | grep -o '[a-z]*"$' | tr -d '"')
[ "$P71" = "assist" ] && ok "71号 assist 保持" || red "71号 mode=$P71(期望 assist)"
C69=$(curl -s -m 15 -o /dev/null -w '%{http_code}' -H "$ADMIN" https://zxjiu.com/api/pay69/channels)
[ "$C69" = "200" ] && ok "69号 channels 200" || red "69号 channels=$C69"
FE=$(curl -s -m 15 -o /dev/null -w '%{http_code}' https://zxjiu.com/)
[ "$FE" = "200" ] && ok "前端 200" || red "前端=$FE"

section "⑦ 容器健康"
CT=$(docker ps --filter name=zhuxiang-backend --format '{{.Status}}')
echo "$CT" | grep -q healthy && ok "容器 $CT" || red "容器 $CT"
ERRLOG=$(docker logs zhuxiang-backend-1 --since 24h 2>&1 \
  | grep -cE 'Traceback \(most recent call last\)|\[ERROR\]' || true)
if [ "${ERRLOG:-0}" -le 0 ] 2>/dev/null; then
  ok "24h 日志零真实异常(RuntimeWarning 不计)"
else
  red "日志异常行数=$ERRLOG——docker logs --since 24h 逐条核对"
fi

section "巡检结论"
echo "  绿: $PASS_CNT 项 | 红: $RED_CNT 项"
if [ "$RED_CNT" -eq 0 ]; then
  echo "  ✅ 全绿——assist 期运行正常"
  echo "  巡检时间: $(date -u +'%Y-%m-%dT%H:%M:%SZ')"
else
  echo "  !! 有红——按脚本头部处置路径跟进"
  echo "  巡检时间: $(date -u +'%Y-%m-%dT%H:%M:%SZ')"
  exit 1
fi
