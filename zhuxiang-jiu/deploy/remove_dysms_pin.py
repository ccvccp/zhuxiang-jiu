"""移除 compose dysms extra_hosts 单 IP 固定(代码层多 IP 轮换取代)"""
import io

COMPOSE = "/opt/zhuxiang/docker-compose.yml"
BLOCK = ("  backend:\n"
         "    extra_hosts:\n"
         "      - \"dysmsapi.aliyuncs.com:106.11.45.35\"\n")


def main() -> None:
    s = io.open(COMPOSE, encoding="utf-8").read()
    if BLOCK not in s:
        print("pin-not-found(可能已移除)")
        return
    io.open(COMPOSE, "w", encoding="utf-8").write(
        s.replace(BLOCK, "  backend:\n", 1))
    print("pin-removed")


main()
