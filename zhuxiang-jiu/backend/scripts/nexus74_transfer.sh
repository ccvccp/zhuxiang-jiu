#!/bin/bash
# nexus74_transfer.sh · 74号NexusFlow智枢·流全域发布大模型 模式转段脚本
# 用法: bash nexus74_transfer.sh status|shadow|assist|full|off|verify
# 前置: 服务器 root SSH; .env 含 NEXUSFLOW74_MODE 条目(shadow 期写入)
# 回滚保险: .env.bak-nexus74-shadow(off 期原件)始终保留
#
# 四档语义(74号规划 §七):
#   off     观测面常开(规则库/人格/矩阵/数据汇总), 发布面关闭
#   shadow  适配包生成留痕不输出(影子期≥7天, 评估签核后转 assist)
#   assist  适配包输出至人工确认位; B 档平台操作发布(人工+回执登记)
#   full    仅 A 档(微信)低风险域自主(compliance pass); B 档永远 assist
#
# 铁律:
#   - 免疫冻结前置: frozen 态禁止转段(先解冻[NEXUSFLOW74_IMMUNITY=1 双保险])
#   - B 档平台操作永远人工: full 档不含 B 档自主(发布显式性优先)
#   - 转段前 verify: 容器内模型状态三层验证

set -e
MODE="${1:-status}"
ENV_FILE=/opt/zhuxiang/.env
BAK_FILE=/opt/zhuxiang/.env.bak-nexus74-shadow

confirm() {
  echo ">>> 即将执行: $1"
  read -r -p "确认? (y/N) " ans
  [ "$ans" = "y" ] || { echo "已取消"; exit 1; }
}

current() {
  grep -E "^NEXUSFLOW74_MODE=" "$ENV_FILE" | cut -d= -f2 || echo "(未设置=off 默认)"
}

container_mode() {
  docker exec zhuxiang-backend-1 printenv NEXUSFLOW74_MODE 2>/dev/null || echo off-默认
}

public_status() {
  curl -s -m 8 -H 'X-Role: admin' \
    https://zxjiu.com/api/nexus74/model/status \
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
    https://zxjiu.com/api/nexus74/immunity \
    | grep -o '"status":"[a-z]*"' | sed 's/"status":"//;s/"//')
  echo "免疫状态: $FROZEN (须 active)"
  [ "$FROZEN" = "active" ] || {
    echo "!! 免疫非 active 态——禁止转段(先解冻: NEXUSFLOW74_IMMUNITY=1 + POST /immunity/unfreeze)"
    exit 1
  }
}

case "$MODE" in
  status)
    echo "=== 74号 NexusFlow 当前状态 ==="
    echo "本地 .env: NEXUSFLOW74_MODE=$(current)"
    echo "容器实际:  $(container_mode)"
    echo "公网状态:  $(public_status)"
    IMM=$(curl -s -m 8 -H 'X-Role: admin' \
      https://zxjiu.com/api/nexus74/immunity)
    echo "免疫看板:  $IMM"
    ;;

  shadow)
    confirm "切至 shadow 影子期(适配包留痕不输出; 影子期≥7天)"
    guard_frozen
    if grep -q '^NEXUSFLOW74_MODE=' "$ENV_FILE"; then
      cp "$ENV_FILE" "$BAK_FILE"   # 回滚保险原件
      sed -i 's/^NEXUSFLOW74_MODE=.*/NEXUSFLOW74_MODE=shadow/' "$ENV_FILE"
    else
      cp "$ENV_FILE" "$BAK_FILE"   # 回滚保险原件
      printf '\nNEXUSFLOW74_MODE=shadow\n' >> "$ENV_FILE"
    fi
    ;;

  assist)
    confirm "shadow→assist 转段(适配包输出至人工确认位; B 档人工操作+回执)"
    echo ">>> 前置人工核验(影子期评估模板签核):"
    echo "    1) 影子期≥7 天(容器 RunningFor)"
    echo "    2) 适配留痕 adaptations 决策分布(delivered/needsReview 比例)"
    echo "    3) 封顶/静默窗命中率(publish 409 分布)"
    echo "    4) 漂移信号为空(POST /meta/drift 三信号皆无)"
    DAYS=$(docker ps --filter name=zhuxiang-backend --format '{{.RunningFor}}')
    echo "容器运行时长: $DAYS"
    guard_frozen
    sed -i 's/^NEXUSFLOW74_MODE=.*/NEXUSFLOW74_MODE=assist/' "$ENV_FILE"
    ;;

  full)
    confirm "assist→full 转段(仅 A 档微信低风险域自主; B 档永远 assist)"
    echo ">>> 前置人工核验(assist 稳定运行评估):"
    echo "    1) assist 稳定期≥7 天"
    echo "    2) B 档回执数据健康(数据汇总 rejected/throttled 占比)"
    echo "    3) 红队四向量全防御(POST /redteam allDefended=true)"
    echo "    4) B 档确认: full 不含 B 档自主(平台操作显式性铁律)"
    guard_frozen
    sed -i 's/^NEXUSFLOW74_MODE=.*/NEXUSFLOW74_MODE=full/' "$ENV_FILE"
    ;;

  off)
    confirm "回滚 off 期(发布面关闭, 观测面不变)"
    if [ -f "$BAK_FILE" ]; then
      cp "$BAK_FILE" "$ENV_FILE"   # 一键回滚原始件
    else
      sed -i 's/^NEXUSFLOW74_MODE=.*/NEXUSFLOW74_MODE=off/' "$ENV_FILE" 2>/dev/null || true
      grep -q '^NEXUSFLOW74_MODE=' "$ENV_FILE" || printf '\nNEXUSFLOW74_MODE=off\n' >> "$ENV_FILE"
    fi
    ;;

  verify)
    echo "=== 三层验证 ==="
    ACTUAL=$(docker exec zhuxiang-backend-1 printenv NEXUSFLOW74_MODE)
    echo "容器 NEXUSFLOW74_MODE=$ACTUAL"
    curl -s -m 8 -o /dev/null -w "本地健康: %{http_code}\n" \
      http://127.0.0.1:8000/api/decision/health
    PUB=$(curl -s -m 15 -H 'X-Role: admin' \
      https://zxjiu.com/api/nexus74/model/status)
    echo "公网状态: $(echo "$PUB" | grep -o '"mode":"[a-z]*"')"
    echo "免疫状态: $(echo "$PUB" | grep -o '"status":"[a-z]*"' | head -1)"
    ;;

  *)
    echo "用法: bash $0 status|shadow|assist|full|off|verify"
    echo ""
    echo "转段链路(只进不跳):"
    echo "    off → shadow(影子期≥7天) → assist(人工确认位) → full(A 档低风险自主)"
    echo "    任意档 → off(一键回滚, .env.bak-nexus74-shadow 保险)"
    exit 2
    ;;
esac

# env 变更须 recreate 生效(off/status/verify 外)
if [ "$MODE" != "status" ] && [ "$MODE" != "verify" ]; then
  recreate
fi

# 落地验证(三层: 容器 env → 本地 API → 公网)
if [ "$MODE" != "status" ]; then
  ACTUAL=$(docker exec zhuxiang-backend-1 printenv NEXUSFLOW74_MODE)
  echo "容器 NEXUSFLOW74_MODE=$ACTUAL"
  curl -s -m 8 -o /dev/null -w "本地健康: %{http_code}\n" \
    http://127.0.0.1:8000/api/decision/health
  PUB=$(curl -s -m 15 -H 'X-Role: admin' \
    https://zxjiu.com/api/nexus74/model/status)
  PUB_MODE=$(echo "$PUB" | grep -o '"mode":"[a-z]*"')

  if echo "$PUB_MODE" | grep -q "\"mode\":\"$MODE\""; then
    echo "✅ 转段成功: $MODE"
    case "$MODE" in
      shadow)
        echo "—— shadow 期语义: 适配包留痕不输出(delivered=False)"
        echo "—— 转段前置(assist): 影子期≥7 天+评估模板签核"
        ;;
      assist)
        echo "—— assist 期语义: 适配包输出至人工确认位"
        echo "—— 铁律: B 档平台操作人工+回执登记(数据诚实)"
        ;;
      full)
        echo "—— full 期语义: 仅 A 档(微信)低风险域自主"
        echo "   (compliance pass); B 档永远 assist(发布显式性优先)"
        ;;
      off)
        echo "—— off 期语义: 发布面关闭; 观测面(规则/人格/矩阵/汇总)常开"
        ;;
    esac
  else
    echo "!! 验证未通过(期望 $MODE, 公网 $PUB_MODE)——执行回滚: bash $0 off"
    exit 1
  fi
fi
