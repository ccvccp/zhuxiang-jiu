"""修复 fix_dysms_hosts 缩进错误(6空格→4空格)"""
import io

COMPOSE = "/opt/zhuxiang/docker-compose.yml"
BAD = ("  backend:\n"
       "      extra_hosts:\n"
       "        - \"dysmsapi.aliyuncs.com:106.11.45.35\"")
GOOD = ("  backend:\n"
        "    extra_hosts:\n"
        "      - \"dysmsapi.aliyuncs.com:106.11.45.35\"")


def main() -> None:
    s = io.open(COMPOSE, encoding="utf-8").read()
    if BAD in s:
        io.open(COMPOSE, "w", encoding="utf-8").write(
            s.replace(BAD, GOOD, 1))
        print("indent-fixed")
    elif GOOD in s:
        print("already-ok")
    else:
        print("pattern-not-found")


main()
