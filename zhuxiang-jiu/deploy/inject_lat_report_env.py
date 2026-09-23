"""生产 compose: 注入 XIAOZHU_LAT_REPORT 环境变量(78号P2·H2 观察期)

幂等: 已存在则跳过。观察期(09-23~09-30)注入 on——语音页
[LAT] 四段上报落 Redis 日键供看板聚合; 09-30 G1 完整版出后
置 off(观察结束)即可停收。
"""
import io

COMPOSE = "/opt/zhuxiang/docker-compose.yml"
ANCHOR = "- XIAOZHU_TTS_STREAM=${XIAOZHU_TTS_STREAM:-on}"
ADD = ANCHOR + "\n" \
    "      # 78号P2·H2 观察期: [LAT] 四段客户端延迟上报(观察期 on,\n" \
    "      #     09-30 观察结束置 off 停收; 看板 /h2-watch.html)\n" \
    "      - XIAOZHU_LAT_REPORT=${XIAOZHU_LAT_REPORT:-on}"


def main() -> None:
    s = io.open(COMPOSE, encoding="utf-8").read()
    if "XIAOZHU_LAT_REPORT" in s:
        print("already-injected")
        return
    assert ANCHOR in s, "anchor XIAOZHU_TTS_STREAM not found"
    io.open(COMPOSE, "w", encoding="utf-8").write(
        s.replace(ANCHOR, ADD, 1))
    print("injected")


if __name__ == "__main__":
    main()
