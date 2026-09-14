#!/bin/bash
# attract72-transfer.sh · 72号AI智能自动引流大模型 四档转段脚本
# 用法: bash attract72-transfer.sh assist|shadow|off|full|status
# 前置: 服务器 root SSH; .env 已含 ATTRACT72_MODE 条目(shadow 期写入)
# 回滚保险: .env.bak-attract72-off(off 期原件)始终保留
# 范式: pay71_transfer.sh 平移 + full 档扩展(引流域首创——红队全防御门控)

set -e
MODE="${1:-status}"
ENV_FILE=/opt/zhuxiang/.env
BAK_FILE=/opt/zhuxiang/.env.bak-attract72-off
API="https://zxjiu.com/api/attract72"

confirm() {
  echo ">>> 即将执行: $1"
  read -r -p "确认? (y/N) " ans
  [ "$ans" = "y" ] || { echo "已取消"; exit 1; }
}

current() {
  grep -E "^ATTRACT72_MODE=" "$ENV_FILE" | cut -d= -f2 || echo "(未设置=off 默认)"
}

case "$MODE" in
  status)
    echo "当前 .env: ATTRACT72_MODE=$(current)"
    echo "容器实际: $(docker exec zhuxiang-backend-1 printenv ATTRACT72_MODE 2>/dev/null || echo off-默认)"
    echo "公网状态: $(curl -s -m 8 -H 'X-Role: admin' $API/model/status | grep -o '"mode":"[a-z]*"')"
    echo "红队核验: $(curl -s -m 8 -H 'X-Role: admin' $API/redteam | grep -o '"allDefended":[a-z]*' | head -1)"
    exit 0
    ;;

  assist)
    # 转段前置检查(影子期评估模板 §五 判据——此处仅机检, 人工签核另核)
    DAYS=$(docker ps --filter name=zhuxiang-backend --format '{{.RunningFor}}')
    echo "容器运行时长: $DAYS (人工确认影子期充分)"
    HEALTH=$(curl -s -m 8 -H 'X-Role: admin' "$API/meta/health" | grep -o '"verdict":"[a-z]*"')
    echo "健康度: $HEALTH (须 healthy/degraded)"
    echo "$HEALTH" | grep -q '"frozen"' && { echo "!! 健康度冻结态——禁止转段(解冻人工专属)"; exit 1; }
    confirm "shadow→assist 转段(决策面: 落档不执行→方案产出+人工确认)"
    sed -i 's/^ATTRACT72_MODE=.*/ATTRACT72_MODE=assist/' "$ENV_FILE"
    ;;

  full)
    # full 转段三重门控(引流域首创 full 档——红队全防御+健康度+逐档)
    CUR=$(current)
    [ "$CUR" = "assist" ] || { echo "!! full 须从 assist 升档(当前 $CUR——逐档铁律)"; exit 1; }
    RT=$(curl -s -m 8 -H 'X-Role: admin' "$API/redteam")
    echo "$RT" | grep -q '"allDefended":true' || { echo "!! 最近红队未全防御——先 POST $API/redteam/run"; exit 1; }
    echo "红队核验: 全防御在案 ✓"
    HEALTH=$(curl -s -m 8 -H 'X-Role: admin' "$API/meta/health" | grep -o '"verdict":"[a-z]*"')
    echo "$HEALTH" | grep -q '"frozen"' && { echo "!! 健康度冻结态——禁止转段"; exit 1; }
    echo "L1 白名单口径: 仅 exploration_ratio/landing_variant_weight/topic_queue_threshold"
    echo "              奖励系数/定律边界/合规参数永不可自主(46号审批)"
    confirm "assist→full 转段(L1 低风险域自主: 选题入队/落地页变体/沙箱实验)"
    sed -i 's/^ATTRACT72_MODE=.*/ATTRACT72_MODE=full/' "$ENV_FILE"
    ;;

  shadow)
    confirm "切至 shadow 影子期(决策面开放·落档不执行)"
    if grep -q '^ATTRACT72_MODE=' "$ENV_FILE"; then
      sed -i 's/^ATTRACT72_MODE=.*/ATTRACT72_MODE=shadow/' "$ENV_FILE"
    else
      printf '\nATTRACT72_MODE=shadow\n' >> "$ENV_FILE"
    fi
    ;;

  off)
    confirm "回滚 off 期(决策面关闭 409, 观测面不变)"
    if [ -f "$BAK_FILE" ]; then
      cp "$BAK_FILE" "$ENV_FILE"   # 一键回滚原始件
    else
      sed -i 's/^ATTRACT72_MODE=.*/ATTRACT72_MODE=off/' "$ENV_FILE" 2>/dev/null || true
      grep -q '^ATTRACT72_MODE=' "$ENV_FILE" || printf '\nATTRACT72_MODE=off\n' >> "$ENV_FILE"
    fi
    ;;

  *)
    echo "用法: bash $0 assist|shadow|off|full|status"
    exit 2
    ;;
esac

# 重建容器(env 变更须 recreate 生效)
cd /opt/zhuxiang
docker compose up -d backend
echo "等待容器就绪..."
sleep 14

# 落地验证(三层: 容器 env → 本地 API → 公网)
ACTUAL=$(docker exec zhuxiang-backend-1 printenv ATTRACT72_MODE)
echo "容器 ATTRACT72_MODE=$ACTUAL"
curl -s -m 8 -o /dev/null -w "本地健康: %{http_code}\n" http://127.0.0.1:8000/api/decision/health
PUB=$(curl -s -m 15 -H 'X-Role: admin' "$API/model/status")
echo "公网状态: $(echo "$PUB" | grep -o '"mode":"[a-z]*"')"

if echo "$PUB" | grep -q "\"mode\":\"$MODE\""; then
  echo "✅ 转段成功: $MODE"
  case "$MODE" in
    assist)
      echo "—— assist 期语义: 决策面方案产出+人工确认位"
      echo "   (卡位决策 confirm→execute / 实验提案 46号 / 系数建议 46号)"
      echo "—— 执行永不直接操作 40号账号(情境参考注入铁律不变)"
      ;;
    full)
      echo "—— full 期语义: L1 白名单低风险域自主(选题入队/落地页变体/沙箱实验)"
      echo "   卡位执行仍 assist 语义(操作 40号情境域永不升级)"
      echo "—— 回滚: bash $0 assist"
      ;;
  esac
else
  echo "!! 验证未通过(期望 $MODE)——执行回滚: bash $0 off"
  exit 1
fi
