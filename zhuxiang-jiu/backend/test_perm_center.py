"""双权限中心升级回归测试(Redis 种子漂移修复)

生产事故背景: 生产 Redis 已存量 32 节点(旧版无 center/centerName 字段),
旧版惰性种子"已有数据即跳过" → 网站权限中心 20 节点永不追加。
修复为进程级幂等漂移同步: 追加缺失节点 + 回填存量节点中心字段。

覆盖:
    1. 漂移修复: 存量 32 生产节点(无 center) → 首读同步出全量双中心
    2. 幂等: 强制二次同步零副作用(无重复节点)
    3. get_node_by_code 可查网站中心节点(SoD 互斥 intact)

运行:
    python -m pytest test_perm_center.py -p fakeredis_plugin -v
"""

import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(BACKEND_DIR))

import repositories.perm_repository as pr
from repositories.backend import get_redis_client
from repositories.perm_repository import (
    PermRepository, _SEED_NODES, PRODUCTION_STAGES,
)


async def _clear_perm_nodes():
    client = await get_redis_client()
    keys = await client.keys(pr._k("perm", "perm_nodes", "*"))
    if keys:
        await client.delete(*keys)


async def _seed_legacy_nodes(repo: PermRepository):
    """构造旧版存量: 仅生产域节点, 无 center/centerName 字段"""
    client = await get_redis_client()
    for nid, node in _SEED_NODES.items():
        if node["stage"] in PRODUCTION_STAGES:
            legacy = {k: v for k, v in node.items()
                      if k not in ("center", "centerName")}
            await client.hset(pr._k("perm", "perm_nodes", nid),
                              mapping=repo._serialize(legacy))


@pytest.fixture
def redis_mode(monkeypatch):
    """Redis 存储模式 + 重置进程级同步标记(须配合 -p fakeredis_plugin)"""
    monkeypatch.setenv("STORE_MODE", "redis")
    pr._NODES_SYNCED = False
    yield
    pr._NODES_SYNCED = False


async def test_drift_repair_appends_and_backfills(redis_mode):
    """存量生产节点(无 center) → 首读同步出全量双中心"""
    repo = PermRepository()
    await _clear_perm_nodes()
    await _seed_legacy_nodes(repo)

    nodes = await repo._list("perm_nodes", limit=500)
    assert len(nodes) >= len(_SEED_NODES)

    site = [n for n in nodes if n.get("center") == "site"]
    prod = [n for n in nodes if n.get("center") == "production"]
    legacy_count = sum(1 for n in _SEED_NODES.values()
                       if n["stage"] in PRODUCTION_STAGES)
    assert len(prod) >= legacy_count
    assert len(site) >= len(_SEED_NODES) - legacy_count
    # 存量节点中心维度回填
    assert all(n.get("centerName") == "生产权限中心" for n in prod)
    assert all(n.get("centerName") == "网站权限中心" for n in site)
    # 存量节点其余字段保持(如责任书清单)
    sample = next(n for n in prod if n["code"] == "purchase.view")
    assert len(sample.get("duties") or []) == 3


async def test_drift_repair_idempotent(redis_mode):
    """强制二次同步零副作用(按 code 去重, 无重复节点)"""
    repo = PermRepository()
    await _clear_perm_nodes()
    await _seed_legacy_nodes(repo)
    await repo._list("perm_nodes", limit=500)

    pr._NODES_SYNCED = False  # 强制再跑一轮同步
    nodes = await repo._list("perm_nodes", limit=500)
    codes = [n["code"] for n in nodes]
    assert len(nodes) >= len(_SEED_NODES)
    assert len(codes) == len(set(codes))


async def test_site_node_query_and_sod(redis_mode):
    """get_node_by_code 可查网站中心节点, SoD 互斥字段 intact"""
    repo = PermRepository()
    await _clear_perm_nodes()
    await _seed_legacy_nodes(repo)

    order = await repo.get_node_by_code("order.operate")
    assert order is not None
    assert order["center"] == "site"
    assert order["centerName"] == "网站权限中心"
    assert "order.approve" in (order.get("conflictWith") or [])
