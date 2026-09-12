"""智客·AI智能会员大模型 P0 洞察中枢(zk_insight_service)

「意图驱动」入口(对齐 zy_qa_service 范式): 自然语言问会员 → 意图路由
(关键词确定性) → 织物统计数字 → 句子模板拼接(数字 100% 插值)。

三大能力:
    - NL 问答: 五域(会员量/等级分布/消费/积分/流失预警)关键词路由
    - 会员健康度: 五维(活跃度/消费力/等级成长/积分活力/生命周期)
      分段 Sigmoid 评分(对齐 xinzhi_radar 三步法①, |z|>30 饱和防溢出)
    - RFM 画像: R/F/M 三轴五档确定性分层(重要价值/潜力/流失风险等)

铁律: LLM 禁入判定链——同输入同输出, 数字全部来自织物查询层。
边界: 只做消费频次/等级生命周期/积分行为画像, 不涉及信值五维。
"""

import logging
import math

from services.zk_fabric_service import (
    _ZkStore, ZkFabricService, _round2, _now_iso, _days_since, status_of)
from services.member_service import (
    LEVEL_THRESHOLDS, KEEP_LEVEL_CONSUME, LEVEL_NAMES)

logger = logging.getLogger(__name__)

# ============================================================
# P0 常量(确定性, 全部可解释)
# ============================================================

# 意图域关键词(命中优先级从上到下)
QA_DOMAINS = (
    ("churn", ("流失", "预警", "沉睡", "唤醒")),
    ("points", ("积分", "竹叶")),
    ("consume", ("消费", "客单", "销售额", "客单价")),
    ("level", ("等级", "成长值", "保级", "分布")),
    ("member", ("会员", "多少", "总量", "注册")),
)

DOMAIN_NAME = {
    "member": "会员量", "level": "等级分布", "consume": "消费",
    "points": "积分", "churn": "流失预警",
}

# 沉睡口径(QA 流失域与唤醒共用)
SLEEP_DAYS = 60

# 健康度五维 Sigmoid 参数(x0=基准值, k=敏感度; 对齐雷达范式)
HEALTH_SIGMOID = {
    "activity": {"x0": 2.0, "k": 1.0},     # 活跃度: 近30天行为频次
    "spending": {"x0": 1.0, "k": 3.0},     # 消费力: 月均消费/等级应达
    "growth": {"x0": 2.0, "k": 0.30},      # 等级成长: 成长值/日 斜率
    "points": {"x0": 50.0, "k": 0.02},     # 积分活力: 月均积分流量
    "lifecycle": {"x0": 6.0, "k": 0.15},   # 生命周期: 注册月数
}

# 五维权重(合计 1.0)
HEALTH_WEIGHTS = {
    "activity": 0.25, "spending": 0.25, "growth": 0.15,
    "points": 0.15, "lifecycle": 0.20,
}

HEALTH_DIM_NAMES = {
    "activity": "活跃度", "spending": "消费力", "growth": "等级成长",
    "points": "积分活力", "lifecycle": "生命周期",
}

# L1 无保级要求时的月消费基准(元/月, 确定性假设)
L1_MONTHLY_BASELINE = 25.0

# RFM 分档阈值(五档, 确定性)
R_TIERS = ((7, 5), (30, 4), (90, 3), (180, 2))    # 距今天数→档(其余 1)
F_TIERS = ((5, 5), (3, 4), (2, 3), (1, 2))        # 近30天单量→档(0→1)
M_TIERS = ((1000, 5), (500, 4), (200, 3), (50, 2))  # 月均消费→档(其余 1)


def sigmoid_score(kind: str, x: float) -> float:
    """分段 Sigmoid 归一化: 100/(1+exp(−k×(x−x0)))

    |z|>30 直接走饱和段(防 exp 溢出), 对齐 68 号雷达算法范式;
    确定性——同输入同输出, 无 LLM。
    """
    p = HEALTH_SIGMOID[kind]
    z = p["k"] * (float(x) - p["x0"])
    if z > 30:
        return 100.0
    if z < -30:
        return 0.0
    return round(100.0 / (1.0 + math.exp(-z)), 1)


def _tier_down(value: float, tiers: tuple) -> int:
    """低值高档(如 R: 距今越近档越高): value ≤ bound → 档位"""
    for bound, score in tiers:
        if value <= bound:
            return score
    return 1


def _tier_up(value: float, tiers: tuple) -> int:
    """高值高档(如 F/M: 越大档越高): value ≥ bound → 档位"""
    for bound, score in tiers:
        if value >= bound:
            return score
    return 1


def _months_since(iso: str) -> float:
    """注册至今月数(下限 1, 封顶 24——防远古账号拉低月均)"""
    days = _days_since(iso)
    return max(1.0, min(24.0, days / 30.0))


class ZkInsightService:
    """P0: NL 问答 + 会员健康度 + RFM 画像"""

    def __init__(self, fabric: ZkFabricService = None,
                 store: _ZkStore = None):
        self.fabric = fabric or ZkFabricService()
        self.store = store or self.fabric.store

    # ============================================================
    # NL 问答(意图路由 → 织物统计 → 模板拼接)
    # ============================================================

    async def qa(self, text: str) -> dict:
        """自然语言会员问答(数字全部来自织物查询层)

        Returns:
            {domain, intent, answer, dataSnapshot, reasoning}
        """
        question = (text or "").strip()
        domain = self._route(question)
        if not domain:
            return {
                "domain": "unknown", "intent": "未识别",
                "answer": ("暂未能识别该问题的会员域意图; 可试试: "
                           "「现在有多少会员」「等级分布如何」"
                           "「总消费多少」「积分总量多少」"
                           "「有多少沉睡流失风险会员」。"),
                "dataSnapshot": {},
                "reasoning": "五域关键词均未命中→引导语(确定性)",
            }
        snap = await self.fabric.overview()
        handlers = {
            "member": self._answer_member,
            "level": self._answer_level,
            "consume": self._answer_consume,
            "points": self._answer_points,
            "churn": self._answer_churn,
        }
        result = await handlers[domain](snap)
        result["reasoning"] = (f"意图路由→{DOMAIN_NAME[domain]}域; "
                               f"取数织物总览(会员 {snap['memberTotal']} 名/"
                               f"有效订单 {snap['validOrderTotal']} 单); "
                               f"数字全部来自查询层")
        return result

    @staticmethod
    def _route(question: str) -> str | None:
        """关键词意图路由(优先级: 流失>积分>消费>等级>会员量)"""
        for domain, keywords in QA_DOMAINS:
            if any(kw in question for kw in keywords):
                return domain
        return None

    @staticmethod
    async def _answer_member(snap: dict) -> dict:
        dist = snap["statusDistribution"]
        return {
            "domain": "member", "intent": "会员量查询",
            "answer": (f"当前会员总量 {snap['memberTotal']} 名"
                       f"(正常 {dist['active']} / 禁用 {dist['disabled']}); "
                       f"累计订单 {snap['orderTotal']} 单, "
                       f"其中有效订单 {snap['validOrderTotal']} 单。"),
            "dataSnapshot": {k: snap[k] for k in
                             ("memberTotal", "orderTotal",
                              "validOrderTotal")},
        }

    @staticmethod
    async def _answer_level(snap: dict) -> dict:
        dist = snap["levelDistribution"]
        parts = " / ".join(
            f"L{lv} {dist[lv]} 名" for lv in sorted(dist))
        top_lv = max(dist, key=lambda k: dist[k]) if dist else 1
        return {
            "domain": "level", "intent": "等级分布查询",
            "answer": (f"等级分布: {parts}; 人数最多的是 "
                       f"L{top_lv}({LEVEL_NAMES.get(top_lv, '')}) "
                       f"{dist[top_lv]} 名。等级门槛 L1-L5: "
                       f"0/500/3000/6999/9999 成长值。"),
            "dataSnapshot": {"levelDistribution": dist},
        }

    @staticmethod
    async def _answer_consume(snap: dict) -> dict:
        return {
            "domain": "consume", "intent": "消费查询",
            "answer": (f"有效订单总消费 ¥{snap['totalConsume']}"
                       f"(口径: PAID/SHIPPED/RECEIVED/COMPLETED 实付额), "
                       f"有效单 {snap['validOrderTotal']} 单, "
                       f"平均消费 ¥{snap['avgConsume']}/单。"),
            "dataSnapshot": {k: snap[k] for k in
                             ("totalConsume", "avgConsume",
                              "validOrderTotal")},
        }

    @staticmethod
    async def _answer_points(snap: dict) -> dict:
        total = snap["pointsTotal"]
        count = snap["memberTotal"]
        per_member = _round2(total / count) if count else 0.0
        return {
            "domain": "points", "intent": "积分查询",
            "answer": (f"会员档案积分总量 {total} 竹叶"
                       f"(会员 {count} 名); "
                       f"人均持有 {per_member} 竹叶。"
                       f"积分运营详情可用 /api/member-ai/points-analysis。"),
            "dataSnapshot": {"pointsTotal": total,
                             "memberTotal": count,
                             "perMember": per_member},
        }

    async def _answer_churn(self, snap: dict) -> dict:
        sleeping = await self._sleeping_members()
        total = snap["memberTotal"]
        ratio = _round2(len(sleeping) / total * 100) if total else 0.0
        return {
            "domain": "churn", "intent": "流失预警查询",
            "answer": (f"近 {SLEEP_DAYS} 天无有效消费的沉睡会员 "
                       f"{len(sleeping)} 名(占比 {ratio}%); "
                       f"三信号分级预警可用 /api/member-ai/churn-scan, "
                       f"唤醒建议书可用 /api/member-ai/wakeup-suggest。"),
            "dataSnapshot": {"sleepingCount": len(sleeping),
                             "memberTotal": total, "sleepRatio": ratio},
        }

    async def _sleeping_members(self) -> list[dict]:
        """沉睡会员(近 60 天无有效消费; 禁用账号不计, 确定性口径)"""
        sleeping = []
        for m in await self.fabric.members():
            if status_of(m) == 0:
                continue
            orders = self.fabric.valid_orders(
                await self.fabric.member_orders(m["id"]))
            last = max((order_paid_of(o) for o in orders), default="")
            if _days_since(last) >= SLEEP_DAYS:
                sleeping.append(m)
        return sleeping

    # ============================================================
    # 会员健康度(五维分段 Sigmoid 评分)
    # ============================================================

    async def health(self, member_id) -> dict:
        """会员健康度五维评分(各维 0-100 + 加权总分, 带 formula)

        维度口径(全部确定性):
            - 活跃度: 近 30 天有效消费单量 + 近 30 天登录(1 次)
            - 消费力: 月均消费 / 等级应达月消费(保级额/12, L1 取基准)
            - 等级成长: 成长值/注册天数 斜率(元→成长值 1:1)
            - 积分活力: 月均积分流量(订单获得+抵扣)
            - 生命周期: 注册月数(近期无消费叠加衰减)

        Raises:
            KeyError: 会员不存在
        """
        member = await self.fabric.get_member(member_id)
        orders = await self.fabric.member_orders(member_id)
        valid = self.fabric.valid_orders(orders)

        days_reg = max(1.0, _days_since(member.get("created_at", "")))
        months = _months_since(member.get("created_at", ""))
        level = int(member.get("level", 1) or 1)

        # ① 活跃度: 近 30 天消费单量 + 登录
        recent30 = sum(1 for o in valid
                       if _days_since(order_paid_of(o)) <= 30)
        login_hit = 1 if _days_since(
            member.get("last_login_at", "")) <= 30 else 0
        activity_raw = _round2(recent30 + login_hit)

        # ② 消费力: 月均消费 vs 等级应达
        total_consume = sum(_amount_of(o) for o in valid)
        monthly_avg = total_consume / months
        expected = (KEEP_LEVEL_CONSUME.get(level, 0) / 12.0
                    if level >= 2 else L1_MONTHLY_BASELINE)
        spending_ratio = (monthly_avg / expected) if expected > 0 else 0.0

        # ③ 等级成长: 成长值斜率(成长值/日)
        growth_slope = int(member.get("growth_value", 0) or 0) / days_reg

        # ④ 积分活力: 月均积分流量(订单获得 + 下单抵扣)
        points_flow = sum(
            (int(o.get("consumedPoints", 0) or 0)
             + int(o.get("usedPoints", 0) or 0)) for o in orders) / months

        # ⑤ 生命周期: 注册月数 × 近期活跃衰减
        lifecycle_raw = months
        no_recent = all(_days_since(order_paid_of(o)) > 90 for o in valid) \
            if valid else True

        dims = [
            {"dimKey": "activity", "dim": HEALTH_DIM_NAMES["activity"],
             "raw": activity_raw,
             "score": sigmoid_score("activity", activity_raw),
             "explain": f"近30天有效消费 {recent30} 单+登录 "
                        f"{login_hit} 次"},
            {"dimKey": "spending", "dim": HEALTH_DIM_NAMES["spending"],
             "raw": _round2(spending_ratio),
             "score": sigmoid_score("spending", spending_ratio),
             "explain": f"月均消费 ¥{_round2(monthly_avg)} / 等级应达 "
                        f"¥{_round2(expected)}(L{level} 保级额/12)"},
            {"dimKey": "growth", "dim": HEALTH_DIM_NAMES["growth"],
             "raw": _round2(growth_slope),
             "score": sigmoid_score("growth", growth_slope),
             "explain": f"成长值斜率 {_round2(growth_slope)}/日"
                        f"(累计 {member.get('growth_value', 0)} / "
                        f"{_round2(days_reg)} 天)"},
            {"dimKey": "points", "dim": HEALTH_DIM_NAMES["points"],
             "raw": _round2(points_flow),
             "score": sigmoid_score("points", points_flow),
             "explain": f"月均积分流量 {_round2(points_flow)} 竹叶"
                        "(订单获得+抵扣)"},
            {"dimKey": "lifecycle", "dim": HEALTH_DIM_NAMES["lifecycle"],
             "raw": _round2(lifecycle_raw),
             "score": _round2(
                 sigmoid_score("lifecycle", lifecycle_raw)
                 * (0.5 if no_recent and months > 6 else 1.0)),
             "explain": f"注册 {_round2(lifecycle_raw)} 个月"
                        + ("(近90天无消费, 衰减×0.5)"
                           if no_recent and months > 6 else "")},
        ]
        total = _round2(sum(
            d["score"] * HEALTH_WEIGHTS[d["dimKey"]] for d in dims))
        grade = ("A 健康" if total >= 75 else "B 良好" if total >= 60
                 else "C 关注" if total >= 40 else "D 预警")
        record = {
            "memberId": member_id,
            "nickname": member.get("nickname", ""),
            "level": level,
            "levelName": LEVEL_NAMES.get(level, ""),
            "dimensions": dims,
            "totalScore": total,
            "grade": grade,
            "weights": dict(HEALTH_WEIGHTS),
            "formula": ("各维 0-100: 100/(1+exp(-k(x-x0))); 总分=Σ维度分×"
                        "权重(活跃0.25/消费力0.25/等级成长0.15/积分活力"
                        "0.15/生命周期0.20); 生命周期在注册>6月且近90天"
                        "无消费时×0.5 衰减(确定性)"),
            "computedAt": _now_iso(),
        }
        await self.store.save("zk_healths", member_id, record)
        return record

    @staticmethod
    def activity_score_of(health_record: dict) -> float:
        """从健康度记录提取活跃维分数(唤醒引擎复用)"""
        for d in health_record.get("dimensions", []):
            if d.get("dimKey") == "activity":
                return float(d.get("score", 0))
        return 0.0

    # ============================================================
    # RFM 画像(确定性分层)
    # ============================================================

    async def portrait(self, member_id) -> dict:
        """RFM 确定性分层(R/F/M 各五档 → 组合标签)

        R: 最近消费距今天数 → 5/4/3/2/1 档
        F: 近 30 天有效单量 → 5/4/3/2/1 档
        M: 月均消费(元) → 5/4/3/2/1 档

        Raises:
            KeyError: 会员不存在
        """
        member = await self.fabric.get_member(member_id)
        orders = await self.fabric.member_orders(member_id)
        valid = self.fabric.valid_orders(orders)
        months = _months_since(member.get("created_at", ""))

        last_paid = max((order_paid_of(o) for o in valid), default="")
        r_days = _round2(_days_since(last_paid)) if last_paid else 999.0
        r_score = _tier_down(r_days, R_TIERS)
        f_count = sum(1 for o in valid
                      if _days_since(order_paid_of(o)) <= 30)
        f_score = _tier_up(f_count, F_TIERS)
        monthly_avg = sum(_amount_of(o) for o in valid) / months
        m_score = _tier_up(monthly_avg, M_TIERS)

        label, action = self._rfm_label(r_score, f_score, m_score)
        record = {
            "memberId": member_id,
            "nickname": member.get("nickname", ""),
            "level": int(member.get("level", 1) or 1),
            "rfm": {
                "recencyDays": r_days, "recencyScore": r_score,
                "frequency30d": f_count, "frequencyScore": f_score,
                "monthlyConsume": _round2(monthly_avg),
                "monetaryScore": m_score,
            },
            "segmentLabel": label,
            "suggestedAction": action,
            "formula": ("R 档: ≤7天→5/≤30→4/≤90→3/≤180→2/其余→1; "
                        "F 档: ≥5单→5/≥3→4/≥2→3/≥1→2/0→1; "
                        "M 档: 月均≥1000→5/≥500→4/≥200→3/≥50→2/"
                        "其余→1; 标签=三档确定性组合(无 LLM)"),
            "computedAt": _now_iso(),
        }
        await self.store.save("zk_portraits", member_id, record)
        return record

    @staticmethod
    def _rfm_label(r: int, f: int, m: int) -> tuple[str, str]:
        """三档 → 分层标签(确定性规则表)"""
        if r >= 4 and f >= 4 and m >= 4:
            return ("重要价值会员", "重点维护: 专属权益+新品优先购")
        if r >= 4 and f >= 4:
            return ("重要保持会员", "消费力待提升: 组合购/满赠提客单")
        if r >= 4 and f <= 2 and m >= 3:
            return ("重要发展会员", "提频: 签到积分/复购券引导")
        if r <= 2 and f >= 3:
            return ("重要挽留会员", "近 90 天未消费: 回归礼包唤回")
        if r <= 2 and f <= 2 and m >= 3:
            return ("流失风险会员", "高价值流失预警: 人工确认后触达")
        if r >= 4 and m >= 4:
            return ("潜力会员", "低频高额: 品鉴会/大单专属服务")
        return ("一般会员", "常规运营: 活动通投+积分促活")

    async def portraits(self) -> list[dict]:
        """全量会员 RFM 分层(逐会员确定性计算)"""
        rows = []
        for m in await self.fabric.members():
            rows.append(await self.portrait(m["id"]))
        return rows


# ============================================================
# 模块级小工具(供同域服务复用, 避免循环导入)
# ============================================================

def order_paid_of(order: dict) -> str:
    """订单支付时间(与织物口径一致)"""
    payment = order.get("payment") or {}
    return payment.get("paidAt") or order.get("createdAt") or ""


def _amount_of(order: dict) -> float:
    """订单实付额(与织物口径一致)"""
    detail = order.get("priceDetail") or {}
    return float(detail.get("actualAmount") or 0)


def level_of_growth(growth_value: int) -> int:
    """按成长值计算等级(与 member_service._calc_level 同口径)"""
    level = 1
    for lv in sorted(LEVEL_THRESHOLDS, reverse=True):
        if int(growth_value) >= LEVEL_THRESHOLDS[lv]:
            level = lv
            break
    return level
