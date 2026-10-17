"""76/84 号·边更边锁+时长双档+标题/标签矩阵专项测试(2026-10-17)

覆盖四批改动:
    A. 76 号边更边锁: published_eps/public_view/release_episode 三态语义、
       episode_states 未放送集隐藏、create clamp、酒剧分批、checkpoint 守卫
    B. 84 号时长双档: episode_length_plan 档位、_normalize_durations clamp
       (含 30s 微档)、rule 轨时长按档、score_series pacing 双档达标
    C. 84 号登记: register_episode 单集标题爽文钩(hook 四字前置)+
       标签矩阵(#剧名三变体)+goldenFinger 拼接
    D. 76 号卡点弹性: release 端点 free_episodes 动态调整(对标点众免费窗)

运行: cd backend && python -m pytest test_drama_rolling_release.py -q
"""

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import services.drama84_script_service as d84
from repositories.promo_repository import PromoRepository
from routes.drama76_routes import register_drama76_routes
from services.drama76_series_service import DramaSeriesService
from services.drama84_script_service import (
    DEFAULT_CAST, Drama84ScriptService, episode_length_plan)


# ============================================================
# fixture: 6 集娱乐剧 + 4 集酒剧(独立 store, 零污染)
# ============================================================

FUN6_BODY = {
    "drama_id": "drama_rr", "title": "逆袭之王",
    "description": "都市爽剧", "tags": ["都市"],
    "free_episodes": 2, "published_episodes": 2, "status": "online",
    "episodes": [
        {"episode_id": f"r{i}", "title": f"第{i}幕",
         "video_url": f"/v/r{i}.mp4", "duration_seconds": 70}
        for i in range(1, 7)],
}
ALC4_BODY = {
    "drama_id": "drama_alc_rr", "title": "竹香酒馆二店",
    "description": "品牌剧", "tags": [], "free_episodes": 2,
    "published_episodes": 2, "status": "online",
    "episodes": [
        {"episode_id": f"a{i}", "title": f"幕{i}",
         "video_url": f"/v/a{i}.mp4", "duration_seconds": 60}
        for i in range(1, 5)],
}


@pytest.fixture()
def env():
    shared = PromoRepository(store={})
    svc = DramaSeriesService(repo=shared)

    async def seed():
        await svc.create(FUN6_BODY)
        await svc.create(ALC4_BODY)
        # 三审闸(2026-10-17 风险⑥)后新建集默认 pending——种子剧走
        # 真实流程: 逐集过审(admin review)后放送闸才放行
        for did, total in (("drama_rr", 6), ("drama_alc_rr", 4)):
            for n in range(1, total + 1):
                await svc.review_episode(did, n, approved=True)

    asyncio.run(seed())
    return svc, shared


@pytest.fixture()
def client(env, monkeypatch):
    svc, shared = env
    monkeypatch.setattr(
        "routes.drama76_routes._svc",
        DramaSeriesService(repo=shared))
    app = FastAPI()
    register_drama76_routes(app)
    return TestClient(app)


def _get(svc, drama_id):
    return asyncio.run(svc.get(drama_id))


# ============================================================
# A. 76 号边更边锁
# ============================================================

class TestRollingRelease:

    def test_published_eps_filters_unpublished(self, env):
        svc, _ = env
        d = _get(svc, "drama_rr")
        # published=2 → 仅 [1,2]; 覆盖整剧(published=0)全放
        assert [e["episode_number"] for e in svc.published_eps(d)] == [1, 2]
        d["published_episodes"] = 0
        assert len(svc.published_eps(d)) == 6

    def test_episode_states_hides_unpublished(self, env):
        svc, _ = env
        d = _get(svc, "drama_rr")
        eps = svc.episode_states(d)
        assert [e["episode_number"] for e in eps] == [1, 2]
        # 卡点集(3=free+1)未放送 → 不可见
        assert not any(e["is_checkpoint"] for e in eps)
        # 放送到 3 → 卡点出现且 locked
        d["published_episodes"] = 3
        eps = svc.episode_states(d)
        assert [e["episode_number"] for e in eps] == [1, 2, 3]
        cp = next(e for e in eps if e["is_checkpoint"])
        assert cp["status"] == "locked" and not cp["is_unlocked"]

    def test_release_first_enables_at_free(self, env):
        svc, _ = env
        async def run():
            await svc.create({**FUN6_BODY, "drama_id": "drama_rr2",
                              "published_episodes": 0})
            # 首发放送的前 free 集须先过审(三审闸)
            for n in (1, 2):
                await svc.review_episode("drama_rr2", n, approved=True)
            return await svc.release_episode("drama_rr2")
        d = asyncio.run(run())
        assert d["published_episodes"] == 2   # 首次启用=放送到免费集数

    def test_release_increment_and_up_to(self, env):
        svc, _ = env
        d = asyncio.run(svc.release_episode("drama_rr"))    # 2→3
        assert d["published_episodes"] == 3
        d = asyncio.run(svc.release_episode("drama_rr"))    # 3→4
        assert d["published_episodes"] == 4
        d = asyncio.run(svc.release_episode("drama_rr", up_to=6))  # 直达完结
        assert d["published_episodes"] == 6
        d = asyncio.run(svc.release_episode("drama_rr", up_to=1))  # 可回拨
        assert d["published_episodes"] == 1

    def test_release_bounds_and_missing(self, env):
        svc, _ = env
        with pytest.raises(ValueError):
            asyncio.run(svc.release_episode("drama_rr", up_to=7))
        with pytest.raises(KeyError):
            asyncio.run(svc.release_episode("drama_nope"))


# ============================================================
# A2. 76 号三审闸(2026-10-17 风险⑥): 未过审集不得放送
# ============================================================

class TestEpisodeReviewGate:

    @pytest.fixture()
    def pending_drama(self, env):
        """新建剧(默认全 pending, published=2 构造边更中状态——
        闸对已放送集既往不究, 只拦后续未过审集)"""
        svc, _ = env

        async def mk():
            await svc.create({**FUN6_BODY, "drama_id": "drama_gate",
                              "published_episodes": 2})
            return await svc.get("drama_gate")
        return svc, asyncio.run(mk())

    def test_new_episodes_default_pending(self, env):
        svc, _ = env
        async def mk():
            await svc.create({**FUN6_BODY, "drama_id": "drama_gate2",
                              "published_episodes": 0})
            return await svc.get("drama_gate2")
        d = asyncio.run(mk())
        assert all(e["review_status"] == "pending"
                   for e in d["episodes"])

    def test_release_blocked_without_review(self, pending_drama):
        svc, d = pending_drama
        # 已放送 2 集(存量无字段=既往有效), 第 3 集 pending → 放送被拦
        with pytest.raises(ValueError, match="未过三审"):
            asyncio.run(svc.release_episode("drama_gate"))

    def test_approve_unlocks_release(self, pending_drama):
        svc, d = pending_drama
        asyncio.run(svc.review_episode("drama_gate", 3, approved=True))
        d = asyncio.run(svc.release_episode("drama_gate"))   # 2→3
        assert d["published_episodes"] == 3
        # 第 4 集仍 pending → 继续放送被拦
        with pytest.raises(ValueError, match="未过三审"):
            asyncio.run(svc.release_episode("drama_gate"))

    def test_rejected_blocks_and_rollback_allowed(self, pending_drama):
        svc, d = pending_drama
        asyncio.run(svc.review_episode("drama_gate", 3, approved=False))
        # rejected → 拦; 回拨 up_to=1(已放送域)不受审闸影响
        with pytest.raises(ValueError, match="未过三审"):
            asyncio.run(svc.release_episode("drama_gate"))
        d = asyncio.run(svc.release_episode("drama_gate", up_to=1))
        assert d["published_episodes"] == 1

    def test_review_endpoint_admin_flow(self, client, env):
        svc, _ = env
        r = client.post("/api/v1/dramas/drama_rr/episodes/review",
                        headers={"X-Role": "admin"},
                        json={"episode_number": 7, "approved": True})
        assert r.status_code == 409          # 第 7 集不存在
        r = client.post("/api/v1/dramas/drama_rr/episodes/review",
                        headers={"X-Role": "admin"},
                        json={"episode_number": 3, "approved": True})
        assert r.status_code == 200
        assert r.json()["data"]["drama_id"] == "drama_rr"
        r = client.post("/api/v1/dramas/drama_rr/episodes/review",
                        json={"episode_number": 3, "approved": True})
        assert r.status_code == 403          # 非 admin 拒绝

    def test_public_view_keeps_total(self, env):
        svc, _ = env
        d = _get(svc, "drama_rr")
        pv = DramaSeriesService.public_view(d)
        assert pv["total_episodes"] == 6
        assert len(pv["episodes"]) == 2
        # 整剧模式原样(无 total_episodes 注入)
        d["published_episodes"] = 0
        pv = DramaSeriesService.public_view(d)
        assert "total_episodes" not in pv and len(pv["episodes"]) == 6

    def test_create_clamps_published(self, env):
        svc, _ = env
        async def run():
            return await svc.create({**FUN6_BODY,
                                     "drama_id": "drama_rr3",
                                     "published_episodes": 99})
        asyncio.run(run())
        d = _get(svc, "drama_rr3")
        assert d["published_episodes"] == 6    # clamp 到总集数

    def test_alcohol_drama_also_gated(self, env):
        svc, _ = env
        d = _get(svc, "drama_alc_rr")
        eps = svc.episode_states(d)
        # 酒剧全集免费, 但分批下未放送集同样不可见
        assert [e["episode_number"] for e in eps] == [1, 2]
        assert all(e["status"] == "free" for e in eps)


class TestRollingReleaseRoutes:

    def test_release_requires_admin(self, client):
        r = client.post("/api/v1/dramas/drama_rr/release", json={})
        assert r.status_code == 403

    def test_release_admin_flow(self, client):
        r = client.post("/api/v1/dramas/drama_rr/release", json={},
                        headers={"x-role": "admin"})
        assert r.status_code == 200
        body = r.json()["data"]
        assert body["published_episodes"] == 3
        assert body["total_episodes"] == 6 and len(body["episodes"]) == 3

    def test_detail_hides_unpublished(self, client):
        r = client.get("/api/v1/dramas/drama_rr")
        body = r.json()["data"]
        assert len(body["episodes"]) == 2 and body["total_episodes"] == 6

    def test_checkpoint_404_until_published(self, client):
        # 第 3 集是卡点集但未放送 → 404
        r = client.get("/api/v1/dramas/drama_rr/episodes/r3/checkpoint")
        assert r.status_code == 404
        client.post("/api/v1/dramas/drama_rr/release", json={},
                    headers={"x-role": "admin"})   # 放送到 3
        r = client.get("/api/v1/dramas/drama_rr/episodes/r3/checkpoint")
        assert r.status_code == 200


# ============================================================
# B. 84 号时长双档
# ============================================================

class TestLengthDualBand:

    def test_plan_1_5_9_long(self):
        longs = [i for i in range(1, 13) if episode_length_plan(i) == "long"]
        assert longs == [1, 5, 9]

    def test_normalize_clamps(self):
        s = {"episodes": [
            {"ep": 1, "duration": 60},     # 长集低于下限 → 90
            {"ep": 2, "duration": 200},    # 短集超上限 → 90
            {"ep": 2, "duration": 10},     # 微档下限内 → 拉回 30(微档)
            {"ep": 5, "duration": 0},      # 缺失 → 长档中值 120
            {"ep": 6, "duration": None},   # 脏值 → 短档中值 60
        ]}
        Drama84ScriptService._normalize_durations(s)
        assert [e["duration"] for e in s["episodes"]] == \
            [90, 90, 30, 120, 60]

    def test_micro_band_30s(self):
        # 微档(2026-10-17 对标第四篇·滑州田影): lint 下限 45→30
        def flaws_of(dur):
            ep = {"ep": 2, "duration": dur,
                  "beats": {k: "x" for k in
                            ("hook", "twist", "cliffhanger")},
                  "lines": [{"persona": "zhuxiaomei",
                             "text": "ok"}] * 4}
            return [f for f in d84.lint_series({"episodes": [ep]})
                    if "时长" in f]
        assert flaws_of(30) == []        # 30s 短集合法
        assert flaws_of(29)              # 29s 仍越界

    def test_rule_series_durations_by_plan(self):
        svc = Drama84ScriptService()
        s = svc._rule_series({"title": "热点", "platform": "douyin",
                              "score": 70}, 6, DEFAULT_CAST, "")
        durs = [e["duration"] for e in s["episodes"]]
        assert durs == [120, 70, 70, 70, 120, 70]

    def test_pacing_dual_band_scoring(self, tmp_path, monkeypatch):
        monkeypatch.setattr(d84, "SERIES_DIR", tmp_path)
        (tmp_path / "d84_pacing.json").write_text(json.dumps({
            "seriesId": "d84_pacing", "title": "档位剧", "track": "rule",
            "cast": [{"persona": "zhuxiaomei"},
                     {"persona": "xiaozhushuaige"}],
            "hotspot": {"score": 80},
            "episodes": [
                {"ep": 1, "duration": 150},   # 长集达标(90-180)
                {"ep": 2, "duration": 130},   # 短集超 120 → 不达标
                {"ep": 2, "duration": 30},    # 微档 30s → 达标
                {"ep": 3, "duration": 29},    # 低于微档下限 → 不达标
                {"ep": 5, "duration": 150},   # 长集达标
            ]}, ensure_ascii=False), "utf-8")
        snap = asyncio.run(Drama84ScriptService().score_series(
            "d84_pacing", unlocks=0, online_days=1.0))
        pacing = next(f for f in snap["factors"]
                      if f["name"] == "pacing")
        assert pacing["score"] == 60.0       # 3/5 达标
        assert "短集30-120s" in pacing["detail"]


# ============================================================
# C. 84 号登记: 复读机标题 + AI 标签
# ============================================================

@pytest.fixture()
def d84_env(tmp_path, monkeypatch):
    monkeypatch.setattr(d84, "SERIES_DIR", tmp_path)
    svc = Drama84ScriptService()
    series = svc._rule_series({"title": "囤货", "platform": "douyin",
                               "score": 80}, 6, DEFAULT_CAST, "")
    series["seriesId"] = "d84_rr"
    (tmp_path / "d84_rr.json").write_text(
        json.dumps(series, ensure_ascii=False), "utf-8")
    return svc, series, PromoRepository(store={})


class TestRegisterEpisodeTitle:

    def test_title_hook_prefix_and_golden_finger(self, d84_env):
        svc, series, repo = d84_env
        content = asyncio.run(svc.register_episode(
            "d84_rr", 1, "https://v.zyjiu.com/t1.mp4", 120))
        # 单集标题独立爽文钩(对标小乔剧院): hook 首四字+! 前置
        # (rule 轨 ep1 hook="第1集: 囤货风波又起" → "囤货风波!")
        assert content["title"].startswith("囤货风波!")
        assert "竹香酒馆·囤货·第1集" in content["title"]
        assert series["goldenFinger"] in content["title"]
        assert " | " in content["title"]

    def test_title_fallback_without_gf(self, d84_env, tmp_path):
        svc, series, _ = d84_env
        series["goldenFinger"] = ""
        (tmp_path / "d84_rr.json").write_text(
            json.dumps(series, ensure_ascii=False), "utf-8")
        content = asyncio.run(svc.register_episode(
            "d84_rr", 2, "https://v.zyjiu.com/t2.mp4", 70))
        assert " | " not in content["title"]
        assert content["title"].endswith("转机")

    def test_hashtags_matrix(self, d84_env):
        svc, _, _ = d84_env
        content = asyncio.run(svc.register_episode(
            "d84_rr", 3, "https://v.zyjiu.com/t3.mp4", 70))
        tags = content["hashtags"].split()
        # 标签矩阵(对标滑州田影): #剧名三变体 + 两泛垂类
        assert "#竹香酒馆·囤货" in tags
        assert "#竹香酒馆·囤货短剧" in tags
        assert "#竹香酒馆·囤货后续" in tags
        assert "#独白短剧" in tags and "#AI短剧" in tags
        assert len(tags) == 5

    def test_register_idempotent(self, d84_env):
        svc, _, _ = d84_env
        c1 = asyncio.run(svc.register_episode(
            "d84_rr", 4, "https://v.zyjiu.com/t4.mp4", 70))
        c2 = asyncio.run(svc.register_episode(
            "d84_rr", 4, "https://v.zyjiu.com/t4.mp4", 70))
        assert c1["contentId"] == c2["contentId"]


# ============================================================
# D. 76 号卡点弹性(free_episodes 动态调整, 对标点众免费窗)
# ============================================================

class TestFreeEpisodesFlex:

    def test_release_moves_checkpoint(self, env):
        svc, _ = env
        # 张力峰在第 4 集 → 免费窗 2→3 + 放送到 4: 卡点=第4集
        d = asyncio.run(svc.release_episode(
            "drama_rr", up_to=4, free_episodes=3))
        assert d["free_episodes"] == 3
        assert d["published_episodes"] == 4
        eps = svc.episode_states(d)
        cp = next(e for e in eps if e["is_checkpoint"])
        assert cp["episode_number"] == 4
        assert cp["status"] == "locked" and not cp["is_unlocked"]

    def test_free_only_no_extra_release(self, env):
        svc, _ = env
        # 只调卡点不额外放送: up_to=当前 published
        d = asyncio.run(svc.release_episode(
            "drama_rr", up_to=2, free_episodes=1))
        assert d["free_episodes"] == 1
        assert d["published_episodes"] == 2

    def test_free_bounds(self, env):
        svc, _ = env
        with pytest.raises(ValueError):   # 0: 无免费集
            asyncio.run(svc.release_episode(
                "drama_rr", up_to=2, free_episodes=0))
        with pytest.raises(ValueError):   # =total: 无卡点集
            asyncio.run(svc.release_episode(
                "drama_rr", up_to=2, free_episodes=6))

    def test_first_release_uses_new_free(self, env):
        svc, _ = env
        async def run():
            await svc.create({**FUN6_BODY, "drama_id": "drama_rr4",
                              "published_episodes": 0})
            # 首发放送的 1-4 集先过审(三审闸)
            for n in range(1, 5):
                await svc.review_episode("drama_rr4", n, approved=True)
            return await svc.release_episode(
                "drama_rr4", free_episodes=4)
        d = asyncio.run(run())
        # 首次启用: 放送到新免费集数(4)而非旧值(2)
        assert d["free_episodes"] == 4
        assert d["published_episodes"] == 4

    def test_release_route_with_free(self, client):
        r = client.post("/api/v1/dramas/drama_rr/release",
                        json={"up_to": 3, "free_episodes": 3},
                        headers={"x-role": "admin"})
        assert r.status_code == 200
        body = r.json()["data"]
        assert body["free_episodes"] == 3
        assert body["published_episodes"] == 3

    def test_release_route_free_invalid(self, client):
        r = client.post("/api/v1/dramas/drama_rr/release",
                        json={"up_to": 3, "free_episodes": 6},
                        headers={"x-role": "admin"})
        assert r.status_code == 409
