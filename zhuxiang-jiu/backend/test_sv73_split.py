"""73号(sv)·短视频智能模型 分段语义专项——渲染步开关+产物挂载

覆盖(生产灰度实证后的方案 a 修正):
    S1 分段: render off(默认)跳渲染——run 只"剧本+登记",
       render.skipped + content.sv73.video 空 + 幂等
    S2 显式 render on: 全链(实机 ffmpeg mp4 产出)
    S3 attach 挂载: 开发机产物元数据回填 content.sv73
       (幂等覆盖/未知 scriptId 404/空 video 409)
    S4 路由: pipeline render_mode 透传 + attach 端点门控

运行: python -m pytest test_sv73_split.py -q
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
from services.sv73_pipeline_service import (
    Sv73PipelineService, render_mode_enabled,
)

HOTSPOT = {
    "fingerprint": "fp-sv73-split-0001",
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


# ------------------------------------------------------------
# S1 分段: 渲染跳过(默认)
# ------------------------------------------------------------


def test_s1_split_render_skipped():
    assert render_mode_enabled() is False      # env off
    assert render_mode_enabled("on") is True   # 显式覆盖
    assert render_mode_enabled("off") is False

    result = _run(Sv73PipelineService().run(dict(HOTSPOT)))
    r = result["render"]
    assert r["skipped"] is True
    assert r["video"] == "" and r["pages"] == []
    assert "attach" in r["note"]
    # content 正常登记(分段不阻断链路前半)
    sv = result["content"]["sv73"]
    assert sv["storyboardId"] == result["storyboard"]["scriptId"]
    assert sv["video"] == ""
    assert result["content"]["status"] == "pending"

    # 幂等: 二跑复用(render 仍 skipped)
    again = _run(Sv73PipelineService().run(dict(HOTSPOT)))
    assert again["reused"] is True
    assert again["render"]["skipped"] is True


# ------------------------------------------------------------
# S2 显式 render on: 全链实机
# ------------------------------------------------------------


def test_s2_explicit_render_full():
    result = _run(Sv73PipelineService().run(
        dict(HOTSPOT), render_mode="on"))
    r = result["render"]
    assert r.get("skipped") is not True
    assert Path(r["video"]).exists()
    assert len(r["pages"]) == 4
    assert r["durationSeconds"] == 16.2


# ------------------------------------------------------------
# S3 attach 挂载(开发机产物元数据)
# ------------------------------------------------------------


def test_s3_attach_render():
    pipe = Sv73PipelineService()
    result = _run(pipe.run(
        {**HOTSPOT, "fingerprint": "fp-sv73-split-attach"}))
    sid = result["storyboard"]["scriptId"]
    cid = result["content"]["contentId"]

    # 挂载开发机路径(元数据)
    att = _run(pipe.attach_render(
        sid, "D:/sv73_videos/%s.mp4" % sid,
        pages=["D:/sv73_videos/%s_p1.png" % sid],
        size_bytes=123456))
    assert att["success"] is True
    assert att["contentId"] == cid
    sv = att["sv73"]
    assert sv["video"].endswith(sid + ".mp4")
    assert sv["renderSource"] == "devmachine"
    assert sv["sizeBytes"] == 123456
    assert sv["attachedAt"]

    # 回读 content 持久
    content = _run(pipe.repo.get_content(cid))
    assert content["sv73"]["video"].endswith(sid + ".mp4")

    # 幂等覆盖: 二挂载换路径
    att2 = _run(pipe.attach_render(
        sid, "D:/sv73_videos/%s_v2.mp4" % sid))
    assert att2["sv73"]["video"].endswith("_v2.mp4")

    # 未知 scriptId → KeyError
    with pytest.raises(KeyError):
        _run(pipe.attach_render("sv73_ffffffffffff",
                                "D:/x.mp4"))

    # 空 video → ValueError
    with pytest.raises(ValueError):
        _run(pipe.attach_render(sid, "  "))


# ------------------------------------------------------------
# S4 路由: render_mode 透传 + attach 门控
# ------------------------------------------------------------


def test_s4_routes(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.sv73_routes import register_sv73_routes

    app = FastAPI()
    register_sv73_routes(app)
    client = TestClient(app)
    admin = {"X-Role": "admin"}
    hs = {**HOTSPOT, "fingerprint": "fp-sv73-split-route"}

    # off 默认: render skipped
    r = client.post("/api/sv73/pipeline/run", headers=admin,
                    json={"hotspot": hs})
    assert r.status_code == 200
    assert r.json()["data"]["render"]["skipped"] is True

    # render_mode=on 透传: 全链
    r = client.post("/api/sv73/pipeline/run", headers=admin,
                    json={"hotspot": {
                        **hs, "fingerprint":
                            "fp-sv73-split-route2"},
                          "render_mode": "on"})
    d = r.json()["data"]
    assert d["render"].get("skipped") is not True
    sid = d["storyboard"]["scriptId"]

    # attach 端点
    r = client.post("/api/sv73/render/attach", headers=admin,
                    json={"scriptId": sid,
                          "video": "D:/x.mp4", "sizeBytes": 99})
    assert r.status_code == 200
    assert r.json()["data"]["sv73"]["video"] == "D:/x.mp4"

    # 未知 scriptId 404
    r = client.post("/api/sv73/render/attach", headers=admin,
                    json={"scriptId": "sv73_000000000000",
                          "video": "D:/x.mp4"})
    assert r.status_code == 404

    # 非 admin 403
    r = client.post("/api/sv73/render/attach",
                    json={"scriptId": sid, "video": "D:/x.mp4"})
    assert r.status_code == 403

    # off 态 409(决策面门控)
    monkeypatch.setenv("SV73_MODE", "off")
    r = client.post("/api/sv73/render/attach", headers=admin,
                    json={"scriptId": sid, "video": "D:/x.mp4"})
    assert r.status_code == 409
