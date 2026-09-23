"""compose 注入 XXIAOZHU_GUARD_AUTO 声明(幂等)

用途: 2026-09-24~09-27 临时置 off(滑动窗口数据
老化期, 语音面恢复供 D1 观察窗采数); 09-27 后
.env 改回 on 即恢复护栏保护。
"""
import io

COMPOSE = "/opt/zhuxiang/docker-compose.yml"
ANCHOR = ("      - SMS_ALIYUN_MARGIN_TEMPLATE_CODE="
          "${SMS_ALIYUN_MARGIN_TEMPLATE_CODE:-}")
ADD = ANCHOR + "\n" \
    "      # 护栏巡检开关(2026-09-24 临时 off 至 09-27 滑窗数据老化;\n" \
    "      #     .env XXIAOZHU_GUARD_AUTO=on 恢复保护)\n" \
    "      - XXIAOZHU_GUARD_AUTO=" \
    "${XXIAOZHU_GUARD_AUTO:-on}"


def main() -> None:
    s = io.open(COMPOSE, encoding="utf-8").read()
    if "XXIAOZHU_GUARD_AUTO" in s:
        print("already-declared")
        return
    assert ANCHOR in s, "anchor not found"
    io.open(COMPOSE, "w", encoding="utf-8").write(
        s.replace(ANCHOR, ADD, 1))
    print("declared")


main()
