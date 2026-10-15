"""85号·第三方 API 字段映射适配器专项测试(fake 响应零网络)

运行:
    python test_drama85_adapter.py

覆盖:
    1. 映射转换全链: 第三方风格响应+FIELD_MAP → 契约 → fetch 成功
       (点路径取数/数值转换/字符串评论归一/platforms 单值归一)
    2. 无映射+非契约响应 → ValueError(提示配置 FIELD_MAP)
    3. 映射 JSON 非法/缺 list/title → ValueError(配置类不重试)
    4. commentsData 三形态归一(字符串列表/{content}/标准对象)
    5. apply_field_map 纯函数(缺字段默认值/float→int/越界丢弃)
    6. .env 第六节预置模板 B/C 风格兼容(下划线/顶层数组短别名;
       模板 A 即上方 FIELD_MAP, 01 段全链已覆盖)
"""
import asyncio
import json
import os
import sys

os.environ.setdefault("LOCK_MODE", "asyncio")
os.environ.setdefault("STORE_MODE", "asyncio")
os.environ.setdefault("AUTH_MODE", "compat")
os.environ.setdefault("AUTH_COMPAT_TRUST_HEADERS", "1")

PASS = 0
FAIL = 0
RESULTS = []

ENV_KEYS = ("DRAMA85_SOURCE_MODE", "DRAMA85_SOURCE_URL",
            "DRAMA85_SOURCE_API_KEY", "DRAMA85_SOURCE_QPS",
            "DRAMA85_SOURCE_FIELD_MAP")

# 第三方风格响应(字段名与契约不同; 嵌套 data.rows)
THIRD_PARTY_RESP = {
    "code": 0, "msg": "ok",
    "data": {"rows": [
        {
            "dramaName": "龙王归来第一季",
            "platform": "douyin",
            "episodeCount": 80,
            "duration": 95,
            "playCnt": 2345678,
            "likeCnt": 156000,
            "commentCnt": 21000,
            "shareCnt": 33000,
            "finishRate": 0.62,
            "hotComments": ["反转太绝了封神", "每天蹲更新根本不够看"],
            "synopsis": ("[开场钩子]战神归来家产被夺。"
                         "[核心冲突]全家逼迫放弃继承权。"
                         "[中段反转]原来他才是真正的继承人。"
                         "[结尾悬念]门后传来陌生的脚步声。"),
        },
        {   # 对象形态评论 + tags 缺失 + 数字串
            "dramaName": "隐婚千金第二季",
            "platform": "kuaishou",
            "episodeCount": "66",
            "playCnt": "980000",
            "likeCnt": 88000,
            "finishRate": 0.71,
            "hotComments": [{"content": "女主终于硬气了爽"}],
        },
        {"platform": "x"},   # 缺 dramaName → 丢弃
    ], "total": 100},
}

FIELD_MAP = {
    "list": "$.data.rows",
    "title": "dramaName",
    "platforms": "platform",
    "episodes": "episodeCount",
    "durationSec": "duration",
    "plays": "playCnt",
    "likes": "likeCnt",
    "comments": "commentCnt",
    "shares": "shareCnt",
    "completionRate": "finishRate",
    "commentsData": "hotComments",
    "subtitleText": "synopsis",
}

# --- .env.example 第六节预置模板 B/C(与模板文本逐字一致, 验证可用性) ---
TPL_B_TEXT = '{"list":"$.data.list","title":"drama_name","platforms":"platform","episodes":"total_episodes","durationSec":"duration_seconds","plays":"play_count","likes":"like_count","comments":"comment_count","shares":"share_count","completionRate":"completion_rate","commentsData":"hot_comments","subtitleText":"subtitle"}'
TPL_C_TEXT = '{"list":"$.list","title":"name","platforms":"platform","episodes":"ep","durationSec":"total_sec","plays":"pv","likes":"like_num","comments":"cmt_num","shares":"share_num","completionRate":"finish_rate","commentsData":"top_comments","subtitleText":"synopsis"}'

RESP_B = {   # 模板B 形态: data.list + 下划线
    "code": 0, "data": {"list": [{
        "drama_name": "下山虎都市录", "platform": ["douyin", "bili"],
        "total_episodes": 72, "duration_seconds": 88,
        "play_count": 1520000, "like_count": 99000,
        "comment_count": 12500, "share_count": 18700,
        "completion_rate": 0.58,
        "hot_comments": [{"platform": "douyin", "text": "打戏过瘾"}],
        "subtitle": ("[开场钩子]师父临终托付木盒。"
                     "[核心冲突]下山寻人反被设局。"
                     "[中段反转]木盒里是失传地契。"
                     "[结尾悬念]追兵已到巷口。"),
    }]},
}
RESP_C = {   # 模板C 形态: 顶层数组 + 短别名
    "list": [{
        "name": "重生九零小厨娘", "platform": "kuaishou",
        "ep": "58", "total_sec": 76,
        "pv": 660000, "like_num": 45000,
        "cmt_num": 6800, "share_num": 9100,
        "finish_rate": "0.66",
        "top_comments": ["年代感拉满"],
        "synopsis": ("[开场钩子]重回1998厨房。"
                     "[核心冲突]家宴被抢功。"
                     "[中段反转]老菜谱认主。"
                     "[结尾悬念]灶火无人自燃。"),
    }],
    "hasMore": False,
}


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} {detail}")


def run(coro):
    from core import locks as _locks
    _locks._async_locks.clear()
    try:
        return asyncio.run(coro)
    finally:
        _locks._async_locks.clear()


class EnvGuard:
    def __enter__(self):
        self._backup = {k: os.environ.get(k) for k in ENV_KEYS}
        for k in ENV_KEYS:
            os.environ.pop(k, None)
        return self

    def __exit__(self, *exc):
        for k, v in self._backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return False


def main():
    from services.drama85_source_gateway import (
        RealDramaSource, apply_field_map, load_field_map,
    )

    print("[01 映射转换全链(第三方风格 → 契约 → fetch)]")

    async def fake_third(url, headers=None):
        return 200, THIRD_PARTY_RESP

    async def _fetch():
        return await RealDramaSource(fake_third).fetch(3, 0)

    with EnvGuard():
        os.environ["DRAMA85_SOURCE_URL"] = "https://third.test/openapi"
        os.environ["DRAMA85_SOURCE_API_KEY"] = "k"
        os.environ["DRAMA85_SOURCE_QPS"] = "10"
        os.environ["DRAMA85_SOURCE_FIELD_MAP"] = json.dumps(
            FIELD_MAP, ensure_ascii=False)
        rows = run(_fetch())
        check("映射转换取数(缺名条目丢弃, 好数目 2)",
              len(rows) == 2, str(len(rows)))
        r0 = rows[0]
        check("字段映射(playCnt→plays/finishRate→completionRate)",
              r0["metrics"]["plays"] == 2345678
              and r0["metrics"]["completionRate"] == 0.62,
              str(r0["metrics"]))
        check("platforms 单值归一列表",
              r0["platforms"] == ["douyin"], str(r0["platforms"]))
        check("字符串评论→commentsData 归一",
              r0["commentsData"][0]
              == {"platform": "unknown", "text": "反转太绝了封神"},
              str(r0["commentsData"]))
        check("subtitleText 标注透传(结构可解析)",
              "[开场钩子]" in r0["subtitleText"], "")
        r1 = rows[1]
        check("数字串容错(episodes/playCnt int 转换)",
              r1["episodes"] == 66
              and r1["metrics"]["plays"] == 980000,
              str((r1["episodes"], r1["metrics"]["plays"])))
        check("对象评论 {content} → text",
              r1["commentsData"][0]["text"] == "女主终于硬气了爽",
              str(r1["commentsData"]))

    print("[02 无映射+非契约 → 提示配置]")

    async def fake_third_nomap(url, headers=None):
        return 200, THIRD_PARTY_RESP

    async def _fetch_nomap():
        return await RealDramaSource(fake_third_nomap).fetch(1, 0)

    with EnvGuard():
        os.environ["DRAMA85_SOURCE_URL"] = "https://third.test/api"
        os.environ["DRAMA85_SOURCE_API_KEY"] = "k"
        os.environ["DRAMA85_SOURCE_QPS"] = "10"
        try:
            run(_fetch_nomap())
            check("非契约无映射 → ValueError", False, "未抛错")
        except ValueError as e:
            check("非契约无映射 → ValueError(提示 FIELD_MAP)",
                  "FIELD_MAP" in str(e), str(e))

    print("[03 映射配置校验(不重试直接抛)]")
    with EnvGuard():
        os.environ["DRAMA85_SOURCE_FIELD_MAP"] = "{bad json"
        try:
            load_field_map()
            check("非法 JSON → ValueError", False, "未抛错")
        except ValueError as e:
            check("非法 JSON → ValueError",
                  "JSON 非法" in str(e), str(e))
        os.environ["DRAMA85_SOURCE_FIELD_MAP"] = json.dumps(
            {"title": "x"})   # 缺 list
        try:
            load_field_map()
            check("缺 list → ValueError", False, "未抛错")
        except ValueError as e:
            check("缺 list → ValueError", "list" in str(e), str(e))
        os.environ["DRAMA85_SOURCE_FIELD_MAP"] = ""
        check("未配置 → None(契约直用模式)",
              load_field_map() is None, "")

    print("[04 apply_field_map 纯函数(容错)]")
    fm = dict(FIELD_MAP)
    fm["tags"] = "tagList"
    out = apply_field_map({"data": {"rows": [
        {"dramaName": "缺指标条目", "platform": "bili"}]}}, fm)
    d = (out.get("dramas") or [{}])[0]
    check("缺字段默认值(指标 0/评论空/tags 空)",
          d.get("metrics", {}).get("plays") == 0
          and d.get("commentsData") == []
          and d.get("tags") == [], str(d))
    out = apply_field_map({}, fm)
    check("list 路径取不到 → 空 dramas",
          out.get("dramas") == [], str(out))
    out = apply_field_map({"data": {"rows": [
        {"dramaName": "浮点数", "playCnt": "1234567.8",
         "finishRate": "0.55"}]}}, fm)
    d = out["dramas"][0]
    check("float→int 转换(playCnt)",
          d["metrics"]["plays"] == 1234567
          and abs(d["metrics"]["completionRate"] - 0.55) < 1e-9,
          str(d["metrics"]))

    print("[05 预置模板 B/C 风格兼容(.env 第六节三模板之二)]")
    fm_b = json.loads(TPL_B_TEXT)
    with EnvGuard():
        os.environ["DRAMA85_SOURCE_FIELD_MAP"] = TPL_B_TEXT
        check("模板B 文本可加载(load_field_map)",
              load_field_map() == fm_b, "")
    db = apply_field_map(RESP_B, fm_b)["dramas"][0]
    check("模板B 下划线全字段映射",
          db["title"] == "下山虎都市录"
          and db["platforms"] == ["douyin", "bili"]
          and db["episodes"] == 72
          and db["durationSec"] == 88
          and db["metrics"]["plays"] == 1520000
          and db["metrics"]["likes"] == 99000
          and db["metrics"]["comments"] == 12500
          and db["metrics"]["shares"] == 18700
          and db["metrics"]["completionRate"] == 0.58,
          str(db))
    check("模板B 对象评论+标注透传",
          db["commentsData"][0]["text"] == "打戏过瘾"
          and "[开场钩子]" in db["subtitleText"], "")

    fm_c = json.loads(TPL_C_TEXT)
    with EnvGuard():
        os.environ["DRAMA85_SOURCE_FIELD_MAP"] = TPL_C_TEXT
        check("模板C 文本可加载(load_field_map)",
              load_field_map() == fm_c, "")
    dc = apply_field_map(RESP_C, fm_c)["dramas"][0]
    check("模板C 顶层 $.list+短别名+数字串容错",
          dc["title"] == "重生九零小厨娘"
          and dc["episodes"] == 58
          and dc["metrics"]["plays"] == 660000
          and abs(dc["metrics"]["completionRate"] - 0.66) < 1e-9,
          str(dc))
    check("模板C 字符串评论归一+标注透传",
          dc["commentsData"][0]["text"] == "年代感拉满"
          and "[结尾悬念]" in dc["subtitleText"], "")

    # 汇总
    print()
    print(f"85号第三方 API 适配器专项: PASS={PASS} FAIL={FAIL}")
    for line in RESULTS:
        print(line)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
