"""生产 compose: 注入 SMS_ALIYUN 五项环境变量声明(短信真实通道管道预埋)

幂等: 已存在则跳过。空默认值——零行为变化, 凭据到手后
/opt/zhuxiang/.env 填值 + docker compose up -d backend 即启用
(b1f464c 留好的 sms_aliyun.py 通道自动从模拟留痕切真实发送)。
申请指引: docs/阿里云短信资质申请与启用指引.md
"""
import io

COMPOSE = "/opt/zhuxiang/docker-compose.yml"
ANCHOR = "- XIAOZHU_LAT_REPORT=${XIAOZHU_LAT_REPORT:-on}"
ADD = ANCHOR + "\n" \
    "      # ---- 短信真实通道凭据(阿里云 dysmsapi, 空默认=模拟留痕;\n" \
    "      #     资质/签名/双模板过审后 .env 填值启用, 见\n" \
    "      #     docs/阿里云短信资质申请与启用指引.md) ----\n" \
    "      - SMS_ALIYUN_ACCESS_KEY_ID=${SMS_ALIYUN_ACCESS_KEY_ID:-}\n" \
    "      - SMS_ALIYUN_ACCESS_KEY_SECRET=${SMS_ALIYUN_ACCESS_KEY_SECRET:-}\n" \
    "      - SMS_ALIYUN_SIGN_NAME=${SMS_ALIYUN_SIGN_NAME:-}\n" \
    "      - SMS_ALIYUN_TEMPLATE_CODE=${SMS_ALIYUN_TEMPLATE_CODE:-}\n" \
    "      - SMS_ALIYUN_MARGIN_TEMPLATE_CODE=" \
    "${SMS_ALIYUN_MARGIN_TEMPLATE_CODE:-}"


def main() -> None:
    s = io.open(COMPOSE, encoding="utf-8").read()
    if "SMS_ALIYUN_ACCESS_KEY_ID" in s:
        print("already-injected")
        return
    assert ANCHOR in s, "anchor XIAOZHU_LAT_REPORT not found"
    io.open(COMPOSE, "w", encoding="utf-8").write(
        s.replace(ANCHOR, ADD, 1))
    print("injected")


if __name__ == "__main__":
    main()
