"""44号·P1 开发者凭证服务(自助申请/审批/吊销/续期/校验)

计划(docs/44号_API智能管理模块实施计划.md §四):
    - 自助申请秒级: 默认自动批(API_KEY_AUTO_APPROVE=on)→
      status=active 即刻可用; off → pending 进管理队列人工批
    - 明文一次性: apiKey 仅签发响应返回一次, 存储只留 SHA-256
      摘要 + 前 8 位展示位; appCode 为应用标识(明文存储可回显)
    - 每 memberId 上限 5 把(pending/active 计入; 吊销/过期/
      驳回不占额度——自助清理腾位)
    - 懒过期: 校验/列表时 expireAt 已过且 status=active →
      标记 expired(不依赖定时任务)
    - 校验(中间件消费): apiKey 摘要单键取 + 双头匹配(appCode)
      + 状态/过期四重检查; 缓存 60s 由中间件层持有
"""

import logging
import os
from datetime import datetime, UTC, timedelta

from core.helpers import ts

from repositories.api_manager_repository import (
    ApiManager44Repository, key_digest, generate_api_key,
    generate_app_code,
)

logger = logging.getLogger(__name__)

# Key 有效期(天)——续期同口径延展
KEY_TTL_DAYS = 90
# 每会员 Key 上限(防滥用; 吊销/过期不占额度)
MAX_KEYS_PER_MEMBER = 5
# 套餐(P2 流量治理消费; P1 仅存储默认 free)
KEY_TIERS = ("free", "basic", "pro")


def auto_approve_enabled() -> bool:
    """申请即批开关(默认 on; off 时进管理队列)"""
    return os.environ.get(
        "API_KEY_AUTO_APPROVE", "on").strip().lower() != "off"


def _is_expired(record: dict) -> bool:
    """expireAt 是否已过(缺失视为未过期)"""
    expire_at = str(record.get("expireAt") or "")
    if not expire_at:
        return False
    try:
        dt = datetime.fromisoformat(expire_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return datetime.now(UTC) >= dt
    except ValueError:
        return False


class ApiKeyService:
    """开发者凭证服务(44号 P1)"""

    def __init__(self,
                 repo: ApiManager44Repository
                 = ApiManager44Repository()):
        self.repo = repo

    # --------------------------------------------------------
    # 自助流程(会员面)
    # --------------------------------------------------------

    async def apply_key(self, member_id: int, name: str) -> dict:
        """申请 API Key(默认自动批, 秒级发放)

        Returns:
            {keyId, apiKey(明文, 仅此一次), appCode, name, tier,
             status, expireAt}
        Raises:
            ValueError: name 空/超长, 超上限
        """
        name = (name or "").strip()
        if not name:
            raise ValueError("Key 名称不能为空")
        if len(name) > 50:
            raise ValueError("Key 名称不能超过 50 字符")

        # 上限检查(pending/active 占额度)
        keys = await self.repo.list_keys_by_member(member_id)
        occupying = [k for k in keys
                     if k.get("status") in ("pending", "active")
                     and not _is_expired(k)]
        if len(occupying) >= MAX_KEYS_PER_MEMBER:
            raise ValueError(
                f"每会员最多 {MAX_KEYS_PER_MEMBER} 把有效 Key"
                f"(吊销或过期后可再申请)")

        api_key = generate_api_key()
        app_code = generate_app_code()
        digest = key_digest(api_key)
        key_id = await self.repo.next_key_id()
        now = datetime.now(UTC)
        expire = now + timedelta(days=KEY_TTL_DAYS)
        status = "active" if auto_approve_enabled() else "pending"
        record = {
            "keyId": key_id, "memberId": member_id,
            "name": name,
            "keyPrefix": api_key[:8],          # 展示位
            "appCode": app_code, "tier": "free",
            "status": status,
            "createdAt": now.isoformat(),
            "expireAt": expire.isoformat(),
            "lastUsedAt": "", "requestCount": 0,
        }
        await self.repo.save_key(digest, record)
        logger.info("api44_key_issued keyId=%s member=%s name=%s "
                    "status=%s", key_id, member_id, name, status)
        return {
            "keyId": key_id,
            "apiKey": api_key,        # 明文仅此一次
            "appCode": app_code,
            "name": name, "tier": "free",
            "status": status,
            "expireAt": record["expireAt"],
            "note": "请立即保存 apiKey(仅本次返回, 之后不可查看)"
                    if status == "active" else
                    "审批开关已关闭(API_KEY_AUTO_APPROVE=off): "
                    "Key 处于待审批状态, 管理员批准后生效",
        }

    async def list_my_keys(self, member_id: int) -> dict:
        """我的 Key 列表(前缀展示/状态/用量; 懒过期收敛)"""
        keys = await self.repo.list_keys_by_member(member_id)
        out = []
        for k in keys:
            k = await self._lazy_expire(k)
            out.append({
                "keyId": k.get("keyId"),
                "name": k.get("name"),
                "keyPrefix": k.get("keyPrefix"),
                "appCode": k.get("appCode"),
                "tier": k.get("tier"),
                "status": k.get("status"),
                "createdAt": k.get("createdAt"),
                "expireAt": k.get("expireAt"),
                "lastUsedAt": k.get("lastUsedAt") or None,
                "requestCount": k.get("requestCount") or 0,
            })
        return {"success": True, "total": len(out), "keys": out}

    async def revoke_key(self, member_id: int,
                         key_id: int) -> dict:
        """自助吊销(仅本人的 Key)"""
        await self._my_key(member_id, key_id)
        d = await self.repo.digest_by_key_id(key_id)
        await self.repo.update_key_fields(
            d, {"status": "revoked"})
        _invalidate_key_cache(d)
        logger.info("api44_key_revoked keyId=%s by=member(%s)",
                    key_id, member_id)
        return {"success": True, "keyId": key_id,
                "status": "revoked"}

    async def renew_key(self, member_id: int, key_id: int) -> dict:
        """续期(expireAt 自当前时刻延展 90 天; revoked 不可续)"""
        rec = await self._my_key(member_id, key_id)
        if rec.get("status") == "revoked":
            raise ValueError("已吊销的 Key 不可续期(请重新申请)")
        rec = await self._lazy_expire(rec)
        d = await self.repo.digest_by_key_id(key_id)
        new_expire = (datetime.now(UTC)
                      + timedelta(days=KEY_TTL_DAYS)).isoformat()
        await self.repo.update_key_fields(
            d, {"status": "active", "expireAt": new_expire})
        _invalidate_key_cache(d)
        logger.info("api44_key_renewed keyId=%s member=%s",
                    key_id, member_id)
        return {"success": True, "keyId": key_id,
                "status": "active", "expireAt": new_expire}

    async def _my_key(self, member_id: int,
                     key_id: int) -> dict:
        """取本人的 Key 记录(越权拒绝)"""
        keys = await self.repo.list_keys_by_member(member_id)
        for k in keys:
            if k.get("keyId") == key_id:
                return k
        raise KeyError(f"Key {key_id} 不存在或不属于当前会员")

    # --------------------------------------------------------
    # 管理面
    # --------------------------------------------------------

    async def admin_list_keys(self, status: str = None,
                              member_id: int = None) -> dict:
        """全量 Key 列表(状态/会员过滤, 懒过期收敛)"""
        keys = await self.repo.list_all_keys(limit=10000)
        out = []
        for k in keys:
            k = await self._lazy_expire(k)
            if status and k.get("status") != status:
                continue
            if member_id is not None and \
                    k.get("memberId") != member_id:
                continue
            out.append({
                "keyId": k.get("keyId"),
                "memberId": k.get("memberId"),
                "name": k.get("name"),
                "keyPrefix": k.get("keyPrefix"),
                "tier": k.get("tier"),
                "status": k.get("status"),
                "createdAt": k.get("createdAt"),
                "expireAt": k.get("expireAt"),
                "lastUsedAt": k.get("lastUsedAt") or None,
                "requestCount": k.get("requestCount") or 0,
            })
        by_status: dict = {}
        for k in out:
            s = k["status"] or "unknown"
            by_status[s] = by_status.get(s, 0) + 1
        return {"success": True, "total": len(out), "keys": out,
                "byStatus": by_status}

    async def admin_approve(self, key_id: int) -> dict:
        """审批通过(pending → active, 有效期自批准起算)"""
        d = await self.repo.digest_by_key_id(key_id)
        if d is None:
            raise KeyError(f"Key {key_id} 不存在")
        rec = await self.repo.get_key(d)
        if rec.get("status") != "pending":
            raise ValueError(
                f"Key 状态为 {rec.get('status')}, 仅 pending "
                "状态可审批")
        new_expire = (datetime.now(UTC)
                      + timedelta(days=KEY_TTL_DAYS)).isoformat()
        await self.repo.update_key_fields(d, {
            "status": "active", "expireAt": new_expire})
        _invalidate_key_cache(d)
        logger.info("api44_key_approved keyId=%s", key_id)
        return {"success": True, "keyId": key_id,
                "status": "active", "expireAt": new_expire}

    async def admin_reject(self, key_id: int) -> dict:
        """驳回申请(pending → rejected)"""
        d = await self.repo.digest_by_key_id(key_id)
        if d is None:
            raise KeyError(f"Key {key_id} 不存在")
        rec = await self.repo.get_key(d)
        if rec.get("status") != "pending":
            raise ValueError(
                f"Key 状态为 {rec.get('status')}, 仅 pending "
                "状态可驳回")
        await self.repo.update_key_fields(
            d, {"status": "rejected"})
        _invalidate_key_cache(d)
        return {"success": True, "keyId": key_id,
                "status": "rejected"}

    async def admin_revoke(self, key_id: int) -> dict:
        """管理员吊销(任意状态 → revoked)"""
        d = await self.repo.digest_by_key_id(key_id)
        if d is None:
            raise KeyError(f"Key {key_id} 不存在")
        await self.repo.update_key_fields(
            d, {"status": "revoked"})
        _invalidate_key_cache(d)
        logger.info("api44_key_revoked keyId=%s by=admin", key_id)
        return {"success": True, "keyId": key_id,
                "status": "revoked"}

    # --------------------------------------------------------
    # 校验(中间件消费)
    # --------------------------------------------------------

    async def validate_key(self, api_key: str,
                           app_code: str) -> dict:
        """双头校验(无缓存版——直查存储)

        Returns:
            {"ok": True, "memberId": int, "keyId": int} 或
            {"ok": False, "reason": str}
        """
        if not api_key or not app_code:
            return {"ok": False,
                    "reason": "需要同时提供 X-Api-Key 与 "
                              "X-App-Code 双头凭证"}
        rec = await self.repo.get_key(key_digest(api_key))
        verdict = self._check_record(rec, app_code)
        if not verdict.get("ok") and rec is not None \
                and "已过期" in verdict.get("reason", ""):
            await self.repo.update_key_fields(
                key_digest(api_key), {"status": "expired"})
        return verdict

    async def validate_key_cached(self, api_key: str,
                                   app_code: str) -> dict:
        """缓存版校验(中间件热路径)

        60s TTL + 进程内主动失效(吊销/续期/审批即失效);
        负缓存(None 记录)防无效 Key 爆破打穿存储。
        """
        if not api_key or not app_code:
            return {"ok": False,
                    "reason": "需要同时提供 X-Api-Key 与 "
                              "X-App-Code 双头凭证"}
        import time
        digest = key_digest(api_key)
        cached = _KEY_CACHE.get(digest)
        if cached is not None:
            cached_at, rec = cached
            if time.monotonic() - cached_at < _KEY_CACHE_TTL:
                return self._check_record(rec, app_code)
        rec = await self.repo.get_key(digest)
        _KEY_CACHE[digest] = (time.monotonic(), rec)
        return self._check_record(rec, app_code)

    @staticmethod
    def _check_record(rec: dict | None,
                      app_code: str) -> dict:
        """纯内存记录检查(缓存命中路径零 IO)"""
        if rec is None:
            return {"ok": False, "reason": "API Key 无效"}
        if str(rec.get("appCode") or "") != app_code:
            return {"ok": False, "reason": "AppCode 不匹配"}
        if rec.get("status") != "active":
            return {"ok": False,
                    "reason": f"API Key 状态异常"
                              f"({rec.get('status')})"}
        if _is_expired(rec):
            return {"ok": False, "reason": "API Key 已过期"
                    "(可续期或重新申请)"}
        return {"ok": True, "memberId": rec.get("memberId"),
                "keyId": rec.get("keyId")}

    async def record_usage(self, key_id: int) -> None:
        """调用留痕(lastUsedAt + requestCount; 中间件通过后调)"""
        d = await self.repo.digest_by_key_id(key_id)
        if d is None:
            return
        from repositories.backend import (
            is_redis_mode, get_redis_client, _k,
        )
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("api44", "api44_keys", d)
            pipe = client.pipeline(transaction=False)
            pipe.hincrby(key, "requestCount", 1)
            pipe.hset(key, mapping={"lastUsedAt": ts()})
            await pipe.execute()
            return
        rec = await self.repo.get_key(d)
        if rec is not None:
            rec["requestCount"] = \
                (rec.get("requestCount") or 0) + 1
            rec["lastUsedAt"] = ts()
            await self.repo.save_key(d, rec)

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------

    async def _lazy_expire(self, record: dict) -> dict:
        """懒过期: active 且 expireAt 已过 → 标记 expired"""
        if record.get("status") == "active" \
                and _is_expired(record):
            d = await self.repo.digest_by_key_id(
                record.get("keyId"))
            if d:
                await self.repo.update_key_fields(
                    d, {"status": "expired"})
                _invalidate_key_cache(d)
                record["status"] = "expired"
        return record


# ============================================================
# Key 校验缓存(中间件层持有; 服务层吊销/续期/审批时失效)
# ============================================================

_KEY_CACHE: dict = {}          # {digest: (cached_at, record)}
_KEY_CACHE_TTL = 60.0          # 秒(计划: 吊销 60s 内收敛)


def _invalidate_key_cache(digest: str) -> None:
    """状态变更后失效缓存(本进程; 跨 worker 由 TTL 收敛)"""
    _KEY_CACHE.pop(digest, None)
