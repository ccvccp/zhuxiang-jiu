"""82号·小竹语音对话逻辑学习进化模型 P1(意图转移矩阵+置信度预载)

P1 边界(计划 §四):
    - 转移矩阵: 全局 + 角色条件双轨, 每意图 Top5 后继裁剪,
      EMA 平滑(α=0.3), 跨会话不连(会话边界断开)
    - 预判器: 当前意图(+角色) → Top3 后继 + 置信度(确定性
      计数归一化——LLM 不产概率, 全站铁律)
    - 预载策略: Top1 置信度 ≥0.6 且 mode≠off → 预热对应
      业务知识(封闭映射: 政策/样品/价格/库存)
    - 冷启动: 角色种子链兜底(dealer: 产品→政策→样品→联系;
      visitor/member: 产品→价格→产品)

存储(backend 双模式):
    voice82_matrix            全局矩阵 hash{from: JSON{to:ema}}
    voice82_matrix_role:{r}   角色条件矩阵(样本≥MIN_TRANSITIONS)
"""

import json
import logging
import os

from core.helpers import ts

from repositories.backend import (
    get_in_memory_store, get_redis_client, is_redis_mode, _k,
)
from repositories.xiaozhu_repository import Xiaozhu48Repository
from services.voice82_dialog_service import (
    EMA_ALPHA, Voice82DialogService, biz_domain_of,
)

logger = logging.getLogger("voice82_predict")

# ------------------------------------------------------------------
# 常量
# ------------------------------------------------------------------

TOP_K = 5                       # 每意图保留后继数(防无限膨胀)
TOP3_PREDICT = 3                # 预判输出条数
MIN_TRANSITIONS = 8             # 矩阵最小转移对数(不足回退)
PRELOAD_THRESHOLD = 0.60        # Top1 置信度预载阈

TBL_MATRIX = "voice82_matrix"
TBL_MATRIX_ROLE = "voice82_matrix_role"

# 预载动作映射(封闭集合——业务域 → 预热物; 集合外无预载)
PRELOAD_MAP = {
    "政策": {"kind": "policy_digest",
             "hint": "招商政策摘要(起订量/区域保护/返利/样品/物流/结算)"},
    "样品": {"kind": "sample_form",
             "hint": "样品表单预热(城市/联系方式/用途)"},
    "价格": {"kind": "price_card", "hint": "价格卡片"},
    "库存": {"kind": "stock_query", "hint": "库存查询预备"},
}

# 角色种子链(冷启动兜底——计划 §在线进化层 角色模板静态版;
# 键=业务域中文口径(与 biz_domain_of 输出一致))
ROLE_SEED_CHAINS = {
    "dealer": [("产品", "政策"), ("政策", "政策"),
               ("政策", "样品"), ("样品", "联系")],
    "visitor": [("产品", "价格"), ("价格", "产品"), ("产品", "闲聊")],
    "member": [("产品", "价格"), ("价格", "产品"), ("产品", "闲聊")],
    "agent": [("政策", "政策"), ("政策", "样品"), ("样品", "联系")],
}


def predict_mode_active() -> bool:
    """预载生效门槛(off 时 predict 仍可观测但 preload 恒 None)"""
    return os.environ.get(
        "VOICE82_MODE", "off").strip().lower() != "off"


def preload_for(goal_intent: str, confidence: float,
                mode_on: bool | None = None) -> dict | None:
    """预载决策(纯函数): 置信度达标 + 域命中 + 模式放行

    域判定: 完整意图经 biz_domain_of 归一; 域级目标(种子链
    输出如"政策")直配 PRELOAD_MAP
    """
    active = predict_mode_active() if mode_on is None else mode_on
    if not active or confidence < PRELOAD_THRESHOLD:
        return None
    domain = biz_domain_of(goal_intent)
    if domain not in PRELOAD_MAP:
        domain = goal_intent if goal_intent in PRELOAD_MAP else None
    if domain is None:
        return None
    return {"goal": goal_intent, "domain": domain,
            **PRELOAD_MAP[domain], "confidence": round(confidence, 3)}


def _normalize_top(counts: dict, top: int = TOP_K) -> dict:
    """计数 → Top-K 归一化概率(确定性, 截断后重归一)"""
    items = sorted(counts.items(), key=lambda x: -x[1])[:top]
    total = sum(n for _, n in items) or 1
    return {k: round(n / total, 4) for k, n in items}


def _seed_chain_rows(role: str) -> dict:
    """角色种子链 → 矩阵行形态(from → {to: prob})"""
    rows: dict = {}
    for frm, to in ROLE_SEED_CHAINS.get(role, []):
        rows.setdefault(frm, {})
        rows[frm][to] = rows[frm].get(to, 0) + 1
    return {k: _normalize_top(v) for k, v in rows.items()}


# ------------------------------------------------------------------
# 服务
# ------------------------------------------------------------------

class Voice82PredictService:
    """转移矩阵构建 + 目标预判(P1 零侵入——同 P0 只读 48号表)"""

    def __init__(self):
        self._dialog = Voice82DialogService()
        self._repo = Xiaozhu48Repository()

    # ---- KV 原语(同 P0 范式) ----

    async def _matrix_raw(self, table: str, key: str = "") -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.hgetall(_k(table, key) if key
                                       else _k(table))
            return {k.decode() if isinstance(k, bytes) else k:
                    v.decode() if isinstance(v, bytes) else v
                    for k, v in (raw or {}).items()}
        store = get_in_memory_store()
        rec = store.get(table) or {}
        if key and isinstance(rec, dict):
            rec = rec.get(key) or {}
        return dict(rec) if isinstance(rec, dict) else {}

    async def _save_matrix(self, table: str, rows: dict,
                           key: str = "") -> None:
        enc = {k: json.dumps(v, ensure_ascii=False)
               for k, v in rows.items()}
        if is_redis_mode():
            client = await get_redis_client()
            full = _k(table, key) if key else _k(table)
            if enc:
                await client.hset(full, mapping=enc)
            return
        store = get_in_memory_store()
        if key:
            store.setdefault(table, {})[key] = enc
        else:
            store[table] = enc

    # ---- 转移对提取(纯逻辑可测) ----

    @staticmethod
    def transition_pairs(turns_by_session: list[list[dict]]) -> list:
        """会话内相邻意图对(跨会话不连; 空意图跳过——
        跳过不连!防 A→(空)→B 被误记 A→B)"""
        pairs = []
        for turns in turns_by_session:
            seq = sorted(turns, key=lambda t: t.get("seq") or 0)
            intents = [str(t.get("intent") or "").strip()
                       for t in seq]
            intents = [i for i in intents if i]
            # 自环也计(同域重复问是真实信号)
            pairs.extend(zip(intents, intents[1:]))
        return pairs

    @staticmethod
    def _count_rows(pair_lists: list[list]) -> dict:
        """多会员转移对集合 → 行计数; 再与旧矩阵 EMA 混合"""
        counts: dict = {}
        for pairs in pair_lists:
            for a, b in pairs:
                counts.setdefault(a, {})
                counts[a][b] = counts[a].get(b, 0) + 1
        return counts

    @staticmethod
    def _ema_merge(old_rows: dict, new_counts: dict) -> dict:
        """新旧 EMA 混合: count_new*α + count_old*(1-α)"""
        merged = {k: dict(v) for k, v in old_rows.items()}
        for a, tos in new_counts.items():
            old_tos = merged.setdefault(a, {})
            for b, n in tos.items():
                old_tos[b] = round(
                    EMA_ALPHA * n + (1 - EMA_ALPHA) * old_tos.get(b, 0),
                    3)
        return merged

    # ---- 构建 ----

    async def rebuild_matrices(self, limit_members: int = 30) -> dict:
        """全量重建全局+角色矩阵(幂等; EMA 平滑)

        生产小机(1.6G)降载口径: ①不嵌套调 rebuild_profile(省
        一倍扫描, role 由本地 infer 快算) ②会员/轮次/评分流
        全限额——首跑 30 会员足够矩阵冷启动
        """
        members = await self._all_members(limit_members)
        global_pairs: list = []
        role_pairs: dict = {}
        roles_resolved: dict = {}
        for mid in members:
            sessions = await self._dialog._member_sessions(mid)
            sids = [s.get("sessionId") for s in sessions]
            turns = await self._dialog._member_turns(sids)
            by_session = []
            for sid in sids:
                by_session.append(
                    [t for t in turns
                     if str(t.get("sessionId")) == str(sid)])
            pairs = self.transition_pairs(by_session)
            if not pairs:
                continue
            # role 本地快算(零额外扫描)
            intent_counts: dict = {}
            for t in turns:
                it = str(t.get("intent") or "")
                if it:
                    intent_counts[it] = intent_counts.get(it, 0) + 1
            dom: dict = {}
            tot = 0
            for it, n in intent_counts.items():
                d = biz_domain_of(it)
                if d:
                    dom[d] = dom.get(d, 0) + n
                    tot += n
            dist = {d: n / (tot or 1) for d, n in dom.items()}
            from services.voice82_dialog_service import infer_role
            role, _, _ = infer_role(mid, intent_counts, dist)
            roles_resolved[mid] = role
            global_pairs.append(pairs)
            role_pairs.setdefault(role, []).append(pairs)

        # 全局矩阵(存 EMA 计数——读取时归一化, 量纲一致)
        old_g: dict = {}
        for k, v in (await self._matrix_raw(TBL_MATRIX)).items():
            try:
                old_g[k] = json.loads(v)
            except (TypeError, ValueError):
                pass
        merged_g = self._ema_merge(old_g, self._count_rows(global_pairs))
        rows_g = {k: v for k, v in merged_g.items() if v}
        await self._save_matrix(TBL_MATRIX, rows_g)

        # 角色矩阵(存计数)
        rows_by_role = {}
        for role, plist in role_pairs.items():
            counts = self._count_rows(plist)
            n_pairs = sum(sum(v.values()) for v in counts.values())
            if n_pairs < MIN_TRANSITIONS:
                rows_by_role[role] = {"_insufficient": True,
                                      "pairs": n_pairs}
                continue
            table_rows = {k: v for k, v in counts.items() if v}
            await self._save_matrix(TBL_MATRIX_ROLE, table_rows,
                                    key=role)
            rows_by_role[role] = {"rows": len(table_rows),
                                  "pairs": n_pairs}

        return {"success": True,
                "members": len(members), "roles": roles_resolved,
                "global": {"rows": len(rows_g),
                           "pairs": sum(
                               sum(1 for _ in v) for v
                               in global_pairs)},
                "roleMatrix": rows_by_role, "updatedAt": ts()}

    async def _all_members(self, limit: int) -> list:
        """扫 sessions 表收集会员集合(去重)"""
        seen: dict = {}
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k(
                "voice48", self._repo.TABLE_SESSIONS, "*"))
            for k in keys[:200]:       # 小机限额(200 会话扫描上限)
                raw = await client.hgetall(k)
                mid = (raw.get(b"memberId") or raw.get("memberId"))
                if mid is not None:
                    mid = mid.decode() if isinstance(mid, bytes) \
                        else mid
                    seen[mid] = 1
        else:
            store = get_in_memory_store()
            for rec in (store.get(self._repo.TABLE_SESSIONS)
                        or {}).values():
                mid = rec.get("memberId")
                if mid is not None:
                    seen[str(mid)] = 1
        out = []
        for mid in seen:
            try:
                out.append(int(mid))
            except (TypeError, ValueError):
                continue
        return out[:limit]

    # ---- 预判 ----

    async def _role_of(self, member_id) -> str:
        key = None
        try:
            prof = await self._dialog.get_profile(member_id)
            if prof.get("success"):
                return ((prof.get("profile") or {})
                        .get("role") or {}).get("role") or "member"
        except Exception:                     # noqa: BLE001 fail-soft
            return "visitor" if member_id is None else "member"
        return "visitor" if member_id is None else "member"

    async def predict(self, member_id, current_intent: str) -> dict:
        """目标预判: 角色矩阵 → 全局矩阵 → 角色种子链 三级回退"""
        intent = str(current_intent or "").strip()
        role = await self._role_of(member_id)

        source = "cold"
        row_key = intent
        rows: dict = {}
        # ① 角色条件矩阵
        role_rows = await self._matrix_raw(TBL_MATRIX_ROLE, key=role)
        if role_rows:
            try:
                rows = {k: json.loads(v) for k, v in role_rows.items()}
                source = "role"
            except (TypeError, ValueError):
                rows = {}
        # ② 全局矩阵
        if intent not in rows:
            g = await self._matrix_raw(TBL_MATRIX)
            try:
                g_rows = {k: json.loads(v) for k, v in g.items()}
            except (TypeError, ValueError):
                g_rows = {}
            if intent in g_rows:
                rows = g_rows
                source = "global"
        # ③ 种子链兜底(域级键——完整意图先直配, 再域归一)
        if intent not in rows:
            seed = _seed_chain_rows(role)
            hit = intent if intent in seed else (
                biz_domain_of(intent) or "")
            if hit and hit in seed:
                rows = seed
                row_key = hit
                source = "role_seed"

        tos_raw = rows.get(row_key) or {}
        tos = _normalize_top(tos_raw) if tos_raw else {}
        ranked = sorted(tos.items(), key=lambda x: -x[1])[:TOP3_PREDICT]
        predictions = [{"goal": g, "confidence": round(c, 3)}
                       for g, c in ranked]
        top1 = predictions[0] if predictions else None
        preload = (preload_for(top1["goal"], top1["confidence"])
                   if top1 else None)
        return {"success": True, "role": role, "source": source,
                "intent": intent, "predictions": predictions,
                "top1": top1, "preload": preload,
                "threshold": PRELOAD_THRESHOLD}

    async def matrix_view(self) -> dict:
        """观测面: 矩阵摘要(计数归一化展示)"""
        g = await self._matrix_raw(TBL_MATRIX)
        try:
            g_rows = {k: json.loads(v) for k, v in g.items()}
        except (TypeError, ValueError):
            g_rows = {}
        sample = {k: _normalize_top(v)
                  for k, v in list(g_rows.items())[:5] if v}
        return {"success": True,
                "global": {"rows": len(g_rows), "sample": sample},
                "topK": TOP_K, "minTransitions": MIN_TRANSITIONS,
                "preloadThreshold": PRELOAD_THRESHOLD,
                "updatedAt": ts()}
