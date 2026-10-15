"""v92-P0b 部署冒烟: 源码级断言三处修复已生效(零数据污染)"""
import inspect

from services import xiaozhu_service as xs

src = inspect.getsource(
    xs.XiaozhuService._evolve_turn)
full = inspect.getsource(
    xs.XiaozhuService._handle_text_internal)

checks = {
    "摊平 intent(_t 嵌套取)": '"intent": _t.get("intent")'
                              in src,
    "摊平 latency(latency_ms 注入)":
        "latency_ms if latency_ms is not None"
        in src,
    "积分排除兜底轨": '_tr not in ("general"'
                     in src,
    "rule 轨传 saved(非 result)":
        "await self._evolve_turn(session, text,"
        " cmd,\n"
        "                                saved)"
        in full
        or ("_evolve_turn(session, text, cmd,"
            in full
            and "saved)" in full.split(
                "_evolve_turn(session, text, cmd,"
            )[-1][:40]),
    "兜底 track 显式 general":
        '"track": "general"' in full,
    "兜底/碎片/超时 latencyMs":
        full.count('"latencyMs": round(') >= 3,
    "chat 轨评分补缺(_exec_smart 后)":
        "_evolve_turn" in inspect.getsource(
            xs.XiaozhuService)
        if False else
        "saved_smart" in full
        and "latency_ms=round" in full,
}

ok = True
for name, passed in checks.items():
    print(("  PASS " if passed else "  FAIL ")
          + name)
    ok = ok and passed
print("SMOKE:", "ALL GREEN" if ok else "HAS FAIL")
