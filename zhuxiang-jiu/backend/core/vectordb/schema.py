"""向量库 Schema 与纯函数工具(设计文档 §3.2 / §3.3 / §3.4)

本模块零第三方依赖(只用 pydantic + 标准库), 因此可在未安装
lancedb/pyarrow 的环境(CI、本地、生产后端启动路径)安全 import。

维度一致性铁律(§3.4):
    同一张表内所有向量维度必须一致。本地 bge-m3 与阿里 API
    Embedding 不可混写入同一列 —— 按 model_version 分表,
    建表时绑定 dim, 写入逐条校验, 检索只查同版本向量。
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterable
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

# 支持的模态(§3.2 modality 字段取值域)
MODALITIES = ("text", "image", "video", "audio")

# 已知模型的向量维度(§3.4); 未登记者由首次写入数据推断
KNOWN_MODEL_DIMS: dict[str, int] = {"bge-m3": 1024}

# 向量落盘量化位数: 6 位小数 —— float32 往返后 round(v, 6)
# 仍还原为同一值(float32 在 1e-1 量级误差约 7.5e-9), 保证
# 校验和在关机重启后可复现(§4 阶段一验收标准 1)
VEC_NDIGITS = 6

# 落盘字段顺序(checksum / 内存后端共用, 保证跨后端一致)
COLUMNS = ("id", "text", "modality", "embedding", "quality_score",
           "access_count", "last_accessed", "model_version",
           "is_synthetic", "cluster_id", "category", "tags", "created_at")

_TS_COLUMNS = ("last_accessed", "created_at")


def utcnow() -> datetime:
    """统一的时区感知当前时间(naive datetime 会被 pyarrow 拒绝)"""
    return datetime.now(UTC)


def quantize(vec: Iterable[float]) -> list[float]:
    """向量量化到 VEC_NDIGITS 位小数(落盘前的唯一收敛点)"""
    return [round(float(v), VEC_NDIGITS) for v in vec]


def cosine(a: Iterable[float], b: Iterable[float]) -> float:
    """余弦相似度(纯 Python, 对齐 kb57_embedding_service._cosine)

    维度不等或任一侧为零向量 → 返回 0.0(不抛异常, 检索 fail-open)。
    """
    a = list(a)
    b = list(b)
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if not na or not nb:
        return 0.0
    return dot / (na * nb)


def fingerprint(vec: Iterable[float]) -> str:
    """向量的稳定字符串指纹(定长 6 位小数, 供校验和拼接)"""
    return ",".join(f"{v:.6f}" for v in quantize(vec))


def table_checksum(rows: Iterable[dict]) -> str:
    """全表校验和(§4 阶段一验收标准 1: 重启后条数与校验和完全一致)

    只纳入稳定字段(id / model_version / 维度 / 量化向量), 刻意排除
    access_count、last_accessed 等每次检索都会变的运行时字段 ——
    否则"数据完整性"校验会被正常访问行为污染。
    """
    lines = []
    for r in rows:
        vec = r.get("embedding") or []
        lines.append("{}|{}|{}|{}".format(
            r.get("id", ""), r.get("model_version", ""),
            len(vec), fingerprint(vec)))
    lines.sort()
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


class ProductVector(BaseModel):
    """向量库核心记录(§3.2 原文 Schema)

    三组字段:
        基础      id / text / modality / embedding
        Agent 元数据(§3.3)  quality_score / access_count / last_accessed
                            / model_version / is_synthetic / cluster_id
        业务      category / tags / created_at

    污染溯源纪律(§5 风险 2): model_version 与 is_synthetic 是
    Agent 写操作的一键回滚唯一依据, 不得为空/默认放行。
    """

    # model_version 是 §3.2 原文字段名, 与 pydantic 保护命名空间
    # "model_" 撞名 —— 显式解除, 否则每次 import 都刷 UserWarning。
    model_config = ConfigDict(protected_namespaces=())

    id: str
    text: str = ""
    modality: str = "text"
    embedding: list[float] = Field(default_factory=list)

    # === Agent 维护元数据 ===
    quality_score: float = 1.0
    access_count: int = 0
    last_accessed: datetime = Field(default_factory=utcnow)
    model_version: str = ""
    is_synthetic: bool = False
    cluster_id: str | None = None

    # === 业务字段 ===
    category: str = ""
    tags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)

    @field_validator("modality")
    @classmethod
    def _check_modality(cls, v: str) -> str:
        if v not in MODALITIES:
            raise ValueError(
                f"modality 必须是 {MODALITIES} 之一, 得到 {v!r}")
        return v

    @field_validator("embedding")
    @classmethod
    def _quantize_embedding(cls, v: list[float]) -> list[float]:
        return quantize(v)

    @field_validator("quality_score")
    @classmethod
    def _clamp_quality(cls, v: float) -> float:
        """质量分收敛到 [0, 1](§3.3 Data Quality Agent 阈值判定前提)"""
        return max(0.0, min(1.0, float(v)))

    @field_validator("last_accessed", "created_at")
    @classmethod
    def _ensure_tz(cls, v: datetime) -> datetime:
        """补齐 UTC 时区 —— pyarrow timestamp(tz="UTC") 拒绝 naive"""
        return v if v.tzinfo else v.replace(tzinfo=UTC)

    @property
    def dim(self) -> int:
        return len(self.embedding)

    def row(self) -> dict:
        """落盘字典(datetime 保持原生对象, Lance / 内存后端通用)"""
        return {k: v for k, v in self.model_dump().items() if k in COLUMNS}


def to_arrow_schema(dim: int):
    """构造 Lance 表的 pyarrow schema(懒加载 pyarrow)

    Args:
        dim: 向量维度, 写成 fixed_size_list —— 维度在建表时即被
             schema 锁死, 是维度一致性铁律的第一道物理防线。

    Raises:
        ImportError: pyarrow 未安装(由 store.open_store 捕获回退)。
    """
    import pyarrow as pa

    fields = [
        ("id", pa.string()),
        ("text", pa.string()),
        ("modality", pa.string()),
        ("embedding", pa.list_(pa.float32(), dim)),
        ("quality_score", pa.float64()),
        ("access_count", pa.int64()),
        ("last_accessed", pa.timestamp("us", tz="UTC")),
        ("model_version", pa.string()),
        ("is_synthetic", pa.bool_()),
        ("cluster_id", pa.string()),
        ("category", pa.string()),
        ("tags", pa.list_(pa.string())),
        ("created_at", pa.timestamp("us", tz="UTC")),
    ]
    return pa.schema(fields)
