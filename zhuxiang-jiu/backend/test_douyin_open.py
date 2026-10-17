"""抖音开放平台对接模块测试(2026-10-17)

覆盖: env fail-hard 凭据闸 / token 缓存单飞(两次调用仅一次网络)
/ err_no 业务异常 / 路由 admin 闸(403) + /status /ma-info 链路。
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

import services.douyin_open_service as svc_mod
from services.douyin_open_service import (
    DouyinAPIError, DouyinConfigError, DouyinOpenService)

FAKE_ENV = {"DOUYIN_APP_ID": "tt90cde6700eb49cef01",
            "DOUYIN_APP_SECRET": "a" * 40}


@pytest.fixture(autouse=True)
def _reset_token(monkeypatch):
    """token 缓存与 env 隔离(模块级单例状态)"""
    monkeypatch.setattr(svc_mod, "_TOKEN", None)
    for k in FAKE_ENV:
        monkeypatch.delenv(k, raising=False)
    yield


def test_creds_missing_env_fail_hard():
    with pytest.raises(DouyinConfigError):
        svc_mod._creds()


def test_creds_reject_illegal_chars(monkeypatch):
    for k, v in FAKE_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("DOUYIN_APP_SECRET", "bad!secret")
    with pytest.raises(DouyinConfigError):
        svc_mod._creds()


def test_token_cached_single_flight(monkeypatch):
    """两次 access_token 仅一次网络请求(7200s 内复用)"""
    asyncio.run(_token_cached_body(monkeypatch))


async def _token_cached_body(monkeypatch):
    for k, v in FAKE_ENV.items():
        monkeypatch.setenv(k, v)
    calls = []

    def fake_get(url, timeout=20):
        calls.append(url)
        assert "grant_type=client_credential" in url
        return {"access_token": "TOK123", "expires_in": 7200}

    monkeypatch.setattr(svc_mod, "_http_get", fake_get)
    s = DouyinOpenService()
    assert await s.access_token() == "TOK123"
    assert await s.access_token() == "TOK123"
    assert len(calls) == 1
    # status 反映缓存存活
    st = await s.status()
    assert st["configured"] and st["token_alive"] and st["token_expire_in"] > 0


def test_api_err_no_raises(monkeypatch):
    with pytest.raises(DouyinAPIError):
        asyncio.run(_err_body(monkeypatch))


async def _err_body(monkeypatch):
    for k, v in FAKE_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(svc_mod, "_http_get", lambda *a, **kw: {
        "err_no": 40014, "err_tips": "bad params"})
    await DouyinOpenService().probe()


# ---------- 路由层 ----------

@pytest.fixture()
def client():
    from main import app
    return TestClient(app)


def test_route_admin_gate(client):
    r = client.get("/api/douyin/status")
    assert r.status_code == 403
    r = client.get("/api/douyin/status", headers={"X-Role": "user"})
    assert r.status_code == 403


def test_route_status_ok(client, monkeypatch):
    for k, v in FAKE_ENV.items():
        monkeypatch.setenv(k, v)
    r = client.get("/api/douyin/status", headers={"X-Role": "admin"})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["configured"] is True and data["app_id"] == FAKE_ENV["DOUYIN_APP_ID"]


def test_route_probe_config_missing_503(client):
    r = client.get("/api/douyin/probe", headers={"X-Role": "admin"})
    assert r.status_code == 503


def test_route_probe_ok(client, monkeypatch):
    for k, v in FAKE_ENV.items():
        monkeypatch.setenv(k, v)

    def fake_get(url, timeout=20):
        assert "/api/apps/token" in url
        return {"access_token": "TOK123xyz", "expires_in": 7200}

    monkeypatch.setattr(svc_mod, "_http_get", fake_get)
    r = client.get("/api/douyin/probe", headers={"X-Role": "admin"})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["app_id"] == FAKE_ENV["DOUYIN_APP_ID"]
    assert data["token_prefix"] == "TOK123xy"
