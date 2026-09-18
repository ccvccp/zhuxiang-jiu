"""竹鉴·BambooVerify(77号)——核心服务

设计依据: D:\竹奕酒的资料 双检测报告工程化裁剪
(纯确定性, LLM 禁入——全站铁律; 75/76 号范式同源):

    1. 质检典藏(双规格检测报告 15 项指标全量——
       ZZ26SW1489303A 52%vol 型 / ZZ26SW1489404B
       42%vol 型, 山东中质华检, 判定依据 Q/SRQ
       0001S-2023 / GB 2760-2024 / GB 7718-2025)
    2. 指标域检索(15 项指标关键词域: 酒精度/甲醇/
       氰化物/铅/防腐剂/甜味剂/二氧化硫/标签/锰/
       总酸/总酯/固形物/杂醇油/安全/全项)
    3. 质检问答(指标值+技术要求+单项判定+检测方法+
       报告编号引证——技术断言必含报告引证, 与 75号
       L3 溯源同源精神)
    4. 规格消歧(52%vol/42%vol 双型并列或显式消歧)
    5. 规格比对(双型全指标对照表)
    6. 合规拦截(医疗功效/夸大宣传——检测数据
       不得用于医疗断言)
"""

import logging
import re
from datetime import datetime, UTC

from repositories.zjian_repository import ZjianRepository
from services.zjian_mode_service import ZjianModeService

logger = logging.getLogger(__name__)

MODEL_VERSION = "v1-zjian"

AGENCY = "山东中质华检测试检验有限公司"
BASIS = ["Q/SRQ 0001S-2023", "GB 2760-2024",
         "GB 7718-2025"]
SIGN_DATE = "2026-06-25"
TEST_PERIOD = "2026-06-17 ~ 2026-06-25"

# 指标模板(双报告通用字段; spec 差异在 REPORTS 种子)
_METRIC_TPL = {
    "alcohol": {
        "name": "酒精度", "unit": "%vol",
        "method": "GB 5009.225-2023",
        "requirement": "38.0~55.0 且符合规格 ±1.0"},
    "methanol": {
        "name": "甲醇", "unit": "g/L",
        "method": "GB 5009.266-2016",
        "requirement": "≤2.0",
        "result": "未检出(定量限:0.025g/L)"},
    "cyanide": {
        "name": "氰化物(以HCN计)", "unit": "mg/L",
        "method": "GB 5009.36-2023 第一法",
        "requirement": "≤8.0",
        "result": "未检出(定量限:0.10mg/L)"},
    "lead": {
        "name": "铅(以Pb计)", "unit": "mg/kg",
        "method": "GB 5009.12-2023 第二法",
        "requirement": "≤0.16",
        "result": "未检出(定量限:0.05mg/kg)"},
    "benzoate": {
        "name": "苯甲酸及其钠盐(以苯甲酸计)",
        "unit": "g/kg", "method": "GB 5009.28-2016 第一法",
        "requirement": "不得使用",
        "result": "未检出(定量限:0.01g/kg)"},
    "sorbate": {
        "name": "山梨酸及其钾盐(以山梨酸计)",
        "unit": "g/kg", "method": "GB 5009.28-2016 第一法",
        "requirement": "不得使用",
        "result": "未检出(定量限:0.01g/kg)"},
    "saccharin": {
        "name": "糖精钠(以糖精计)", "unit": "g/kg",
        "method": "GB 5009.28-2016 第一法",
        "requirement": "不得使用",
        "result": "未检出(定量限:0.01g/kg)"},
    "cyclamate": {
        "name": "甜蜜素(以环己基氨基磺酸计)",
        "unit": "g/kg", "method": "GB 5009.97-2023 第二法",
        "requirement": "不得使用",
        "result": "未检出(定量限:0.03g/kg)"},
    "so2": {
        "name": "二氧化硫(以残留量计)", "unit": "g/kg",
        "method": "GB 5009.34-2022 第一法",
        "requirement": "不得使用",
        "result": "未检出(定量限:0.00600g/kg)"},
    "label": {
        "name": "标签*", "unit": "/",
        "method": "GB 7718-2025 GB 2757-2012",
        "requirement": "应符合 GB 7718-2025 及相关"
                       "法律法规要求",
        "result": "符合要求"},
    "manganese": {
        "name": "锰(Mn)", "unit": "mg/kg",
        "method": "GB 5009.242-2017 第二法",
        "requirement": "/",
        "result": "未检出(定量限:0.3mg/kg)"},
    "total_acid": {
        "name": "总酸(以乙酸计)", "unit": "g/L",
        "method": "GB 12456-2021 第一法",
        "requirement": "/"},
    "total ester": {
        "name": "总酯(以乙酸乙酯计)", "unit": "g/L",
        "method": "GB/T 10345-2022",
        "requirement": "/"},
    "solids": {
        "name": "固形物", "unit": "g/L",
        "method": "GB/T 10345-2022",
        "requirement": "/"},
    "fusel_oil": {
        "name": "杂醇油*", "unit": "g/100mL",
        "method": "GB/T 5009.48-2003",
        "requirement": "/",
        "result": "未检出(检出限:0.03g/100mL)"},
}

# 总酯键修正(键名含空格容错——统一用 total_ester)
_METRIC_TPL["total_ester"] = _METRIC_TPL.pop(
    "total ester")

# 双规格典藏种子(检测报告 15 项指标全量)
REPORTS = [
    {
        "reportId": "ZZ26SW1489303A",
        "product": "瑞麒竹奕酒",
        "spec": "52%vol 型", "volume": "500ml/瓶",
        "agency": AGENCY, "basis": BASIS,
        "signDate": SIGN_DATE, "testPeriod": TEST_PERIOD,
        "conclusion": (
            "该样品本次检测, 有技术要求项目符合 "
            "Q/SRQ 0001S-2023、GB 2760-2024、"
            "GB 7718-2025 要求; 无技术要求的项目仅"
            "提供实测数据。"),
        "metrics": {
            "alcohol": {"result": "51.3",
                        "verdict": "符合"},
            "methanol": {"verdict": "符合"},
            "cyanide": {"verdict": "符合"},
            "lead": {"verdict": "符合"},
            "benzoate": {"verdict": "符合"},
            "sorbate": {"verdict": "符合"},
            "saccharin": {"verdict": "符合"},
            "cyclamate": {"verdict": "符合"},
            "so2": {"verdict": "符合"},
            "label": {"verdict": "符合"},
            "manganese": {"verdict": "/"},
            "total_acid": {"result": "0.84",
                           "verdict": "/"},
            "total_ester": {"result": "1.40",
                            "verdict": "/"},
            "solids": {"result": "0.23", "verdict": "/"},
            "fusel_oil": {"verdict": "/"},
        },
    },
    {
        "reportId": "ZZ26SW1489404B",
        "product": "瑞麒竹奕酒",
        "spec": "42%vol 型", "volume": "500ml/瓶",
        "agency": AGENCY, "basis": BASIS,
        "signDate": SIGN_DATE, "testPeriod": TEST_PERIOD,
        "conclusion": (
            "该样品本次检测, 有技术要求项目符合 "
            "Q/SRQ 0001S-2023、GB 2760-2024、"
            "GB 7718-2025 要求; 无技术要求的项目仅"
            "提供实测数据。"),
        "metrics": {
            "alcohol": {"result": "41.7",
                        "verdict": "符合"},
            "methanol": {"verdict": "符合"},
            "cyanide": {"verdict": "符合"},
            "lead": {"verdict": "符合"},
            "benzoate": {"verdict": "符合"},
            "sorbate": {"verdict": "符合"},
            "saccharin": {"verdict": "符合"},
            "cyclamate": {"verdict": "符合"},
            "so2": {"verdict": "符合"},
            "label": {"verdict": "符合"},
            "manganese": {"verdict": "/"},
            "total_acid": {"result": "0.78",
                           "verdict": "/"},
            "total_ester": {"result": "1.10",
                            "verdict": "/"},
            "solids": {"result": "0.21", "verdict": "/"},
            "fusel_oil": {"verdict": "/"},
        },
    },
]

# 指标关键词域(检索路由; 注: 52/42 为规格消歧词
# 在 _match_spec 处理, 不入指标域——防"42型甲醇"误命中酒精度)
METRIC_KEYWORDS = {
    "alcohol": ["酒精度", "度数", "酒精", "vol"],
    "methanol": ["甲醇"],
    "cyanide": ["氰化物", "氰"],
    "lead": ["铅", "重金属"],
    "benzoate": ["苯甲酸", "防腐剂"],
    "sorbate": ["山梨酸", "防腐剂"],
    "saccharin": ["糖精", "甜味剂"],
    "cyclamate": ["甜蜜素", "甜味剂"],
    "so2": ["二氧化硫", "硫"],
    "label": ["标签", "标识"],
    "manganese": ["锰"],
    "total_acid": ["总酸", "酸度"],
    "total_ester": ["总酯", "酯"],
    "solids": ["固形物"],
    "fusel_oil": ["杂醇油", "杂醇"],
}
SAFETY_KEYS = ["methanol", "cyanide", "lead",
               "benzoate", "sorbate", "saccharin",
               "cyclamate", "so2"]

# 合规拦截(检测数据不得用于医疗/夸大断言)
BLOCK_PATTERNS = {
    "medical": r"(治病|疗效|降血压|降血脂|保健功效|"
               r"包治|药效|药用)",
    "exaggerate": r"(最好|第一|唯一|最先进|行业首创|"
                  r"保证|绝对安全|零风险)",
}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class ZjianService:
    """竹鉴·质检典藏 + 质检问答核心"""

    def __init__(self):
        self.repo = ZjianRepository()
        self.mode = ZjianModeService()
        self._seeded = False

    async def _ensure_seed(self) -> None:
        """报告典藏播种(幂等)"""
        if self._seeded:
            return
        for r in REPORTS:
            if await self.repo.get_report(
                    r["reportId"]) is None:
                await self.repo.upsert_report(
                    r["reportId"], r)
        self._seeded = True

    # ============================================================
    # 指标域检索(确定性路由)
    # ============================================================

    def _match_metrics(self, question: str) -> list[str]:
        """问题 → 命中指标键列表(保序去重)"""
        q = str(question or "")
        hits = []
        for key, words in METRIC_KEYWORDS.items():
            if any(w in q for w in words) \
                    and key not in hits:
                hits.append(key)
        # 安全域聚合词
        if any(w in q for w in ("安全", "有害物质",
                                 "添加剂", "卫生")):
            hits = SAFETY_KEYS + [
                h for h in hits
                if h not in SAFETY_KEYS]
        return hits

    def _match_spec(self, question: str) -> list[str]:
        """问题 → 规格消歧(52 型 / 42 型 / 双型)"""
        q = str(question or "")
        wants = []
        if "52" in q:
            wants.append("ZZ26SW1489303A")
        if "42" in q:
            wants.append("ZZ26SW1489404B")
        return wants or ["ZZ26SW1489303A",
                         "ZZ26SW1489404B"]

    def _block_check(self, question: str) -> str | None:
        for key, pat in BLOCK_PATTERNS.items():
            if re.search(pat, question or ""):
                return key
        return None

    # ============================================================
    # 质检问答(决策面)
    # ============================================================

    async def verify_chat(self, question: str) -> dict:
        """质检问答: 指标检索 → 引证应答

        应答含: 指标值/技术要求/单项判定/检测方法/
        报告编号引证(技术断言必含)。
        拦截: 医疗/夸大断言(检测数据不得医用)。
        """
        await self._ensure_seed()
        q = str(question or "").strip()
        if not q:
            raise ValueError("问题不能为空")
        await self.repo.bump_stat("guard", "total")

        base = {"success": True, "question": q,
                "modelVersion": MODEL_VERSION}

        # 合规拦截(医疗/夸大)
        blocked = self._block_check(q)
        if blocked:
            await self.repo.bump_stat(
                "guard", "blocked")
            return {**base,
                    "guardrailsTriggered": True,
                    "guardRule": blocked,
                    "content": (
                        "检测数据仅证明产品符合食品安全"
                        "标准, 不作医疗功效或绝对化宣传。"
                        "瑞麒竹奕酒具有独特的清雅风味与"
                        "文化价值(合规红线: "
                        + "; ".join(BASIS) + ")"),
                    "citations": [
                        {"type": "report",
                         "id": r["reportId"]}
                        for r in REPORTS]}

        # 指标检索
        keys = self._match_metrics(q)
        spec_ids = self._match_spec(q)
        if not keys:
            await self.repo.bump_stat(
                "guard", "no_hit")
            return {**base, "guardrailsTriggered": False,
                    "noMetricHit": True,
                    "content": (
                        "未命中具体检测指标。可询问: 酒精度/"
                        "甲醇/氰化物/铅/防腐剂/甜味剂/"
                        "二氧化硫/标签/锰/总酸/总酯/固形物/"
                        "杂醇油, 或'安全性'汇总、'全项'总览。"
                        "双规格: 52%vol 型(报告"
                        "ZZ26SW1489303A) / 42%vol 型"
                        "(报告 ZZ26SW1489404B)。"),
                    "citations": []}

        # 引证应答
        reports = []
        for rid in spec_ids:
            r = await self.repo.get_report(rid)
            if r:
                reports.append(r)
        items, citations = [], []
        for r in reports:
            for key in keys:
                tpl = _METRIC_TPL.get(key, {})
                m = (r.get("metrics") or {}).get(key, {})
                items.append({
                    "reportId": r["reportId"],
                    "spec": r["spec"],
                    "metric": tpl.get("name", key),
                    "requirement": tpl.get("requirement", "/"),
                    "result": m.get("result",
                                    tpl.get("result", "/")),
                    "verdict": m.get("verdict", "/"),
                    "unit": tpl.get("unit", "/"),
                    "method": tpl.get("method", "/"),
                })
            citations.append({
                "type": "report", "id": r["reportId"],
                "agency": r["agency"],
                "signDate": r["signDate"]})

        await self.repo.bump_stat(
            "metric", ",".join(keys))

        content = self._render_answer(items, keys)

        record = {
            "askId": 0, "question": q,
            "metricKeys": keys, "specIds": spec_ids,
            "blocked": False, "noHit": False,
            "createdAt": _now_iso(),
        }
        aid = await self.repo.next_id("ask")
        record["askId"] = aid
        await self.repo.save_ask(aid, record)

        return {**base,
                "guardrailsTriggered": False,
                "metricKeys": keys,
                "specs": [r["spec"] for r in reports],
                "items": items, "citations": citations,
                "content": content}

    def _render_answer(self, items: list[dict],
                       keys: list[str]) -> str:
        """确定性应答渲染(引证式)"""
        lines = []
        for it in items:
            lines.append(
                f"[{it['spec']}] {it['metric']}: "
                f"实测 {it['result']}"
                f"{it['unit'] if it['unit'] != '/' else ''} "
                f"(技术要求 {it['requirement']}, "
                f"{it['verdict']}); 检测方法 "
                f"{it['method']}; 报告 {it['reportId']}。")
        if len(keys) >= 5:
            head = ("安全性汇总(检测报告 "
                    "ZZ26SW1489303A / ZZ26SW1489404B, "
                    f"{AGENCY}, 签发 {SIGN_DATE}): ")
        else:
            head = "依检测报告: "
        return head + " ".join(lines) + (
            " 判定依据: " + "; ".join(BASIS) + "。")

    # ============================================================
    # 规格比对(决策面)
    # ============================================================

    async def compare(self) -> dict:
        """双规格全指标对照(15 项)"""
        await self._ensure_seed()
        r52 = await self.repo.get_report(
            "ZZ26SW1489303A")
        r42 = await self.repo.get_report(
            "ZZ26SW1489404B")
        if not r52 or not r42:
            raise KeyError("典藏报告缺失")
        rows = []
        for key, tpl in _METRIC_TPL.items():
            m52 = (r52["metrics"] or {}).get(key, {})
            m42 = (r42["metrics"] or {}).get(key, {})
            rows.append({
                "metric": tpl["name"],
                "requirement": tpl["requirement"],
                "r52": m52.get("result",
                               tpl.get("result", "/")),
                "r42": m42.get("result",
                               tpl.get("result", "/")),
                "unit": tpl["unit"],
                "method": tpl["method"],
            })
        return {"success": True,
                "specA": r52["spec"],
                "specB": r42["spec"],
                "rows": rows,
                "citations": [
                    {"type": "report", "id": r52["reportId"]},
                    {"type": "report", "id": r42["reportId"]}],
                "modelVersion": MODEL_VERSION}

    # ============================================================
    # 观测面
    # ============================================================

    async def list_reports(self) -> dict:
        await self._ensure_seed()
        return {"success": True,
                "total": 2,
                "items": await self.repo.list_reports()}

    async def get_report(self, rid: str) -> dict:
        await self._ensure_seed()
        r = await self.repo.get_report(rid)
        if r is None:
            raise KeyError(f"报告不存在: {rid}")
        return {"success": True, "report": r}

    async def metric_catalog(self) -> dict:
        """指标目录(15 项名称/单位/方法/要求)"""
        return {"success": True,
                "total": len(_METRIC_TPL),
                "items": [
                    {"key": k, "name": v["name"],
                     "unit": v["unit"],
                     "method": v["method"],
                     "requirement": v["requirement"]}
                    for k, v in _METRIC_TPL.items()]}

    async def get_metrics(self) -> dict:
        guard = await self.repo.get_stats("guard")
        metric = await self.repo.get_stats("metric")
        return {"success": True,
                "totalAsks": guard.get("total", 0),
                "guard": {
                    "blocked": guard.get("blocked", 0),
                    "noHit": guard.get("no_hit", 0),
                },
                "metricHits": metric,
                "modelVersion": MODEL_VERSION}

    async def list_asks(self, limit: int = 20) -> dict:
        return {"success": True,
                "items": await self.repo.list_asks(limit)}
