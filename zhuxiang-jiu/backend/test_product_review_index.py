"""P1-14 评价索引专项测试(fakeredis 仿真 Redis 模式全代码路径)

运行:
    python test_product_review_index.py

不依赖真实 Redis: 以 fakeredis 替换 backend._redis_client 单例,
STORE_MODE 动态切 redis, 验证 ProductRepository 评价/举报索引的
写入-读取-回退-自愈-回填全链路:

    1. 索引写入: add_review → owner + by-member ZSet + 完成标记
    2. 我的评价索引直达: 跨产品聚合 + 时间倒序
    3. 部分索引态回归(关键): 旧评价(无索引)+新评价混合不漏数据
       ——读路径完成标记门控 + 写路径一次性存量回填
    4. 点赞 owner 直达 / owner 缺失自愈补建
    5. 删除评价同步清理索引
    6. 举报: 时间线 ZSet + flag 直达 + 存量回填 + 回退
    7. _redis_list_all 过滤修复: review:* 单数键不再 hgetall 崩溃
    8. 种子对齐: seed_product_reviews 写索引 + 完成标记
"""
import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["AUTH_MODE"] = "compat"

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


def _product(pid, name):
    return {"product_id": pid, "name": name, "series": "test",
            "base_price": 100, "status": "on"}


async def run_index_tests():
    import fakeredis.aioredis as fakemod
    import repositories.backend as backend_mod
    from repositories.product_repository import ProductRepository

    # ---- fakeredis 注入(STORE_MODE 动态切 redis) ----
    fake = fakemod.FakeRedis(decode_responses=True)
    orig_client = backend_mod._redis_client
    orig_mode = os.environ.get("STORE_MODE")
    backend_mod._redis_client = fake
    os.environ["STORE_MODE"] = "redis"
    try:
        repo = ProductRepository()
        await repo.save_product(_product("P1", "竹韵"))
        await repo.save_product(_product("P2", "竹香"))

        # ========== 1. 索引写入 ==========
        r1 = await repo.add_review("P1", {"member_id": 7, "rating": 5,
                                          "content": "好"})
        r2 = await repo.add_review("P2", {"member_id": 7, "rating": 4,
                                          "content": "不错"})
        owner = await fake.get(
            backend_mod._k("product", "review", "owner", r1["review_id"]))
        check("索引: owner 写入", owner == "P1", f"got {owner}")
        zkey, done = repo._member_index_keys(7)
        zcard = await fake.zcard(zkey)
        check("索引: by-member ZSet 2 条", zcard == 2, f"got {zcard}")
        check("索引: 完成标记落库(首次写入触发回填)",
              await fake.exists(done) == 1)

        # ========== 2. 我的评价索引直达 ==========
        mine = await repo.list_reviews_by_member(7)
        check("我的评价: 跨产品聚合 2 条", len(mine) == 2, f"got {len(mine)}")
        check("我的评价: 时间倒序", mine[0]["review_id"] == r2["review_id"])

        # ========== 3. 部分索引态回归(旧数据混合) ==========
        # 模拟升级前旧评价: 直接 rpush 绕过 add_review(无索引无标记)
        import json as _json
        legacy = {"review_id": "rv_legacy_1", "member_id": 9, "rating": 3,
                  "content": "旧评价", "created_at": "2026-01-01T00:00:00+00:00",
                  "status": "published"}
        await fake.rpush(backend_mod._k("product", "reviews", "P1"),
                         _json.dumps(legacy, ensure_ascii=False))
        # 旧会员读 → 无完成标记 → 回退全量遍历, 旧评价可见
        mine9 = await repo.list_reviews_by_member(9)
        check("旧数据: 无标记走回退, 旧评价可见", len(mine9) == 1,
              f"got {len(mine9)}")
        # 旧会员发新评价 → 触发一次性回填 → 索引含旧+新, 不漏
        r9 = await repo.add_review("P2", {"member_id": 9, "rating": 5,
                                          "content": "新评价"})
        mine9 = await repo.list_reviews_by_member(9)
        ids9 = {m["review_id"] for m in mine9}
        check("回填: 旧+新混合不漏(部分索引态回归)",
              ids9 == {"rv_legacy_1", r9["review_id"]}, f"got {ids9}")

        # ========== 4. 点赞 owner 直达 + 自愈 ==========
        await repo.add_like(r1["review_id"], 100)
        reviews_p1 = await repo._redis_get_reviews("P1")
        liked = next(x for x in reviews_p1
                     if x["review_id"] == r1["review_id"])
        check("点赞: owner 直达更新 like_count",
              liked["like_count"] == 1, f"got {liked['like_count']}")
        # 删 owner 键模拟旧数据 → 回退扫描 + 自愈补建
        await fake.delete(backend_mod._k("product", "review", "owner",
                                         r1["review_id"]))
        await repo.add_like(r1["review_id"], 101)
        healed = await fake.get(backend_mod._k("product", "review", "owner",
                                               r1["review_id"]))
        reviews_p1 = await repo._redis_get_reviews("P1")
        liked = next(x for x in reviews_p1
                     if x["review_id"] == r1["review_id"])
        check("点赞: owner 缺失回退仍正确", liked["like_count"] == 2,
              f"got {liked['like_count']}")
        check("点赞: 自愈补建 owner 索引", healed == "P1", f"got {healed}")

        # ========== 5. 删除清理索引 ==========
        await repo.delete_review("P2", r9["review_id"])
        gone = await fake.exists(backend_mod._k("product", "review", "owner",
                                                r9["review_id"]))
        zcard9 = await fake.zcard(repo._member_index_keys(9)[0])
        check("删除: owner 索引清理", gone == 0)
        check("删除: by-member zrem", zcard9 == 1, f"got {zcard9}")

        # ========== 6. 举报索引 + 回填 + 回退 ==========
        # 模拟旧举报(直接 SET, 无索引)
        old_report = {"report_id": "rpt_legacy", "review_id": r1["review_id"],
                      "reporter_id": 200, "reason": "旧",
                      "created_at": "2026-01-01T00:00:00+00:00",
                      "status": "pending"}
        await fake.set(backend_mod._k("product", "review", "report",
                                      "rpt_legacy"),
                       _json.dumps(old_report, ensure_ascii=False))
        # 旧态读 → 回退可见
        reports = await repo.list_reports()
        check("举报: 旧数据回退可见", len(reports) == 1, f"got {len(reports)}")
        # 新建举报 → 回填旧举报进时间线
        rep = await repo.create_report({"review_id": r1["review_id"],
                                        "reporter_id": 201, "reason": "广告"})
        reports = await repo.list_reports()
        rids = {x["report_id"] for x in reports}
        check("举报: 新建触发存量回填(旧+新都在)",
              rids == {"rpt_legacy", rep["report_id"]}, f"got {rids}")
        check("举报: 时间线倒序(新在前)",
              reports[0]["report_id"] == rep["report_id"])
        dup = await repo.get_report_by_review_reporter(
            r1["review_id"], 201)
        check("举报: flag 直达查重", dup is not None
              and dup["report_id"] == rep["report_id"])
        legacy_dup = await repo.get_report_by_review_reporter(
            r1["review_id"], 200)
        check("举报: 旧举报回退查重(无 flag)",
              legacy_dup is not None
              and legacy_dup["report_id"] == "rpt_legacy")
        # 删完成标记 → 回退仍全量正确
        await fake.delete(backend_mod._k("product", "review", "report",
                                         "index", "done"))
        reports = await repo.list_reports()
        check("举报: 标记删除走回退全量", len(reports) == 2,
              f"got {len(reports)}")

        # ========== 7. _redis_list_all 过滤修复 ==========
        # (此刻 review:owner/report/flag/index 等单数键大量存在,
        #  修复前 hgetall 命中 String/ZSet → WRONGTYPE 崩溃)
        products = await repo.list_all()
        pids = {p["product_id"] for p in products}
        check("list_all: review:* 单数键不炸且只返回产品",
              pids == {"P1", "P2"}, f"got {pids}")

        # ========== 8. 种子对齐 ==========
        from scripts.seed_redis import seed_product_reviews
        seed_fake = fakemod.FakeRedis(decode_responses=True)
        n = await seed_product_reviews(seed_fake)
        check("种子: 写入种子评价", n >= 3, f"got {n}")
        seed_owner = await seed_fake.get(backend_mod._k(
            "product", "review", "owner", "rv_seed_001"))
        check("种子: owner 索引齐备", seed_owner is not None,
              f"got {seed_owner}")
        seed_done = await seed_fake.exists(backend_mod._k(
            "product", "reviews", "by", "member", "1", "done"))
        check("种子: 会员完成标记", seed_done == 1)
        seed_rep_done = await seed_fake.exists(backend_mod._k(
            "product", "review", "report", "index", "done"))
        check("种子: 举报索引完成标记", seed_rep_done == 1)
    finally:
        backend_mod._redis_client = orig_client
        os.environ["STORE_MODE"] = orig_mode or "asyncio"
        try:
            await fake.aclose()
        except Exception:
            pass


def main():
    asyncio.run(run_index_tests())
    print("\n".join(RESULTS))
    print("-" * 64)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
