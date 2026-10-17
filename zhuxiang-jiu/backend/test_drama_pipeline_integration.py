"""76/84 号·剧本-登记-组剧-放送-解锁 全链路集成测试(2026-10-17)

区别于单元测试(test_drama_rolling_release): 跨服务跨层走 HTTP 真实链路,
模拟运营方完整动作序列:

    链路1 剧本到上映: 84号生成(rule 轨) → register_episode×6 落 36号
         content 表 → 76号 admin 组剧(published=free) → 游客三面视图
    链路2 边更边锁节奏: checkpoint 未放送 404 → release 放送 → 200
         → 注册解锁(剧级) → 继续放送集直接可见
    链路3 内容引用一致性: 76 组剧 episode_id 与 36 号 contentId 闭环

运行: cd backend && python -m pytest test_drama_pipeline_integration.py -q
"""

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import services.drama84_script_service as d84
from repositories.promo_repository import PromoRepository
from routes.drama76_routes import register_drama76_routes
from services.drama84_script_service import (
    DEFAULT_CAST, Drama84ScriptService)

ADMIN = {"x-role": "admin"}
USER_U1 = {"x-member-id": "u_pipeline_1"}


@pytest.fixture()
def pipeline(tmp_path, monkeypatch):
    """全链路环境: 84 落盘目录+76 双路由+共享内存 repo"""
    monkeypatch.setattr(d84, "SERIES_DIR", tmp_path)
    shared = PromoRepository(store={})

    # 84 号: rule 轨生成 6 集连续剧并落盘(不走 LLM, 确定性)
    d84svc = Drama84ScriptService()
    series = d84svc._rule_series(
        {"title": "囤货风暴", "platform": "douyin", "score": 85},
        6, DEFAULT_CAST, "")
    series["seriesId"] = "d84_pipe"
    (tmp_path / "d84_pipe.json").write_text(
        json.dumps(series, ensure_ascii=False), "utf-8")

    # 36 号: 6 集全部登记 content(pending 三审态)
    contents = [asyncio.run(d84svc.register_episode(
        "d84_pipe", n, f"https://v.zyjiu.com/pipe_ep{n}.mp4",
        120 if n in (1, 5) else 70)) for n in range(1, 7)]

    # 76 号: admin 用 contentId 组剧(边更边锁: published=free=2)
    monkeypatch.setattr(
        "routes.drama76_routes._svc",
        __import__("services.drama76_series_service",
                   fromlist=["DramaSeriesService"]).DramaSeriesService(
            repo=shared))
    monkeypatch.setattr(
        "routes.drama76_routes._growth",
        __import__("services.drama76_growth_service",
                   fromlist=["DramaGrowthService"]).DramaGrowthService(
            repo=shared))
    app = FastAPI()
    register_drama76_routes(app)
    client = TestClient(app)

    r = client.post("/api/v1/dramas", headers=ADMIN, json={
        "drama_id": "drama_pipe", "title": "囤货风暴·首富之路",
        "description": "年代逆袭爽剧", "tags": ["逆袭"],
        "free_episodes": 2, "published_episodes": 2,
        "episodes": [{
            "episode_id": str(c["contentId"]),
            "title": f"第{i}集",
            "video_url": c["sv73"]["video"],
            "duration_seconds": int(c["sv73"]["totalDuration"]),
        } for i, c in enumerate(contents, 1)]})
    assert r.status_code == 200, r.text
    return {"client": client, "contents": contents, "series": series,
            "repo": shared}


class TestScriptToScreen:
    """链路1: 剧本到上映"""

    def test_six_contents_registered_pending(self, pipeline):
        cs = pipeline["contents"]
        assert len(cs) == 6
        assert all(c["status"] == "pending" for c in cs)
        # 复读机+AI 标签贯穿 6 条 content
        assert all("#AI短剧" in c["hashtags"] for c in cs)
        assert all(" | " in c["title"] for c in cs)

    def test_guest_views_only_published(self, pipeline):
        client = pipeline["client"]
        detail = client.get("/api/v1/dramas/drama_pipe").json()["data"]
        assert detail["total_episodes"] == 6
        assert len(detail["episodes"]) == 2
        eps = client.get("/api/v1/dramas/drama_pipe/episodes").json()["data"]["episodes"]
        assert [e["episode_number"] for e in eps] == [1, 2]
        assert all(e["status"] == "free" for e in eps)
        # 卡点集(3)未放送 → 无卡点可见
        assert not any(e["is_checkpoint"] for e in eps)

    def test_unpublished_episode_video_hidden(self, pipeline):
        client = pipeline["client"]
        detail = client.get("/api/v1/dramas/drama_pipe").json()["data"]
        # 未放送集的 video_url 不出现在任何对外视图
        hidden = {str(c["contentId"]) for c in pipeline["contents"][2:]}
        shown = {e["episode_id"] for e in detail["episodes"]}
        assert shown.isdisjoint(hidden)


class TestRollingRhythm:
    """链路2: 边更边锁节奏×解锁交互"""

    def test_checkpoint_lifecycle(self, pipeline):
        client = pipeline["client"]
        ep3 = str(pipeline["contents"][2]["contentId"])
        # 未放送 → 404(不暴露)
        assert client.get(
            f"/api/v1/dramas/drama_pipe/episodes/{ep3}/checkpoint"
        ).status_code == 404
        # 放送到 3 → 卡点页 200 且 register 优先
        client.post("/api/v1/dramas/drama_pipe/release",
                    headers=ADMIN, json={})
        r = client.get(
            f"/api/v1/dramas/drama_pipe/episodes/{ep3}/checkpoint")
        assert r.status_code == 200
        assert r.json()["data"]["unlock_methods"][0]["type"] == "register"

    def test_unlock_then_more_release_visible(self, pipeline):
        client = pipeline["client"]
        ep3 = str(pipeline["contents"][2]["contentId"])
        client.post("/api/v1/dramas/drama_pipe/release",
                    headers=ADMIN, json={})          # published=3
        # 注册解锁(剧级): 一次解锁后段
        r = client.post("/api/v1/unlocks", headers=USER_U1,
                        json={"episode_id": ep3, "method": "register"})
        assert r.status_code == 200
        # 再放送 2 集(published=5): 新放送集对已解锁用户直接可见
        client.post("/api/v1/dramas/drama_pipe/release",
                    headers=ADMIN, json={})
        client.post("/api/v1/dramas/drama_pipe/release",
                    headers=ADMIN, json={})
        me = client.get("/api/v1/dramas/drama_pipe/episodes/me",
                        headers=USER_U1).json()["data"]
        assert [e["episode_number"] for e in me["episodes"]] == [1, 2, 3, 4, 5]
        assert all(e["is_unlocked"] for e in me["episodes"])

    def test_other_user_still_locked_at_same_checkpoint(self, pipeline):
        client = pipeline["client"]
        other = {"x-member-id": "u_pipeline_2"}
        # fixture 为函数级: 自足放送到 5
        client.post("/api/v1/dramas/drama_pipe/release",
                    headers=ADMIN, json={"up_to": 5})
        me = client.get("/api/v1/dramas/drama_pipe/episodes/me",
                        headers=other).json()["data"]["episodes"]
        # 未解锁用户: 卡点恒在 free+1=3(不随放送移动), 4-5 不可见
        assert [e["episode_number"] for e in me] == [1, 2, 3, 4, 5]
        locked = [e for e in me if not e["is_unlocked"]]
        assert [e["episode_number"] for e in locked] == [3, 4, 5]
        assert locked[0]["is_checkpoint"]


class TestContentReferenceIntegrity:
    """链路3: 36 号 content ↔ 76 号 episode 引用闭环"""

    def test_episode_ids_map_to_contents(self, pipeline):
        client = pipeline["client"]
        detail = client.get("/api/v1/dramas/drama_pipe").json()["data"]
        # 完结放送后全集可见且引用闭合
        client.post("/api/v1/dramas/drama_pipe/release",
                    headers=ADMIN, json={"up_to": 6})
        full = client.get("/api/v1/dramas/drama_pipe").json()["data"]
        content_ids = {str(c["contentId"]) for c in pipeline["contents"]}
        assert {e["episode_id"] for e in full["episodes"]} == content_ids
        assert detail["total_episodes"] == 6

    def test_video_urls_flow_from_contents(self, pipeline):
        client = pipeline["client"]
        client.post("/api/v1/dramas/drama_pipe/release",
                    headers=ADMIN, json={"up_to": 6})
        full = client.get("/api/v1/dramas/drama_pipe").json()["data"]
        by_url = {c["sv73"]["video"] for c in pipeline["contents"]}
        assert {e["video_url"] for e in full["episodes"]} == by_url
