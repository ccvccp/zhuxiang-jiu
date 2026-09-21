"""小竹 P2 方言模块测试(山东话规范化种子 + 删除保护 + 双轨生效)

覆盖:
    - 方言种子种入: builtin 批 + dialect 批(source 标记)
    - 方言整词规范化: 俺→我/俺们→我们(长词优先)/哈酒→喝酒/
      恁→您/木有→没有/夜来→昨天/咋→怎么
    - 组合场景: "俺夜来哈酒了" 多词同句
    - 存量升级补种: 表已有 builtin/运营词条时 dialect 批幂等补种
    - 删除保护: dialect 词条 HTTP 409(builtin 同)
    - 双轨生效: 流式轨 textTranscript(final 文本)同样过修正

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; $env:AUTH_MODE="compat"
    python test_xiaozhu_dialect.py
"""
import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["AUTH_MODE"] = "compat"
os.environ["XIAOZHU_MODE"] = "assist"
os.environ["LLM_ENABLED"] = "off"
os.environ["XIAOZHU_LLM_MODE"] = "off"

from repositories.store import reset_store

PASS = 0
FAIL = 0
RESULTS = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} {detail}")


async def run_tests():
    reset_store()
    from services.xiaozhu_service import (
        ASR_DIALECT_FIXES, XiaozhuService,
    )
    from repositories.xiaozhu_repository import Xiaozhu48Repository

    print("[D 方言规范化]")
    svc = XiaozhuService()
    repo = Xiaozhu48Repository()

    # D1 首调种入: builtin 3 条 + dialect 7 条
    out = await svc._fix_asr_mishear("小竹，看新品")
    fixes = await repo.list_asr_fixes()
    dialect_seeded = {w for w in dict(ASR_DIALECT_FIXES)
                      if w in fixes}
    check("D1 方言种子种入(7 条 dialect)",
          len(dialect_seeded) == len(ASR_DIALECT_FIXES)
          and all(fixes[w].get("source") == "dialect"
                  for w in dialect_seeded),
          f"n={len(dialect_seeded)}")

    # D2 单词规范化
    cases = [
        ("俺要一瓶竹香", "我要一瓶竹香"),
        ("俺们哈酒", "我们喝酒"),
        ("恁看", "您看"),
        ("木有听清", "没有听清"),
        ("夜来买的酒", "昨天买的酒"),
        ("这是咋回事", "这是怎么回事"),
    ]
    for src, want in cases:
        out = await svc._fix_asr_mishear(src)
        check(f"D2 规范化 {src!r}→{want!r}", out == want,
              f"out={out!r}")

    # D3 组合场景(多方言词同句)
    out = await svc._fix_asr_mishear("俺夜来哈酒了")
    check("D3 组合: 俺夜来哈酒→我昨天喝酒",
          out == "我昨天喝酒了", f"out={out!r}")

    # D4 存量升级补种: 手动删表后预置 builtin+运营词条,
    #    dialect 批仍逐条补种且不覆盖已有方向
    await repo.save_asr_fix("运营词", "测试", source="manual")
    await repo.save_asr_fix("俺", "俺家", source="manual")
    fixes = await repo.list_asr_fixes()
    # 模拟存量: 只保留运营两条, 清掉其他
    for w in list(fixes):
        if w not in ("运营词", "俺"):
            await repo.delete_asr_fix(w)
    fixes = await repo.list_asr_fixes()
    check("D4a 存量表(2 条, 无 builtin)",
          set(fixes) == {"运营词", "俺"}, str(set(fixes)))
    await svc._fix_asr_mishear("触发补种")
    fixes = await repo.list_asr_fixes()
    dialect_seeded = {w for w in dict(ASR_DIALECT_FIXES)
                      if w in fixes and w != "俺"}
    check("D4b dialect 批补种(俺 保留运营方向不被覆盖)",
          len(dialect_seeded) == len(ASR_DIALECT_FIXES) - 1
          and fixes.get("俺", {}).get("to") == "俺家",
          f"n={len(dialect_seeded)} 俺→{fixes.get('俺', {}).get('to')}")

    # D5 普通话不受影响(无误听词原文返回)
    out = await svc._fix_asr_mishear("小竹，看看有什么新品")
    check("D5 普通话原文不变",
          out == "小竹，看看有什么新品", f"out={out!r}")

    # D6 删除保护(HTTP 409): dialect 与 builtin 同受保护
    # (D4 清表动过词条——先确保两词条在场)
    from urllib.parse import quote

    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    await repo.save_asr_fix("加入国五车", "加入购物车",
                            source="builtin")
    r = client.delete(
        "/api/xiaozhu/dashboard/asr-fixes?wrong=" + quote("哈酒"),
        headers={"X-Role": "admin"})
    body = r.json()
    check("D6 dialect 词条删除保护 409",
          r.status_code == 409 and "dialect" in str(
              body.get("error") or body.get("detail") or ""),
          f"code={r.status_code} {body}")
    r = client.delete(
        "/api/xiaozhu/dashboard/asr-fixes?wrong="
        + quote("加入国五车"),
        headers={"X-Role": "admin"})
    check("D6b builtin 词条删除保护仍 409",
          r.status_code == 409, f"code={r.status_code}")
    r = client.delete(
        "/api/xiaozhu/dashboard/asr-fixes?wrong=" + quote("运营词"),
        headers={"X-Role": "admin"})
    check("D6c manual 词条删除仍放行",
          r.status_code == 200, f"code={r.status_code}")

    # D7 流式轨生效: textTranscript(final 文本)过方言修正
    reset_store()
    svc2 = XiaozhuService()
    sid = (await svc2.open_session(3001, "voice"))["sessionId"]
    r = client.post(f"/api/xiaozhu/sessions/{sid}/voice",
                    headers={"X-Member-Id": "3001"},
                    json={"textTranscript": "小竹，俺要一瓶竹香",
                          "durationSec": 3.0, "streamBytes": 96000})
    j = r.json()
    check("D7 流式轨 final 过方言修正(rawText=俺→我)",
          r.status_code == 200
          and (j.get("turn") or {}).get("rawText")
          == "小竹，我要一瓶竹香",
          str((j.get("turn") or {}).get("rawText")))
    check("D7b 指令链正常响应(reply 非空)",
          isinstance(j.get("reply"), str) and j.get("reply"),
          str(j.get("reply"))[:80])
    await svc2.delete_session(sid)


async def main():
    await run_tests()
    print()
    print(f"总计: {PASS} 通过, {FAIL} 失败")
    for line in RESULTS:
        if "[FAIL]" in line:
            print(line)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    asyncio.run(main())
