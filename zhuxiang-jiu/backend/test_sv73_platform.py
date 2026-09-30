"""73号(sv)·短视频智能模型 平台扩展专项——小红书/微博 RPA 通道

覆盖(2026-09-30 P2 平台扩展):
    P1 平台白名单: 四平台全开(wechat_channels/douyin/
       xiaohongshu/weibo) + 形态注册表(weibo=card 降级)
    P2 xhs 全链: run(xiaohongshu) → 三审 → rpa_pending 出队
       + list_pending(platform=xiaohongshu) 可见
    P3 weibo card: run(weibo) → platformForm=card + nextSteps
       含 weibo-cli 三步指引 → rpa_pending 全链
    P4 旧白名单语义保持: 未知平台仍 ValueError

运行: python -m pytest test_sv73_platform.py -q
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
    Sv73PipelineService, SV73_PLATFORMS, SV73_PLATFORM_FORMS,
)
from services.promo_service import PromoService

HOTSPOT = {
    "fingerprint": "fp-sv73-plat-0001",
    "platform": "douyin",
    "title": "双11囤酒清单来了",
    "score": 85,
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
    # rpa_pending 出队口径(36号: CHANNEL_MODE=real 且无平台
    # 凭证 → RPA 平台回 rpa_pending; 默认 mock 走 mock 回执)
    monkeypatch.setenv("PROMO_CHANNEL_MODE", "real")
    yield


# ------------------------------------------------------------
# P1 平台白名单+形态注册表
# ------------------------------------------------------------


def test_p1_platform_registry():
    assert SV73_PLATFORMS == ("wechat_channels", "douyin",
                              "xiaohongshu", "weibo")
    assert SV73_PLATFORM_FORMS["wechat_channels"] == "video"
    assert SV73_PLATFORM_FORMS["douyin"] == "video"
    assert SV73_PLATFORM_FORMS["xiaohongshu"] == "video"
    assert SV73_PLATFORM_FORMS["weibo"] == "card"   # CLI 无视频命令


# ------------------------------------------------------------
# P2 xiaohongshu 全链(rpa_pending 出队+清单可见)
# ------------------------------------------------------------


def test_p2_xhs_full_chain():
    result = _run(Sv73PipelineService().run(
        dict(HOTSPOT), platform="xiaohongshu"))
    assert result["platformForm"] == "video"
    assert "xhs-bot" in result["nextSteps"]
    cid = result["content"]["contentId"]

    promo = PromoService()
    _run(promo.review_content(cid, approved=True))
    _run(promo.publish_content(
        cid, publish_at="2020-01-01T00:00:00+00:00"))
    published = _run(promo.process_publish_queue())
    hit = next(c for c in published if c["contentId"] == cid)
    # 36号 rpa_pending 闭环对 xiaohongshu 平台无关实证
    assert hit["receipt"]["mode"] == "rpa_pending"
    assert hit["platform"] == "xiaohongshu"
    from services.promo_rpa_channel_service import (
        PromoRpaChannelService,
    )
    rows = _run(PromoRpaChannelService().list_pending(
        platform="xiaohongshu"))
    assert any(r["contentId"] == cid for r in rows)


# ------------------------------------------------------------
# P3 weibo card 形态(降级指引+rpa_pending)
# ------------------------------------------------------------


def test_p3_weibo_card():
    result = _run(Sv73PipelineService().run(
        {**HOTSPOT, "fingerprint": "fp-sv73-plat-weibo"},
        platform="weibo"))
    assert result["platformForm"] == "card"
    assert "weibo-cli" in result["nextSteps"]
    assert "upload_pic" in result["nextSteps"]
    cid = result["content"]["contentId"]

    promo = PromoService()
    _run(promo.review_content(cid, approved=True))
    _run(promo.publish_content(
        cid, publish_at="2020-01-01T00:00:00+00:00"))
    published = _run(promo.process_publish_queue())
    hit = next(c for c in published if c["contentId"] == cid)
    assert hit["receipt"]["mode"] == "rpa_pending"
    assert hit["platform"] == "weibo"


# ------------------------------------------------------------
# P4 未知平台仍拒(白名单语义保持)
# ------------------------------------------------------------


def test_p4_unknown_platform():
    with pytest.raises(ValueError):
        _run(Sv73PipelineService().run(
            dict(HOTSPOT), platform="bilibili"))
