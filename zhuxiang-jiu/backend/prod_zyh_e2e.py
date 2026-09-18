"""竹韵·智衡(75号)生产 E2E: JWT 范式全链实证(e2e_auth_helper)"""
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
    # 0. 健康探针
    r = c.get("/api/decision/health")
    record("健康探针", r.status_code == 200)

    # 1. 观测面(公开语义, 无头可达——知识内核事实锚点)
    r = c.get("/api/zyh/knowledge")
    record("knowledge 播种(>=8)", r.status_code == 200
           and r.json().get("total", 0) >= 8,
           f"{r.status_code} {r.text[:80]}")
    r = c.get("/api/zyh/graph")
    record("graph(8节点8关系)", r.status_code == 200
           and r.json().get("nodeCount") == 8)
    r = c.get("/api/zyh/rules")
    record("rules 三层公示", r.status_code == 200)
    r = c.get("/api/zyh/mode")
    record("mode 总览", r.status_code == 200
           and r.json().get("mode") == "off")

    # 2. 决策面无 JWT → strict 401(鉴权优先于门控铁律)
    r = c.post("/api/zyh/chat", json={"prompt": "竹香是什么"})
    record("chat 无JWT 401(strict)", r.status_code == 401,
           f"{r.status_code}")

    # 3. JWT 管理面: override 切 shadow
    auth = E2EAuth(c)
    admin = auth.admin_headers()
    member = auth.member_headers()
    r = c.post("/api/zyh/mode/override", json={"mode": "shadow"},
               headers=admin)
    record("override shadow(admin JWT)", r.status_code == 200
           and r.json().get("mode") == "shadow", f"{r.status_code}")

    # 3b. off 门控验证(先切回 off 再带 JWT 验证 409)
    c.post("/api/zyh/mode/override", json={"mode": "off"},
           headers=admin)
    r = c.post("/api/zyh/chat", json={"prompt": "竹香是什么"},
               headers=member)
    record("chat off 409(带JWT门控)", r.status_code == 409,
           f"{r.status_code}")
    c.post("/api/zyh/mode/override", json={"mode": "shadow"},
           headers=admin)

    # 4. 决策面 shadow 放行 + 守门/检索全链(用户 JWT)
    r = c.post("/api/zyh/chat",
               json={"prompt": "竹奕酒是什么工艺？和竹筒酒有什么区别？"},
               headers=member)
    b = r.json()
    record("chat shadow 放行", r.status_code == 200
           and b.get("zyhMode") == "shadow"
           and b.get("knowledgeId") in (
               "craft_constitution", "competitor_contrast"),
           f"{r.status_code} kid={b.get('knowledgeId')}")
    # 守门: 旧工艺表述拦截(带纠正+citations)
    r = c.post("/api/zyh/chat",
               json={"prompt": "你们的酒是灌进竹子里泡出来的吗？"},
               headers=member)
    b = r.json()
    record("守门 L1 拦截(生产)",
           r.status_code == 200
           and b.get("guardrailsTriggered") is True
           and b.get("guardLayer") == 1, f"{b.get('guardLayer')}")
    # 缓存: 变体命中
    r = c.post("/api/zyh/chat",
               json={"prompt": "竹香香型到底什么定义"},
               headers=member)
    record("香型问答(缓存写)", r.status_code == 200)
    r = c.post("/api/zyh/chat",
               json={"prompt": " 竹香香型 到底什么定义 "},
               headers=member)
    record("缓存归一化命中", r.json().get("cacheHit") is True)

    # 5. 压力推演 + 辩题
    r = c.post("/api/zyh/stress-test",
               json={"scenario": "material_moisture",
                     "parameters": {"moisture_increase": 0.12}},
               headers=member)
    record("stress-test(含量化)", r.status_code == 200
           and "+12%" in r.json()["analysis"]["processImpact"])
    r = c.post("/api/zyh/probe/debate", json={"count": 2},
               headers=member)
    record("probe/debate", r.status_code == 200
           and r.json().get("total") == 2)

    # 6. 统计 + 护栏巡检
    r = c.get("/api/zyh/stats")
    record("stats(守门分布)", r.status_code == 200
           and r.json().get("totalRequests", 0) >= 3)
    r = c.post("/api/zyh/mode/guard", headers=admin)
    record("guard 巡检(小样本跳过)",
           r.status_code == 200, f"{r.status_code}")

    # 7. 收尾: 回 off(生产默认态)
    r = c.post("/api/zyh/mode/override", json={"mode": ""},
               headers=admin)
    record("override 清除回 off", r.status_code == 200
           and r.json().get("mode") == "off")

    # 8. E2E 数据清理(零残留原则)
    r = c.post("/api/zyh/cache/clear", headers=admin)
    record("缓存清零(零残留)", r.status_code == 200
           and r.json().get("removed", 0) >= 1)

print("-" * 64)
print(f"通过 {PASS} 项 / 失败 {FAIL} 项")
raise SystemExit(1 if FAIL else 0)
