"""生产 compose: 注入 XIAOZHU_TTS_STREAM 环境变量(78号P1·竹语)

幂等: 已存在则跳过。默认 on(生产启用流式首声;
设环境变量 XIAOZHU_TTS_STREAM=off 于 .env 可一键关)。
"""
import io

COMPOSE = "/opt/zhuxiang/docker-compose.yml"
ANCHOR = "- XIAOZHU_WS_DUMP=${XIAOZHU_WS_DUMP:-off}"
ADD = ANCHOR + "\n" \
    "      # 78号P1·竹语: 流式 TTS 首声(on=/tts/stream SSE+voices" \
    " 开关下发; off=整句双轨)\n" \
    "      - XIAOZHU_TTS_STREAM=${XIAOZHU_TTS_STREAM:-on}"


def main() -> None:
    s = io.open(COMPOSE, encoding="utf-8").read()
    if "XIAOZHU_TTS_STREAM" in s:
        print("already-injected")
        return
    assert ANCHOR in s, "anchor XIAOZHU_WS_DUMP not found"
    io.open(COMPOSE, "w", encoding="utf-8").write(
        s.replace(ANCHOR, ADD, 1))
    print("injected")


if __name__ == "__main__":
    main()
