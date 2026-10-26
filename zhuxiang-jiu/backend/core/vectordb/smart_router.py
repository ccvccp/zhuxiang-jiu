"""额度感知双轨路由(设计文档 §2.3 Resource Orchestrator / §4 阶段一)

用户原文决策树 —— 逐条对应, 书写顺序即优先级:
    1. ali_quota_remaining < SAFE_THRESHOLD -> FORCE_GPU   额度告急
    2. query.requires_rerank or is_batch    -> GPU_BATCH   重型任务
    3. gpu_is_ready and gpu_load < 70%      -> LOCAL_GPU   GPU 空闲
    4. 其余                                 -> ALI_API_FALLBACK

★ 硬约束(阶段一"纯代码, 不涉及远端开机"):
    本模块只做决策与向量分发, 绝不触发 AutoDLPool.acquire() ——
    那会真实开机并产生按量计费。GPU 状态一律由调用方以 GpuState
    注入(阶段二 Resource Orchestrator Agent 负责探测与开机)。
    未注入时 GPU 视为不可用, 决策自然落到 ALI 轨。

fail-open 纪律(对齐 kb57_embedding_service / llm_client 既有惯例):
    额度采集失败   -> 不当"告急"处理(否则监控抖一下就开机烧钱)
    GPU 轨未接线   -> 无缝改走 ALI 轨, 零请求失败(§4 验收标准 2)
    GPU 轨执行失败 -> 同上, 并埋点 reason=gpu_failed
    ALI 轨也失败   -> 返回 None, 由调用方回退 2-gram
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from core.metrics import vectordb_embed_duration, vectordb_route_total

logger = logging.getLogger("vectordb")

# ---------------------------------------------------------------
# 四个路由出口(用户原文命名, 不得改)
# ---------------------------------------------------------------
FORCE_GPU = "FORCE_GPU"                      # 额度告急, 强制启用 GPU
GPU_BATCH = "GPU_BATCH"                      # rerank / 批量重型任务
LOCAL_GPU = "LOCAL_GPU"                      # GPU 空闲且就绪, 优先本地
ALI_API_FALLBACK = "ALI_API_FALLBACK"        # 默认走阿里 API 兜底

ROUTES = (FORCE_GPU, GPU_BATCH, LOCAL_GPU, ALI_API_FALLBACK)
GPU_ROUTES = (FORCE_GPU, GPU_BATCH, LOCAL_GPU)

# 阈值默认值(env 可覆盖; 键名见 backend/.env.example VECTORDB_* 段)
DEFAULT_QUOTA_SAFE_THRESHOLD = 0.2   # §5 风险1: 已用 80% 即切 GPU
DEFAULT_GPU_LOAD_CEILING = 0.7       # 用户原文: gpu_load < 70%

# 向量化函数签名: 入参文本批, 出参等长向量列表; 失败返回 None
EmbedFn = Callable[[list[str]], "list[list[float]] | None"]


def _env_float(key: str, default: float) -> float:
    """读 float 型 env(空/非法 -> 默认值并告警, 不抛异常)"""
    raw = (os.environ.get(key) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("vectordb_router_bad_env %s=%r(用默认 %s)",
                       key, raw, default)
        return default


# ===============================================================
#  决策输入 / 输出
# ===============================================================
@dataclass(frozen=True)
class QueryProfile:
    """决策树第 2 步的输入(用户原文 query 参数)

    requires_rerank  需要重排(GPU 侧 bge-reranker, API 轨无此能力)
    is_batch         离线批量任务(建库/回灌/蒸馏采样), 不是实时单查
    """

    requires_rerank: bool = False
    is_batch: bool = False

    @property
    def heavy(self) -> bool:
        """重型任务判定 —— 用户原文 query.requires_rerank or is_batch"""
        return self.requires_rerank or self.is_batch


@dataclass(frozen=True)
class QuotaState:
    """决策树第 1 步的输入: 阿里百炼包月套餐剩余额度

    remaining_ratio  剩余比例 0~1(1 = 全量未动用)。由 v3 采集器填充。
    unknown          采集失败 / 采集器未接线 —— 既不当"充足"也不当
                     "告急": 继续走后续判定并告警, 避免监控抖动开机。
    """

    remaining_ratio: float = 1.0
    unknown: bool = False


@dataclass(frozen=True)
class GpuState:
    """决策树第 3 步的输入

    ready  实例已开机且向量服务健康(由调用方探测后注入, 本模块不探测)
    load   GPU 利用率 0~1(nvidia-smi 口径)。默认 1.0 = 满载,
           保证"探测数据缺失"时绝不会被误判为空闲。
    """

    ready: bool = False
    load: float = 1.0


@dataclass(frozen=True)
class RouterState:
    """用户原文 current_state —— 决策所需的全部外部状态快照"""

    quota: QuotaState = field(default_factory=QuotaState)
    gpu: GpuState = field(default_factory=GpuState)


@dataclass(frozen=True)
class RouteDecision:
    """一次路由决策; route/reason 两维直接作为埋点标签"""

    route: str
    reason: str

    @property
    def uses_gpu(self) -> bool:
        """是否决策到 GPU 轨(三个 GPU 出口统称)"""
        return self.route in GPU_ROUTES


# ===============================================================
#  决策(纯函数)
# ===============================================================
def decide_route(query: QueryProfile,
                 current_state: RouterState) -> RouteDecision:
    """额度感知双轨路由 —— 用户原文决策树的逐条实现

    纯函数: 除读 env 阈值外不碰任何外部状态, 无 I/O, 不开机,
    不埋点(埋点由 SmartRouter 在实际执行后打一次, 避免重复计数)。
    """
    quota = current_state.quota
    gpu = current_state.gpu
    threshold = _env_float("VECTORDB_QUOTA_SAFE_THRESHOLD",
                           DEFAULT_QUOTA_SAFE_THRESHOLD)
    ceiling = _env_float("VECTORDB_GPU_LOAD_CEILING",
                         DEFAULT_GPU_LOAD_CEILING)

    # 1. 检查阿里套餐剩余额度 —— 额度告急, 强制启用 GPU
    if quota.unknown:
        logger.warning("vectordb_quota_unknown(按充足处理, 不触发开机)")
    elif quota.remaining_ratio < threshold:
        return RouteDecision(FORCE_GPU, "quota_low")

    # 2. 检查查询复杂度 —— 重型任务走 GPU
    if query.heavy:
        return RouteDecision(GPU_BATCH, "heavy_query")

    # 3. 检查 GPU 状态 —— 空闲且就绪, 优先本地(零边际成本)
    if gpu.ready and gpu.load < ceiling:
        return RouteDecision(LOCAL_GPU, "gpu_idle")

    # 4. 默认走阿里 API 兜底
    return RouteDecision(ALI_API_FALLBACK,
                         "gpu_busy" if gpu.ready else "gpu_not_ready")


# ===============================================================
#  分发器
# ===============================================================
class SmartRouter:
    """双轨向量分发器(§4 阶段一交付物: 冷启动期间无缝兜底)

    两个 embedder 均可注入, 默认值:
        ali_embed=None -> 懒加载 services.llm_client.provider_client.embed
                          (走既有 OpenAI 兼容 /embeddings 通道; 切阿里
                          百炼只需改 LLM_BASE_URL/EMBEDDING_MODEL 三个 env,
                          不引入 dashscope SDK)
        gpu_embed=None -> GPU 轨未接线, 自动改走 ALI 轨
        state=None     -> RouterState() 默认值(额度未采集 + GPU 未就绪)

    阶段二接 Resource Orchestrator Agent 时, 只需把 state 换成会真实
    探测额度与 GPU 的可调用对象, 本类逻辑不变。
    """

    def __init__(self, state: Callable[[], RouterState] | None = None,
                 ali_embed: EmbedFn | None = None,
                 gpu_embed: EmbedFn | None = None):
        self._state = state or (lambda: RouterState())
        self._ali = ali_embed
        self._gpu = gpu_embed

    # --- 埋点 ---
    @staticmethod
    def _bump(route: str, reason: str) -> None:
        vectordb_route_total.inc({"route": route, "reason": reason})

    # --- 决策 ---
    def decide(self, query: QueryProfile | None = None) -> RouteDecision:
        """只做决策并埋点一次(不产生向量, 用于看板/自检/阶段二验收)"""
        d = decide_route(query or QueryProfile(), self._state())
        self._bump(d.route, d.reason)
        return d

    # --- 向量化 ---
    def _ali_embed(self, texts: list[str]) -> list[list[float]] | None:
        """ALI 轨: 复用既有 provider_client.embed(不新建 HTTP 栈)"""
        fn = self._ali
        if fn is None:
            from services.llm_client import provider_client
            fn = provider_client.embed
        return fn(texts)

    def _run(self, fn: EmbedFn, texts: list[str],
             route: str) -> list[list[float]] | None:
        """执行一条轨并计时; 异常/条数不符一律降级为 None(fail-open)"""
        start = time.perf_counter()
        try:
            vecs = fn(list(texts))
        except Exception as exc:
            logger.warning("vectordb_embed_error route=%s: %s", route, exc)
            vecs = None
        vectordb_embed_duration.observe(time.perf_counter() - start,
                                        {"route": route})
        if vecs is not None and len(vecs) != len(texts):
            logger.warning("vectordb_embed_count_mismatch route=%s %s!=%s",
                           route, len(vecs), len(texts))
            return None
        return vecs

    def embed(self, texts: Sequence[str], *,
              requires_rerank: bool = False,
              is_batch: bool = False,
              ) -> tuple[list[list[float]] | None, RouteDecision]:
        """向量化一批文本, 返回 (向量列表 | None, 实际执行的路由)

        第二个返回值是"实际走通的轨", 不是"决策的轨" —— GPU 轨失败
        降级到 ALI 时, 返回 ALI_API_FALLBACK + reason=gpu_failed。
        向量全 None 时 fail-open 返回 None, 由调用方回退 2-gram。
        """
        words = [str(t) for t in texts if t]
        if not words:
            return None, RouteDecision(ALI_API_FALLBACK, "empty_input")

        decided = decide_route(
            QueryProfile(requires_rerank=requires_rerank, is_batch=is_batch),
            self._state())
        reason = decided.reason

        if decided.uses_gpu:
            if self._gpu is None:
                # 阶段一默认态: GPU 轨尚未接线 -> 无缝兜底, 零请求失败
                logger.warning("vectordb_gpu_unwired(无缝兜底 ALI 轨)")
                reason = "gpu_unwired"
            else:
                vecs = self._run(self._gpu, words, decided.route)
                if vecs is not None:
                    self._bump(decided.route, reason)
                    return vecs, decided
                reason = "gpu_failed"

        # ALI 轨(默认出口, 或从 GPU 轨降级而来)。
        # ★ 埋点纪律: 一次非空 embed 恰好 bump 一次 —— GPU 降级不提前计数,
        #   否则"降级 + ALI 又失败"会把同一次调用记成两条, 污染出口总量。
        vecs = self._run(self._ali_embed, words, ALI_API_FALLBACK)
        if vecs is None:
            logger.warning("vectordb_embed_all_tracks_failed(prior=%s)",
                           reason)
            reason = "embed_failed"
        self._bump(ALI_API_FALLBACK, reason)
        return vecs, RouteDecision(ALI_API_FALLBACK, reason)


# ---------------------------------------------------------------
# 模块级单例(懒加载; 对齐 store.get_store / provider_client 惯例)
# ---------------------------------------------------------------
_router: SmartRouter | None = None


def get_router(**kwargs) -> SmartRouter:
    """取全局路由单例(首次调用时构造; kwargs 见 SmartRouter.__init__)"""
    global _router
    if _router is None:
        _router = SmartRouter(**kwargs)
    return _router


def reset_router() -> None:
    """清空单例(测试隔离用)"""
    global _router
    _router = None
