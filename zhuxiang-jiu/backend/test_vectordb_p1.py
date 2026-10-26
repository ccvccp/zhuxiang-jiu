"""向量库阶段一单测(设计文档 §3 Schema / §3.4 维度铁律 / §4 验收标准 1)

全离线: 不依赖真实 lancedb、pyarrow、阿里百炼、AutoDL GPU。
默认只跑纯 Python 轨(schema 纯函数 + MemoryStore + 工厂回退),
LanceStore 持久化用例挂 pytest.mark.lancedb, 未安装依赖时自动跳过。

打桩策略——
  * vs.LanceStore       → 抛 ImportError 的空壳(验证 auto 回退 /
                          lance 档硬失败两条分支)
  * monkeypatch.setenv  → VECTORDB_* 全部键(验证 env 装配与路由阈值)
  * tmp_path            → Lance 落盘目录(验证关机重启后条数与
                          校验和一致, 即阶段一验收标准 1)
  * _st()/_embedder()   → 注入额度/GPU 状态与假向量化函数(第六节:
                          不触真实阿里 API, 更绝不触 AutoDLPool 开机)
  * _probe()/_seed_embed() → 套餐真值探针替身与 embed 日计数种子(第七节:
                          额度采集同样零网络, 探针一律由用例注入)

覆盖的三条设计铁律:
  1. 维度一致性(§3.4): 建表绑定 dim, 写入/查询逐条校验, 不符即拒
  2. model_version 隔离(§3.4): 不同语义空间不得混写, 检索只查同版本
  3. 校验和抗污染(§4 验收 1): 只纳入稳定字段, 正常检索打点不得
     改变"数据完整性"结论

覆盖的阶段一硬约束(第六节末):
  smart_router 只决策不开机 —— 用 AST 解析真实 import 白名单钉死,
  确保全模块不出现 AutoDLPool(文件 docstring 里的说明文字不算)。

运行: pytest test_vectordb_p1.py
"""

import ast
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent))

import core.metrics as cm
from core.metrics import (llm_timer, metrics_text, reset_metrics,
                          vectordb_embed_duration, vectordb_quota_alert_total,
                          vectordb_quota_remaining, vectordb_route_total)
from core.vectordb import quota_probe as qp
from core.vectordb import smart_router as sr
from core.vectordb import store as vs
from core.vectordb.schema import (COLUMNS, KNOWN_MODEL_DIMS, MODALITIES,
                                  ProductVector, cosine, fingerprint,
                                  quantize, table_checksum,
                                  to_arrow_schema, utcnow)
from core.vectordb.store import (DEFAULT_DIM, DimensionMismatch, Hit,
                                 LanceStore, MemoryStore,
                                 ModelVersionMismatch, open_store)

try:
    import lancedb
    import pyarrow
    _LANCE = True
except ImportError:
    _LANCE = False

# env 装配键(open_store 五项 + smart_router 两阈值 + quota_probe 两键)
_VDB_ENV_KEYS = ("VECTORDB_BACKEND", "VECTORDB_URI", "VECTORDB_TABLE",
                 "VECTORDB_DIM", "VECTORDB_MODEL_VERSION",
                 "VECTORDB_QUOTA_SAFE_THRESHOLD", "VECTORDB_GPU_LOAD_CEILING",
                 "VECTORDB_QUOTA_MONTHLY_CALLS",
                 "VECTORDB_QUOTA_CRITICAL_THRESHOLD")

MV = "bge-m3"                           # 测试统一模型版本


# ============================================================
#  替身与工具
# ============================================================

class _NoLance:
    """LanceStore 替身: 构造即抛 ImportError(模拟未装依赖)"""

    def __init__(self, **kwargs):
        raise ImportError("No module named 'lancedb'")


def _rec(id_, vec, **kw) -> ProductVector:
    """造一条记录(model_version 默认对齐表绑定版本)"""
    kw.setdefault("model_version", MV)
    return ProductVector(id=id_, embedding=list(vec), **kw)


def _mem(dim=2, mv=MV) -> MemoryStore:
    return MemoryStore(dim=dim, model_version=mv)


@pytest.fixture(autouse=True)
def _reset_vectordb_singleton():
    """单例隔离: 每个用例前后清空, 防 env 装配与路由注入串味"""
    vs.reset_store()
    sr.reset_router()
    yield
    vs.reset_store()
    sr.reset_router()


@pytest.fixture(autouse=True)
def _clean_vdb_env(monkeypatch):
    """默认清空 VECTORDB_* 全部键(需要时用例内自行 setenv)"""
    for k in _VDB_ENV_KEYS:
        monkeypatch.delenv(k, raising=False)


@pytest.fixture(autouse=True)
def _clean_vectordb_metrics():
    """埋点隔离(第六/七节): 指标容器 + 额度边沿告警状态每用例清零

    ★ 两者必须成对清: reset_metrics 不能反向 import quota_probe(会成
    import 环), 故 _last_level 只能由 reset_quota_probe 负责。漏清后者
    会让"边沿触发"跨用例串味 —— 上个用例留下的 warn 会吞掉本用例的告警。
    """
    reset_metrics()
    qp.reset_quota_probe()
    yield
    reset_metrics()
    qp.reset_quota_probe()


# ============================================================
#  一、Schema 纯函数
# ============================================================

def test_productvector_defaults():
    """§3.2 原文 Schema 的默认值必须与文档一致"""
    r = ProductVector(id="p1")
    assert r.text == ""
    assert r.modality == "text"
    assert r.embedding == []
    assert r.dim == 0
    assert r.quality_score == 1.0
    assert r.access_count == 0
    assert r.is_synthetic is False
    assert r.cluster_id is None
    assert r.category == ""
    assert r.tags == []
    assert r.last_accessed.tzinfo is not None
    assert r.created_at.tzinfo is not None


def test_columns_cover_every_model_field():
    """COLUMNS 与模型字段必须同步(防加字段后漏落盘)"""
    assert set(COLUMNS) == set(ProductVector.model_fields)


def test_all_declared_modalities_accepted():
    for m in MODALITIES:
        assert ProductVector(id="x", modality=m).modality == m


@pytest.mark.parametrize("bad", ["txt", "TEXT", "", "img"])
def test_modality_out_of_domain_rejected(bad):
    """modality 取值域封闭(§3.2) —— 写脏值等于污染多模态检索"""
    with pytest.raises(ValidationError):
        ProductVector(id="x", modality=bad)


def test_embedding_quantized_on_validate():
    """落盘前唯一收敛点: 6 位小数(schema.VEC_NDIGITS)"""
    r = _rec("q", [0.1234567891, -0.9876543219])
    assert r.embedding == [0.123457, -0.987654]


@pytest.mark.parametrize("raw, want", [(-0.5, 0.0), (1.5, 1.0),
                                       (0.7, 0.7), (0, 0.0), (1, 1.0)])
def test_quality_score_clamped_to_unit_interval(raw, want):
    """质量分收敛 [0,1] —— Data Quality Agent 阈值判定的前提(§3.3)"""
    assert _rec("c", [1.0], quality_score=raw).quality_score == want


def test_naive_datetime_gets_utc_tzinfo():
    """pyarrow timestamp(tz='UTC') 拒绝 naive datetime"""
    naive = datetime(2026, 10, 26, 8, 0, 0)
    r = ProductVector(id="t", created_at=naive, last_accessed=naive)
    assert r.created_at.tzinfo == UTC
    assert r.last_accessed.tzinfo == UTC


def test_utcnow_is_tz_aware():
    assert utcnow().tzinfo is not None
    assert utcnow().utcoffset().total_seconds() == 0


def test_row_exposes_exactly_declared_columns():
    r = _rec("x", [1.0, 0.0], category="裙", tags=["a", "b"])
    row = r.row()
    assert set(row) == set(COLUMNS)
    assert isinstance(row["created_at"], datetime)   # 保持原生对象
    assert row["tags"] == ["a", "b"]
    assert row["category"] == "裙"


def test_quantize_returns_plain_floats():
    out = quantize([1, 0.5, "0.25"])
    assert out == [1.0, 0.5, 0.25]
    assert all(type(v) is float for v in out)


def test_cosine_identical_vectors_is_one():
    assert cosine([1, 2, 3], [1, 2, 3]) == pytest.approx(1.0)


def test_cosine_orthogonal_vectors_is_zero():
    assert cosine([1, 0], [0, 1]) == pytest.approx(0.0)


def test_cosine_opposite_vectors_is_minus_one():
    assert cosine([1, 0], [-1, 0]) == pytest.approx(-1.0)


@pytest.mark.parametrize("a, b", [
    ([1, 2], [1, 2, 3]),      # 维度不等
    ([], []),                 # 双空
    ([0, 0], [1, 0]),         # 零向量
    ([1, 0], [0, 0]),
])
def test_cosine_degenerate_inputs_fail_open(a, b):
    """检索是增强轨, 退化输入返回 0.0 而非抛异常(对齐 kb57 惯例)"""
    assert cosine(a, b) == 0.0


def test_cosine_is_scale_invariant():
    assert cosine([1, 1], [3, 3]) == pytest.approx(1.0)


def test_fingerprint_is_fixed_width_and_stable():
    assert fingerprint([1, -2.5]) == "1.000000,-2.500000"
    assert fingerprint([1.0, -2.5]) == fingerprint([1, -2.5])


def _rows(*specs):
    """[(id, vec, extra...)] → table_checksum 入参"""
    return [{"id": i, "model_version": MV, "embedding": v}
            for i, v in specs]


def test_table_checksum_ignores_row_order():
    """校验和必须与读回顺序无关(重启后 Lance 行序不保证)"""
    a = table_checksum(_rows(("x", [1.0, 0.0]), ("y", [0.0, 1.0])))
    b = table_checksum(_rows(("y", [0.0, 1.0]), ("x", [1.0, 0.0])))
    assert a == b
    assert len(a) == 64                   # sha256 hex


def test_table_checksum_detects_vector_drift():
    a = table_checksum(_rows(("x", [1.0, 0.0])))
    b = table_checksum(_rows(("x", [1.0, 0.1])))
    assert a != b


def test_table_checksum_detects_id_and_version_change():
    base = table_checksum(_rows(("x", [1.0, 0.0])))
    assert base != table_checksum(_rows(("z", [1.0, 0.0])))
    assert base != table_checksum(
        [{"id": "x", "model_version": "clip-v2", "embedding": [1.0, 0.0]}])


def test_table_checksum_ignores_runtime_fields():
    """★ access_count/last_accessed 是运行时字段, 不得进校验和 ——
    否则每次检索打点都会让"数据完整性"结论变化(§4 验收标准 1 失真)"""
    stable = _rows(("x", [1.0, 0.0]))
    touched = [{"id": "x", "model_version": MV, "embedding": [1.0, 0.0],
                "access_count": 99,
                "last_accessed": datetime(2030, 1, 1, tzinfo=UTC),
                "quality_score": 0.01}]
    assert table_checksum(stable) == table_checksum(touched)


def test_table_checksum_handles_missing_embedding():
    """embedding 缺失按零维处理, 不抛 KeyError"""
    assert table_checksum([{"id": "x", "model_version": MV}])


def test_known_model_dims_matches_default():
    assert KNOWN_MODEL_DIMS["bge-m3"] == 1024
    assert DEFAULT_DIM == 1024


# ============================================================
#  二、MemoryStore 写入与两道铁律
# ============================================================

def test_memory_upsert_and_count():
    s = _mem()
    assert s.count() == 0
    assert s.upsert([_rec("a", [1.0, 0.0]), _rec("b", [0.0, 1.0])]) == 2
    assert s.count() == 2


def test_memory_backend_tag():
    assert MemoryStore().backend == "memory"
    assert LanceStore.backend == "lance"


def test_memory_upsert_accepts_plain_dict():
    """调用方可直接传 dict(不必先构造 pydantic 模型)"""
    s = _mem()
    n = s.upsert([{"id": "d1", "embedding": [1.0, 0.0],
                   "model_version": MV}])
    assert n == 1
    assert s.get("d1")["modality"] == "text"      # 默认值补齐


def test_memory_upsert_is_idempotent_by_id():
    """同 id 重复写 = 覆盖, 不产生重复行(merge_insert 语义)"""
    s = _mem()
    s.upsert([_rec("a", [1.0, 0.0], category="旧")])
    s.upsert([_rec("a", [1.0, 0.0], category="新")])
    assert s.count() == 1
    assert s.get("a")["category"] == "新"


def test_memory_upsert_empty_is_noop():
    s = _mem()
    assert s.upsert([]) == 0
    assert s.count() == 0


def test_memory_upsert_rejects_dimension_mismatch():
    """★ §3.4 铁律一: 维度不符直接拒写, 不静默截断/补零"""
    s = _mem(dim=2)
    with pytest.raises(DimensionMismatch) as ei:
        s.upsert([_rec("bad", [1.0, 0.0, 0.0])])
    assert "表维度 2" in str(ei.value)
    assert s.count() == 0                          # 整批拒绝, 无半写


def test_memory_upsert_rejects_model_version_mismatch():
    """★ §3.4 铁律二: 本地 bge-m3 与阿里 Embedding 语义空间不同,
    混写同一列 = 检索结果不可信, 必须分表"""
    s = _mem(dim=2, mv=MV)
    with pytest.raises(ModelVersionMismatch):
        s.upsert([_rec("bad", [1.0, 0.0], model_version="text-embedding-v3")])
    assert s.count() == 0


def test_memory_upsert_allows_empty_table_version():
    """表未绑定版本时不校验(留作混合视图/迁移期口子)"""
    s = _mem(dim=2, mv="")
    assert s.upsert([_rec("a", [1.0, 0.0], model_version="随便")]) == 1


def test_memory_get_missing_returns_none():
    assert _mem().get("nope") is None


def test_memory_get_returns_copy_not_reference():
    """返回值必须与内部存储解耦(调用方改字典不得污染库)"""
    s = _mem()
    s.upsert([_rec("a", [1.0, 0.0])])
    s.get("a")["id"] = "hacked"
    s.all_rows()[0]["id"] = "hacked2"
    assert s.get("a")["id"] == "a"


def test_memory_delete_returns_actual_removed_count():
    s = _mem()
    s.upsert([_rec("a", [1.0, 0.0]), _rec("b", [0.0, 1.0])])
    assert s.delete(["nope"]) == 0
    assert s.delete(["a", "a"]) == 1               # 重复 id 只算一次
    assert s.delete(["b"]) == 1
    assert s.count() == 0


def test_memory_touch_increments_access_count():
    """§3.3 热度打点: Data Quality Agent 识别冷数据的输入"""
    s = _mem()
    s.upsert([_rec("a", [1.0, 0.0])])
    before = s.get("a")["last_accessed"]
    assert s.touch(["a"]) == 1
    assert s.get("a")["access_count"] == 1
    assert s.get("a")["last_accessed"] >= before
    s.touch(["a"])
    assert s.get("a")["access_count"] == 2


def test_memory_touch_dedups_ids():
    """★ 同一次检索重复命中同一条只记一次访问 ——
    与 LanceStore.touch(set 去重) 同口径, 否则两后端热度会漂移"""
    s = _mem()
    s.upsert([_rec("a", [1.0, 0.0])])
    assert s.touch(["a", "a", "a"]) == 1
    assert s.get("a")["access_count"] == 1


def test_memory_touch_ignores_unknown_and_empty():
    s = _mem()
    s.upsert([_rec("a", [1.0, 0.0])])
    assert s.touch([]) == 0
    assert s.touch(["ghost", "a"]) == 1


def test_memory_checksum_stable_across_touch():
    """打点不得改变校验和(与 test_table_checksum_ignores_runtime_fields 闭环)"""
    s = _mem()
    s.upsert([_rec("a", [1.0, 0.0])])
    c0 = s.checksum()
    s.touch(["a", "a"])
    assert s.checksum() == c0
    s.upsert([_rec("b", [0.0, 1.0])])
    assert s.checksum() != c0                      # 真数据变了才变


# ============================================================
#  三、MemoryStore 检索
# ============================================================

@pytest.fixture
def _shop():
    """四条商品: i1/t1 与查询同向, t2 同向但异类目, t3 正交"""
    s = _mem(dim=2)
    s.upsert([
        _rec("i1", [1.0, 0.0], modality="image", category="裙"),
        _rec("t1", [1.0, 0.0], modality="text", category="裙"),
        _rec("t2", [1.0, 0.0], modality="text", category="裤"),
        _rec("t3", [0.0, 1.0], modality="text", category="裙"),
    ])
    return s


Q = [1.0, 0.0]


def test_memory_search_orders_by_score_then_id(_shop):
    """同分按 id 升序 → 结果确定可复现(A/B 测试的前提)"""
    hits = _shop.search(Q)
    assert [h.id for h in hits] == ["i1", "t1", "t2", "t3"]
    assert hits[0].score == pytest.approx(1.0)
    assert hits[-1].score == pytest.approx(0.0)
    assert all(isinstance(h, Hit) for h in hits)


def test_memory_search_respects_k(_shop):
    assert [h.id for h in _shop.search(Q, k=2)] == ["i1", "t1"]


def test_memory_search_k_zero_or_negative_returns_empty(_shop):
    assert _shop.search(Q, k=0) == []
    assert _shop.search(Q, k=-3) == []


def test_memory_search_filter_by_modality(_shop):
    """无阈值时 0 分也应返回(过滤与打分是两件事)"""
    assert [h.id for h in _shop.search(Q, modality="text")] == \
        ["t1", "t2", "t3"]
    assert [h.id for h in _shop.search(Q, modality="image")] == ["i1"]


def test_memory_search_filter_by_category(_shop):
    assert [h.id for h in _shop.search(Q, category="裙")] == \
        ["i1", "t1", "t3"]


def test_memory_search_filter_combined(_shop):
    assert [h.id for h in _shop.search(
        Q, modality="text", category="裙")] == ["t1", "t3"]


def test_memory_search_min_score_drops_weak_hits(_shop):
    assert [h.id for h in _shop.search(Q, min_score=0.5)] == \
        ["i1", "t1", "t2"]
    assert _shop.search(Q, min_score=1.5) == []


def test_memory_search_min_score_zero_keeps_orthogonal(_shop):
    """0.0 分满足 >= 0 阈值(边界不得误杀)"""
    assert [h.id for h in _shop.search(Q, min_score=0.0)] == \
        ["i1", "t1", "t2", "t3"]


def test_memory_search_query_dim_mismatch_raises(_shop):
    with pytest.raises(DimensionMismatch):
        _shop.search([1.0, 0.0, 0.0])


def test_memory_search_isolates_model_version():
    """★ §3.4: 默认只查表绑定版本; 显式传版本可跨版本查"""
    s = _mem(dim=2, mv=MV)
    s.upsert([_rec("a", [1.0, 0.0])])
    # 绕过写入校验直塞一条异版本行(模拟历史遗留/迁移中的数据)
    alien = _rec("b", [1.0, 0.0], model_version="clip-v2").row()
    s._rows["b"] = alien
    assert [h.id for h in s.search(Q)] == ["a"]
    assert [h.id for h in s.search(Q, model_version="clip-v2")] == ["b"]


def test_memory_search_hit_record_carries_metadata(_shop):
    """命中体要带 Agent 元数据, 否则上层还得回表查一次"""
    h = _shop.search(Q)[0]
    for f in ("model_version", "quality_score", "access_count",
              "is_synthetic", "cluster_id", "category", "tags"):
        assert f in h.record


# ============================================================
#  四、工厂与 env 装配
# ============================================================

def test_open_store_memory_backend_explicit():
    s = open_store(backend="memory", dim=8, table="t8", model_version="m")
    assert s.backend == "memory"
    assert s.dim == 8
    assert s.table == "t8"
    assert s.model_version == "m"


def test_open_store_auto_falls_back_when_lance_missing(monkeypatch, caplog):
    """★ 依赖纪律: requirements.txt 无 lancedb → auto 档必须 fail-open,
    绝不能让后端启动路径因缺依赖而崩"""
    monkeypatch.setattr(vs, "LanceStore", _NoLance)
    with caplog.at_level(logging.WARNING, logger="vectordb"):
        s = open_store(backend="auto")
    assert s.backend == "memory"
    assert "回退内存后端" in caplog.text


def test_open_store_default_backend_is_auto_fallback(monkeypatch):
    """未设 VECTORDB_BACKEND 时等同 auto"""
    monkeypatch.setattr(vs, "LanceStore", _NoLance)
    assert open_store().backend == "memory"


def test_open_store_lance_backend_hard_fails(monkeypatch):
    """★ 生产档: 缺依赖必须硬失败 —— 静默退化成内存后端等于
    "重启即丢数据", 比启动失败危险得多"""
    monkeypatch.setattr(vs, "LanceStore", _NoLance)
    with pytest.raises(ImportError):
        open_store(backend="lance")


def test_open_store_backend_value_is_normalized(monkeypatch):
    monkeypatch.setattr(vs, "LanceStore", _NoLance)
    assert open_store(backend=" MEMORY ").backend == "memory"


def test_open_store_reads_env(monkeypatch, tmp_path):
    uri = str(tmp_path / "vdb")
    monkeypatch.setenv("VECTORDB_BACKEND", "memory")
    monkeypatch.setenv("VECTORDB_URI", uri)
    monkeypatch.setenv("VECTORDB_TABLE", "goods")
    monkeypatch.setenv("VECTORDB_DIM", "16")
    monkeypatch.setenv("VECTORDB_MODEL_VERSION", "clip-v2")
    s = open_store()
    assert (s.uri, s.table, s.dim, s.model_version) == \
        (uri, "goods", 16, "clip-v2")


def test_open_store_kwargs_override_env(monkeypatch):
    """显式入参优先于 env(供 smart_router 按 model_version 分表)"""
    monkeypatch.setenv("VECTORDB_BACKEND", "memory")
    monkeypatch.setenv("VECTORDB_TABLE", "from_env")
    monkeypatch.setenv("VECTORDB_DIM", "16")
    s = open_store(table="from_arg", dim=4)
    assert s.table == "from_arg"
    assert s.dim == 4


def test_get_store_is_singleton_and_resettable(monkeypatch):
    monkeypatch.setenv("VECTORDB_BACKEND", "memory")
    a = vs.get_store()
    assert vs.get_store() is a
    vs.reset_store()
    b = vs.get_store()
    assert b is not a


# ============================================================
#  五、LanceStore(需 lancedb/pyarrow, 未装自动跳过)
# ============================================================

@pytest.mark.skipif(not _LANCE, reason="lancedb/pyarrow 未安装")
@pytest.mark.lancedb
def test_lance_persistence_survives_reopen(tmp_path):
    """★ §4 阶段一验收标准 1: 关机 → 重启 → 条数与校验和完全一致"""
    uri = str(tmp_path / "vdb")
    s = LanceStore(uri=uri, table="pv", dim=2, model_version=MV)
    s.upsert([_rec("a", [1.0, 0.0], category="裙"),
              _rec("b", [0.5, 0.5], category="裤")])
    n0, c0 = s.count(), s.checksum()
    assert n0 == 2

    again = LanceStore(uri=uri, table="pv", dim=2, model_version=MV)
    assert again.count() == n0
    assert again.checksum() == c0
    assert again.get("a")["category"] == "裙"


@pytest.mark.skipif(not _LANCE, reason="lancedb/pyarrow 未安装")
@pytest.mark.lancedb
def test_lance_rebinds_dim_from_existing_table(tmp_path):
    """已存在表的维度以 schema 为准(建表即锁死, §3.4 第一道物理防线)"""
    uri = str(tmp_path / "vdb")
    LanceStore(uri=uri, table="pv", dim=2, model_version=MV)
    s = LanceStore(uri=uri, table="pv", dim=0, model_version=MV)
    assert s.dim == 2


@pytest.mark.skipif(not _LANCE, reason="lancedb/pyarrow 未安装")
@pytest.mark.lancedb
def test_lance_dim_conflict_rejected(tmp_path):
    """同表名换维度 → 硬拒绝, 提示换表名"""
    uri = str(tmp_path / "vdb")
    LanceStore(uri=uri, table="pv", dim=2, model_version=MV)
    with pytest.raises(DimensionMismatch) as ei:
        LanceStore(uri=uri, table="pv", dim=4, model_version=MV)
    assert "换表名" in str(ei.value)


@pytest.mark.skipif(not _LANCE, reason="lancedb/pyarrow 未安装")
@pytest.mark.lancedb
def test_lance_new_table_requires_positive_dim(tmp_path):
    """dim=0 只在重开旧表时表示"自动探测"; 建新表必须显式给维度"""
    with pytest.raises(DimensionMismatch) as ei:
        LanceStore(uri=str(tmp_path / "vdb"), table="pv", dim=0,
                   model_version=MV)
    assert "dim > 0" in str(ei.value)


@pytest.mark.skipif(not _LANCE, reason="lancedb/pyarrow 未安装")
@pytest.mark.lancedb
def test_lance_upsert_rejects_wrong_dim(tmp_path):
    """写入侧第二道校验(schema 之外还有应用层, 错误信息更可读)"""
    s = LanceStore(uri=str(tmp_path / "vdb"), table="pv", dim=2,
                   model_version=MV)
    with pytest.raises(DimensionMismatch):
        s.upsert([_rec("bad", [1.0, 0.0, 0.0])])
    assert s.count() == 0


@pytest.mark.skipif(not _LANCE, reason="lancedb/pyarrow 未安装")
@pytest.mark.lancedb
def test_lance_search_agrees_with_memory_backend(tmp_path):
    """★ 跨后端语义一致: 同数据同查询, 命中序与分数必须对齐
    (否则 auto 档回退内存时线上排序会突变)"""
    rows = [_rec("a", [1.0, 0.0], category="裙"),
            _rec("b", [0.6, 0.8], category="裙"),
            _rec("c", [0.0, 1.0], category="裤")]
    lance = LanceStore(uri=str(tmp_path / "vdb"), table="pv", dim=2,
                       model_version=MV)
    lance.upsert(rows)
    mem = _mem(dim=2)
    mem.upsert(rows)

    hl = lance.search(Q, k=3)
    hm = mem.search(Q, k=3)
    assert [h.id for h in hl] == [h.id for h in hm] == ["a", "b", "c"]
    for x, y in zip(hl, hm, strict=True):
        assert x.score == pytest.approx(y.score, abs=1e-5)


@pytest.mark.skipif(not _LANCE, reason="lancedb/pyarrow 未安装")
@pytest.mark.lancedb
def test_lance_search_filters_and_min_score(tmp_path):
    s = LanceStore(uri=str(tmp_path / "vdb"), table="pv", dim=2,
                   model_version=MV)
    s.upsert([_rec("a", [1.0, 0.0], modality="image", category="裙"),
              _rec("b", [1.0, 0.0], modality="text", category="裙"),
              _rec("c", [1.0, 0.0], modality="text", category="裤"),
              _rec("d", [0.0, 1.0], modality="text", category="裙")])
    assert [h.id for h in s.search(Q, modality="text")] == ["b", "c", "d"]
    assert [h.id for h in s.search(Q, category="裙")] == ["a", "b", "d"]
    # ★ min_score 必须先超采再过滤, 否则 limit 会提前吃掉低分命中
    assert [h.id for h in s.search(Q, min_score=0.5)] == ["a", "b", "c"]
    assert s.search(Q, k=0) == []


@pytest.mark.skipif(not _LANCE, reason="lancedb/pyarrow 未安装")
@pytest.mark.lancedb
def test_lance_min_score_does_not_lose_recall(tmp_path):
    """超采回归: k 小于命中数时, 带阈值仍须返回满 k 条"""
    s = LanceStore(uri=str(tmp_path / "vdb"), table="pv", dim=2,
                   model_version=MV)
    s.upsert([_rec(f"r{i}", [1.0, 0.0]) for i in range(12)])
    s.upsert([_rec("noise", [0.0, 1.0])])
    assert len(s.search(Q, k=10, min_score=0.9)) == 10


@pytest.mark.skipif(not _LANCE, reason="lancedb/pyarrow 未安装")
@pytest.mark.lancedb
def test_lance_touch_and_delete(tmp_path):
    s = LanceStore(uri=str(tmp_path / "vdb"), table="pv", dim=2,
                   model_version=MV)
    s.upsert([_rec("a", [1.0, 0.0]), _rec("b", [0.0, 1.0])])
    assert s.touch(["a", "a", "ghost"]) == 1
    assert s.get("a")["access_count"] == 1
    assert s.touch([]) == 0
    assert s.delete(["a", "a"]) == 1
    assert s.count() == 1
    assert s.get("a") is None


@pytest.mark.skipif(not _LANCE, reason="lancedb/pyarrow 未安装")
@pytest.mark.lancedb
def test_lance_upsert_is_idempotent(tmp_path):
    s = LanceStore(uri=str(tmp_path / "vdb"), table="pv", dim=2,
                   model_version=MV)
    s.upsert([_rec("a", [1.0, 0.0], category="旧")])
    s.upsert([_rec("a", [1.0, 0.0], category="新")])
    assert s.count() == 1
    assert s.get("a")["category"] == "新"


@pytest.mark.skipif(not _LANCE, reason="lancedb/pyarrow 未安装")
@pytest.mark.lancedb
def test_arrow_schema_locks_dim_and_types():
    """to_arrow_schema: fixed_size_list 把维度写进 schema"""
    sch = to_arrow_schema(8)
    assert sch.field("embedding").type.list_size == 8
    assert sch.field("created_at").type == pyarrow.timestamp("us", tz="UTC")
    assert sch.field("last_accessed").type == pyarrow.timestamp("us", tz="UTC")
    assert sch.field("is_synthetic").type == pyarrow.bool_()
    assert sch.field("access_count").type == pyarrow.int64()
    assert sch.field("tags").type.value_type == pyarrow.string()
    assert [f.name for f in sch] == list(COLUMNS)


# ============================================================
#  六、smart_router 额度感知双轨路由(§2.3 / §4 阶段一)
# ============================================================

# ★ AST 白名单: 阶段一"纯代码, 不涉及远端开机"的可执行闸门。
#   smart_router 只允许依赖标准库 + 本仓两个模块; 任何新增 import
#   都会让 test_router_imports_stay_within_whitelist 直接红灯。
_ROUTER_IMPORT_WHITELIST = {
    "__future__", "logging", "os", "time", "collections.abc",
    "dataclasses", "core.metrics", "services.llm_client",
}

# 代码节点里绝不允许出现的名字(docstring 的说明文字不计)
_ROUTER_BANNED_IDS = {
    "AutoDLPool", "autodl_pool", "acquire", "release", "adh_power",
    "paramiko", "subprocess", "socket", "urllib", "requests", "httpx",
}


def _st(quota=(1.0, False), gpu=(False, 1.0)) -> sr.RouterState:
    """造一份路由状态快照(元组默认值 -> 不触发 ruff B006)"""
    return sr.RouterState(
        quota=sr.QuotaState(remaining_ratio=quota[0], unknown=quota[1]),
        gpu=sr.GpuState(ready=gpu[0], load=gpu[1]))


def _embedder(vec=(1.0, 0.0), calls=None, mode="ok"):
    """假向量化函数: mode = ok / none / short / raise"""

    def fn(texts):
        if calls is not None:
            calls.append(list(texts))
        if mode == "raise":
            raise RuntimeError("boom")
        if mode == "none":
            return None
        if mode == "short":
            return [list(vec)] * max(0, len(texts) - 1)
        return [list(vec) for _ in texts]

    return fn


def _module_code_ids(mod) -> set[str]:
    """收集模块源码里的全部标识符与模块名(跳过字符串常量)

    泛化版: 第六节查 smart_router, 第七节查 quota_probe —— 两个受
    "纯代码, 不涉及远端开机"闸门保护的模块共用同一套解析逻辑。
    """
    tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
    ids: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            ids.add(node.id)
        elif isinstance(node, ast.Attribute):
            ids.add(node.attr)
        elif isinstance(node, ast.Import):
            ids.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            ids.add(node.module)
    return ids


# --- 6.1 阈值 env 解析 ---------------------------------------

def test_env_float_uses_default_when_unset():
    assert sr._env_float("VECTORDB_QUOTA_SAFE_THRESHOLD", 0.2) == 0.2


def test_env_float_parses_override(monkeypatch):
    monkeypatch.setenv("VECTORDB_GPU_LOAD_CEILING", "0.35")
    assert sr._env_float("VECTORDB_GPU_LOAD_CEILING", 0.7) == 0.35


def test_env_float_blank_falls_back(monkeypatch):
    monkeypatch.setenv("VECTORDB_GPU_LOAD_CEILING", "   ")
    assert sr._env_float("VECTORDB_GPU_LOAD_CEILING", 0.7) == 0.7


def test_env_float_bad_value_warns_not_raises(monkeypatch, caplog):
    """监控阈值写错不能让路由崩 —— 告警 + 用默认值"""
    monkeypatch.setenv("VECTORDB_QUOTA_SAFE_THRESHOLD", "八十")
    with caplog.at_level(logging.WARNING, logger="vectordb"):
        got = sr._env_float("VECTORDB_QUOTA_SAFE_THRESHOLD", 0.2)
    assert got == 0.2
    assert "vectordb_router_bad_env" in caplog.text


# --- 6.2 决策输入/输出契约 -----------------------------------

def test_route_names_match_user_spec():
    """四个出口是用户原文命名, 改名即破坏看板与验收口径"""
    assert sr.ROUTES == ("FORCE_GPU", "GPU_BATCH", "LOCAL_GPU",
                         "ALI_API_FALLBACK")
    assert sr.ROUTES[:3] == sr.GPU_ROUTES


def test_threshold_defaults_match_spec():
    """§5 风险1 已用 80% 预警 -> 剩余 0.2; 用户原文 gpu_load < 70%"""
    assert sr.DEFAULT_QUOTA_SAFE_THRESHOLD == 0.2
    assert sr.DEFAULT_GPU_LOAD_CEILING == 0.7


def test_query_profile_defaults_to_light():
    q = sr.QueryProfile()
    assert (q.requires_rerank, q.is_batch, q.heavy) == (False, False, False)


def test_query_profile_heavy_on_either_flag():
    assert sr.QueryProfile(requires_rerank=True).heavy
    assert sr.QueryProfile(is_batch=True).heavy


def test_quota_state_defaults_full_and_known():
    q = sr.QuotaState()
    assert q.remaining_ratio == 1.0
    assert q.unknown is False


def test_gpu_state_defaults_to_busy():
    """★ 探测数据缺失必须视为满载, 否则会被误判为空闲而开机烧钱"""
    g = sr.GpuState()
    assert g.ready is False
    assert g.load == 1.0


def test_router_state_composes_defaults():
    st = sr.RouterState()
    assert st.quota.remaining_ratio == 1.0
    assert st.gpu.ready is False


def test_route_decision_uses_gpu_only_for_gpu_exits():
    for name in sr.GPU_ROUTES:
        assert sr.RouteDecision(name, "x").uses_gpu
    assert not sr.RouteDecision(sr.ALI_API_FALLBACK, "x").uses_gpu


def test_decision_inputs_are_frozen():
    """决策输入不可就地篡改(污染溯源的前提)"""
    with pytest.raises(AttributeError):
        sr.GpuState().ready = True
    with pytest.raises(AttributeError):
        sr.QuotaState().remaining_ratio = 0.0
    with pytest.raises(AttributeError):
        sr.RouteDecision(sr.FORCE_GPU, "x").route = sr.LOCAL_GPU


# --- 6.3 decide_route 决策树 ---------------------------------

def test_decide_defaults_to_ali_api_fallback():
    d = sr.decide_route(sr.QueryProfile(), sr.RouterState())
    assert (d.route, d.reason) == (sr.ALI_API_FALLBACK, "gpu_not_ready")


def test_decide_step1_quota_low_forces_gpu():
    d = sr.decide_route(sr.QueryProfile(), _st(quota=(0.1, False)))
    assert (d.route, d.reason) == (sr.FORCE_GPU, "quota_low")
    assert d.uses_gpu


def test_decide_step1_boundary_is_strict_less_than():
    """用户原文是 < SAFE_THRESHOLD: 恰好等于阈值不切"""
    assert sr.decide_route(sr.QueryProfile(),
                           _st(quota=(0.2, False))).route != sr.FORCE_GPU
    assert sr.decide_route(sr.QueryProfile(),
                           _st(quota=(0.1999, False))).route == sr.FORCE_GPU


def test_decide_step1_quota_unknown_is_not_low(caplog):
    """★ fail-open: 采集失败绝不当"告急", 否则监控抖一下就开机"""
    with caplog.at_level(logging.WARNING, logger="vectordb"):
        d = sr.decide_route(sr.QueryProfile(), _st(quota=(0.0, True)))
    assert d.route == sr.ALI_API_FALLBACK
    assert "vectordb_quota_unknown" in caplog.text


def test_decide_quota_unknown_does_not_block_later_steps():
    """unknown 只跳过第 1 步, 重型任务仍照走 GPU_BATCH"""
    d = sr.decide_route(sr.QueryProfile(is_batch=True),
                        _st(quota=(0.0, True)))
    assert d.route == sr.GPU_BATCH


def test_decide_step1_beats_step2():
    """书写顺序即优先级: 额度告急时重型任务也归 FORCE_GPU"""
    d = sr.decide_route(sr.QueryProfile(requires_rerank=True, is_batch=True),
                        _st(quota=(0.05, False)))
    assert d.route == sr.FORCE_GPU


def test_decide_step1_beats_step3():
    d = sr.decide_route(sr.QueryProfile(),
                        _st(quota=(0.0, False), gpu=(True, 0.0)))
    assert d.route == sr.FORCE_GPU


def test_decide_step2_heavy_goes_gpu_batch_without_gpu():
    """第 2 步在第 3 步之前: 重型任务不依赖 GPU 是否就绪"""
    d = sr.decide_route(sr.QueryProfile(requires_rerank=True),
                        sr.RouterState())
    assert (d.route, d.reason) == (sr.GPU_BATCH, "heavy_query")


def test_decide_step2_beats_step3_busy_gpu():
    d = sr.decide_route(sr.QueryProfile(is_batch=True), _st(gpu=(True, 0.99)))
    assert d.route == sr.GPU_BATCH


def test_decide_step3_idle_gpu_prefers_local():
    d = sr.decide_route(sr.QueryProfile(), _st(gpu=(True, 0.3)))
    assert (d.route, d.reason) == (sr.LOCAL_GPU, "gpu_idle")


def test_decide_step3_load_ceiling_is_strict_less_than():
    assert sr.decide_route(sr.QueryProfile(),
                           _st(gpu=(True, 0.7))).route != sr.LOCAL_GPU
    assert sr.decide_route(sr.QueryProfile(),
                           _st(gpu=(True, 0.6999))).route == sr.LOCAL_GPU


def test_decide_step4_busy_gpu_reason():
    d = sr.decide_route(sr.QueryProfile(), _st(gpu=(True, 0.95)))
    assert (d.route, d.reason) == (sr.ALI_API_FALLBACK, "gpu_busy")


def test_decide_step4_not_ready_ignores_load():
    d = sr.decide_route(sr.QueryProfile(), _st(gpu=(False, 0.0)))
    assert (d.route, d.reason) == (sr.ALI_API_FALLBACK, "gpu_not_ready")


def test_decide_reads_thresholds_from_env(monkeypatch):
    monkeypatch.setenv("VECTORDB_QUOTA_SAFE_THRESHOLD", "0.5")
    monkeypatch.setenv("VECTORDB_GPU_LOAD_CEILING", "0.1")
    assert sr.decide_route(sr.QueryProfile(),
                           _st(quota=(0.4, False))).route == sr.FORCE_GPU
    assert sr.decide_route(sr.QueryProfile(),
                           _st(gpu=(True, 0.5))).route == sr.ALI_API_FALLBACK


def test_decide_is_pure_and_does_not_bump():
    """纯函数纪律: 埋点只由 SmartRouter 在实际执行后打一次"""
    for _ in range(3):
        sr.decide_route(sr.QueryProfile(), _st(quota=(0.0, False)))
    assert vectordb_route_total.snapshot() == {}
    assert vectordb_embed_duration.snapshot() == ({}, {})


# --- 6.4 SmartRouter.embed 双轨分发 --------------------------

def test_embed_empty_input_short_circuits():
    """空输入既不请求也不埋点(否则看板会被健康检查灌满)"""
    calls = []
    r = sr.SmartRouter(ali_embed=_embedder(calls=calls))
    vecs, d = r.embed([])
    assert vecs is None
    assert (d.route, d.reason) == (sr.ALI_API_FALLBACK, "empty_input")
    assert calls == []
    assert vectordb_route_total.snapshot() == {}


def test_embed_drops_blank_texts():
    calls = []
    r = sr.SmartRouter(ali_embed=_embedder(calls=calls))
    vecs, _d = r.embed(["甲", "", None, "乙"])
    assert calls == [["甲", "乙"]]
    assert len(vecs) == 2


def test_embed_coerces_texts_to_str():
    calls = []
    sr.SmartRouter(ali_embed=_embedder(calls=calls)).embed([1, 2.5])
    assert calls == [["1", "2.5"]]


def test_embed_ali_track_success_bumps_once():
    r = sr.SmartRouter(ali_embed=_embedder(vec=(0.6, 0.8)))
    vecs, d = r.embed(["甲"])
    assert vecs == [[0.6, 0.8]]
    assert (d.route, d.reason) == (sr.ALI_API_FALLBACK, "gpu_not_ready")
    assert vectordb_route_total.snapshot() == {
        (sr.ALI_API_FALLBACK, "gpu_not_ready"): 1.0}


def test_embed_gpu_unwired_seamlessly_uses_ali(caplog):
    """★ §4 验收标准 2: GPU 侧不可用时实时检索自动切阿里, 零请求失败"""
    ali_calls = []
    r = sr.SmartRouter(state=lambda: _st(gpu=(True, 0.1)),
                       ali_embed=_embedder(calls=ali_calls), gpu_embed=None)
    with caplog.at_level(logging.WARNING, logger="vectordb"):
        vecs, d = r.embed(["甲"])
    assert vecs == [[1.0, 0.0]]
    assert (d.route, d.reason) == (sr.ALI_API_FALLBACK, "gpu_unwired")
    assert "vectordb_gpu_unwired" in caplog.text
    assert ali_calls == [["甲"]]


def test_embed_gpu_wired_success_stays_on_gpu():
    ali_calls, gpu_calls = [], []
    r = sr.SmartRouter(state=lambda: _st(gpu=(True, 0.1)),
                       ali_embed=_embedder(calls=ali_calls),
                       gpu_embed=_embedder(vec=(0.5, 0.5), calls=gpu_calls))
    vecs, d = r.embed(["甲", "乙"])
    assert vecs == [[0.5, 0.5], [0.5, 0.5]]
    assert (d.route, d.reason) == (sr.LOCAL_GPU, "gpu_idle")
    assert gpu_calls == [["甲", "乙"]]
    assert ali_calls == []            # ALI 轨零消耗 -> 包月额度不被浪费
    assert vectordb_route_total.snapshot() == {(sr.LOCAL_GPU, "gpu_idle"): 1.0}


def test_embed_force_gpu_route_uses_gpu_track():
    r = sr.SmartRouter(state=lambda: _st(quota=(0.05, False)),
                       ali_embed=_embedder(),
                       gpu_embed=_embedder(vec=(0.5, 0.5)))
    _vecs, d = r.embed(["甲"])
    assert (d.route, d.reason) == (sr.FORCE_GPU, "quota_low")


def test_embed_requires_rerank_selects_gpu_batch():
    r = sr.SmartRouter(ali_embed=_embedder(),
                       gpu_embed=_embedder(vec=(0.5, 0.5)))
    vecs, d = r.embed(["甲"], requires_rerank=True)
    assert (d.route, d.reason) == (sr.GPU_BATCH, "heavy_query")
    assert vecs == [[0.5, 0.5]]


def test_embed_is_batch_selects_gpu_batch():
    r = sr.SmartRouter(ali_embed=_embedder(),
                       gpu_embed=_embedder(vec=(0.5, 0.5)))
    assert r.embed(["甲"], is_batch=True)[1].route == sr.GPU_BATCH


def test_embed_light_query_stays_on_ali_when_gpu_busy():
    r = sr.SmartRouter(state=lambda: _st(gpu=(True, 0.99)),
                       ali_embed=_embedder(),
                       gpu_embed=_embedder(vec=(0.5, 0.5)))
    _vecs, d = r.embed(["甲"])
    assert (d.route, d.reason) == (sr.ALI_API_FALLBACK, "gpu_busy")


def test_embed_gpu_returning_none_degrades_to_ali():
    """返回的第二个值是"实际走通的轨", 不是"决策的轨" """
    ali_calls = []
    r = sr.SmartRouter(state=lambda: _st(quota=(0.05, False)),
                       ali_embed=_embedder(calls=ali_calls),
                       gpu_embed=_embedder(mode="none"))
    vecs, d = r.embed(["甲"])
    assert vecs == [[1.0, 0.0]]
    assert (d.route, d.reason) == (sr.ALI_API_FALLBACK, "gpu_failed")
    assert ali_calls == [["甲"]]
    assert vectordb_route_total.snapshot() == {
        (sr.ALI_API_FALLBACK, "gpu_failed"): 1.0}


def test_embed_gpu_raising_degrades_to_ali(caplog):
    r = sr.SmartRouter(state=lambda: _st(gpu=(True, 0.0)),
                       ali_embed=_embedder(),
                       gpu_embed=_embedder(mode="raise"))
    with caplog.at_level(logging.WARNING, logger="vectordb"):
        vecs, d = r.embed(["甲"])
    assert vecs == [[1.0, 0.0]]
    assert d.reason == "gpu_failed"
    assert "vectordb_embed_error route=LOCAL_GPU" in caplog.text


def test_embed_degradation_bumps_exactly_once_when_ali_also_fails(caplog):
    """★ 埋点纪律: GPU 降级不提前计数, 否则一次调用记两条污染出口总量"""
    r = sr.SmartRouter(state=lambda: _st(quota=(0.0, False)),
                       ali_embed=_embedder(mode="none"),
                       gpu_embed=_embedder(mode="none"))
    with caplog.at_level(logging.WARNING, logger="vectordb"):
        vecs, d = r.embed(["甲"])
    assert vecs is None
    assert (d.route, d.reason) == (sr.ALI_API_FALLBACK, "embed_failed")
    snap = vectordb_route_total.snapshot()
    assert sum(snap.values()) == 1.0
    assert list(snap) == [(sr.ALI_API_FALLBACK, "embed_failed")]
    # prior 记录"最近一条失败轨"的原因, 而非路由决策原因
    assert "vectordb_embed_all_tracks_failed(prior=gpu_failed)" in caplog.text


def test_embed_ali_raising_returns_none_for_2gram_fallback(caplog):
    """两轨全废时 fail-open 返回 None, 由调用方回退 2-gram"""
    r = sr.SmartRouter(ali_embed=_embedder(mode="raise"))
    with caplog.at_level(logging.WARNING, logger="vectordb"):
        vecs, d = r.embed(["甲"])
    assert vecs is None
    assert d.reason == "embed_failed"
    assert "vectordb_embed_error route=ALI_API_FALLBACK" in caplog.text


def test_embed_count_mismatch_is_rejected(caplog):
    """对齐 llm_client.embed 既有惯例: 条数不符整批作废"""
    r = sr.SmartRouter(ali_embed=_embedder(mode="short"))
    with caplog.at_level(logging.WARNING, logger="vectordb"):
        vecs, d = r.embed(["甲", "乙"])
    assert vecs is None
    assert d.reason == "embed_failed"
    assert "vectordb_embed_count_mismatch" in caplog.text


def test_embed_reads_state_fresh_each_call():
    """阶段二接线点: state 是可调用对象, 每次 embed 重新取快照"""
    box = {"ratio": 0.9}
    r = sr.SmartRouter(
        state=lambda: _st(quota=(box["ratio"], False), gpu=(True, 0.1)),
        ali_embed=_embedder(), gpu_embed=_embedder(vec=(0.5, 0.5)))
    assert r.embed(["甲"])[1].route == sr.LOCAL_GPU
    box["ratio"] = 0.05
    assert r.embed(["甲"])[1].route == sr.FORCE_GPU


def test_embed_ali_track_lazily_binds_provider_client(monkeypatch):
    """不注入 ali_embed 时懒绑定既有通道(不新建 HTTP 栈/不引 SDK)"""
    seen = []

    class _Fake:
        def embed(self, texts):
            seen.append(list(texts))
            return [[0.25, 0.75] for _ in texts]

    class _FakeModule:
        provider_client = _Fake()

    monkeypatch.setitem(sys.modules, "services.llm_client", _FakeModule())
    vecs, d = sr.SmartRouter().embed(["甲"])
    assert vecs == [[0.25, 0.75]]
    assert seen == [["甲"]]
    assert d.route == sr.ALI_API_FALLBACK


def test_router_output_feeds_store_roundtrip():
    """阶段一交付物: 路由产出的向量可直接入库并被检索回来"""
    s = vs.MemoryStore(dim=2, model_version=MV)
    r = sr.SmartRouter(ali_embed=_embedder(vec=(1.0, 0.0)))
    vecs, _d = r.embed(["红色连衣裙", "蓝色运动鞋"])
    assert s.upsert([_rec("p1", vecs[0]),
                     _rec("p2", vecs[1], category="鞋")]) == 2
    hits = s.search(vecs[0], k=1)
    assert [h.id for h in hits] == ["p1"]
    assert hits[0].score > 0.99


# --- 6.5 埋点与 Prometheus 渲染 ------------------------------

def test_decide_bumps_route_counter():
    r = sr.SmartRouter(state=lambda: _st(quota=(0.1, False)))
    r.decide()
    r.decide()
    assert vectordb_route_total.snapshot() == {
        (sr.FORCE_GPU, "quota_low"): 2.0}


def test_decide_uses_default_query_profile():
    d = sr.SmartRouter().decide()
    assert (d.route, d.reason) == (sr.ALI_API_FALLBACK, "gpu_not_ready")


def test_quota_drop_flips_route_with_metric():
    """★ §4 阶段二验收原文: 模拟额度降至 80% -> 出口从 ALI_API_FALLBACK
    变 FORCE_GPU 且"有埋点可查" """
    ratio = {"v": 0.9}
    r = sr.SmartRouter(state=lambda: _st(quota=(ratio["v"], False)))
    assert r.decide().route == sr.ALI_API_FALLBACK
    ratio["v"] = 0.15                       # 已用 85%, 越过 0.2 安全线
    assert r.decide().route == sr.FORCE_GPU
    snap = vectordb_route_total.snapshot()
    assert snap[(sr.ALI_API_FALLBACK, "gpu_not_ready")] == 1.0
    assert snap[(sr.FORCE_GPU, "quota_low")] == 1.0


def test_embed_observes_duration_per_executed_track():
    """直方图按"实际执行的轨"记: GPU 降级时两轨各一条(耗时都要看)"""
    r = sr.SmartRouter(state=lambda: _st(gpu=(True, 0.1)),
                       ali_embed=_embedder(),
                       gpu_embed=_embedder(mode="none"))
    r.embed(["甲"])
    counts, sums = vectordb_embed_duration.snapshot()
    gpu_key = (("route", sr.LOCAL_GPU),)
    ali_key = (("route", sr.ALI_API_FALLBACK),)
    # ★ Histogram 只计入最紧桶(非累积) -> 断总量必须 sum()
    assert sum(counts[gpu_key]) == 1
    assert sum(counts[ali_key]) == 1
    assert sums[ali_key] >= 0.0


def test_embed_duration_bucket_layout():
    assert len(vectordb_embed_duration.buckets) == 10   # 9 配置桶 + inf
    assert vectordb_embed_duration.buckets[-1] == float("inf")


def test_metrics_text_exposes_both_new_series():
    sr.SmartRouter(ali_embed=_embedder()).embed(["甲"])
    body = metrics_text()
    assert "# TYPE app_vectordb_route_total counter" in body
    # Counter 渲染值是 float(1.0) -> 只断标签前缀, 数值另由 snapshot 校验
    assert ('app_vectordb_route_total{route="ALI_API_FALLBACK",'
            'reason="gpu_not_ready"}') in body
    assert "# TYPE app_vectordb_embed_duration_seconds histogram" in body
    assert ('app_vectordb_embed_duration_seconds_count'
            '{route="ALI_API_FALLBACK"} 1') in body
    assert ('app_vectordb_embed_duration_seconds_sum'
            '{route="ALI_API_FALLBACK"}') in body
    assert ('app_vectordb_embed_duration_seconds_bucket'
            '{route="ALI_API_FALLBACK",le="+Inf"} 1') in body


def test_reset_metrics_clears_vectordb_series():
    sr.SmartRouter(ali_embed=_embedder()).embed(["甲"])
    assert vectordb_route_total.snapshot()
    reset_metrics()
    assert vectordb_route_total.snapshot() == {}
    assert vectordb_embed_duration.snapshot() == ({}, {})


# --- 6.6 单例 ------------------------------------------------

def test_get_router_is_singleton_and_resettable():
    r1 = sr.get_router(ali_embed=_embedder())
    assert sr.get_router() is r1          # 二次调用不覆盖已注入的依赖
    sr.reset_router()
    assert sr.get_router() is not r1


def test_get_router_default_state_is_inert():
    """默认单例不注入 state -> 额度未采集 + GPU 未就绪 -> 落 ALI 轨"""
    assert sr.get_router(ali_embed=_embedder()).decide().route == \
        sr.ALI_API_FALLBACK


# --- 6.7 阶段一硬约束闸门 ------------------------------------

def test_router_imports_stay_within_whitelist():
    """★ "纯代码, 不涉及远端开机": 依赖面被 AST 钉死"""
    ids = _module_code_ids(sr)
    extra = {i for i in ids if "." in i or i in _ROUTER_IMPORT_WHITELIST}
    assert extra <= _ROUTER_IMPORT_WHITELIST, sorted(
        extra - _ROUTER_IMPORT_WHITELIST)


def test_router_code_never_references_autodl_or_network():
    """docstring 里的说明文字不算; 只看真实代码节点"""
    hit = _module_code_ids(sr) & _ROUTER_BANNED_IDS
    assert not hit, sorted(hit)


def test_env_example_documents_router_constraints():
    """配置锚点不得漂移: 两个阈值键 + 硬约束说明必须在 .env.example"""
    env = Path(__file__).parent / ".env.example"
    body = env.read_text(encoding="utf-8")
    for token in ("VECTORDB_QUOTA_SAFE_THRESHOLD",
                  "VECTORDB_GPU_LOAD_CEILING",
                  "AutoDLPool.acquire()"):
        assert token in body, token


# ============================================================
#  七、quota_probe 阿里套餐额度采集(§2.3 决策树第 1 步 / §5 风险1)
# ============================================================

# ★ 与第六节同一套闸门: quota_probe 也必须"纯代码, 不发网络请求"。
#   套餐真值只能由调用方注入 probe 回调(阶段二 Resource Orchestrator
#   Agent 接线); 模块自身不得出现任何网络/开机标识符, 否则默认部署
#   一跑监控就会真实开机烧钱。
_QUOTA_IMPORT_WHITELIST = {
    "__future__", "logging", "os", "threading", "time",
    "collections.abc", "dataclasses", "core.metrics",
    "core.vectordb.smart_router",
}

# 禁用标识符直接复用第六节的 _ROUTER_BANNED_IDS —— 两个模块的硬约束同源,
# 拆成两份只会让闸门漂移。


def _probe(value=None, raises=False, calls=None):
    """套餐真值探针替身(零网络)

    value=None  -> 返回 None, 即文档化的"本次采不到"信号
    raises=True -> 抛异常, 验证 fail-open 不被监控抖动带崩
    calls       -> 传入 list 可统计调用次数
    """

    def fn():
        if calls is not None:
            calls.append(1)
        if raises:
            raise RuntimeError("probe down")
        return value

    return fn


def _seed_embed(n, date=None, method=None):
    """种 n 次 embed 成功批次(覆盖式直写日计数字典)

    ★ 覆盖而非累加: 与 llm_timer 路径(累加)语义不同, 同一用例内混用后
    不要再期望累加结果。date 缺省取本地今日 —— 必须与
    core.metrics._bump_llm_daily 的 time.strftime("%Y%m%d") 同口径,
    用 UTC 会在 00:00~08:00(Asia/Shanghai)之间错位一天。
    """
    key = date or time.strftime("%Y%m%d")
    cm._llm_daily[key][method or qp.EMBED_METHOD] = {"ok": n, "error": 0}


# --- 7.1 env 解析与本月用量口径 ------------------------------

def test_env_num_uses_default_when_unset():
    assert qp._env_num(qp.MONTHLY_CALLS_ENV, 0) == 0


def test_env_num_parses_override(monkeypatch):
    monkeypatch.setenv(qp.MONTHLY_CALLS_ENV, "250")
    assert qp._env_num(qp.MONTHLY_CALLS_ENV, 0) == 250.0


def test_env_num_blank_falls_back(monkeypatch):
    monkeypatch.setenv(qp.MONTHLY_CALLS_ENV, "   ")
    assert qp._env_num(qp.MONTHLY_CALLS_ENV, 0) == 0


def test_env_num_bad_value_warns_not_raises(monkeypatch, caplog):
    """额度上限写错不能让采集崩 —— 告警 + 用默认值(同 smart_router 纪律)"""
    monkeypatch.setenv(qp.MONTHLY_CALLS_ENV, "十万")
    with caplog.at_level(logging.WARNING, logger="vectordb"):
        got = qp._env_num(qp.MONTHLY_CALLS_ENV, 0)
    assert got == 0
    assert "vectordb_quota_bad_env" in caplog.text


def test_month_used_zero_when_no_counts():
    assert qp.month_used() == 0


def test_month_used_counts_only_embed_ok():
    """口径钉死: 只数 embed 成功批次 —— chat 不计, 失败批次不计费"""
    today = time.strftime("%Y%m%d")
    _seed_embed(7)
    _seed_embed(5, method="chat")
    cm._llm_daily[today][qp.EMBED_METHOD]["error"] = 99
    assert qp.month_used() == 7


def test_month_used_aggregates_days_within_month():
    _seed_embed(3, date="20250110")
    _seed_embed(4, date="20250120")
    assert qp.month_used("202501") == 7


def test_month_used_ignores_other_months():
    _seed_embed(3, date="20250110")
    _seed_embed(5, date="20250310")
    assert qp.month_used("202501") == 3
    assert qp.month_used("202503") == 5


def test_month_used_prefix_match_is_not_substring_loose():
    """"20250" 这类残缺前缀不应把 202501/202503 都算进来"""
    _seed_embed(3, date="20250110")
    _seed_embed(5, date="20250310")
    assert qp.month_used("20250") == 8          # 前缀语义: 确实都匹配
    assert qp.month_used("202513") == 0         # 不存在的月份 -> 0


def test_llm_timer_embed_batches_feed_month_used():
    """真实链路: llm_client.embed() 每批包一次 llm_timer("embed")"""
    assert qp.month_used() == 0
    with llm_timer(qp.EMBED_METHOD):
        pass
    with llm_timer(qp.EMBED_METHOD):
        pass
    assert qp.month_used() == 2
    with pytest.raises(RuntimeError), llm_timer(qp.EMBED_METHOD):
        raise RuntimeError("boom")
    assert qp.month_used() == 2                 # 失败批次不计费


# --- 7.2 classify 两级阈值 -----------------------------------

def test_classify_default_thresholds():
    assert qp.classify(1.0) == qp.LEVEL_OK
    assert qp.classify(0.3) == qp.LEVEL_OK
    assert qp.classify(0.15) == qp.LEVEL_WARN
    assert qp.classify(0.01) == qp.LEVEL_CRITICAL


def test_classify_boundaries_are_strict_less_than():
    """边界值归上一档: 阈值本身不算"低于" """
    assert qp.classify(0.2) == qp.LEVEL_OK
    assert qp.classify(0.05) == qp.LEVEL_WARN


def test_classify_respects_env_overrides(monkeypatch):
    """★ warn 档区间必须非空: 只抬 CRITICAL 会让 [0.3,0.2) 成空集"""
    monkeypatch.setenv(qp.SAFE_THRESHOLD_ENV, "0.6")
    monkeypatch.setenv(qp.CRITICAL_THRESHOLD_ENV, "0.3")
    assert qp.classify(0.8) == qp.LEVEL_OK
    assert qp.classify(0.5) == qp.LEVEL_WARN
    assert qp.classify(0.2) == qp.LEVEL_CRITICAL


def test_classify_bad_env_falls_back_to_default(monkeypatch, caplog):
    monkeypatch.setenv(qp.CRITICAL_THRESHOLD_ENV, "abc")
    with caplog.at_level(logging.WARNING, logger="vectordb"):
        got = qp.classify(0.15)
    assert got == qp.LEVEL_WARN                 # 默认 SAFE=0.2 -> warn
    assert "vectordb_quota_bad_env" in caplog.text


# --- 7.3 read_quota 四条来源分支 -----------------------------

def test_read_quota_disabled_when_nothing_configured():
    """默认部署: 不限额 -> 视为充足, 绝不触发开机(§5 风险1 反向约束)"""
    _seed_embed(3)
    got = qp.read_quota()
    assert (got.source, got.unknown, got.level) == (qp.SOURCE_DISABLED,
                                                    False, qp.LEVEL_OK)
    assert got.remaining_ratio == 1.0
    assert (got.used, got.total) == (3, -1)     # 用量照报, 上限未知


def test_read_quota_local_computes_remaining(monkeypatch):
    monkeypatch.setenv(qp.MONTHLY_CALLS_ENV, "100")
    _seed_embed(10)
    got = qp.read_quota()
    assert got.source == qp.SOURCE_LOCAL
    assert got.remaining_ratio == pytest.approx(0.9)
    assert (got.used, got.total, got.unknown) == (10, 100, False)
    assert got.level == qp.LEVEL_OK


def test_read_quota_local_clamps_at_zero(monkeypatch):
    """超额使用不得出现负余量(否则面板与阈值判定都会失真)"""
    monkeypatch.setenv(qp.MONTHLY_CALLS_ENV, "10")
    _seed_embed(25)
    got = qp.read_quota()
    assert got.remaining_ratio == 0.0
    assert got.level == qp.LEVEL_CRITICAL


def test_read_quota_local_warn_and_critical_levels(monkeypatch):
    monkeypatch.setenv(qp.MONTHLY_CALLS_ENV, "100")
    _seed_embed(85)
    assert qp.read_quota().level == qp.LEVEL_WARN
    _seed_embed(99)
    assert qp.read_quota().level == qp.LEVEL_CRITICAL


def test_read_quota_probe_wins_over_local(monkeypatch):
    """套餐真值优先: probe 有值时本地推算让位, 且不混填批次口径"""
    monkeypatch.setenv(qp.MONTHLY_CALLS_ENV, "100")
    _seed_embed(10)
    got = qp.read_quota(_probe(value=0.3))
    assert got.source == qp.SOURCE_PROBE
    assert got.remaining_ratio == pytest.approx(0.3)
    assert (got.used, got.total) == (-1, -1)
    assert got.level == qp.LEVEL_OK


def test_read_quota_calls_probe_exactly_once():
    calls: list[int] = []
    qp.read_quota(_probe(value=0.5, calls=calls))
    assert len(calls) == 1


@pytest.mark.parametrize("ratio", [0.0, 1.0])
def test_read_quota_probe_boundary_values_accepted(ratio):
    """0 与 1 都是合法剩余比例(耗尽/全新), 不得当越界丢弃"""
    got = qp.read_quota(_probe(value=ratio))
    assert got.source == qp.SOURCE_PROBE
    assert got.remaining_ratio == ratio


@pytest.mark.parametrize("bad", [1.5, -0.1, 2, -1])
def test_read_quota_probe_out_of_range_falls_back(monkeypatch, caplog, bad):
    monkeypatch.setenv(qp.MONTHLY_CALLS_ENV, "100")
    _seed_embed(10)
    with caplog.at_level(logging.WARNING, logger="vectordb"):
        got = qp.read_quota(_probe(value=bad))
    assert got.source == qp.SOURCE_LOCAL        # fail-open 到本地推算
    assert got.remaining_ratio == pytest.approx(0.9)
    assert "vectordb_quota_probe_out_of_range" in caplog.text


@pytest.mark.parametrize("bad", ["十万", [0.5]])
def test_read_quota_probe_bad_value_falls_back(monkeypatch, caplog, bad):
    monkeypatch.setenv(qp.MONTHLY_CALLS_ENV, "100")
    _seed_embed(10)
    with caplog.at_level(logging.WARNING, logger="vectordb"):
        got = qp.read_quota(_probe(value=bad))
    assert got.source == qp.SOURCE_LOCAL
    assert "vectordb_quota_probe_bad_value" in caplog.text


def test_read_quota_probe_error_falls_back(monkeypatch, caplog):
    """监控抖动不得带崩采集, 更不得被误读成"额度告急" """
    monkeypatch.setenv(qp.MONTHLY_CALLS_ENV, "100")
    _seed_embed(10)
    with caplog.at_level(logging.WARNING, logger="vectordb"):
        got = qp.read_quota(_probe(raises=True))
    assert got.source == qp.SOURCE_LOCAL
    assert got.remaining_ratio == pytest.approx(0.9)
    assert "vectordb_quota_probe_error" in caplog.text
    assert "回退本地推算" in caplog.text


def test_read_quota_probe_none_falls_back_to_local_silently(monkeypatch,
                                                            caplog):
    """None 是文档化的"采不到"信号 -> 回退但不告警(免噪音)"""
    monkeypatch.setenv(qp.MONTHLY_CALLS_ENV, "100")
    _seed_embed(10)
    with caplog.at_level(logging.WARNING, logger="vectordb"):
        got = qp.read_quota(_probe(value=None))
    assert got.source == qp.SOURCE_LOCAL
    assert caplog.text == ""


def test_read_quota_unknown_when_probe_wired_but_unreadable(caplog):
    """探针接线了却采不到、本地又无上限 -> 明确报"无法判定" """
    _seed_embed(4)
    with caplog.at_level(logging.WARNING, logger="vectordb"):
        got = qp.read_quota(_probe(value=None))
    assert (got.source, got.unknown) == (qp.SOURCE_UNKNOWN, True)
    assert got.remaining_ratio == 1.0
    assert got.level == qp.LEVEL_OK             # 不当"告急", 不触发开机
    assert (got.used, got.total) == (4, -1)
    assert "vectordb_quota_unavailable" in caplog.text


def test_read_quota_unknown_when_probe_raises_and_no_cap(caplog):
    with caplog.at_level(logging.WARNING, logger="vectordb"):
        got = qp.read_quota(_probe(raises=True))
    assert got.source == qp.SOURCE_UNKNOWN
    assert got.unknown is True
    assert "vectordb_quota_probe_error" in caplog.text


def test_read_quota_month_arg_scopes_local_usage(monkeypatch):
    monkeypatch.setenv(qp.MONTHLY_CALLS_ENV, "100")
    _seed_embed(30, date="20250110")
    _seed_embed(90, date="20250310")
    got = qp.read_quota(month="202501")
    assert (got.used, got.total) == (30, 100)
    assert got.remaining_ratio == pytest.approx(0.7)


# --- 7.4 埋点: gauge + 边沿告警 ------------------------------

def test_read_quota_sets_remaining_gauge_with_source_label(monkeypatch):
    monkeypatch.setenv(qp.MONTHLY_CALLS_ENV, "100")
    _seed_embed(10)
    qp.read_quota()
    snap = vectordb_quota_remaining.snapshot()
    assert list(snap) == [("local",)]
    assert snap[("local",)] == pytest.approx(0.9)


def test_read_quota_disabled_still_sets_gauge():
    qp.read_quota()
    assert vectordb_quota_remaining.snapshot()[("disabled",)] == 1.0


def test_read_quota_unknown_skips_gauge(caplog):
    """无法判定时不写 1.0 —— 否则面板会把"未知"画成"满额" """
    with caplog.at_level(logging.WARNING, logger="vectordb"):
        qp.read_quota(_probe(value=None))
    assert vectordb_quota_remaining.snapshot() == {}


def test_first_ok_read_produces_no_alert(monkeypatch):
    """隐式基线就是 ok: 健康读数不该刷计数(免告警风暴)"""
    monkeypatch.setenv(qp.MONTHLY_CALLS_ENV, "100")
    _seed_embed(10)
    qp.read_quota()
    assert vectordb_quota_alert_total.snapshot() == {}


def test_alert_counter_is_edge_triggered(monkeypatch, caplog):
    """同一 level 连续读只计一次; 档位翻转才 +1"""
    monkeypatch.setenv(qp.MONTHLY_CALLS_ENV, "100")
    _seed_embed(85)
    with caplog.at_level(logging.WARNING, logger="vectordb"):
        assert qp.read_quota().level == qp.LEVEL_WARN
        assert qp.read_quota().level == qp.LEVEL_WARN
        assert qp.read_quota().level == qp.LEVEL_WARN
    snap = vectordb_quota_alert_total.snapshot()
    assert list(snap) == [("local", "warn")]
    assert snap[("local", "warn")] == 1.0
    assert caplog.text.count("vectordb_quota_alert") == 1


def test_alert_escalates_ok_warn_critical(monkeypatch):
    monkeypatch.setenv(qp.MONTHLY_CALLS_ENV, "100")
    _seed_embed(10)
    qp.read_quota()
    _seed_embed(85)
    qp.read_quota()
    _seed_embed(99)
    qp.read_quota()
    snap = vectordb_quota_alert_total.snapshot()
    assert snap[("local", "warn")] == 1.0
    assert snap[("local", "critical")] == 1.0
    assert ("local", "ok") not in snap


def test_alert_recovery_counts_without_warning(monkeypatch, caplog):
    """恢复(->ok)也要计数(面板需要闭环), 但不打 WARNING"""
    monkeypatch.setenv(qp.MONTHLY_CALLS_ENV, "100")
    _seed_embed(85)
    with caplog.at_level(logging.WARNING, logger="vectordb"):
        qp.read_quota()
        _seed_embed(10)
        qp.read_quota()
    snap = vectordb_quota_alert_total.snapshot()
    assert snap[("local", "warn")] == 1.0
    assert snap[("local", "ok")] == 1.0
    assert caplog.text.count("vectordb_quota_alert") == 1


def test_alert_first_critical_from_clean_state_warns(monkeypatch, caplog):
    """进程重启后额度已告急 -> 首次读数即产出预警, 不漏报"""
    monkeypatch.setenv(qp.MONTHLY_CALLS_ENV, "100")
    _seed_embed(99)
    with caplog.at_level(logging.WARNING, logger="vectordb"):
        qp.read_quota()
    assert vectordb_quota_alert_total.snapshot()[("local", "critical")] == 1.0
    assert "level=critical" in caplog.text


def test_alert_sources_are_tracked_independently(monkeypatch, caplog):
    """probe 与 local 各自维护边沿状态, 互不吞告警"""
    monkeypatch.setenv(qp.MONTHLY_CALLS_ENV, "100")
    _seed_embed(85)
    with caplog.at_level(logging.WARNING, logger="vectordb"):
        qp.read_quota()                       # local -> warn
        qp.read_quota(_probe(value=0.01))     # probe -> critical
    snap = vectordb_quota_alert_total.snapshot()
    assert snap[("local", "warn")] == 1.0
    assert snap[("probe", "critical")] == 1.0


def test_reset_quota_probe_clears_edge_state(monkeypatch):
    monkeypatch.setenv(qp.MONTHLY_CALLS_ENV, "100")
    _seed_embed(85)
    qp.read_quota()
    assert vectordb_quota_alert_total.snapshot()[("local", "warn")] == 1.0
    qp.reset_quota_probe()
    reset_metrics()
    # ★ reset_metrics 连 _llm_daily 一起清, 必须重新种子: 否则第二次读到
    #   used=0 -> remaining=1.0 -> level=ok, 计的是 ("local","ok") 而非 warn
    _seed_embed(85)
    qp.read_quota()
    # 状态被清 -> 同样的 warn 相对隐式基线 ok 又是一次翻转, 重新计数
    assert vectordb_quota_alert_total.snapshot()[("local", "warn")] == 1.0


def test_reset_quota_probe_without_metrics_reset_keeps_accumulating(monkeypatch):
    """两个 reset 职责不重叠: 只清边沿状态时计数继续累加"""
    monkeypatch.setenv(qp.MONTHLY_CALLS_ENV, "100")
    _seed_embed(85)
    qp.read_quota()
    qp.reset_quota_probe()
    qp.read_quota()
    assert vectordb_quota_alert_total.snapshot()[("local", "warn")] == 2.0


def test_metrics_text_exposes_quota_series(monkeypatch):
    monkeypatch.setenv(qp.MONTHLY_CALLS_ENV, "100")
    _seed_embed(85)
    qp.read_quota()
    body = metrics_text()
    assert "# TYPE app_vectordb_quota_remaining_ratio gauge" in body
    assert 'app_vectordb_quota_remaining_ratio{source="local"}' in body
    assert "# TYPE app_vectordb_quota_alert_total counter" in body
    # 标签顺序随 label_names=("source","level"), 非字典序
    assert ('app_vectordb_quota_alert_total{source="local",'
            'level="warn"}') in body


def test_reset_metrics_clears_quota_series(monkeypatch):
    monkeypatch.setenv(qp.MONTHLY_CALLS_ENV, "100")
    _seed_embed(85)
    qp.read_quota()
    assert vectordb_quota_remaining.snapshot()
    assert vectordb_quota_alert_total.snapshot()
    reset_metrics()
    assert vectordb_quota_remaining.snapshot() == {}
    assert vectordb_quota_alert_total.snapshot() == {}


# --- 7.5 与 smart_router 联动(决策树第 1 步) ----------------

def test_to_state_drops_local_counters(monkeypatch):
    """路由输入只带 ratio + unknown: 本站批次口径不外泄到决策树"""
    monkeypatch.setenv(qp.MONTHLY_CALLS_ENV, "100")
    _seed_embed(10)
    st = qp.read_quota().to_state()
    assert isinstance(st, sr.QuotaState)
    assert st.remaining_ratio == pytest.approx(0.9)
    assert st.unknown is False
    assert not hasattr(st, "used")


def test_quota_state_low_flips_route_to_force_gpu():
    """★ 决策树第 1 步落地: 额度告急 -> FORCE_GPU(§5 风险1)"""
    st = sr.RouterState(quota=qp.quota_state(_probe(value=0.1)))
    d = sr.decide_route(sr.QueryProfile(), st)
    assert (d.route, d.reason) == (sr.FORCE_GPU, "quota_low")


def test_quota_state_healthy_keeps_ali_route():
    st = sr.RouterState(quota=qp.quota_state(_probe(value=1.0)))
    d = sr.decide_route(sr.QueryProfile(), st)
    assert (d.route, d.reason) == (sr.ALI_API_FALLBACK, "gpu_not_ready")


def test_quota_state_local_drop_also_flips_route(monkeypatch):
    """未接探针时, 本地推算同样能驱动切轨(代理指标也要生效)"""
    monkeypatch.setenv(qp.MONTHLY_CALLS_ENV, "100")
    _seed_embed(95)
    st = sr.RouterState(quota=qp.quota_state())
    d = sr.decide_route(sr.QueryProfile(), st)
    assert d.route == sr.FORCE_GPU


def test_quota_unknown_does_not_force_gpu(caplog):
    """无法判定 -> 告警但按充足处理: 监控故障不得变成开机账单"""
    with caplog.at_level(logging.WARNING, logger="vectordb"):
        st = sr.RouterState(quota=qp.quota_state(_probe(value=None)))
        d = sr.decide_route(sr.QueryProfile(), st)
    assert d.route != sr.FORCE_GPU
    assert d.route == sr.ALI_API_FALLBACK
    assert "vectordb_quota_unavailable" in caplog.text
    assert "vectordb_quota_unknown" in caplog.text


def test_quota_low_outranks_heavy_query_reason():
    """第 1 步优先级高于第 2 步: reason 必须是 quota_low 而非 heavy_query"""
    st = sr.RouterState(quota=qp.quota_state(_probe(value=0.0)))
    d = sr.decide_route(sr.QueryProfile(is_batch=True, requires_rerank=True),
                        st)
    assert (d.route, d.reason) == (sr.FORCE_GPU, "quota_low")


def test_router_accepts_probe_backed_state_callable():
    """模块 docstring 的推荐接线方式必须真的可用"""
    r = sr.get_router(state=lambda: sr.RouterState(
                          quota=qp.quota_state(_probe(value=0.01))),
                      ali_embed=_embedder())
    d = r.decide()
    assert (d.route, d.reason) == (sr.FORCE_GPU, "quota_low")
    assert d.uses_gpu


def test_router_probe_failure_degrades_to_ali_embed():
    """探针挂掉 + GPU 未接线 -> 仍能用阿里轨出向量(fail-open 端到端)"""
    r = sr.get_router(state=lambda: sr.RouterState(
                          quota=qp.quota_state(_probe(raises=True))),
                      ali_embed=_embedder())
    out = r.embed(["甲", "乙"])
    assert len(out) == 2


# --- 7.6 阶段一硬约束闸门(quota_probe) ----------------------

def test_quota_probe_imports_stay_within_whitelist():
    """★ "纯代码, 不发网络请求": 依赖面被 AST 钉死"""
    ids = _module_code_ids(qp)
    extra = {i for i in ids if "." in i or i in _QUOTA_IMPORT_WHITELIST}
    assert extra <= _QUOTA_IMPORT_WHITELIST, sorted(
        extra - _QUOTA_IMPORT_WHITELIST)


def test_quota_probe_code_never_references_autodl_or_network():
    """真值只能靠注入 probe 回调拿, 模块内不得自带任何出网/开机手段"""
    hit = _module_code_ids(qp) & _ROUTER_BANNED_IDS
    assert not hit, sorted(hit)


def test_smart_router_still_does_not_depend_on_quota_probe():
    """单向依赖闸门: quota_probe -> smart_router, 反向 import 会让
    第六节的白名单收集到 "core.vectordb.quota_probe" 而直接红灯。
    接线由调用方(get_router 的 state 回调)完成, 不在模块层耦合。"""
    hit = {i for i in _module_code_ids(sr) if "quota_probe" in i}
    assert not hit, sorted(hit)


def test_env_example_documents_quota_probe_keys():
    """配置锚点不得漂移: 两个新键 + 两条指标名必须在 .env.example"""
    body = (Path(__file__).parent / ".env.example").read_text(
        encoding="utf-8")
    for token in ("VECTORDB_QUOTA_MONTHLY_CALLS",
                  "VECTORDB_QUOTA_CRITICAL_THRESHOLD",
                  "quota_probe.read_quota",
                  "app_vectordb_quota_remaining_ratio",
                  "app_vectordb_quota_alert_total"):
        assert token in body, token
