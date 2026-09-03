"""财务管理 Repository

双模式(内存/Redis)透明切换,5 个数据实体:
    - vouchers(凭证):       主信息 + 分录列表 + 按账期索引
    - invoices(发票):       主信息 + 按账期索引
    - tax_declarations(税务申报): 主信息 + 按账期索引
    - payments(付款):       主信息 + 按类型索引
    - reconciliations(对账): 主信息, 按 date+type 唯一

锁键: finance:voucher:{voucherNo} / finance:payment:{paymentNo}
     (并发安全由 services 层负责)

Redis Key 设计:
    finance:voucher:{voucherNo}        Hash(凭证主信息)
    finance:voucher:entries:{voucherNo} List(分录 JSON 数组)
    finance:voucher:index:{period}     Set(该账期内的凭证号集合)
    finance:invoice:{invoiceNo}        Hash(发票主信息)
    finance:invoice:index:{period}     Set(该账期内的发票号集合)
    finance:tax:{declarationNo}        Hash(申报主信息)
    finance:tax:index:{period}         Set(该账期内的申报号集合)
    finance:payment:{paymentNo}        Hash(付款主信息)
    finance:payment:index:{type}       Set(该类型的付款号集合)
    finance:recon:{date}:{type}       Hash(对账记录, 主键 date+type 唯一)
    finance:voucher:seq                String(INCR 凭证序列)
    finance:invoice:seq                String(INCR 发票序列)
    finance:tax:seq                    String(INCR 申报序列)
    finance:payment:seq                String(INCR 付款序列)
"""

import contextlib
import json

from repositories.backend import is_redis_mode, get_redis_client, get_in_memory_store, _k


# ============================================================
# 序列号格式辅助
# ============================================================

def _voucher_no_prefix() -> str:
    """凭证号前缀: FZ + YYYYMMDD"""
    from datetime import datetime, timezone, timedelta
    # 项目时区为 Asia/Shanghai, 此处使用东 8 区当前日期
    now = datetime.now(timezone(timedelta(hours=8)))
    return f"FZ{now.strftime('%Y%m%d')}"


def _invoice_no_prefix() -> str:
    """发票号前缀: FP + YYYYMMDD"""
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone(timedelta(hours=8)))
    return f"FP{now.strftime('%Y%m%d')}"


def _declaration_no_prefix(period: str) -> str:
    """申报号前缀: SB + YYYYMM(period 形如 2026-07 或 202607)"""
    clean = period.replace("-", "").replace("/", "")
    return f"SB{clean}"


def _payment_no_prefix() -> str:
    """付款编号前缀: FK + YYYYMMDD"""
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone(timedelta(hours=8)))
    return f"FK{now.strftime('%Y%m%d')}"


def _normalize_period(period: str) -> str:
    """规范化账期为 YYYYMM(便于索引)"""
    return period.replace("-", "").replace("/", "")


class FinanceRepository:
    """财务数据访问(双模式)"""

    def __init__(self, store: dict = None):
        self.store = store if store is not None else get_in_memory_store()

    # ============================================================
    # 序列号生成(账期内 INCR, 序号在 prefix 后补 3 位)
    # ============================================================

    async def next_voucher_no(self) -> str:
        """生成下一个凭证号: FZ + YYYYMMDD + 3 位序号"""
        if is_redis_mode():
            return await self._redis_next_seq_no("voucher:seq", _voucher_no_prefix())
        return self._mem_next_seq_no("voucher", _voucher_no_prefix())

    async def next_invoice_no(self) -> str:
        """生成下一个发票号: FP + YYYYMMDD + 3 位序号"""
        if is_redis_mode():
            return await self._redis_next_seq_no("invoice:seq", _invoice_no_prefix())
        return self._mem_next_seq_no("invoice", _invoice_no_prefix())

    async def next_declaration_no(self, period: str) -> str:
        """生成下一个申报号: SB + YYYYMM + 3 位序号"""
        prefix = _declaration_no_prefix(period)
        if is_redis_mode():
            return await self._redis_next_seq_no("tax:seq", prefix)
        return self._mem_next_seq_no("tax", prefix)

    async def next_payment_no(self) -> str:
        """生成下一个付款编号: FK + YYYYMMDD + 3 位序号"""
        if is_redis_mode():
            return await self._redis_next_seq_no("payment:seq", _payment_no_prefix())
        return self._mem_next_seq_no("payment", _payment_no_prefix())

    def _mem_next_seq_no(self, kind: str, prefix: str) -> str:
        """内存模式序列号生成(按 prefix 维护计数器)"""
        self._ensure_store()
        counters = self.store.setdefault("_finance_seq", {})
        key = f"{kind}:{prefix}"
        counters[key] = counters.get(key, 0) + 1
        return f"{prefix}{counters[key]:03d}"

    async def _redis_next_seq_no(self, seq_key: str, prefix: str) -> str:
        """Redis 模式序列号生成(INCR 原子自增)"""
        client = await get_redis_client()
        # 同一 prefix 共用一个计数器, 避免不同日期 prefix 复用导致重号
        full_key = _k("finance", seq_key, prefix)
        n = await client.incr(full_key)
        return f"{prefix}{n:03d}"

    # ============================================================
    # 凭证(vouchers)
    # ============================================================

    async def save_voucher(self, voucher: dict) -> dict:
        """新增/覆盖凭证(含分录列表)"""
        if is_redis_mode():
            return await self._redis_save_voucher(voucher)
        return self._mem_save_voucher(voucher)

    async def get_voucher(self, voucher_no: str) -> dict | None:
        """按凭证号查询凭证(含分录)"""
        if is_redis_mode():
            return await self._redis_get_voucher(voucher_no)
        return self._mem_get_voucher(voucher_no)

    async def update_voucher_fields(self, voucher_no: str, fields: dict) -> dict:
        """部分字段更新,返回更新后的完整凭证(不含分录)

        Raises:
            KeyError: 凭证不存在
        """
        if is_redis_mode():
            return await self._redis_update_voucher_fields(voucher_no, fields)
        return self._mem_update_voucher_fields(voucher_no, fields)

    async def list_vouchers(self, period: str = None, voucher_type: str = None,
                            source: str = None, status: str = None) -> list[dict]:
        """列出凭证(可按 period/type/source/status 筛选)"""
        if is_redis_mode():
            return await self._redis_list_vouchers(period, voucher_type, source, status)
        return self._mem_list_vouchers(period, voucher_type, source, status)

    async def delete_voucher(self, voucher_no: str) -> None:
        """删除凭证

        Raises:
            KeyError: 凭证不存在
        """
        if is_redis_mode():
            return await self._redis_delete_voucher(voucher_no)
        return self._mem_delete_voucher(voucher_no)

    # ============================================================
    # 发票(invoices)
    # ============================================================

    async def save_invoice(self, invoice: dict) -> dict:
        """新增/覆盖发票"""
        if is_redis_mode():
            return await self._redis_save_invoice(invoice)
        return self._mem_save_invoice(invoice)

    async def get_invoice(self, invoice_no: str) -> dict | None:
        """按发票号查询"""
        if is_redis_mode():
            return await self._redis_get_invoice(invoice_no)
        return self._mem_get_invoice(invoice_no)

    async def update_invoice_fields(self, invoice_no: str, fields: dict) -> dict:
        """部分字段更新

        Raises:
            KeyError: 发票不存在
        """
        if is_redis_mode():
            return await self._redis_update_invoice_fields(invoice_no, fields)
        return self._mem_update_invoice_fields(invoice_no, fields)

    async def list_invoices(self, status: str = None, invoice_type: str = None,
                            period: str = None) -> list[dict]:
        """列出发票"""
        if is_redis_mode():
            return await self._redis_list_invoices(status, invoice_type, period)
        return self._mem_list_invoices(status, invoice_type, period)

    # ============================================================
    # 税务申报(tax_declarations)
    # ============================================================

    async def save_tax_declaration(self, decl: dict) -> dict:
        """新增/覆盖税务申报"""
        if is_redis_mode():
            return await self._redis_save_tax_declaration(decl)
        return self._mem_save_tax_declaration(decl)

    async def get_tax_declaration(self, decl_no: str) -> dict | None:
        """按申报号查询"""
        if is_redis_mode():
            return await self._redis_get_tax_declaration(decl_no)
        return self._mem_get_tax_declaration(decl_no)

    async def update_tax_fields(self, decl_no: str, fields: dict) -> dict:
        """部分字段更新

        Raises:
            KeyError: 申报不存在
        """
        if is_redis_mode():
            return await self._redis_update_tax_fields(decl_no, fields)
        return self._mem_update_tax_fields(decl_no, fields)

    async def list_tax_declarations(self, tax_type: str = None, period: str = None,
                                    status: str = None) -> list[dict]:
        """列出税务申报"""
        if is_redis_mode():
            return await self._redis_list_tax_declarations(tax_type, period, status)
        return self._mem_list_tax_declarations(tax_type, period, status)

    # ============================================================
    # 付款(payments)
    # ============================================================

    async def save_payment(self, payment: dict) -> dict:
        """新增/覆盖付款"""
        if is_redis_mode():
            return await self._redis_save_payment(payment)
        return self._mem_save_payment(payment)

    async def get_payment(self, payment_no: str) -> dict | None:
        """按付款编号查询"""
        if is_redis_mode():
            return await self._redis_get_payment(payment_no)
        return self._mem_get_payment(payment_no)

    async def update_payment_fields(self, payment_no: str, fields: dict) -> dict:
        """部分字段更新

        Raises:
            KeyError: 付款不存在
        """
        if is_redis_mode():
            return await self._redis_update_payment_fields(payment_no, fields)
        return self._mem_update_payment_fields(payment_no, fields)

    async def list_payments(self, payment_type: str = None,
                             status: str = None) -> list[dict]:
        """列出付款"""
        if is_redis_mode():
            return await self._redis_list_payments(payment_type, status)
        return self._mem_list_payments(payment_type, status)

    # ============================================================
    # 对账(reconciliations)
    # ============================================================

    async def save_reconciliation(self, recon: dict) -> dict:
        """新增/覆盖对账记录(主键 date+type 唯一)"""
        if is_redis_mode():
            return await self._redis_save_reconciliation(recon)
        return self._mem_save_reconciliation(recon)

    async def get_reconciliation(self, date: str, recon_type: str) -> dict | None:
        """按日期+类型查询对账记录"""
        if is_redis_mode():
            return await self._redis_get_reconciliation(date, recon_type)
        return self._mem_get_reconciliation(date, recon_type)

    async def update_recon_fields(self, date: str, recon_type: str, fields: dict) -> dict:
        """部分字段更新

        Raises:
            KeyError: 对账记录不存在
        """
        if is_redis_mode():
            return await self._redis_update_recon_fields(date, recon_type, fields)
        return self._mem_update_recon_fields(date, recon_type, fields)

    async def list_reconciliations(self, date: str = None, recon_type: str = None,
                                    status: str = None) -> list[dict]:
        """列出对账记录"""
        if is_redis_mode():
            return await self._redis_list_reconciliations(date, recon_type, status)
        return self._mem_list_reconciliations(date, recon_type, status)

    # ============================================================
    # 内存后端
    # ============================================================

    def _ensure_store(self):
        """确保 store 包含 finance 相关键"""
        if "finance_vouchers" not in self.store:
            self.store["finance_vouchers"] = {}
        if "finance_invoices" not in self.store:
            self.store["finance_invoices"] = {}
        if "finance_tax_declarations" not in self.store:
            self.store["finance_tax_declarations"] = {}
        if "finance_payments" not in self.store:
            self.store["finance_payments"] = {}
        if "finance_reconciliations" not in self.store:
            self.store["finance_reconciliations"] = {}
        if "_finance_seq" not in self.store:
            self.store["_finance_seq"] = {}

    # ---------- 凭证(内存) ----------

    def _mem_save_voucher(self, voucher: dict) -> dict:
        self._ensure_store()
        voucher_no = voucher["voucherNo"]
        period = _normalize_period(voucher.get("period", ""))
        self.store["finance_vouchers"][voucher_no] = voucher
        # 索引
        index_set = self.store.setdefault("_finance_voucher_index", {})
        index_set.setdefault(period, set()).add(voucher_no)
        return voucher

    def _mem_get_voucher(self, voucher_no: str) -> dict | None:
        self._ensure_store()
        return self.store["finance_vouchers"].get(voucher_no)

    def _mem_update_voucher_fields(self, voucher_no: str, fields: dict) -> dict:
        self._ensure_store()
        voucher = self.store["finance_vouchers"].get(voucher_no)
        if not voucher:
            raise KeyError(voucher_no)
        voucher.update(fields)
        return voucher

    def _mem_list_vouchers(self, period: str = None, voucher_type: str = None,
                            source: str = None, status: str = None) -> list[dict]:
        self._ensure_store()
        result = []
        for v in self.store["finance_vouchers"].values():
            if period and _normalize_period(v.get("period", "")) != _normalize_period(period):
                continue
            if voucher_type and v.get("type") != voucher_type:
                continue
            if source and v.get("source") != source:
                continue
            if status and v.get("status") != status:
                continue
            result.append(v)
        result.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
        return result

    def _mem_delete_voucher(self, voucher_no: str) -> None:
        self._ensure_store()
        if voucher_no not in self.store["finance_vouchers"]:
            raise KeyError(voucher_no)
        del self.store["finance_vouchers"][voucher_no]
        # 清理索引
        for v_set in self.store.get("_finance_voucher_index", {}).values():
            v_set.discard(voucher_no)

    # ---------- 发票(内存) ----------

    def _mem_save_invoice(self, invoice: dict) -> dict:
        self._ensure_store()
        invoice_no = invoice["invoiceNo"]
        period = _normalize_period(invoice.get("period", ""))
        self.store["finance_invoices"][invoice_no] = invoice
        index_set = self.store.setdefault("_finance_invoice_index", {})
        index_set.setdefault(period, set()).add(invoice_no)
        return invoice

    def _mem_get_invoice(self, invoice_no: str) -> dict | None:
        self._ensure_store()
        return self.store["finance_invoices"].get(invoice_no)

    def _mem_update_invoice_fields(self, invoice_no: str, fields: dict) -> dict:
        self._ensure_store()
        invoice = self.store["finance_invoices"].get(invoice_no)
        if not invoice:
            raise KeyError(invoice_no)
        invoice.update(fields)
        return invoice

    def _mem_list_invoices(self, status: str = None, invoice_type: str = None,
                            period: str = None) -> list[dict]:
        self._ensure_store()
        result = []
        for inv in self.store["finance_invoices"].values():
            if status and inv.get("status") != status:
                continue
            if invoice_type and inv.get("type") != invoice_type:
                continue
            if period and _normalize_period(inv.get("period", "")) != _normalize_period(period):
                continue
            result.append(inv)
        result.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
        return result

    # ---------- 税务申报(内存) ----------

    def _mem_save_tax_declaration(self, decl: dict) -> dict:
        self._ensure_store()
        decl_no = decl["declarationNo"]
        period = _normalize_period(decl.get("period", ""))
        self.store["finance_tax_declarations"][decl_no] = decl
        index_set = self.store.setdefault("_finance_tax_index", {})
        index_set.setdefault(period, set()).add(decl_no)
        return decl

    def _mem_get_tax_declaration(self, decl_no: str) -> dict | None:
        self._ensure_store()
        return self.store["finance_tax_declarations"].get(decl_no)

    def _mem_update_tax_fields(self, decl_no: str, fields: dict) -> dict:
        self._ensure_store()
        decl = self.store["finance_tax_declarations"].get(decl_no)
        if not decl:
            raise KeyError(decl_no)
        decl.update(fields)
        return decl

    def _mem_list_tax_declarations(self, tax_type: str = None, period: str = None,
                                    status: str = None) -> list[dict]:
        self._ensure_store()
        result = []
        for decl in self.store["finance_tax_declarations"].values():
            if tax_type and decl.get("taxType") != tax_type:
                continue
            if period and _normalize_period(decl.get("period", "")) != _normalize_period(period):
                continue
            if status and decl.get("status") != status:
                continue
            result.append(decl)
        result.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
        return result

    # ---------- 付款(内存) ----------

    def _mem_save_payment(self, payment: dict) -> dict:
        self._ensure_store()
        payment_no = payment["paymentNo"]
        ptype = payment.get("type", "")
        self.store["finance_payments"][payment_no] = payment
        index_set = self.store.setdefault("_finance_payment_index", {})
        index_set.setdefault(ptype, set()).add(payment_no)
        return payment

    def _mem_get_payment(self, payment_no: str) -> dict | None:
        self._ensure_store()
        return self.store["finance_payments"].get(payment_no)

    def _mem_update_payment_fields(self, payment_no: str, fields: dict) -> dict:
        self._ensure_store()
        payment = self.store["finance_payments"].get(payment_no)
        if not payment:
            raise KeyError(payment_no)
        payment.update(fields)
        return payment

    def _mem_list_payments(self, payment_type: str = None,
                            status: str = None) -> list[dict]:
        self._ensure_store()
        result = []
        for pmt in self.store["finance_payments"].values():
            if payment_type and pmt.get("type") != payment_type:
                continue
            if status and pmt.get("status") != status:
                continue
            result.append(pmt)
        result.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
        return result

    # ---------- 对账(内存) ----------

    def _mem_save_reconciliation(self, recon: dict) -> dict:
        self._ensure_store()
        date = recon["date"]
        recon_type = recon["type"]
        key = f"{date}:{recon_type}"
        self.store["finance_reconciliations"][key] = recon
        return recon

    def _mem_get_reconciliation(self, date: str, recon_type: str) -> dict | None:
        self._ensure_store()
        return self.store["finance_reconciliations"].get(f"{date}:{recon_type}")

    def _mem_update_recon_fields(self, date: str, recon_type: str, fields: dict) -> dict:
        self._ensure_store()
        key = f"{date}:{recon_type}"
        recon = self.store["finance_reconciliations"].get(key)
        if not recon:
            raise KeyError(key)
        recon.update(fields)
        return recon

    def _mem_list_reconciliations(self, date: str = None, recon_type: str = None,
                                    status: str = None) -> list[dict]:
        self._ensure_store()
        result = []
        for recon in self.store["finance_reconciliations"].values():
            if date and recon.get("date") != date:
                continue
            if recon_type and recon.get("type") != recon_type:
                continue
            if status and recon.get("status") != status:
                continue
            result.append(recon)
        result.sort(key=lambda x: (x.get("date", ""), x.get("type", "")), reverse=True)
        return result

    # ============================================================
    # Redis 后端
    # ============================================================

    # ---------- 凭证(Redis) ----------

    async def _redis_save_voucher(self, voucher: dict) -> dict:
        client = await get_redis_client()
        voucher_no = voucher["voucherNo"]
        period = _normalize_period(voucher.get("period", ""))
        entries = voucher.pop("entries", []) if "entries" in voucher else voucher.get("entries", [])
        # 主信息 Hash(不含分录, 分录单独存 List)
        mapping = self._serialize_hash({k: v for k, v in voucher.items() if k != "entries"})
        await client.hset(_k("finance", "voucher", voucher_no), mapping=mapping)
        # 分录 List(先清空再写入)
        entries_key = _k("finance", "voucher", "entries", voucher_no)
        await client.delete(entries_key)
        for entry in entries:
            await client.rpush(entries_key, json.dumps(entry, ensure_ascii=False))
        # 账期索引
        await client.sadd(_k("finance", "voucher", "index", period), voucher_no)
        # 重新挂回 entries(便于调用方继续使用)
        voucher["entries"] = entries
        return voucher

    async def _redis_get_voucher(self, voucher_no: str) -> dict | None:
        client = await get_redis_client()
        data = await client.hgetall(_k("finance", "voucher", voucher_no))
        if not data:
            return None
        voucher = self._deserialize_hash(data)
        entries_raw = await client.lrange(_k("finance", "voucher", "entries", voucher_no), 0, -1)
        voucher["entries"] = [json.loads(e) for e in entries_raw]
        return voucher

    async def _redis_update_voucher_fields(self, voucher_no: str, fields: dict) -> dict:
        client = await get_redis_client()
        key = _k("finance", "voucher", voucher_no)
        if not await client.exists(key):
            raise KeyError(voucher_no)
        await client.hset(key, mapping=self._serialize_hash(fields))
        data = await client.hgetall(key)
        return self._deserialize_hash(data)

    async def _redis_list_vouchers(self, period: str = None, voucher_type: str = None,
                                    source: str = None, status: str = None) -> list[dict]:
        client = await get_redis_client()
        # 按 period 索引快速过滤, 否则全扫
        if period:
            voucher_nos = await client.smembers(_k("finance", "voucher", "index", _normalize_period(period)))
            keys = [_k("finance", "voucher", vn) for vn in voucher_nos]
        else:
            keys = await client.keys(_k("finance", "voucher", "*"))
            # 排除索引键/分录键/seq 键
            keys = [k for k in keys if ":index:" not in k and ":entries:" not in k
                    and not k.endswith(":voucher:seq")]
        result = []
        for key in keys:
            data = await client.hgetall(key)
            if not data:
                continue
            voucher = self._deserialize_hash(data)
            if voucher_type and voucher.get("type") != voucher_type:
                continue
            if source and voucher.get("source") != source:
                continue
            if status and voucher.get("status") != status:
                continue
            result.append(voucher)
        result.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
        return result

    async def _redis_delete_voucher(self, voucher_no: str) -> None:
        client = await get_redis_client()
        key = _k("finance", "voucher", voucher_no)
        if not await client.exists(key):
            raise KeyError(voucher_no)
        voucher = self._deserialize_hash(await client.hgetall(key))
        # 移除索引
        period = _normalize_period(voucher.get("period", ""))
        await client.srem(_k("finance", "voucher", "index", period), voucher_no)
        await client.delete(key)
        await client.delete(_k("finance", "voucher", "entries", voucher_no))

    # ---------- 发票(Redis) ----------

    async def _redis_save_invoice(self, invoice: dict) -> dict:
        client = await get_redis_client()
        invoice_no = invoice["invoiceNo"]
        period = _normalize_period(invoice.get("period", ""))
        await client.hset(_k("finance", "invoice", invoice_no),
                          mapping=self._serialize_hash(invoice))
        await client.sadd(_k("finance", "invoice", "index", period), invoice_no)
        return invoice

    async def _redis_get_invoice(self, invoice_no: str) -> dict | None:
        client = await get_redis_client()
        data = await client.hgetall(_k("finance", "invoice", invoice_no))
        if not data:
            return None
        return self._deserialize_hash(data)

    async def _redis_update_invoice_fields(self, invoice_no: str, fields: dict) -> dict:
        client = await get_redis_client()
        key = _k("finance", "invoice", invoice_no)
        if not await client.exists(key):
            raise KeyError(invoice_no)
        await client.hset(key, mapping=self._serialize_hash(fields))
        return self._deserialize_hash(await client.hgetall(key))

    async def _redis_list_invoices(self, status: str = None, invoice_type: str = None,
                                    period: str = None) -> list[dict]:
        client = await get_redis_client()
        if period:
            nos = await client.smembers(_k("finance", "invoice", "index", _normalize_period(period)))
            keys = [_k("finance", "invoice", n) for n in nos]
        else:
            keys = await client.keys(_k("finance", "invoice", "*"))
            keys = [k for k in keys if ":index:" not in k and ":invoice:seq" not in k]
        result = []
        for key in keys:
            data = await client.hgetall(key)
            if not data:
                continue
            inv = self._deserialize_hash(data)
            if status and inv.get("status") != status:
                continue
            if invoice_type and inv.get("type") != invoice_type:
                continue
            result.append(inv)
        result.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
        return result

    # ---------- 税务申报(Redis) ----------

    async def _redis_save_tax_declaration(self, decl: dict) -> dict:
        client = await get_redis_client()
        decl_no = decl["declarationNo"]
        period = _normalize_period(decl.get("period", ""))
        await client.hset(_k("finance", "tax", decl_no),
                          mapping=self._serialize_hash(decl))
        await client.sadd(_k("finance", "tax", "index", period), decl_no)
        return decl

    async def _redis_get_tax_declaration(self, decl_no: str) -> dict | None:
        client = await get_redis_client()
        data = await client.hgetall(_k("finance", "tax", decl_no))
        if not data:
            return None
        decl = self._deserialize_hash(data)
        # detail 是嵌套结构, JSON 还原
        if "detail" in decl and isinstance(decl["detail"], str):
            with contextlib.suppress(TypeError, ValueError):
                decl["detail"] = json.loads(decl["detail"])
        return decl

    async def _redis_update_tax_fields(self, decl_no: str, fields: dict) -> dict:
        client = await get_redis_client()
        key = _k("finance", "tax", decl_no)
        if not await client.exists(key):
            raise KeyError(decl_no)
        await client.hset(key, mapping=self._serialize_hash(fields))
        decl = self._deserialize_hash(await client.hgetall(key))
        if "detail" in decl and isinstance(decl["detail"], str):
            with contextlib.suppress(TypeError, ValueError):
                decl["detail"] = json.loads(decl["detail"])
        return decl

    async def _redis_list_tax_declarations(self, tax_type: str = None, period: str = None,
                                            status: str = None) -> list[dict]:
        client = await get_redis_client()
        if period:
            nos = await client.smembers(_k("finance", "tax", "index", _normalize_period(period)))
            keys = [_k("finance", "tax", n) for n in nos]
        else:
            keys = await client.keys(_k("finance", "tax", "*"))
            keys = [k for k in keys if ":index:" not in k and not k.endswith(":tax:seq")]
        result = []
        for key in keys:
            data = await client.hgetall(key)
            if not data:
                continue
            decl = self._deserialize_hash(data)
            if "detail" in decl and isinstance(decl["detail"], str):
                with contextlib.suppress(TypeError, ValueError):
                    decl["detail"] = json.loads(decl["detail"])
            if tax_type and decl.get("taxType") != tax_type:
                continue
            if status and decl.get("status") != status:
                continue
            result.append(decl)
        result.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
        return result

    # ---------- 付款(Redis) ----------

    async def _redis_save_payment(self, payment: dict) -> dict:
        client = await get_redis_client()
        payment_no = payment["paymentNo"]
        ptype = payment.get("type", "")
        await client.hset(_k("finance", "payment", payment_no),
                          mapping=self._serialize_hash(payment))
        await client.sadd(_k("finance", "payment", "index", ptype), payment_no)
        return payment

    async def _redis_get_payment(self, payment_no: str) -> dict | None:
        client = await get_redis_client()
        data = await client.hgetall(_k("finance", "payment", payment_no))
        if not data:
            return None
        payment = self._deserialize_hash(data)
        if "approvals" in payment and isinstance(payment["approvals"], str):
            with contextlib.suppress(TypeError, ValueError):
                payment["approvals"] = json.loads(payment["approvals"])
        return payment

    async def _redis_update_payment_fields(self, payment_no: str, fields: dict) -> dict:
        client = await get_redis_client()
        key = _k("finance", "payment", payment_no)
        if not await client.exists(key):
            raise KeyError(payment_no)
        await client.hset(key, mapping=self._serialize_hash(fields))
        payment = self._deserialize_hash(await client.hgetall(key))
        if "approvals" in payment and isinstance(payment["approvals"], str):
            with contextlib.suppress(TypeError, ValueError):
                payment["approvals"] = json.loads(payment["approvals"])
        return payment

    async def _redis_list_payments(self, payment_type: str = None,
                                    status: str = None) -> list[dict]:
        client = await get_redis_client()
        if payment_type:
            nos = await client.smembers(_k("finance", "payment", "index", payment_type))
            keys = [_k("finance", "payment", n) for n in nos]
        else:
            keys = await client.keys(_k("finance", "payment", "*"))
            keys = [k for k in keys if ":index:" not in k and ":payment:seq" not in k]
        result = []
        for key in keys:
            data = await client.hgetall(key)
            if not data:
                continue
            pmt = self._deserialize_hash(data)
            if "approvals" in pmt and isinstance(pmt["approvals"], str):
                with contextlib.suppress(TypeError, ValueError):
                    pmt["approvals"] = json.loads(pmt["approvals"])
            if status and pmt.get("status") != status:
                continue
            result.append(pmt)
        result.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
        return result

    # ---------- 对账(Redis) ----------

    async def _redis_save_reconciliation(self, recon: dict) -> dict:
        client = await get_redis_client()
        date = recon["date"]
        recon_type = recon["type"]
        await client.hset(_k("finance", "recon", date, recon_type),
                          mapping=self._serialize_hash(recon))
        return recon

    async def _redis_get_reconciliation(self, date: str, recon_type: str) -> dict | None:
        client = await get_redis_client()
        data = await client.hgetall(_k("finance", "recon", date, recon_type))
        if not data:
            return None
        recon = self._deserialize_hash(data)
        for k in ("orderSide", "paySide", "bankSide", "differences"):
            if k in recon and isinstance(recon[k], str):
                with contextlib.suppress(TypeError, ValueError):
                    recon[k] = json.loads(recon[k])
        return recon

    async def _redis_update_recon_fields(self, date: str, recon_type: str, fields: dict) -> dict:
        client = await get_redis_client()
        key = _k("finance", "recon", date, recon_type)
        if not await client.exists(key):
            raise KeyError(f"{date}:{recon_type}")
        await client.hset(key, mapping=self._serialize_hash(fields))
        recon = self._deserialize_hash(await client.hgetall(key))
        for k in ("orderSide", "paySide", "bankSide", "differences"):
            if k in recon and isinstance(recon[k], str):
                with contextlib.suppress(TypeError, ValueError):
                    recon[k] = json.loads(recon[k])
        return recon

    async def _redis_list_reconciliations(self, date: str = None, recon_type: str = None,
                                            status: str = None) -> list[dict]:
        client = await get_redis_client()
        if date and recon_type:
            keys = [_k("finance", "recon", date, recon_type)]
        elif date:
            all_keys = await client.keys(_k("finance", "recon", date, "*"))
            keys = all_keys
        else:
            keys = await client.keys(_k("finance", "recon", "*"))
        result = []
        for key in keys:
            data = await client.hgetall(key)
            if not data:
                continue
            recon = self._deserialize_hash(data)
            if recon_type and recon.get("type") != recon_type:
                continue
            if status and recon.get("status") != status:
                continue
            for k in ("orderSide", "paySide", "bankSide", "differences"):
                if k in recon and isinstance(recon[k], str):
                    with contextlib.suppress(TypeError, ValueError):
                        recon[k] = json.loads(recon[k])
            result.append(recon)
        result.sort(key=lambda x: (x.get("date", ""), x.get("type", "")), reverse=True)
        return result

    # ============================================================
    # 序列化辅助(Redis Hash 要求 value 为 str/int/float)
    # ============================================================

    def _serialize_hash(self, data: dict) -> dict:
        """将 dict 序列化为 Redis Hash 兼容的 mapping

        - None 跳过
        - bool → 0/1
        - list/dict → JSON 字符串
        - int/float 原样保留(redis-py 支持)
        - 其他 → str
        """
        result = {}
        for k, v in data.items():
            if v is None:
                continue
            if isinstance(v, bool):
                result[k] = 1 if v else 0
            elif isinstance(v, (list, dict)):
                result[k] = json.dumps(v, ensure_ascii=False)
            elif isinstance(v, (int, float)):
                result[k] = v
            else:
                result[k] = str(v)
        return result

    def _deserialize_hash(self, data: dict) -> dict:
        """将 Redis hgetall 返回的 dict 反序列化

        数值字段自动还原为 float, 布尔字段还原为 int(0/1)。
        嵌套结构由调用方根据字段名单独 JSON 反序列化。
        """
        def _to_number(v):
            if v is None:
                return None
            try:
                if "." in str(v):
                    return float(v)
                return int(v)
            except (TypeError, ValueError):
                return v

        result = dict(data)
        # 数值字段白名单(金额/数量/计数)
        numeric_fields = {
            "amount", "amountWithTax", "amountWithoutTax", "taxAmount",
            "paidAmount", "refundedAmount", "actualAmount",
            "vatAmount", "consumptionTaxAmount", "incomeTaxAmount",
            "surtaxAmount", "totalTaxAmount",
            "orderAmount", "payAmount", "bankAmount", "diffAmount",
            "rate", "quantity", "totalQuantity",
        }
        for k in numeric_fields:
            if k in result:
                result[k] = _to_number(result[k])
        # 布尔/状态字段保持原样
        return result
