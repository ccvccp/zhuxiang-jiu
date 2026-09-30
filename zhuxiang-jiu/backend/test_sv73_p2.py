"""73号(sv)·短视频智能模型 P2 专项——数据闭环(44号池)

覆盖:
    R1 评分器入册: 第 53 档案/权重和为 1/阈值三级/可学习
    R2 风格因子: 六因子确定性派生(模板/轨/合规/匹配/时长/人设)
    R3 回流: published+clicks → submit_feedback 落 44号池
       (factors 契约/learningFed 幂等/未发布拒/非 sv73 拒)
    R4 学习链: ≥10 条 pending → run_learning_cycle → 清零
    R5 路由: feedback 决策面门控 + status 观测面常开

运行: python -m pytest test_sv73_p2.py -q
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

sys.path.insert(0, str(Path(__file__).resolve().parent))

import services.sv73_script_service as sv73mod
import services.sv73_render_service as sv73ren
from services.sv73_script_service import (
    Sv73ScriptService, TRACK_RULE,
)
from services.sv73_pipeline_service import Sv73PipelineService
from services.sv73_p2_service import Sv73P2Service
from services.promo_service import PromoService

HOTSPOT = {
    "fingerprint": "fp-sv73-p2-0001",
    "platform": "douyin",
    "title": "重阳敬老酒礼推荐",
    "score": 80,
}


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(sv73mod, "_STORYBOARD_DIR", tmp_path / "sb")
    monkeypatch.setattr(sv73ren, "SV73_VIDEO_DIR", tmp_path / "vd")
    monkeypatch.setattr(
        Sv73ScriptService, "_chat_json",
        lambda self, s, u: (None, TRACK_RULE))
    monkeypatch.setenv("SV73_MODE", "real")
    monkeypatch.setenv("SV73_TTS_MODE", "off")
    monkeypatch.setenv("SV73_RENDER_MODE", "off")
    yield


def _make_published(fp=None):
    """造一条已发布 sv73 content(真实链全跑——同步壳)"""
    r = _run(Sv73PipelineService().run(
        {**HOTSPOT, "fingerprint": fp or "fp-sv73-p2-0001"}))
    cid = r["content"]["contentId"]
    promo = PromoService()
    _run(promo.review_content(cid, approved=True))
    _run(promo.publish_content(
        cid, publish_at="2020-01-01T00:00:00+00:00"))
    published = _run(promo.process_publish_queue())
    hit = next(c for c in published if c["contentId"] == cid)
    assert hit["status"] == "published"
    return cid


# ------------------------------------------------------------
# R1 评分器入册(44号第 53 档案)
# ------------------------------------------------------------


def test_r1_registry():
    from services.ai_learning_service import (
        SCORER_REGISTRY, DECISION_THRESHOLDS,
        default_weights, is_learnable,
    )
    meta = SCORER_REGISTRY.get("sv73_storyboard")
    assert meta is not None
    assert meta["batch"] == 53
    assert "73" in meta["module"]
    assert DECISION_THRESHOLDS["sv73_storyboard"] == [
        (60.0, "high"), (30.0, "medium"), (0.0, "low")]
    w = default_weights("sv73_storyboard")
    assert len(w) == 6
    assert abs(sum(w.values()) - 1.0) < 1e-6
    assert is_learnable("sv73_storyboard") is True


# ------------------------------------------------------------
# R2 风格因子(确定性派生)
# ------------------------------------------------------------


def test_r2_factors():
    from services.sv73_scorer import (
        factors_from_content, duration_score,
        Sv73StoryboardScorer,
    )
    assert duration_score(18.0) == 100
    assert duration_score(15.6) == 81
    factors = factors_from_content({
        "template": "fast", "track": "glm-4-flash",
        "complianceScore": 70, "matchFallback": False,
        "totalDuration": 15.6})
    names = [f["name"] for f in factors]
    assert names == ["template_fit", "track_quality",
                     "compliance_health", "category_match",
                     "duration_fit", "persona_fit"]
    by = {f["name"]: f["score"] for f in factors}
    assert by["template_fit"] == 78
    assert by["track_quality"] == 75
    assert by["compliance_health"] == 70
    assert by["category_match"] == 80
    scorer = Sv73StoryboardScorer()
    with pytest.raises(ValueError):
        _run(scorer.score({}))
    r = _run(scorer.score({
        "template": "fast", "track": "glm-4-flash",
        "complianceScore": 70, "matchFallback": False,
        "totalDuration": 15.6}))
    assert r["success"] and r["scorer"] == "sv73_storyboard"
    assert r["score"] > 0 and r["level"] in (
        "high", "medium", "low")


# ------------------------------------------------------------
# R3 回流(44号池契约)
# ------------------------------------------------------------


def test_r3_feedback():
    from repositories.ai_learning_repository import (
        AiLearningRepository,
    )
    cid = _make_published()

    svc = Sv73P2Service()
    r = _run(svc.submit_learning_feedback(cid, clicks=3))
    assert r["success"] is True
    assert r["correct"] is True
    assert r["feedbackId"] > 0
    f = r["factors"][0]
    assert set(f.keys()) >= {"name", "score", "weight",
                             "contribution"}
    rows = _run(AiLearningRepository().list_feedback(
        "sv73_storyboard"))
    assert any(row.get("feedbackId") == r["feedbackId"]
               for row in rows)

    # learningFed 幂等闸门
    with pytest.raises(ValueError, match="幂等"):
        _run(svc.submit_learning_feedback(cid, clicks=5))

    # 未发布拒
    r2 = _run(Sv73PipelineService().run(
        {**HOTSPOT, "fingerprint": "fp-sv73-p2-pending"}))
    cid2 = r2["content"]["contentId"]
    with pytest.raises(ValueError, match="未发布"):
        _run(svc.submit_learning_feedback(cid2, clicks=1))

    # 非 sv73 content 拒
    from repositories.promo_repository import PromoRepository
    repo = PromoRepository()
    plain_id = _run(repo.next_id("content"))
    _run(repo.save_content({
        "contentId": plain_id, "platform": "douyin",
        "title": "常规图文", "body": "x", "status": "published",
        "receipt": {"mode": "mock"}, "shortCode": "",
        "createdAt": "2026-01-01T00:00:00+00:00",
    }))
    with pytest.raises(ValueError, match="非 73号"):
        _run(svc.submit_learning_feedback(plain_id, clicks=1))


# ------------------------------------------------------------
# R4 学习链(Hedge 演化)
# ------------------------------------------------------------


def test_r4_learning_cycle():
    for i in range(10):
        cid = _make_published(fp=f"fp-sv73-p2-learn-{i}")
        _run(Sv73P2Service().submit_learning_feedback(
            cid, clicks=i + 1))

    status = _run(Sv73P2Service().learning_status())
    assert status["pendingFeedback"] >= 10
    assert status["learnable"] is True

    from services.ai_learning_service import run_learning_cycle
    result = _run(run_learning_cycle("sv73_storyboard"))
    assert result.get("success") is True

    status2 = _run(Sv73P2Service().learning_status())
    assert status2["pendingFeedback"] == 0


# ------------------------------------------------------------
# R5 路由门控
# ------------------------------------------------------------


def test_r5_routes(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.sv73_routes import register_sv73_routes

    app = FastAPI()
    register_sv73_routes(app)
    client = TestClient(app)
    admin = {"X-Role": "admin"}

    monkeypatch.setenv("SV73_MODE", "off")
    r = client.get("/api/sv73/learning/status",
                    headers=admin)
    assert r.status_code == 200
    assert r.json()["data"]["scorerId"] == "sv73_storyboard"

    r = client.post("/api/sv73/learning/feedback", headers=admin,
                    json={"contentId": 999})
    assert r.status_code == 409

    monkeypatch.setenv("SV73_MODE", "real")
    r = client.post("/api/sv73/learning/feedback", headers=admin,
                    json={"contentId": 999999})
    assert r.status_code == 404
