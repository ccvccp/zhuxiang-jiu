"""76号·酒剧免注册专项测试(2026-10-09 规则定案)

规则: 剧名/简介/标签任一含「酒」=品牌向酒剧 → 全集免注册无障碍观看
      (free/unlocked/无卡点/地址暴露); 其他娱乐剧维持 free+注册解锁。

覆盖五域:
    A. is_alcohol_drama 判定矩阵(正/反/边界)
    B. episode_states 视图(游客/会员/已解锁 × 酒剧/娱乐剧)
    C. HTTP 路由(公开 episodes/checkpoint 409 防护/娱乐剧 200 不误伤/404)
    D. unlock 交互(酒剧解锁无害/娱乐剧解锁回归/幂等)
    E. 前端契约(episode_states 键完整性 + list_online 混排)

运行: cd backend && python -m pytest test_drama76_alcohol_free.py -q
"""

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from repositories.promo_repository import PromoRepository
from routes.drama76_routes import register_drama76_routes
from services.drama76_series_service import DramaSeriesService


# ============================================================
# fixture: 双剧环境(酒剧4集 + 娱乐剧3集, free_episodes=2)
# ============================================================

ALC_BODY = {
    "drama_id": "drama_alc", "title": "竹香酒馆",
    "description": "非遗热点品牌短剧", "tags": ["品牌"],
    "free_episodes": 2, "status": "online",
    "episodes": [
        {"episode_id": "a1", "title": "风波", "video_url": "/v/a1.mp4",
         "duration_seconds": 18},
        {"episode_id": "a2", "title": "转机", "video_url": "/v/a2.mp4",
         "duration_seconds": 20},
        {"episode_id": "a3", "title": "考验", "video_url": "/v/a3.mp4",
         "duration_seconds": 19},
        {"episode_id": "a4", "title": "圆梦", "video_url": "/v/a4.mp4",
         "duration_seconds": 21}],
}
FUN_BODY = {
    "drama_id": "drama_fun", "title": "逆袭之王",
    "description": "都市娱乐爽剧", "tags": ["都市", "逆袭"],
    "free_episodes": 2, "status": "online",
    "episodes": [
        {"episode_id": "f1", "title": "开局", "video_url": "/v/f1.mp4"},
        {"episode_id": "f2", "title": "铺垫", "video_url": "/v/f2.mp4"},
        {"episode_id": "f3", "title": "翻盘", "video_url": "/v/f3.mp4"}],
}


@pytest.fixture()
def env():
    """双剧内存环境: (svc, shared_repo)
    独立 store 注入(不落全局 get_in_memory_store)——零污染
    同进程其他套件(T34 阶梯发奖依赖干净 drama76_series 表)"""
    shared = PromoRepository(store={})
    svc = DramaSeriesService(repo=shared)

    async def seed():
        await svc.create(ALC_BODY)
        await svc.create(FUN_BODY)

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
# A. 判定矩阵
# ============================================================

class TestIsAlcoholDrama:

    def test_title_contains_jiu(self):
        assert DramaSeriesService.is_alcohol_drama(
            {"title": "竹香酒馆"}) is True

    def test_title_brand_series(self):
        assert DramaSeriesService.is_alcohol_drama(
            {"title": "竹香酒·逆袭独白"}) is True

    def test_description_contains(self):
        assert DramaSeriesService.is_alcohol_drama(
            {"title": "酒馆风云", "description": "一家老酒馆的守望"}) is True

    def test_description_only_contains(self):
        assert DramaSeriesService.is_alcohol_drama(
            {"title": "匠心", "description": "讲述白酒酿造工艺"}) is True

    def test_tags_contains(self):
        assert DramaSeriesService.is_alcohol_drama(
            {"title": "品鉴之旅", "tags": ["白酒品鉴"]}) is True

    def test_pure_entertainment_negative(self):
        assert DramaSeriesService.is_alcohol_drama(
            {"title": "逆袭之王", "description": "都市娱乐爽剧",
             "tags": ["都市"]}) is False

    def test_empty_drama_safe(self):
        assert DramaSeriesService.is_alcohol_drama({}) is False

    def test_none_fields_safe(self):
        assert DramaSeriesService.is_alcohol_drama(
            {"title": None, "description": None, "tags": None}) is False

    def test_episode_body_not_scanned(self):
        """口径: 不扫剧集正文——台词/分集标题含酒不触发(防误伤)"""
        assert DramaSeriesService.is_alcohol_drama(
            {"title": "逆袭之王",
             "episodes": [{"title": "一杯酒的约定",
                           "cliff_note": "举杯畅饮白酒"}]}) is False

    def test_partial_char_not_jiu(self):
        """「洒/酉」等形近字不误判"""
        assert DramaSeriesService.is_alcohol_drama(
            {"title": "潇洒先生"}) is False


# ============================================================
# B. episode_states 视图
# ============================================================

class TestEpisodeStates:

    def test_alcohol_guest_all_free(self, env):
        svc, _ = env
        eps = svc.episode_states(_get(svc, "drama_alc"))
        assert len(eps) == 4
        assert all(e["status"] == "free" for e in eps)
        assert all(e["is_unlocked"] is True for e in eps)
        assert all(e["is_checkpoint"] is False for e in eps)
        assert all(e["unlock_methods"] == [] for e in eps)
        # 地址全暴露(免登录可看的核心)
        assert [e["video_url"] for e in eps] == [
            "/v/a1.mp4", "/v/a2.mp4", "/v/a3.mp4", "/v/a4.mp4"]

    def test_alcohol_member_consistent(self, env):
        """会员视角与游客一致(全 free, 不出现 member_only)"""
        svc, _ = env
        eps = svc.episode_states(_get(svc, "drama_alc"),
                                 is_member=True)
        assert all(e["status"] == "free" and e["is_unlocked"]
                   for e in eps)

    def test_alcohol_unlocked_user_consistent(self, env):
        svc, _ = env
        eps = svc.episode_states(_get(svc, "drama_alc"),
                                 unlocked_dramas={"drama_alc"})
        assert all(e["status"] == "free" for e in eps)

    def test_entertainment_guest_unchanged(self, env):
        """娱乐剧游客: 免费2+卡点锁定(原逻辑零影响)"""
        svc, _ = env
        eps = svc.episode_states(_get(svc, "drama_fun"))
        assert [e["status"] for e in eps] == [
            "free", "free", "locked"]
        assert eps[2]["is_checkpoint"] is True
        assert eps[2]["video_url"] == ""          # 锁集不暴露地址
        assert eps[2]["unlock_methods"] == [
            "register", "invite", "member"]

    def test_entertainment_member_unchanged(self, env):
        svc, _ = env
        eps = svc.episode_states(_get(svc, "drama_fun"),
                                 is_member=True)
        assert all(e["is_unlocked"] for e in eps)

    def test_entertainment_unlock_flow_unchanged(self, env):
        svc, _ = env
        eps = svc.episode_states(_get(svc, "drama_fun"),
                                 unlocked_dramas={"drama_fun"})
        assert all(e["is_unlocked"] for e in eps)

    def test_alcohol_free_episodes_field_ignored(self, env):
        """酒剧判定优先于 free_episodes 字段(=2 仍全集 free)"""
        svc, _ = env
        d = _get(svc, "drama_alc")
        assert d["free_episodes"] == 2
        eps = svc.episode_states(d)
        assert len([e for e in eps if e["status"] == "locked"]) == 0

    def test_frontend_contract_keys(self, env):
        """前端 drama.html 契约: 每集键完整性(缺键=前端渲染崩)"""
        svc, _ = env
        for drama_id in ("drama_alc", "drama_fun"):
            eps = svc.episode_states(_get(svc, drama_id))
            for e in eps:
                assert {"episode_id", "episode_number", "title",
                        "video_url", "duration_seconds", "status",
                        "is_unlocked", "is_checkpoint",
                        "unlock_methods"} <= set(e)


# ============================================================
# C. HTTP 路由
# ============================================================

class TestRoutes:

    def test_public_episodes_alcohol_all_unlocked(self, client):
        r = client.get("/api/v1/dramas/drama_alc/episodes")
        assert r.status_code == 200
        eps = r.json()["data"]["episodes"]
        assert len(eps) == 4
        assert all(e["is_unlocked"] for e in eps)
        assert all(e["video_url"] for e in eps)

    def test_public_episodes_entertainment_locked_tail(self, client):
        r = client.get("/api/v1/dramas/drama_fun/episodes")
        eps = r.json()["data"]["episodes"]
        assert [e["is_unlocked"] for e in eps] == [
            True, True, False]

    def test_checkpoint_alcohol_409(self, client):
        r = client.get(
            "/api/v1/dramas/drama_alc/episodes/a3/checkpoint")
        assert r.status_code == 409
        assert "酒类剧" in r.json()["detail"]

    def test_checkpoint_alcohol_last_episode_409(self, client):
        r = client.get(
            "/api/v1/dramas/drama_alc/episodes/a4/checkpoint")
        assert r.status_code == 409

    def test_checkpoint_entertainment_locked_200_not_hurt(
            self, client):
        """防护不误伤: 娱乐剧卡点集 checkpoint 仍 200 配置"""
        r = client.get(
            "/api/v1/dramas/drama_fun/episodes/f3/checkpoint")
        assert r.status_code == 200
        body = r.json()["data"]
        assert [m["type"] for m in body["unlock_methods"]] == [
            "register", "invite", "member"]

    def test_checkpoint_entertainment_free_ep_409(self, client):
        r = client.get(
            "/api/v1/dramas/drama_fun/episodes/f1/checkpoint")
        assert r.status_code == 409
        assert r.json()["detail"] == "该集免费无卡点"

    def test_checkpoint_drama_404(self, client):
        r = client.get(
            "/api/v1/dramas/drama_none/episodes/x/checkpoint")
        assert r.status_code == 404

    def test_checkpoint_episode_404(self, client):
        r = client.get(
            "/api/v1/dramas/drama_alc/episodes/nope/checkpoint")
        assert r.status_code == 404

    def test_list_online_mixed(self, client):
        """混排列表: 两剧均在架(公开发现面不受判定影响)"""
        r = client.get("/api/v1/dramas")
        data = r.json()["data"]
        rows = data["dramas"] if isinstance(data, dict) else data
        ids = [d["drama_id"] for d in rows]
        assert {"drama_alc", "drama_fun"} <= set(ids)


# ============================================================
# D. unlock 交互(口径锁定)
# ============================================================

class TestUnlockInteraction:

    def test_unlock_alcohol_harmless_idempotent(self, env):
        """口径: 酒剧 unlock API 不拒绝(视图已全 free, 记录无害)
        且幂等——老用户带 token 直调不报错"""
        svc, _ = env
        drama, rec = asyncio.run(
            svc.unlock("u_alc", "a3", method="register"))
        assert drama["drama_id"] == "drama_alc"
        assert rec["unlock_id"] == "ul_u_alc_drama_alc"
        _, rec2 = asyncio.run(svc.unlock("u_alc", "a3"))
        assert rec2["unlock_id"] == rec["unlock_id"]  # 幂等

    def test_unlock_free_episode_409_semantics(self, env):
        svc, _ = env
        with pytest.raises(ValueError):       # a1<=free_n → 该集免费
            asyncio.run(svc.unlock("u_x", "a1"))

    def test_unlock_entertainment_regression(self, env):
        svc, _ = env
        drama, rec = asyncio.run(
            svc.unlock("u_fun", "f3", method="invite", src="rLINK"))
        assert drama["drama_id"] == "drama_fun"
        assert rec["method"] == "invite"
        # 解锁后全集可看
        eps = svc.episode_states(
            _get(svc, "drama_fun"),
            unlocked_dramas={"drama_fun"})
        assert all(e["is_unlocked"] for e in eps)

    def test_unlocked_dramas_of_alcohol_not_required(self, env):
        """游客无解锁记录也全 free——解锁表非酒剧观看前置"""
        svc, _ = env
        unlocked = asyncio.run(svc.unlocked_dramas_of("stranger"))
        assert unlocked == set()
        eps = svc.episode_states(_get(svc, "drama_alc"))
        assert all(e["is_unlocked"] for e in eps)


# ============================================================
# E. 生产剧本回归(真实剧名判定)
# ============================================================

class TestProductionNaming:

    @pytest.mark.parametrize("title,expect", [
        ("竹香酒馆", True),                     # drama_zhuxiang_02
        ("竹香酒·逆袭独白", True),               # drama_zhuxiang_01
        ("非遗奇遇记", False),                  # 假想娱乐剧
        ("厨神争霸", False),
    ])
    def test_production_titles(self, title, expect):
        assert DramaSeriesService.is_alcohol_drama(
            {"title": title}) is expect

    def test_drama76_build_created_alcohol_auto_free(self, env):
        """组剧链(drama76_build)创建的酒剧自动落入免登录
        (无需手工标记——判定在读取侧, 建剧侧零感知)"""
        svc, _ = env
        body = dict(ALC_BODY, drama_id="drama_build_99")
        asyncio.run(svc.create(body))
        eps = svc.episode_states(_get(svc, "drama_build_99"))
        assert all(e["is_unlocked"] for e in eps)
