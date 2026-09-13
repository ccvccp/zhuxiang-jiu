#!/bin/bash
# pay71-transfer.sh · 71号支付端口大模型 shadow→assist 转段脚本
# 用法: bash pay71-transfer.sh assist|shadow|off|status
# 前置: 服务器 root SSH; .env 已含 PAY71_MODE 条目(shadow 期写入)
# 回滚保险: .env.bak-pay71-shadow(off 期原件)始终保留

set -e
MODE="${1:-status}"
ENV_FILE=/opt/zhuxiang/.env
BAK_FILE=/opt/zhuxiang/.env.bak-pay71-shadow

confirm() {
  echo ">>> 即将执行: $1"
  read -r -p "确认? (y/N) " ans
  [ "$ans" = "y" ] || { echo "已取消"; exit 1; }
}

current() {
  grep -E "^PAY71_MODE=" "$ENV_FILE" | cut -d= -f2 || echo "(未设置=off 默认)"
}

case "$MODE" in
  status)
    echo "当前 .env: PAY71_MODE=$(current)"
    echo "容器实际: $(docker exec zhuxiang-backend-1 printenv PAY71_MODE 2>/dev/null || echo off-默认)"
    echo "公网状态: $(curl -s -m 8 -H 'X-Role: admin' https://zxjiu.com/api/pay71/model/status | grep -o '"mode":"[a-z]*"')"
    exit 0
    ;;

  assist)
    # 转段前置检查(评估模板 §五 判据——此处仅机检, 人工签核另核)
    DAYS=$(docker ps --filter name=zhuxiang-backend --format '{{.RunningFor}}')
    echo "容器运行时长: $DAYS (人工确认影子期 ≥7 天)"
    FROZEN=$(curl -s -m 8 -H 'X-Role: admin' https://zxjiu.com/api/pay71/immunity | grep -o '"status":"[a-z]*"')
    echo "免疫状态: $FROZEN (须 active)"
    echo "$FROZEN" | grep -q '"active"' || { echo "!! 免疫非 active 态——禁止转段"; exit 1; }
    confirm "shadow→assist 转段(决策面留痕不执行→建议注入 69号 路由参考)"
    sed -i 's/^PAY71_MODE=.*/PAY71_MODE=assist/' "$ENV_FILE"
    ;;

  shadow)
    confirm "切至 shadow 影子期(决策面开放留痕不执行)"
    if grep -q '^PAY71_MODE=' "$ENV_FILE"; then
      sed -i 's/^PAY71_MODE=.*/PAY71_MODE=shadow/' "$ENV_FILE"
    else
      printf '\nPAY71_MODE=shadow\n' >> "$ENV_FILE"
    fi
    ;;

  off)
    confirm "回滚 off 期(决策面关闭 409, 观测面不变)"
    if [ -f "$BAK_FILE" ]; then
      cp "$BAK_FILE" "$ENV_FILE"   # 一键回滚原始件
    else
      sed -i 's/^PAY71_MODE=.*/PAY71_MODE=off/' "$ENV_FILE" 2>/dev/null || true
      grep -q '^PAY71_MODE=' "$ENV_FILE" || printf '\nPAY71_MODE=off\n' >> "$ENV_FILE"
    fi
    ;;

  *)
    echo "用法: bash $0 assist|shadow|off|status"
    exit 2
    ;;
esac

# 重建容器(env 变更须 recreate 生效)
cd /opt/zhuxiang
docker compose up -d backend
echo "等待容器就绪..."
sleep 14

# 落地验证(三层: 容器 env → 本地 API → 公网)
ACTUAL=$(docker exec zhuxiang-backend-1 printenv PAY71_MODE)
echo "容器 PAY71_MODE=$ACTUAL"
curl -s -m 8 -o /dev/null -w "本地健康: %{http_code}\n" http://127.0.0.1:8000/api/decision/health
PUB=$(curl -s -m 15 -H 'X-Role: admin' https://zxjiu.com/api/pay71/model/status)
echo "公网状态: $(echo "$PUB" | grep -o '"mode":"[a-z]*"')"

if echo "$PUB" | grep -q "\"mode\":\"$MODE\""; then
  echo "✅ 转段成功: $MODE"
  if [ "$MODE" = "assist" ]; then
    echo "—— assist 期语义: 71号调配结果作为建议注入 69号 P1 路由情境参考"
    echo "—— 资金路由仍由 69号 P1 唯一执行(advisoryOnly 铁律不变)"
  fi
else
  echo "!! 验证未通过(期望 $MODE)——执行回滚: bash $0 off"
  exit 1
fi
