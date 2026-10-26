"""向量存储层(设计文档 §3 选型 / §4 阶段一)

双后端, 同一套 API(duck typing, 不引抽象基类):
    LanceStore   嵌入式持久化, 落 AutoDL 数据盘 /root/autodl-tmp
                 (§3.1 选型理由: 关机不丢、零服务依赖、原生版本管理)
    MemoryStore  纯 Python 暴力余弦, 零第三方依赖

依赖纪律: requirements.txt 无 lancedb/pyarrow —— import lancedb
绝不出现在生产后端启动路径上, 一律函数内懒加载; 未安装时
open_store() 捕获 ImportError 回退内存后端(fail-open, 对齐
kb57_embedding_service 与 llm_client 的既有回退惯例)。

维度一致性铁律(§3.4): 每张表建表时绑定 dim 与 model_version,
upsert 逐条校验, 不符直接拒绝写入(不静默截断/补零)。
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from core.vectordb.schema import (ProductVector, cosine, table_checksum, utcnow)

logger = logging.getLogger("vectordb")

# ---------------------------------------------------------------
# 默认配置(env 可覆盖; 键名见 backend/.env.example VECTORDB_* 段)
# ---------------------------------------------------------------
DEFAULT_URI = "/root/autodl-tmp/vectordb"
DEFAULT_TABLE = "product_vector"
DEFAULT_DIM = 1024                 # bge-m3(§3.4)
DEFAULT_MODEL_VERSION = "bge-m3"
DEFAULT_TOP_K = 10


# ---------------------------------------------------------------
# 错误族
# ---------------------------------------------------------------
class VectorStoreError(ValueError):
    """存储层拒绝写入/查询(调用方应记日志并 fail-open)"""


class DimensionMismatch(VectorStoreError):
    """向量维度与表绑定维度不符(§3.4 铁律)"""


class ModelVersionMismatch(VectorStoreError):
    """model_version 与表绑定版本不符 —— 必须分表, 不得混写"""


@dataclass
class Hit:
    """一条检索命中

    score 为余弦相似度 [-1, 1], 越大越相似(两后端同口径 ——
    Lance 侧由 L2/cosine distance 换算, 不是原始 _distance)。
    """

    id: str
    score: float
    record: dict


# ---------------------------------------------------------------
# 写入校验(两后端共用)
# ---------------------------------------------------------------
def _coerce(items: Iterable[ProductVector | dict], dim: int,
            model_version: str) -> list[ProductVector]:
    """入参归一为 ProductVector 并执行两道铁律校验"""
    out: list[ProductVector] = []
    for it in items:
        rec = (it if isinstance(it, ProductVector)
               else ProductVector.model_validate(it))
        if rec.dim != dim:
            raise DimensionMismatch(
                f"id={rec.id} 向量维度 {rec.dim} != 表维度 {dim}")
        if model_version and rec.model_version != model_version:
            raise ModelVersionMismatch(
                f"id={rec.id} model_version={rec.model_version!r} "
                f"!= 表版本 {model_version!r}(须分表写入)")
        out.append(rec)
    return out


def _lit(v: object) -> str:
    """SQL 字符串字面量(单引号翻倍转义)"""
    return "'" + str(v).replace("'", "''") + "'"


def _where(model_version: str = "", modality: str = "",
           category: str = "") -> str:
    """拼装 Lance 侧 SQL 过滤表达式(空条件返回 "")"""
    parts = []
    if model_version:
        parts.append(f"model_version = {_lit(model_version)}")
    if modality:
        parts.append(f"modality = {_lit(modality)}")
    if category:
        parts.append(f"category = {_lit(category)}")
    return " AND ".join(parts)


def _check_query_dim(query: Sequence[float], dim: int) -> list[float]:
    q = [float(v) for v in query]
    if len(q) != dim:
        raise DimensionMismatch(f"查询向量维度 {len(q)} != 表维度 {dim}")
    return q


# ===============================================================
#  内存后端
# ===============================================================
class MemoryStore:
    """纯 Python 内存后端(零第三方依赖)

    定位: 本地开发 / CI / lancedb 缺失时的 fail-open 回退。
    进程退出即丢失, 不满足阶段一验收标准 1 —— 生产必须 LanceStore。
    检索为全表暴力扫描, 与 kb57_embedding_service 现行做法同级,
    百级~千级量级足够。
    """

    backend = "memory"

    def __init__(self, uri: str = "", table: str = DEFAULT_TABLE,
                 dim: int = DEFAULT_DIM,
                 model_version: str = DEFAULT_MODEL_VERSION):
        self.uri = uri
        self.table = table
        self.dim = int(dim)
        self.model_version = model_version
        self._rows: dict[str, dict] = {}

    # --- 写 ---
    def upsert(self, items: Iterable[ProductVector | dict]) -> int:
        rows = _coerce(items, self.dim, self.model_version)
        for rec in rows:
            self._rows[rec.id] = rec.row()
        return len(rows)

    def touch(self, ids: Iterable[str]) -> int:
        """访问热度打点(§3.3 access_count / last_accessed)

        ids 去重 —— 与 LanceStore 同口径: 同一次检索重复命中同一条
        只记一次访问, 否则两后端的 access_count 会漂移。
        """
        now = utcnow()
        n = 0
        for i in dict.fromkeys(str(x) for x in ids):
            r = self._rows.get(i)
            if r is None:
                continue
            r["access_count"] = int(r.get("access_count") or 0) + 1
            r["last_accessed"] = now
            n += 1
        return n

    def delete(self, ids: Iterable[str]) -> int:
        n = 0
        for i in ids:
            if self._rows.pop(i, None) is not None:
                n += 1
        return n

    # --- 读 ---
    def count(self) -> int:
        return len(self._rows)

    def get(self, id_: str) -> dict | None:
        r = self._rows.get(id_)
        return dict(r) if r is not None else None

    def all_rows(self) -> list[dict]:
        return [dict(r) for r in self._rows.values()]

    def checksum(self) -> str:
        return table_checksum(self._rows.values())

    def search(self, query: Sequence[float], k: int = DEFAULT_TOP_K,
               model_version: str = "", modality: str = "",
               category: str = "",
               min_score: float | None = None) -> list[Hit]:
        q = _check_query_dim(query, self.dim)
        mv = model_version or self.model_version
        out: list[Hit] = []
        for r in self._rows.values():
            if mv and r.get("model_version") != mv:
                continue
            if modality and r.get("modality") != modality:
                continue
            if category and r.get("category") != category:
                continue
            s = cosine(q, r.get("embedding") or [])
            if min_score is not None and s < min_score:
                continue
            out.append(Hit(r["id"], s, dict(r)))
        # 分数降序; 同分按 id 升序 → 结果确定(可复现, 便于测试与 A/B)
        out.sort(key=lambda h: (-h.score, h.id))
        return out[:max(0, int(k))]


# ===============================================================
#  Lance 后端
# ===============================================================
class LanceStore:
    """LanceDB 嵌入式持久化后端(§3.1 选型)

    lancedb / pyarrow 在本类构造时才 import —— 未安装抛 ImportError,
    由 open_store() 决定是回退还是硬失败(VECTORDB_BACKEND=lance 时
    硬失败, 避免生产静默退化成"重启即丢数据")。
    """

    backend = "lance"

    def __init__(self, uri: str = "", table: str = DEFAULT_TABLE,
                 dim: int = DEFAULT_DIM,
                 model_version: str = DEFAULT_MODEL_VERSION):
        import lancedb

        from core.vectordb.schema import to_arrow_schema

        self.uri = uri or os.environ.get("VECTORDB_URI", DEFAULT_URI)
        self.table = table
        self.model_version = model_version
        os.makedirs(self.uri, exist_ok=True)
        self._db = lancedb.connect(self.uri)

        names = set(self._db.table_names())
        want = int(dim)
        if table in names:
            self._tbl = self._db.open_table(table)
            got = self._stored_dim()
            # want <= 0 表示"未指定, 以已存在表的 schema 为准"
            # (重开旧表时调用方未必知道维度); 指定了就必须一致。
            if want > 0 and got and got != want:
                raise DimensionMismatch(
                    f"表 {table} 已绑定维度 {got}, 请求 {want} —— "
                    f"不同维度须换表名(§3.4 铁律)")
            self.dim = got or want
            if self.dim <= 0:
                raise DimensionMismatch(
                    f"表 {table} 读不到向量维度, 且未指定 dim")
        else:
            if want <= 0:
                raise DimensionMismatch(
                    f"新建表 {table} 必须指定 dim > 0(建表即锁死维度)")
            self.dim = want
            self._tbl = self._db.create_table(
                table, schema=to_arrow_schema(self.dim))
        logger.info("vectordb_lance_opened uri=%s table=%s dim=%s",
                    self.uri, self.table, self.dim)

    def _stored_dim(self) -> int:
        """从已存在表的 schema 读回 fixed_size_list 长度"""
        try:
            f = self._tbl.schema.field("embedding")
            return int(f.type.list_size)
        except Exception:
            return 0

    def _merge(self, rows: list[dict]) -> int:
        (self._tbl.merge_insert("id")
         .when_matched_update_all()
         .when_not_matched_insert_all()
         .execute(rows))
        return len(rows)

    def _filter(self, **eq) -> list[dict]:
        """等值过滤(走 pyarrow compute, 不依赖 Lance 查询 DSL)"""
        import pyarrow.compute as pc

        t = self._tbl.to_arrow()
        for col, val in eq.items():
            if not t.num_rows:
                break
            t = t.filter(pc.equal(t.column(col), val))
        return t.to_pylist()

    # --- 写 ---
    def upsert(self, items: Iterable[ProductVector | dict]) -> int:
        recs = _coerce(items, self.dim, self.model_version)
        if not recs:
            return 0
        return self._merge([r.row() for r in recs])

    def touch(self, ids: Iterable[str]) -> int:
        want = list({str(i) for i in ids})
        if not want:
            return 0
        import pyarrow as pa
        import pyarrow.compute as pc

        t = self._tbl.to_arrow()
        t = t.filter(pc.is_in(t.column("id"),
                              value_set=pa.array(want, pa.string())))
        rows = t.to_pylist()
        if not rows:
            return 0
        now = utcnow()
        for r in rows:
            r["access_count"] = int(r.get("access_count") or 0) + 1
            r["last_accessed"] = now
        return self._merge(rows)

    def delete(self, ids: Iterable[str]) -> int:
        want = [str(i) for i in ids]
        if not want:
            return 0
        before = self.count()
        self._tbl.delete("id IN ({})".format(
            ", ".join(_lit(i) for i in want)))
        return max(0, before - self.count())

    # --- 读 ---
    def count(self) -> int:
        try:
            return int(self._tbl.count_rows())
        except Exception:
            return len(self.all_rows())

    def all_rows(self) -> list[dict]:
        return self._tbl.to_arrow().to_pylist()

    def get(self, id_: str) -> dict | None:
        rows = self._filter(id=str(id_))
        return dict(rows[0]) if rows else None

    def checksum(self) -> str:
        return table_checksum(self.all_rows())

    def search(self, query: Sequence[float], k: int = DEFAULT_TOP_K,
               model_version: str = "", modality: str = "",
               category: str = "",
               min_score: float | None = None) -> list[Hit]:
        want = max(0, int(k))
        if not want:
            return []
        q = _check_query_dim(query, self.dim)
        mv = model_version or self.model_version
        expr = _where(mv, modality, category)
        # min_score 只能在取回后按分数过滤(Lance 无"距离阈值"稳定入口),
        # 若先 limit 再过滤会漏召回 → 有阈值时按 4 倍超采后再截断。
        # 表量级百~千, 超采成本可忽略。
        fetch = want * 4 if min_score is not None else want
        qb = self._tbl.search(q).metric("cosine").limit(fetch)
        if expr:
            qb = qb.where(expr)
        out: list[Hit] = []
        for r in qb.to_list():
            # cosine metric 下 _distance = 1 - 余弦相似度
            score = 1.0 - float(r.get("_distance") or 0.0)
            if min_score is not None and score < min_score:
                continue
            rec = {kk: vv for kk, vv in r.items()
                   if not kk.startswith("_")}
            out.append(Hit(str(r.get("id", "")), score, rec))
        out.sort(key=lambda h: (-h.score, h.id))
        return out[:want]


# ===============================================================
#  工厂
# ===============================================================
def open_store(backend: str = "", uri: str = "", table: str = "",
               dim: int = 0, model_version: str = ""):
    """打开向量存储(fail-open: lance 不可用自动回退 memory)

    backend 取值(env VECTORDB_BACKEND):
        auto    默认; 试 Lance, 缺依赖则回退内存并告警
        lance   强制 Lance; 缺依赖直接抛 ImportError(生产建议)
        memory  强制内存(测试/本地)
    """
    want = (backend or os.environ.get("VECTORDB_BACKEND", "auto")
            ).strip().lower()
    kwargs = {
        "uri": uri or os.environ.get("VECTORDB_URI", ""),
        "table": table or os.environ.get("VECTORDB_TABLE", DEFAULT_TABLE),
        "dim": int(dim or os.environ.get("VECTORDB_DIM", DEFAULT_DIM)),
        "model_version": (model_version
                          or os.environ.get("VECTORDB_MODEL_VERSION",
                                            DEFAULT_MODEL_VERSION)),
    }
    if want == "memory":
        return MemoryStore(**kwargs)
    try:
        return LanceStore(**kwargs)
    except ImportError as exc:
        if want == "lance":
            raise
        logger.warning("vectordb_lance_unavailable(回退内存后端): %s", exc)
        return MemoryStore(**kwargs)


# 模块级单例(懒加载; 对齐 llm_client.provider_client 惯例)
_store = None


def get_store(**kwargs):
    """取全局向量存储单例(首次调用时按 env 构造)"""
    global _store
    if _store is None:
        _store = open_store(**kwargs)
    return _store


def reset_store() -> None:
    """清空单例(测试隔离用)"""
    global _store
    _store = None
