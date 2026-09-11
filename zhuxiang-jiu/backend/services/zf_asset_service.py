"""智法·AI智能法务大模型 P2 数据资产合规(zf_asset_service)

深化方案 §二(三) 数据资产合规: 从"隐私保护"到"数据要素流通"
    - 数据分类分级自动化: 扫描识别消费者个人信息/生产工艺秘方/
      供应链价格 → 《数据资产目录》+《合规使用边界》
    - 数据交易合约生成: 《数据许可协议》(用途限制+安全审计+
      收益分成三要素)
    - 跨境传输评估: 目标国法规匹配(GDPR/CCPA) → 《数据出境
      安全自评估报告》

铁律: 分级规则全确定性; 评估结论为建议, 跨境传输须人工申报。
"""

import logging

from services.zf_fabric_service import _ZfStore, _round2, _now_iso

logger = logging.getLogger(__name__)

# ============================================================
# 数据分类分级规则表(确定性——合规知识图谱数据域)
# ============================================================

CLASSIFY_RULES = [
    {"pattern": ("phone", "姓名", "身份证", "地址", "member",
                 "手机", "银行卡"),
     "level": "L4", "name": "消费者个人信息",
     "law": "个人信息保护法", "boundary": "禁止出库; 最小必要+脱敏"},
    {"pattern": ("发酵", "勾调", "工艺", "秘方", "基酒", "酿造",
                 "配方"),
     "level": "L3", "name": "生产工艺秘方",
     "law": "反不正当竞争法(商业秘密)", "boundary": "禁止对外; 保密协议+最小授权"},
    {"pattern": ("价格", "成本", "进价", "供应商", "毛利", "报价"),
     "level": "L2", "name": "供应链商业数据",
     "law": "反不正当竞争法", "boundary": "受限流通; 聚合脱敏后可授权"},
    {"pattern": ("商品名", "库存", "销量", "评分", "spu"),
     "level": "L1", "name": "公开经营数据",
     "law": "数据安全法(一般数据)", "boundary": "可流通; 标注来源+用途限制"},
]

LEVEL_NAME = {
    "L4": "核心(个人信息)", "L3": "重要(商业秘密)",
    "L2": "业务(受限流通)", "L1": "一般(可流通)",
}

# 跨境法规矩阵(确定性匹配——GDPR/CCPA/默认保守)
CROSSBORDER_MATRIX = {
    "EU": {"law": "GDPR",
           "requirements": ("充分性认定/SCC 标准合同条款+DPIA 评估",
                             "数据主体权利响应(访问/删除/可携带)",
                             "出境后当地政府调取披露机制"),
           "extraFields": ["scc", "dpia", "dpo"]},
    "US": {"law": "CCPA/CPRA",
           "requirements": ("消费者知情权与退出权(opt-out)",
                            "敏感个人信息限制处理",
                            "服务商合同条款(数据用途限制)"),
           "extraFields": ["optout", "sensitiveLimit"]},
    "OTHER": {"law": "目标国数据法规(保守口径)",
              "requirements": ("安全评估+合同约束(保守采用欧盟 SCC 口径)",
                               "最小化出境+境内留存副本",
                               "出境事件报告机制"),
              "extraFields": ["assessment", "localCopy"]},
}

REGION_NAME = {"EU": "欧盟(GDPR)", "US": "美国加州(CCPA/CPRA)",
               "OTHER": "其他国家(保守口径)"}


def _match_level(field: str) -> tuple[str, dict]:
    """字段名 → (等级, 规则)——确定性关键词匹配"""
    for rule in CLASSIFY_RULES:
        if any(p.lower() in field.lower() for p in rule["pattern"]):
            return rule["level"], rule
    return "L1", CLASSIFY_RULES[-1]


class ZfAssetService:
    """P2: 数据分类分级 + 数据许可协议 + 跨境传输评估"""

    def __init__(self, store: _ZfStore = None):
        self.store = store or _ZfStore()

    # ============================================================
    # 数据分类分级自动化
    # ============================================================

    async def classify(self, data_samples: list[str],
                       source: str = "") -> dict:
        """扫描数据字段 → 《数据资产目录》+《合规使用边界》

        规则(确定性关键词匹配): 个人信息→L4 禁止出库 /
        工艺秘方→L3 禁止对外 / 商业数据→L2 受限 /
        其余→L1 可流通。
        """
        if not data_samples:
            raise ValueError("数据样本不可为空")

        items = []
        for field in data_samples:
            level, rule = _match_level(str(field))
            items.append({
                "field": str(field), "level": level,
                "levelName": LEVEL_NAME[level],
                "category": rule["name"], "law": rule["law"],
                "boundary": rule["boundary"],
            })
        by_level = {}
        for it in items:
            by_level.setdefault(it["level"], []).append(it["field"])
        catalog_id = await self.store.next_id("catalog")
        record = {
            "catalogId": catalog_id, "source": source or "全站扫描",
            "totalFields": len(items),
            "summary": {lv: len(fs) for lv, fs in by_level.items()},
            "catalog": items,
            "boundaries": {
                lv: next(r["boundary"] for r in CLASSIFY_RULES
                         if r["level"] == lv)
                for lv in sorted(set(by_level) | {"L4", "L3", "L2", "L1"})
                if any(r["level"] == lv for r in CLASSIFY_RULES)},
            "note": "分级为确定性规则; L4/L3 级数据禁止对外授权",
            "classifiedAt": _now_iso(),
        }
        await self.store.save("asset_catalog", catalog_id, record)
        return record

    async def catalogs(self, limit: int = 20) -> list[dict]:
        rows = await self.store.list("asset_catalog")
        return sorted(rows, key=lambda r: r.get("classifiedAt", ""),
                      reverse=True)[:limit]

    # ============================================================
    # 数据交易合约生成(三要素)
    # ============================================================

    async def license_generate(self, asset_desc: str, data_level: str,
                               licensee: str, revenue_share: float = 0.1,
                               term_months: int = 12) -> dict:
        """《数据许可协议》(三要素: 用途限制+安全审计+收益分成)

        红线: L4/L3 级数据(个人信息/工艺秘方)禁止许可, 直接 409。
        """
        if not asset_desc or not licensee:
            raise ValueError("数据资产描述与被许可方不可为空")
        if data_level not in LEVEL_NAME:
            raise ValueError(f"数据等级无效({data_level})")
        if data_level in ("L4", "L3"):
            raise ValueError(f"{data_level} 级数据({LEVEL_NAME[data_level]})"
                             ")禁止对外许可——红线(个保法/商业秘密)")
        if not 0 < revenue_share <= 0.5:
            raise ValueError("收益分成比例须在 (0, 0.5]")
        if not 1 <= term_months <= 60:
            raise ValueError("许可期限须在 [1, 60] 月")

        license_id = await self.store.next_id("license")
        record = {
            "licenseId": license_id, "assetDesc": asset_desc,
            "dataLevel": data_level, "levelName": LEVEL_NAME[data_level],
            "licensee": licensee, "termMonths": term_months,
            "revenueShare": revenue_share,
            "elements": {
                "purposeRestriction": ("仅限约定用途(用途变更须书面同意); "
                                       "禁止再许可/禁止反向工程"),
                "securityAudit": ("被许可方接受年度安全审计+泄露 24h "
                                  "报告义务+审计日志留存 3 年"),
                "revenueShare": f"数据服务收益按 {_round2(revenue_share):.0%} 分成, "
                                 "季度对账",
            },
            "clauses": [
                "第1条 用途限制: 数据仅用于约定场景, 禁止转授权",
                "第2条 安全审计: 年度审计+重大泄露 24 小时报告",
                f"第3条 收益分成: {_round2(revenue_share):.0%} 季度结算",
                f"第4条 期限: {term_months} 个月, 到期自动终止",
                "第5条 违约: 超用途使用→立即终止+违约金+数据销毁证明",
            ],
            "note": "三要素模板(确定性); 签署须电子签+人工审批",
            "generatedAt": _now_iso(),
        }
        await self.store.save("licenses", license_id, record)
        return record

    # ============================================================
    # 跨境传输安全自评估
    # ============================================================

    async def cross_border_assess(self, region: str, data_levels: list[str],
                                  business_purpose: str = "") -> dict:
        """《数据出境安全自评估报告》(GDPR/CCPA 匹配)

        红线: 含 L4(个人信息) → 评估结论直接"不建议出境";
        L3(工艺秘方) → 强烈不建议(商业秘密出境)。
        """
        if region not in CROSSBORDER_MATRIX:
            raise ValueError(f"目标区域无效({region}); "
                             f"支持: {'/'.join(CROSSBORDER_MATRIX)}")
        if not data_levels:
            raise ValueError("涉及数据等级不可为空")
        for lv in data_levels:
            if lv not in LEVEL_NAME:
                raise ValueError(f"数据等级无效({lv})")

        matrix = CROSSBORDER_MATRIX[region]
        contains_l4 = "L4" in data_levels
        contains_l3 = "L3" in data_levels
        if contains_l4:
            conclusion = ("不建议出境(含个人信息, 须网信安全评估"
                          "+单独同意, 保守拒绝)")
        elif contains_l3:
            conclusion = ("强烈不建议出境(工艺秘方属商业秘密, "
                          "出境即丧失保护)")
        else:
            conclusion = "可以有条件出境(落实法规要求+合同约束)"

        assess_id = await self.store.next_id("crossborder")
        record = {
            "assessId": assess_id, "region": region,
            "regionName": REGION_NAME[region],
            "dataLevels": data_levels,
            "dataLevelNames": [LEVEL_NAME[lv] for lv in data_levels],
            "businessPurpose": business_purpose,
            "targetLaw": matrix["law"],
            "requirements": list(matrix["requirements"]),
            "checklist": {f: "须落实" for f in matrix["extraFields"]},
            "conclusion": conclusion,
            "passable": not (contains_l4 or contains_l3),
            "note": "评估为建议书; 实际出境须监管申报+人工审批",
            "assessedAt": _now_iso(),
        }
        await self.store.save("crossborders", assess_id, record)
        return record
