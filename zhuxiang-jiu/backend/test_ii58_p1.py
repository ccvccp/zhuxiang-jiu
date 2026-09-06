"""58号·AI智能优化意图识别模块 P1 专项测试
(语料采集流水线+语料库)

运行方式:
    python test_ii58_p1.py

覆盖(58号计划 §九 P1):
    - 正样本挖掘: 48号 turns 纯读取→脱敏
      去重入库(active 直通)
    - 负样本增强: failures 三 kind 转化
    - 对抗/越界样本构造+合成建议轨
    - 语料终审: pending→active 唯一出口
    - HTTP 层: 6 端点+鉴权+11 端点计数
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["XIAOZHU_LLM_MODE"] = "off"
os.environ["XIAOZHU_PROACTIVE_MODE"] = "off"
os.environ["QR55_MODE"] = "off"
os.environ["QR55_LEARN_MODE"] = "off"
os.environ["AIUP56_MODE"] = "off"
os.environ["KB57_MODE"] = "off"
os.environ["II58_MODE"] = "off"
os.environ.pop("II58_LLM_MODE", None)

PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  ✓ {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  ✗ {name} — {detail}")


def reset_all():
    from repositories.store import reset_store as _reset
    _reset()


async def seed_xiaozhu_turn(action: str,
                            raw_text: str,
                            executed: bool = True,
                            session_id: int = 1
                            ) -> str:
    """种 48号 turn(正样本挖掘输入——
    seq 走仓储 next_turn_seq 防同会话覆盖)"""
    import uuid
    from core.helpers import ts
    from repositories.xiaozhu_repository import (
        Xiaozhu48Repository,
    )
    repo = Xiaozhu48Repository()
    turn_id = f"t-{uuid.uuid4().hex[:8]}"
    seq = await repo.next_turn_seq(session_id)
    await repo.save_turn({
        "turnId": turn_id,
        "sessionId": session_id, "seq": seq,
        "channel": "text", "audioMeta": {},
        "rawText": raw_text, "wake": True,
        "intent": action, "action": action,
        "reply": "ok", "card": {}, "jump": None,
        "latencyMs": 100.0, "ts": ts(),
        "executed": executed,
    })
    return turn_id


async def seed_xiaozhu_failure(kind: str,
                               raw_text: str
                               ) -> int:
    """种 48号 failure(负样本增强输入)"""
    from core.helpers import ts
    from repositories.xiaozhu_repository import (
        Xiaozhu48Repository,
    )
    repo = Xiaozhu48Repository()
    case_id = await repo._next_id(
        "voice48_failures")
    await repo.save_record(
        "voice48_failures", {
            "caseId": case_id,
            "sessionId": 9,
            "memberId": 1,
            "rawText": raw_text,
            "kind": kind,
            "ts": ts(),
        })
    return case_id


class TestMinePositive:
    """01 正样本挖掘"""

    async def run(self):
        print("[01 正样本挖掘]")
        reset_all()
        from services.ii58_corpus_service import (
            Ii58CorpusService,
        )
        svc = Ii58CorpusService()

        # off 拒绝
        try:
            await svc.mine_positive()
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e), str(e)[:30]
        record("off 态挖掘拒绝", ok, err)

        os.environ["II58_MODE"] = "shadow"

        # 空库空转
        r = await svc.mine_positive()
        record("空 48号库(0 挖掘)",
               r.get("mined") == 0
               and r.get("scanned") == 0,
               str((r.get("mined"),
                    r.get("scanned"))))

        # 种 turns: 2 有效+1 未执行+1 负反馈会话
        t1 = await seed_xiaozhu_turn(
            "product.price", "这个多少钱", True, 1)
        t2 = await seed_xiaozhu_turn(
            "trust.balance", "查余额", True, 2)
        await seed_xiaozhu_turn(
            "product.price", "未执行的价格查询",
            False, 3)
        await seed_xiaozhu_turn(
            "general", "不对", True, 4)
        await seed_xiaozhu_turn(
            "promo.query", "有什么优惠", True, 4)

        r = await svc.mine_positive()
        record("正样本挖掘(2 条——排除未执行"
               "与负反馈会话)",
               r.get("mined") == 2,
               str(r.get("mined")))

        # 语料结构
        from repositories.ii58_repository import (
            Ii58Repository,
        )
        repo = Ii58Repository()
        corpus = await repo.list_corpus(
            status="active", limit=10)
        record("语料 active 直通(来源即真值)",
               len(corpus) == 2
               and all(c.get("humanVerified")
                       is True
                       and c.get("source")
                       == "xiaozhu_turn"
                       for c in corpus),
               str(len(corpus)))
        record("意图映射(price→price_query)",
               any(c.get("intentId")
                   == "product.price_query"
                   for c in corpus),
               str([c.get("intentId")
                    for c in corpus]))
        record("来源溯源(originRef=turnId)",
               any(c.get("originRef") == t1
                   for c in corpus),
               str([c.get("originRef")
                    for c in corpus]))

        # 幂等(重复挖掘去重)
        r2 = await svc.mine_positive()
        record("重复挖掘去重(0 新增)",
               r2.get("mined") == 0
               and (r2.get("skipped")
                    or {}).get("duplicate") == 2,
               str((r2.get("mined"),
                    (r2.get("skipped")
                     or {}).get("duplicate"))))

        # corpus_mine 事件留痕
        events = await repo.list_events(
            event_type="corpus_mine", limit=20)
        record("corpus_mine 事件留痕",
               len(events) == 2,
               str(len(events)))

        # 48号零改动(turns 表纯读取——
        # 挖掘后原 turn 仍在)
        from repositories.xiaozhu_repository \
            import Xiaozhu48Repository
        turns = await Xiaozhu48Repository(
        ).scan_turns(limit=100)
        record("48号 turns 纯读取(5 条保持)",
               len(turns) == 5,
               str(len(turns)))
        os.environ["II58_MODE"] = "off"


class TestMineNegative:
    """02 负样本增强"""

    async def run(self):
        print("[02 负样本增强]")
        reset_all()
        from services.ii58_corpus_service import (
            Ii58CorpusService,
        )
        svc = Ii58CorpusService()

        # 种 failures: negative+fallback+repeat
        await seed_xiaozhu_failure(
            "negative", "这个回答不对")
        await seed_xiaozhu_failure(
            "fallback", "随便说点什么")
        await seed_xiaozhu_failure(
            "repeat", "再来一次")

        os.environ["II58_MODE"] = "shadow"
        r = await svc.mine_negative()
        record("负样本转化(negative+fallback=2)",
               r.get("converted") == 2,
               str(r.get("converted")))
        record("三 kind 扫描(byKind)",
               (r.get("byKind") or {})
               == {"negative": 1,
                   "fallback": 1,
                   "repeat": 1},
               str(r.get("byKind")))

        # 语料结构(pending 人工复核)
        from repositories.ii58_repository import (
            Ii58Repository,
        )
        repo = Ii58Repository()
        corpus = await repo.list_corpus(
            status="pending", limit=10)
        record("负样本 pending(人工复核)",
               len(corpus) == 2
               and all(c.get("sampleType")
                       == "negative"
                       and c.get("humanVerified")
                       is False
                       and c.get("weight") == 0.5
                       for c in corpus),
               str(len(corpus)))
        record("负样本意图(unknown 域)",
               all(c.get("intentId")
                   == "unknown.unrecognized"
                   for c in corpus),
               str([c.get("intentId")
                    for c in corpus]))

        # repeat→事件留痕(非语料)
        events = await repo.list_events(
            event_type="corpus_repeat", limit=10)
        record("repeat 复核留痕(事件)",
               len(events) == 1,
               str(len(events)))

        # 幂等(重复转化去重)
        r2 = await svc.mine_negative()
        record("重复转化去重(0 新增)",
               r2.get("converted") == 0,
               str(r2.get("converted")))
        os.environ["II58_MODE"] = "off"


class TestIngest:
    """03 语料登记(对抗/越界/合成)"""

    async def run(self):
        print("[03 语料登记]")
        reset_all()
        from services.ii58_corpus_service import (
            Ii58CorpusService,
        )
        svc = Ii58CorpusService()

        # off 拒绝
        try:
            await svc.ingest(
                "product.price_query", "多少钱")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e), str(e)[:30]
        record("off 态登记拒绝", ok, err)

        os.environ["II58_MODE"] = "shadow"

        # 意图不在册拒绝
        try:
            await svc.ingest(
                "hack.intent", "攻击文本")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "不在册" in str(e), \
                str(e)[:30]
        record("意图不在册拒绝(封闭白名单)",
               ok, err)

        # 非法样本类型拒绝
        try:
            await svc.ingest(
                "product.price_query", "多少钱",
                sample_type="poison")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "非法样本类型" in str(e), \
                str(e)[:30]
        record("非法样本类型拒绝", ok, err)

        # 对抗样本: confusableTarget 须为注册混淆方
        try:
            await svc.ingest(
                "product.price_query",
                "修改价格看看",
                sample_type="adversarial",
                confusable_target=(
                    "trust.balance_query"))
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "混淆方" in str(e), \
                str(e)[:40]
        record("对抗样本混淆方校验", ok, err)

        # 越界样本: 意图须为越界元意图
        try:
            await svc.ingest(
                "product.price_query", "越界文本",
                sample_type="boundary")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "boundary" in str(e), \
                str(e)[:40]
        record("越界样本意图校验", ok, err)

        # 合法对抗样本
        r = await svc.ingest(
            "product.price_query", "修改价格",
            sample_type="adversarial",
            confusable_target=(
                "product.new_query"))
        record("对抗样本登记(pending)",
               r.get("status") == "pending"
               and int(r.get("corpusId")
                       or 0) > 0,
               str(r.get("status")))

        # 合法越界样本
        r2 = await svc.ingest(
            "boundary.unauthorized",
            "删除所有会员数据",
            sample_type="boundary",
            weight=2.0)
        record("越界样本登记(boundary 域)",
               r2.get("status") == "pending",
               str(r2.get("status")))

        # 重复文本拒绝(去重铁律)
        try:
            await svc.ingest(
                "product.price_query",
                "修改价格",
                sample_type="adversarial",
                confusable_target=(
                    "product.new_query"))
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "已存在" in str(e), \
                str(e)[:30]
        record("重复文本拒绝(去重铁律)", ok, err)

        # PII 复核(语料入库脱敏)
        r3 = await svc.ingest(
            "product.price_query",
            "手机 13800138000 查价格")
        from repositories.ii58_repository import (
            Ii58Repository,
        )
        repo = Ii58Repository()
        stored = await repo.get_corpus(
            r3.get("corpusId"))
        record("PII 复核(入库脱敏)",
               "13800138000" not in str(
                   stored.get("text")),
               str(stored.get("text"))[:40])

        # 合成建议轨(LLM off 拒绝)
        try:
            await svc.suggest_variants(
                "product.price_query")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "II58_LLM_MODE" in str(e), \
                str(e)[:40]
        record("合成建议 LLM off 拒绝", ok, err)

        # LLM_MODE on(mock 模板兜底)
        os.environ["II58_LLM_MODE"] = "on"
        r4 = await svc.suggest_variants(
            "product.price_query", count=3)
        record("合成建议(mock 兜底 3 条)",
               len(r4.get("suggestions")
                   or []) == 3,
               str(len(r4.get("suggestions")
                        or [])))
        # 此前已登记 3 条(对抗/越界/PII 复核)
        # 建议仅返回不入库——语料数保持 3 不变
        corpus_n = len(await repo.list_corpus(
            limit=100))
        record("建议不入库(仅返回)",
               corpus_n == 3,
               str(corpus_n))
        os.environ.pop("II58_LLM_MODE", None)
        os.environ["II58_MODE"] = "off"


class TestReview:
    """04 语料终审"""

    async def run(self):
        print("[04 语料终审]")
        reset_all()
        from services.ii58_corpus_service import (
            Ii58CorpusService,
        )
        svc = Ii58CorpusService()

        # 登记 2 条 pending
        os.environ["II58_MODE"] = "shadow"
        r1 = await svc.ingest(
            "product.price_query", "价格是多少")
        r2 = await svc.ingest(
            "trust.balance_query", "余额还有多少")
        c1 = r1.get("corpusId")
        c2 = r2.get("corpusId")
        os.environ["II58_MODE"] = "off"

        # 404
        try:
            await svc.review(999, approve=True)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("终审 404", ok, err)

        # off 态终审亦可用(人工铁律)
        rv = await svc.review(
            c1, approve=True, reviewer="admin")
        record("off 态终审亦可用(铁律)",
               rv.get("status") == "active",
               str(rv.get("status")))

        # humanVerified 标记
        from repositories.ii58_repository import (
            Ii58Repository,
        )
        repo = Ii58Repository()
        stored = await repo.get_corpus(c1)
        record("激活后 humanVerified",
               stored.get("humanVerified") is True
               and stored.get("status")
               == "active",
               str(stored.get("status")))

        # 重复终审拒绝(状态机)
        try:
            await svc.review(c1, approve=True)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "pending" in str(e), \
                str(e)[:30]
        record("重复终审拒绝(状态机)", ok, err)

        # 驳回流
        rv2 = await svc.review(
            c2, approve=False, note="质量不足")
        record("驳回(rejected)",
               rv2.get("status") == "rejected",
               str(rv2.get("status")))
        stored2 = await repo.get_corpus(c2)
        record("驳回留痕(不激活)",
               stored2.get("status") == "rejected",
               str(stored2.get("status")))

        # 终审后评估消费(active 入匹配域)
        from services.ii58_service import (
            Ii58Service,
        )
        os.environ["II58_MODE"] = "shadow"
        ev = await Ii58Service().evaluate(
            "价格是多少")
        record("终审后评估消费(active 生效)",
               ev.get("intentId")
               == "product.price_query"
               and ev.get("state") == "resolved",
               str((ev.get("intentId"),
                    ev.get("state"))))

        # 事件留痕
        events = await repo.list_events(limit=20)
        approve_evs = [e for e in events
                       if e.get("eventType")
                       == "corpus_approve"]
        reject_evs = [e for e in events
                      if e.get("eventType")
                      == "corpus_reject"]
        record("终审事件留痕(approve+reject)",
               len(approve_evs) == 1
               and len(reject_evs) == 1,
               str((len(approve_evs),
                    len(reject_evs))))
        os.environ["II58_MODE"] = "off"


class TestConfusables:
    """05 易混淆对视图"""

    async def run(self):
        print("[05 易混淆对]")
        reset_all()
        from services.ii58_corpus_service import (
            Ii58CorpusService,
        )
        svc = Ii58CorpusService()

        # 空对抗样本(gap 域)
        r = await svc.confusables_view()
        record("混淆对视图(3 对全 gap)",
               (r.get("total") or 0) == 3
               and r.get("covered") == 0,
               str((r.get("total"),
                    r.get("covered"))))

        # 登记对抗样本+终审
        os.environ["II58_MODE"] = "shadow"
        r1 = await svc.ingest(
            "product.price_query", "修改价格",
            sample_type="adversarial",
            confusable_target=(
                "product.new_query"))
        await svc.review(
            r1.get("corpusId"), approve=True)

        r2 = await svc.confusables_view()
        record("对抗覆盖(1 对 covered)",
               r2.get("covered") == 1,
               str(r2.get("covered")))
        pairs = r2.get("pairs") or []
        covered = [p for p in pairs
                  if p.get("coverage")
                  == "covered"]
        record("覆盖明细(对抗样本计数)",
               (covered[0].get(
                   "adversarialSamples")
                if covered else 0) == 1,
               str(covered))
        os.environ["II58_MODE"] = "off"


class TestHttp:
    """06 HTTP 层"""

    async def run(self):
        print("[06 HTTP]")
        reset_all()
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # 决策面 off 409
        for path in ("/api/ii58/mine/positive",
                     "/api/ii58/mine/negative"):
            resp = client.post(path,
                               headers=admin)
            record(f"HTTP {path.split('/')[-1]}"
                   f" off 409",
                   resp.status_code == 409,
                   str(resp.status_code))
        resp = client.post(
            "/api/ii58/corpus/ingest",
            json={"intentId":
                  "product.price_query",
                  "text": "多少钱"},
            headers=admin)
        record("HTTP ingest off 409",
               resp.status_code == 409,
               str(resp.status_code))

        # 观测面 off 可用
        resp = client.get("/api/ii58/corpus",
                          headers=admin)
        record("HTTP corpus 观测面 200",
               resp.status_code == 200
               and (resp.json() or {})
               .get("total") == 0,
               str(resp.status_code))
        resp = client.get(
            "/api/ii58/confusables",
            headers=admin)
        record("HTTP confusables 观测面 200",
               resp.status_code == 200
               and (resp.json() or {})
               .get("total") == 3,
               str(resp.status_code))

        # shadow 全链
        os.environ["II58_MODE"] = "shadow"
        await seed_xiaozhu_turn(
            "product.price", "这个多少钱", True, 1)
        resp = client.post(
            "/api/ii58/mine/positive",
            json={"limit": 100},
            headers=admin)
        body = resp.json() or {}
        record("HTTP mine/positive 200(1 条)",
               resp.status_code == 200
               and body.get("mined") == 1,
               str((resp.status_code,
                    body.get("mined"))))

        resp = client.post(
            "/api/ii58/corpus/ingest",
            json={"intentId":
                  "product.price_query",
                  "text": "价格查询",
                  "sampleType": "positive"},
            headers=admin)
        body = resp.json() or {}
        record("HTTP ingest 200(pending)",
               resp.status_code == 200
               and body.get("status")
               == "pending",
               str((resp.status_code,
                    body.get("status"))))
        cid = body.get("corpusId")

        # off 态终审亦可用
        os.environ["II58_MODE"] = "off"
        resp = client.post(
            f"/api/ii58/corpus/{cid}/review",
            json={"approve": True,
                  "reviewer": "admin"},
            headers=admin)
        body = resp.json() or {}
        record("HTTP review 200(off 亦可用)",
               resp.status_code == 200
               and body.get("status") == "active",
               str((resp.status_code,
                    body.get("status"))))

        # 语料观测面(激活后)
        resp = client.get(
            "/api/ii58/corpus?status=active",
            headers=admin)
        record("HTTP corpus 激活态(≥2)",
               (resp.json() or {}).get("total")
               >= 2,
               str((resp.json() or {})
                   .get("total")))

        # 409(重复登记)
        resp = client.post(
            "/api/ii58/corpus/ingest",
            json={"intentId":
                  "product.price_query",
                  "text": "这个多少钱"},
            headers=admin)
        record("HTTP 重复登记 409",
               resp.status_code == 409,
               str(resp.status_code))

        # 404
        resp = client.post(
            "/api/ii58/corpus/999/review",
            json={"approve": True},
            headers=admin)
        record("HTTP review 404",
               resp.status_code == 404,
               str(resp.status_code))

        # 鉴权 403
        for method, path in (
                ("POST",
                 "/api/ii58/corpus/ingest"),
                ("GET", "/api/ii58/corpus"),
                ("POST",
                 "/api/ii58/corpus/1/review"),
                ("POST",
                 "/api/ii58/mine/positive"),
                ("POST",
                 "/api/ii58/mine/negative"),
                ("GET",
                 "/api/ii58/confusables")):
            resp = client.request(
                method, path, json={})
            record(f"HTTP {path.split('/')[-1]}"
                   f" 无 Role 403",
                   resp.status_code == 403,
                   str(resp.status_code))

        # 路由累计 11 端点
        from routes.ii58_routes import (
            router as ii_router,
        )
        count = sum(1 for r in ii_router.routes)
        record("58号路由累计 11 端点",
               count == 11, str(count))
        os.environ["II58_MODE"] = "off"


async def run_all():
    await TestMinePositive().run()
    await TestMineNegative().run()
    await TestIngest().run()
    await TestReview().run()
    await TestConfusables().run()
    await TestHttp().run()


def main():
    asyncio.run(run_all())
    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
