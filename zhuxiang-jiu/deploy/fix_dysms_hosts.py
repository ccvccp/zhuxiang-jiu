"""compose: backend 服务加固 dysmsapi hosts(防容器重建丢失)

DNS 轮询含不可达 IP(新加坡实测), 固定 106.11.45.35。
幂等: 已有 extra_hosts 或 dysmsapi 行则跳过。
"""
import io

COMPOSE = "/opt/zhuxiang/docker-compose.yml"
ANCHOR = "  backend:"


def main() -> None:
    s = io.open(COMPOSE, encoding="utf-8").read()
    if "dysmsapi.aliyuncs.com" in s:
        print("already-fixed")
        return
    assert ANCHOR in s, "backend service not found"
    add = (ANCHOR + "\n"
           "      extra_hosts:\n"
           "        - \"dysmsapi.aliyuncs.com:106.11.45.35\"")
    io.open(COMPOSE, "w", encoding="utf-8").write(
        s.replace(ANCHOR, add, 1))
    print("extra_hosts-added")


main()
