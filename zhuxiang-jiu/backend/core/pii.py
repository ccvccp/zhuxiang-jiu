"""站内 PII 展示最小化公共件(2026-10-18 自 86号 hook86 提取推广)

治理范式(86号 P0-P3 验证过的三件套, 供含 PII 模块复用):
    ① 出口脱敏: 任何 API 出口不返回明文手机号/邮箱
    ② 数据主体权利: 导出(PIPL §45, 完整副本+审计留痕)/删除(§47)
    ③ 生命周期: TTL 到期匿名化 → 超期物理压实

适用边界(2026-10-18 摸底结论):
    - 营销留资类(无合同关系冷数据): 全套三件套
      (hook86 留资 / cooperation B端申请 / agent 经销商 / venue 场地)
    - 交易凭证类(运单/发票/订单收件人): 仅①出口脱敏, 法定保留不 TTL
    - 账号主体类(member/auth): 本人明文合理, 管理端已脱敏, 不 TTL
"""
from __future__ import annotations

import re

_PHONE_RE = re.compile(r"1[3-9][0-9]{9}")
_EMAIL_RE = re.compile(r"([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*"
                       r"@([A-Za-z0-9-]+)\.[A-Za-z0-9.-]+")


def mask_phone(phone: str) -> str:
    """手机号出口脱敏(前3后4): 138****1234"""
    return _PHONE_RE.sub(
        lambda m: m.group(0)[:3] + "****" + m.group(0)[7:], str(phone))


def mask_email(email: str) -> str:
    """邮箱出口脱敏(首字符+域名保留): a***@example.com"""
    def _sub(m: re.Match) -> str:
        return f"{m.group(1)}***@{m.group(2)}.{m.group(0).split('.')[-1]}"
    return _EMAIL_RE.sub(_sub, str(email))


def mask_contact(record: dict, *,
                 phone_keys: tuple[str, ...] = ("contactPhone",
                                                "contact_phone"),
                 email_keys: tuple[str, ...] = ("contactEmail",
                                                "contact_email")) -> dict:
    """就地脱敏记录中的联系人字段(返回同引用, 便于批量出口处理)"""
    for k in phone_keys:
        if record.get(k):
            record[k] = mask_phone(record[k])
    for k in email_keys:
        if record.get(k):
            record[k] = mask_email(record[k])
    return record
