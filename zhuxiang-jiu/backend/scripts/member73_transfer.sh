#!/bin/bash
# member73-transfer.sh · 73号会员体验大模型 模式转段脚本
# 用法: bash member73_transfer.sh status|shadow|assist|full|off|verify
# 前置: 服务器 root SSH; .env 含 MEMBER73_MODE 条目(shadow 期写入)
# 回滚保险: .env.bak-member73-shadow(off 期原件)始终保留
#
# 四档语义(73号规划 §六):
#   off     观测面常开(视野/统计/面板), 触达面关闭
#   shadow  触达留痕不呈现(影子期≥7天, 评估签核后转 assist)
#   assist  触达建议注入前端确认位; 代办执行永远显式授权
#   full    低风险触达域自主(L1 白名单: hint_render/
#           silence_rule/form_ranking); 代办域永远 assist
#
# 铁律:
#   - 免疫冻结前置: frozen 态禁止转段(先解冻[双保险])
#   - 代办域永远 assist: full 档不含 delegate(授权显式性优先)
#   - 转段前 verify: 容器内模型状态三层验证

set -e
MODE="${1:-status}"
ENV_FILE=/opt/zhuxiang/.env
BAK_FILE=/opt/zhuxiang/.env.bak-member73-shadow

confirm() {
  echo ">>> 即将执行: $1"
  read -r -p "确认? (y/N) " ans
  [ "$ans" = "y" ] || { echo "已取消"; exit 1; }
}

current() {
  grep -E "^MEMBER73_MODE=" "$ENV_FILE" | cut -d= -f2 || echo "(未设置=off 默认)"
}

container_mode() {
  docker exec zhuxiang-backend-1 printenv MEMBER73_MODE 2>/dev/null || echo off-默认
}

public_status() {
  curl -s -m 8 -H 'X-Role: admin' \
    https://zxjiu.com/api/member73/model/status \
    | grep -o '"mode":"[a-z]*"'
}

recreate() {
  cd /opt/zhuxiang
  docker compose up -d backend
  echo "等待容器就绪..."
  sleep 14
}

# 免疫冻结前置(转段禁止——先解冻)
guard_frozen() {
  FROZEN=$(curl -s -m 8 -H 'X-Role: admin' \
    https://zxjiu.com/api/member73/immunity \
    | grep -o '"status":"[a-z]*"')
  echo "免疫状态: $FROZEN (须 active)"
  echo "$FROZEN" | grep -q '"active"' || {
    echo "!! 免疫非 active 态——禁止转段(先解冻: MEMBER73_IMMUNITY=1 + POST /immunity/unfreeze)"
    exit 1
  }
}

case "$MODE" in
  status)
    echo "=== 73号当前状态 ==="
    echo "本地 .env: MEMBER73_MODE=$(current)"
    echo "容器实际:  $(container_mode)"
    echo "公网状态:  $(public_status)"
    IMM=$(curl -s -m 8 -H 'X-Role: admin' \
      https://zxjiu.com/api/member73/immunity)
    echo "免疫看板:  $IMM"
    ;;

  shadow)
    confirm "切至 shadow 影子期(触达留痕不呈现; 影子期≥7天)"
    guard_frozen
    if grep -q '^MEMBER73_MODE=' "$ENV_FILE"; then
      cp "$ENV_FILE" "$BAK_FILE"   # 回滚保险原件
      sed -i 's/^MEMBER73_MODE=.*/MEMBER73_MODE=shadow/' "$ENV_FILE"
    else
      cp "$ENV_FILE" "$BAK_FILE"   # 回滚保险原件
      printf '\nMEMBER73_MODE=shadow\n' >> "$ENV_FILE"
    fi
    ;;

  assist)
    confirm "shadow→assist 转段(触达建议注入前端确认位; 代办永远显式授权)"
    echo ">>> 前置人工核验(影子期评估模板签核):"
    echo "    1) 影子期≥7 天(容器 RunningFor)"
    echo "    2) 触达留痕 moments 决策分布(defer/present 比例)"
    echo "    3) 静默窗/封顶命中率(immunity_frozen/daily_cap 计数)"
    echo "    4) 漂移信号为空(POST /meta/drift 三信号皆无)"
    DAYS=$(docker ps --filter name=zhuxiang-backend --format '{{.RunningFor}}')
    echo "容器运行时长: $DAYS"
    guard_frozen
    sed -i 's/^MEMBER73_MODE=.*/MEMBER73_MODE=assist/' "$ENV_FILE"
    ;;

  full)
    confirm "assist→full 转段(低风险触达域自主: hint_render/silence_rule/form_ranking)"
    echo ">>> 前置人工核验(assist 稳定运行评估):"
    echo "    1) assist 稳定期≥7 天"
    echo "    2) 响应率健康(信任报告 responseRate 无骤降)"
    echo "    3) 红队四向量全防御(POST /redteam allDefended=true)"
    echo "    4) 代办域确认: full 不含 delegate(授权显式性铁律)"
    guard_frozen
    sed -i 's/^MEMBER73_MODE=.*/MEMBER73_MODE=full/' "$ENV_FILE"
    ;;

  off)
    confirm "回滚 off 期(触达面关闭, 观测面不变)"
    if [ -f "$BAK_FILE" ]; then
      cp "$BAK_FILE" "$ENV_FILE"   # 一键回滚原始件
    else
      sed -i 's/^MEMBER73_MODE=.*/MEMBER73_MODE=off/' "$ENV_FILE" 2>/dev/null || true
      grep -q '^MEMBER73_MODE=' "$ENV_FILE" || printf '\nMEMBER73_MODE=off\n' >> "$ENV_FILE"
    fi
    ;;

  verify)
    echo "=== 三层验证 ==="
    ACTUAL=$(docker exec zhuxiang-backend-1 printenv MEMBER73_MODE)
    echo "容器 MEMBER73_MODE=$ACTUAL"
    curl -s -m 8 -o /dev/null -w "本地健康: %{http_code}\n" \
      http://127.0.0.1:8000/api/decision/health
    PUB=$(curl -s -m 15 -H 'X-Role: admin' \
      https://zxjiu.com/api/member73/model/status)
    echo "公网状态: $(echo "$PUB" | grep -o '"mode":"[a-z]*"')"
    echo "免疫状态: $(echo "$PUB" | grep -o '"status":"[a-z]*"' | head -1)"
    ;;

  *)
    echo "用法: bash $0 status|shadow|assist|full|off|verify"
    echo ""
    echo "转段链路(只进不跳):"
    echo "  off → shadow(影子期≥7天) → assist(建议注入) → full(低风险自主)"
    echo "  任意档 → off(一键回滚, .env.bak-member73-shadow 保险)"
    exit 2
    ;;
esac

# env 变更须 recreate 生效(off/status/verify 外)
if [ "$MODE" != "status" ] && [ "$MODE" != "verify" ]; then
  recreate
fi

# 落地验证(三层: 容器 env → 本地 API → 公网)
if [ "$MODE" != "status" ]; then
  ACTUAL=$(docker exec zhuxiang-backend-1 printenv MEMBER73_MODE)
  echo "容器 MEMBER73_MODE=$ACTUAL"
  curl -s -m 8 -o /dev/null -w "本地健康: %{http_code}\n" \
    http://127.0.0.1:8000/api/decision/health
  PUB=$(curl -s -m 15 -H 'X-Role: admin' \
    https://zxjiu.com/api/member73/model/status)
  PUB_MODE=$(echo "$PUB" | grep -o '"mode":"[a-z]*"')

  if echo "$PUB_MODE" | grep -q "\"mode\":\"$MODE\""; then
    echo "✅ 转段成功: $MODE"
    case "$MODE" in
      shadow)
        echo "—— shadow 期语义: 触达留痕不呈现; 新会员冷启动影子期 7 天"
        echo "—— 转段前置(assist): 影子期≥7 天+评估模板签核"
        ;;
      assist)
        echo "—— assist 期语义: 触达建议注入前端确认位"
        echo "—— 铁律: 代办执行永远显式授权(grant 白名单)"
        ;;
      full)
        echo "—— full 期语义: L1 白名单自主(hint_render/silence_rule/"
        echo "   form_ranking); 代办域永远 assist(授权显式性优先)"
        ;;
      off)
        echo "—— off 期语义: 触达面关闭; 观测面(视野/统计/面板)常开"
        ;;
    esac
  else
    echo "!! 验证未通过(期望 $MODE, 公网 $PUB_MODE)——执行回滚: bash $0 off"
    exit 1
  fi
fi
