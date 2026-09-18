"""竹韵·智衡(75号)灰度启用后验证脚本——三档自适应零破坏验证

用法(生产容器内):
    docker exec zhuxiang-backend-1 python prod_zyh_gradation_verify.py

依赖: e2e_auth_helper.py 已注入容器(/app)——若重建过容器需重新:
    docker cp /tmp/e2e_auth_helper.py zhuxiang-backend-1:/app/

验证矩阵(按当前档位自动适配):
    [通用] 健康探针 / 观测面 8 GET(白名单) / 灰度态读取
    [off]    决策面 3 POST 带JWT → 409(门控) + 观测面不受影响
    [shadow] 决策面 200 + zyhMode=shadow 留痕标记 + 守门三层抽查
             + 实体消歧 + 语义缓存命中 + 韧性推演量化
    [assist] 同 shadow(zyhMode=assist)

零破坏原则:
    - 只读观测面 + 少量 chat 探测(留痕无害, 不清生产缓存
      不动 override 档位——验证结束保持启用时状态)
"""
import sys

sys.path.insert(0, "/app")

import httpx

from e2e_auth_helper import E2EAuth

PASS = 0
FAIL = 0


def record(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  \u2713 {name}")
    else:
        FAIL += 1
        print(f"  \u2717 {name} \u2014 {detail}")


with httpx.Client(base_url="http://127.0.0.1:8000", timeout=30) as c:
    # ============================================================
    # 一、基础健康与灰度态
    # ============================================================
    r = c.get("/api/decision/health")
    record("健康探针", r.status_code == 200, f"{r.status_code}")

    r = c.get("/api/zyh/mode")
    mode_state = r.json() if r.status_code == 200 else {}
    mode = mode_state.get("mode", "?")
    source = mode_state.get("source", "?")
    record(f"灰度态读取: {mode}({source})",
           r.status_code == 200 and mode in ("off", "shadow",
                                             "assist"))
    if mode_state.get("paused"):
        print(f"  ⚠ 护栏暂停中: {mode_state.get('pausedReason')}")

    # ============================================================
    # 二、观测面 8 GET(白名单, 三档均永不关停)
    # ============================================================
    OBS = [
        ("/api/zyh/knowledge", "knowledge", lambda b:
         b.get("total", 0) >= 8),
        ("/api/zyh/graph", "graph", lambda b:
         b.get("nodeCount") == 8),
        ("/api/zyh/rules", "rules", lambda b: "l1" in b),
        ("/api/zyh/stats", "stats", lambda b:
         "guard" in b),
        ("/api/zyh/cache/stats", "cache/stats", lambda b:
         "hitRate" in b),
        ("/api/zyh/mode", "mode", lambda b:
         b.get("modeValues") == ["off", "shadow", "assist"]),
        ("/api/zyh/qa", "qa", lambda b: "items" in b),
        ("/api/zyh/knowledge/aroma_type", "knowledge单条",
         lambda b: "item" in b),
    ]
    for path, label, check in OBS:
        r = c.get(path)
        record(f"观测面 {label}", r.status_code == 200
               and check(r.json()), f"{r.status_code}")

    # ============================================================
    # 三、决策面(按档位)
    # ============================================================
    auth = E2EAuth(c)
    admin = auth.admin_headers()
    member = auth.member_headers()

    if mode == "off":
        # 决策面门控: 3 POST 全 409
        for path, body, label in (
                ("/api/zyh/chat", {"prompt": "竹香是什么"},
                 "chat"),
                ("/api/zyh/stress-test",
                 {"scenario": "competitor_impact"}, "stress-test"),
                ("/api/zyh/probe/debate", {"count": 1},
                 "probe/debate")):
            r = c.post(path, json=body, headers=member)
            record(f"决策面 {label} off→409",
                   r.status_code == 409, f"{r.status_code}")
        print("\n  ℹ 当前 off 档——启用 shadow 后重跑本脚本"
              "验证决策面放行")
    else:
        # shadow/assist: 决策面放行 + zyhMode 留痕
        r = c.post("/api/zyh/chat",
                   json={"prompt": "竹奕酒的工艺和竹筒酒的区别"},
                   headers=member)
        b = r.json()
        record(f"chat 放行(zyhMode={mode})",
               r.status_code == 200
               and b.get("zyhMode") == mode
               and b.get("knowledgeId") in (
                   "craft_constitution", "competitor_contrast"),
               f"{r.status_code} mode={b.get('zyhMode')}")

        # 守门 L1 抽查(旧工艺表述拦截)
        r = c.post("/api/zyh/chat",
                   json={"prompt": "你们的酒是活竹种酒灌进竹子里的吗"},
                   headers=member)
        b = r.json()
        record("守门 L1 旧工艺拦截",
               r.status_code == 200
               and b.get("guardrailsTriggered") is True
               and b.get("guardLayer") == 1,
               f"layer={b.get('guardLayer')}")

        # 守门 L2 抽查(医疗断言)
        r = c.post("/api/zyh/chat",
                   json={"prompt": "竹香酒能治病有疗效吗"},
                   headers=member)
        record("守门 L2/L1 医疗拦截",
               r.json().get("guardrailsTriggered") is True)

        # 实体消歧
        r = c.post("/api/zyh/chat",
                   json={"prompt": "竹筒酒和竹奕酒的香型一样吗"},
                   headers=member)
        d = r.json().get("entityDisambiguation") or {}
        record("消歧(竹筒酒竞品维)",
               d.get("resolvedCompetitor")
               == "PRODUCT:ZHU_TONG_JIU"
               and d.get("resolvedAroma") == "AROMA:ZHU_XIANG")

        # 语义缓存(二次同query命中——生产缓存不清, 命中即健康)
        q = "竹香香型的定义和依据标准是什么"
        c.post("/api/zyh/chat", json={"prompt": q}, headers=member)
        r = c.post("/api/zyh/chat", json={"prompt": q},
                   headers=member)
        record("语义缓存二次命中", r.json().get("cacheHit") is True)

        # 韧性推演(量化)
        r = c.post("/api/zyh/stress-test",
                   json={"scenario": "material_moisture",
                         "parameters": {"moisture_increase": 0.12}},
                   headers=member)
        record("压力推演量化",
               r.status_code == 200
               and "+12%" in r.json()["analysis"]
               ["processImpact"], f"{r.status_code}")

        # 辩题生成
        r = c.post("/api/zyh/probe/debate", json={"count": 2},
                   headers=member)
        record("辩题生成", r.status_code == 200
               and r.json().get("total") == 2)

    # ============================================================
    # 四、管理面 + 护栏(只读巡检, 不改档)
    # ============================================================
    r = c.post("/api/zyh/mode/guard", headers=admin)
    b = r.json()
    record("护栏巡检(只读)",
           r.status_code == 200
           and "breaches" in b
           and not b.get("pausedNow", False),
           f"breaches={b.get('breaches')} "
           f"pausedNow={b.get('pausedNow')} "
           f"skip={b.get('skippedSmallSample')}")

    # 管理面权限边界(无 JWT 401)
    r = c.post("/api/zyh/mode/override", json={"mode": "assist"})
    record("管理面无JWT 401", r.status_code == 401,
           f"{r.status_code}")

    # 统计健康(守门有计数/请求有留痕)
    r = c.get("/api/zyh/stats")
    b = r.json()
    record("统计健康(请求有计数)",
           r.status_code == 200
           and b.get("totalRequests", 0) >= 1,
           f"total={b.get('totalRequests')}")

print("-" * 64)
print(f"通过 {PASS} 项 / 失败 {FAIL} 项 "
      f"(档位: {mode})")
raise SystemExit(1 if FAIL else 0)
