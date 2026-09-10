"""40号·平台流量DV博主模块·P6f-3 行业标准输出专项测试

覆盖(设计文档《40号 P6f 规划方案》§5):
    1. PII 防线: 扫描命中(手机/身份证/邮箱)/脱敏打码/零命中
    2. 年度数据聚合: 授权/深审/撤回/水印覆盖
    3. 白皮书结构: 四章节固定/数据出数/发布责任注明/
       PII 扫描兜底
    4. 开放数据集: 平台聚合/样本门过滤/许可/零个体数据

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_blogger_p6f3.py
"""

import asyncio
import os
import sys


# 确保使用内存模式 + LLM 关闭(规则轨确定性测试)
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
# mock 槽位/日期固定(测试确定性)
os.environ["BLOGGER_MOCK_SLOT"] = "1"
os.environ["BLOGGER_MOCK_DATE"] = "20260910"

from services.blogger_whitepaper_service import (
    BloggerWhitepaperService, scan_pii, mask_pii,
    MIN_SAMPLE_GATE, OPEN_LICENSE,
)

PASS = 0
FAIL = 0
RESULTS = []

GOOD_META = {"originUrl": "https://example.com/w/1",
             "creatorVerified": True, "platform": "douyin"}


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  \u2713 {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  \u2717 {name} \u2014 {detail}")


def reset_store():
    from repositories.store import reset_store as _reset
    _reset()


async def _seed_data():
    """构造聚合底座: 2 授权 + 3 转发 + 3 作品"""
    from services.blogger_fwd_service import BloggerFwdService
    from services.blogger_av_create_service import \
        BloggerAVCreateService
    from services.blogger_av_publish_service import \
        BloggerAVPublishService
    fwd = BloggerFwdService()
    # 授权 1(revoked)+ 授权 2(active)
    a1 = await fwd.register_auth(
        "wp-src-1", "cc", "创作者甲", "私信", "本人")
    a2 = await fwd.register_auth(
        "wp-src-2", "mcn", "创作者乙", "私信", "MCN")
    await fwd.revoke_auth(a1["authId"])
    # 转发 3 条(2 auto + 1 拒绝——价值观冲突)
    await fwd.deep_review(a2["authId"], "选酒避坑指南",
                          source_meta=GOOD_META)
    await fwd.deep_review(a2["authId"], "周末配酒攻略",
                          source_meta=GOOD_META)
    try:
        await fwd.deep_review(a2["authId"], "炫富必备高端酒",
                              source_meta=GOOD_META)
    except ValueError:
        pass   # 深审拒绝(预期)
    # AV 作品 3 条(同平台——过样本门)
    create = BloggerAVCreateService()
    pub = BloggerAVPublishService(
        repo=create.repo, blogger_service=create.svc)
    persona = await create.register_persona(
        "白皮书人设", "original_ip")
    for i in range(3):
        script = await create.generate_script(
            f"选酒指南{i}", "douyin", persona["personaId"],
            "hook_price_anchor")
        work = await create.render_work(script["scriptId"])
        await pub.publish_av_work(work["avWorkId"])
    return fwd


# ============================================================
# 1. PII 防线(3 断言)
# ============================================================

class TestPII:
    async def run(self):
        reset_store()

        # 1) 扫描命中(手机/身份证/邮箱——身份证串同时命中
        #    手机子模式, 去重后三类型全覆盖)
        hits = scan_pii(
            "联系 13812345678 或身份证 11010119900101123X "
            "邮箱 a@b.com")
        record("PII-扫描命中三类",
               len(hits) >= 3
               and "13812345678" in hits
               and "11010119900101123X" in hits
               and "a@b.com" in hits,
               f"hits={hits}")

        # 2) 脱敏打码
        masked = mask_pii("手机 13812345678 后联系")
        record("PII-脱敏打码",
               "13812345678" not in masked and "***" in masked,
               f"m={masked}")

        # 3) 干净文本零命中
        record("PII-干净零命中",
               scan_pii("竹香型白酒品鉴指南") == [])


# ============================================================
# 2. 年度数据聚合(3 断言)
# ============================================================

class TestAnnualData:
    async def run(self):
        reset_store()
        await _seed_data()
        svc = BloggerWhitepaperService()

        # 4) 授权聚合(2 总/1 在役/1 撤回)
        d = await svc.annual_data()
        record("年度-授权聚合",
               d["authTotal"] == 2 and d["authActive"] == 1
               and d["authRevoked"] == 1,
               f"d={d}")

        # 5) 深审分布(2 auto 入库; 拒绝轨不留库——价值观
        #    拦截在深审入口即阻断, 全量留痕在日志不在表)
        record("年度-深审分布",
               d["reviewAuto"] == 2
               and d["reviewRejected"] == 0
               and d["valuesBlocked"] == 0,
               f"a={d['reviewAuto']} r={d['reviewRejected']}")

        # 6) 水印覆盖(3 脚本全覆盖)
        record("年度-水印覆盖",
               d["scriptTotal"] == 3
               and d["watermarkCoverage"] == 1.0,
               f"n={d['scriptTotal']} c={d['watermarkCoverage']}")


# ============================================================
# 3. 白皮书结构(4 断言)
# ============================================================

class TestWhitepaper:
    async def run(self):
        reset_store()
        await _seed_data()
        svc = BloggerWhitepaperService()

        # 7) 四章节固定结构
        wp = await svc.build_whitepaper(year=2026)
        record("白皮书-四章节",
               set(wp["sections"].keys())
               == {"framework", "annual_data",
                   "redline_cases", "initiative"}
               and wp["year"] == 2026,
               f"s={list(wp['sections'].keys())}")

        # 8) 数据出数(模板出数——年度数据嵌入)
        record("白皮书-数据出数",
               wp["sections"]["annual_data"]["data"]
               ["authTotal"] == 2,
               f"d={wp['sections']['annual_data']['data'].get(
                   'authTotal')}")

        # 9) 发布责任注明(AI 仅展示)
        record("白皮书-发布责任",
               "AI 仅展示" in wp["publishNote"]
               and "审批" in wp["publishNote"],
               f"n={wp['publishNote']}")

        # 10) PII 扫描兜底(已扫描+零命中)
        record("白皮书-PII兜底",
               wp["piiScanned"] is True
               and wp["piiHits"] == 0,
               f"h={wp['piiHits']}")


# ============================================================
# 4. 开放数据集(4 断言)
# ============================================================

class TestDataset:
    async def run(self):
        reset_store()
        await _seed_data()
        svc = BloggerWhitepaperService()

        # 11) 平台聚合(douyin 3 作品——过样本门)
        ds = await svc.open_dataset()
        record("数据集-平台聚合",
               ds["platformDistribution"].get(
                   "douyin", {}).get("works") == 3,
               f"p={ds['platformDistribution']}")

        # 12) 样本门过滤(<3 的平台被过滤并列出)
        record("数据集-样本门",
               all(d["works"] >= MIN_SAMPLE_GATE
                   for d in
                   ds["platformDistribution"].values()),
               f"g={ds['gatedPlatforms']}")

        # 13) 许可 + 零个体数据(创作者名不在输出)
        blob = str(ds)
        record("数据集-许可与零个体",
               ds["license"] == OPEN_LICENSE
               and "创作者甲" not in blob
               and "创作者乙" not in blob,
               f"l={ds['license']}")

        # 14) 转发聚合(仅计数——2 总; 拒绝轨不留库)
        record("数据集-转发计数",
               ds["forwardSummary"]["totalForwards"] == 2
               and ds["forwardSummary"]["byReviewStatus"]
               ["auto"] == 2,
               f"f={ds['forwardSummary']}")


async def main():
    tests = [TestPII(), TestAnnualData(), TestWhitepaper(),
             TestDataset()]
    for t in tests:
        await t.run()
    print("=" * 60)
    print("40号 P6f-3 行业标准输出专项测试")
    print("=" * 60)
    for line in RESULTS:
        print(line)
    print("-" * 60)
    print(f"通过: {PASS} / {PASS + FAIL}")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
