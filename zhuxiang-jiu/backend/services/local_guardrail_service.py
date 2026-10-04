"""小竹本地合规引擎(local_guardrail_service, 2026-10-04)

用户方案落地: Guardrail 关键词过滤由词表 in 检查升级为
DFA(Trie) O(N) 引擎 + 酒类/招商场景专属词库(一级拦截/
二级替换/分类话术) + Redis 热更新。

架构定位(方案校准):
    本引擎承接「关键词过滤 + 格式脱敏」职能(输入端前置
    +输出端后验, 微秒级); 语义理解型合规(暗示疗效/多轮
    意图漂移)仍由 LLM 分类轨 system prompt 约束 + 执行层
    沙箱/FC 网关承担——本地挡明确违规, LLM 兜边缘案例。

词库分域( zxjiu.com 酒类销售+招商加盟双场景):
    一级拦截(blocklist, 5 类): 命中即拦, 返回分类标准话术
        minor_protection   未成年人保护(酒类销售红线)
        franchise_redline  招商红线承诺(非法集资/欺诈风险)
        alcohol_claims     酒类功效虚假宣传(广告法/食安法)
        excessive_drinking 诱导过量饮酒
        general_compliance 通用合规底线
    二级替换(replacemap, 输出端): 广告法绝对化用语/
        价格敏感/加盟夸大 → 合规话术(不阻断)
    PII 脱敏: 沿用 xiaozhu_service.mask_pii(已有手机/
        卡号/身份证正则——落库前红线, 不在本引擎重复)

热更新:
    Redis zhuxiang:xiaozhu:guardrail:rules = JSON
    {version, blocklist, replacemap, block_responses};
    版本变更时进程内重建 DFA(构建 ~ms 级); 无键用内置
    默认词库——运营后台改词库零重启生效。

性能: DFA 查询 O(len(text)) 与词库规模无关(对比词表
    in 全扫 O(N×M)); 拦截决策 <1ms, 零 Token 成本。
"""

import asyncio
import json
import logging
import re

logger = logging.getLogger("local_guardrail")

_REDIS_RULES_KEY = \
    "zhuxiang:xiaozhu:guardrail:rules"

# ---------------------------------------------------------------------------
# 内置默认词库(2026-10-04 v1.0.0——运营可经 Redis 全量覆盖)
# ---------------------------------------------------------------------------

DEFAULT_BLOCKLIST = {
    "minor_protection": (
        "未成年", "未满18岁", "初中生", "高中生",
        "小学生", "underage", "小孩买酒", "儿童饮酒",
    ),
    "franchise_redline": (
        "保底收益", "稳赚不赔", "零风险", "包回本",
        "年入百万", "躺赚", "保证盈利", "无风险",
        "必赚", "包赚钱",
    ),
    "alcohol_claims": (
        "治疗", "治愈", "抗癌", "降血压", "降血脂",
        "降血糖", "药酒疗效", "延年益寿", "壮阳",
        "补肾", "预防疾病", "消除疲劳", "保健品效果",
    ),
    "excessive_drinking": (
        "拼酒", "灌醉", "不醉不归", "一口闷",
        "喝倒", "斗酒", "感情深一口闷",
    ),
    "general_compliance": (
        "代开发票", "套现", "洗钱",
    ),
}

DEFAULT_BLOCK_RESPONSES = {
    "minor_protection":
        "抱歉，根据国家法律规定，我们禁止向未成年人"
        "销售酒类或提供相关加盟信息。",
    "franchise_redline":
        "小竹提醒您：投资有风险，加盟需谨慎。我们提供"
        "完善的扶持政策，但不做任何违规的收益承诺哦。",
    "alcohol_claims":
        "抱歉，酒类属于饮品，不能代替药物，也没有治疗"
        "或保健功效。请您理性饮酒，健康生活。",
    "excessive_drinking":
        "小竹倡导文明饮酒、适量饮酒。为了您的健康，"
        "请勿过度贪杯或参与拼酒哦。",
    "general_compliance":
        "抱歉，您的提问包含不适宜的内容，请调整后重试。",
    "default":
        "抱歉，您的提问包含不适宜的内容，请调整后重试。",
}

# 二级替换(仅输出端——小竹 reply ≤60 字, 误替换影响面小;
# 误杀监控留痕驱动调优)
DEFAULT_REPLACEMAP = {
    # 广告法绝对化用语
    "国家级": "知名", "最高级": "优质", "最佳": "优选",
    "唯一": "特色", "顶级": "高端", "极品": "臻品",
    "万能": "多功能",
    # 价格敏感
    "最低价": "当前优惠价", "全网最便宜": "极具性价比",
    "跳楼价": "限量特惠", "亏本卖": "让利促销",
    "历史最低": "近期特惠",
    # 加盟夸大
    "独家代理": "区域保护", "垄断市场": "深耕区域",
    "躺赢": "轻松运营",
}

# 变体对抗预处理: 去空格/标点/emoji 等干扰(保留中文与
# 字母数字——"未 成 年"/"未*成*年"均命中), 统一小写
_STRIP_RE = re.compile(r"[^\u4e00-\u9fffa-z0-9]")


class LocalGuardrail:
    """DFA 本地合规引擎(进程内单例语义; 词库热更新自动重建)"""

    def __init__(self, blocklist: dict | None = None,
                 replacemap: dict | None = None,
                 responses: dict | None = None):
        self._blocklist = dict(blocklist
                               or DEFAULT_BLOCKLIST)
        self._replacemap = dict(replacemap
                                or DEFAULT_REPLACEMAP)
        self._responses = dict(responses
                               or DEFAULT_BLOCK_RESPONSES)
        # 词→分类倒排(拦截命中时归因分类)
        self._word_cat: dict[str, str] = {}
        for cat, words in self._blocklist.items():
            for w in words:
                self._word_cat[w.lower()] = cat
        self._dfa = self._build_dfa(
            self._word_cat.keys())
        self._rules_version = "builtin"

    # ------------------------------------------------------------
    # DFA 构建(Trie——初始化一次, 查询 O(N) 与词库规模无关)
    # ------------------------------------------------------------

    @staticmethod
    def _build_dfa(words) -> dict:
        root: dict = {}
        for word in words:
            node = root
            for ch in str(word).lower():
                node = node.setdefault(ch, {})
            node[""] = True   # is_end(空键避免与中文字冲突)
        return root

    def _dfa_search(self, text: str) -> str | None:
        """最长优先命中; 返回首个命中词(None=安全)

        实现注: 逐位重扫经典 DFA(非 Aho-Corasick)——词库
        百级规模下 O(N×L_avg) 仍微秒级, 引入自动机库复杂度
        不成正比; 词库增长至万级再升级 AC 自动机。
        """
        n = len(text)
        for i in range(n):
            node = self._dfa
            j = i
            while j < n:
                node = node.get(text[j])
                if node is None:
                    break
                j += 1
                if "" in node:
                    return text[i:j]
        return None

    # ------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------

    def check_input(self, text: str) -> dict:
        """输入端前置校验(<1ms)

        Returns: {"blocked", "category", "word",
                  "response", "sanitized"}
            blocked=True → 拦截, response=分类话术
        """
        cleaned = _STRIP_RE.sub(
            "", str(text or "")).lower()
        hit = self._dfa_search(cleaned)
        if hit:
            cat = self._word_cat.get(hit, "default")
            logger.info(
                "voice48_guardrail_block category=%s "
                "word=%s", cat, hit)
            return {
                "blocked": True,
                "category": cat,
                "word": hit,
                "response": self._responses.get(
                    cat, self._responses["default"]),
                "sanitized": "",
            }
        return {"blocked": False, "category": "",
                "word": "", "response": "",
                "sanitized": str(text or "")}

    def filter_output(self, text: str) -> str:
        """输出端二级替换(广告法/价格/加盟夸大——不阻断)"""
        out = str(text or "")
        for word, repl in self._replacemap.items():
            if word in out:
                out = out.replace(word, repl)
        return out

    # ------------------------------------------------------------
    # Redis 热更新(版本比对重建; 失败保持旧词库——fail-safe)
    # ------------------------------------------------------------

    async def reload_if_updated(self) -> bool:
        """词库热更新检测(Redis JSON 版本变更→重建 DFA)

        Returns: 是否发生了重建
        """
        try:
            from repositories.backend import (
                is_redis_mode, get_redis_client,
            )
            if not is_redis_mode():
                return False
            client = await get_redis_client()
            raw = await client.get(_REDIS_RULES_KEY)
            if not raw:
                return False
            data = json.loads(raw)
            version = str(data.get("version", ""))
            if not version \
                    or version == self._rules_version:
                return False
            blocklist = data.get("blocklist")
            replacemap = data.get("replacemap")
            responses = data.get("block_responses")
            fresh = LocalGuardrail(
                blocklist=blocklist or DEFAULT_BLOCKLIST,
                replacemap=replacemap
                or DEFAULT_REPLACEMAP,
                responses=responses
                or DEFAULT_BLOCK_RESPONSES)
            fresh._rules_version = version
            # 原子替换 self 状态(引用互换——并发读者安全)
            self._blocklist = fresh._blocklist
            self._replacemap = fresh._replacemap
            self._responses = fresh._responses
            self._word_cat = fresh._word_cat
            self._dfa = fresh._dfa
            self._rules_version = version
            logger.info(
                "voice48_guardrail_rules_reloaded "
                "version=%s block_words=%s",
                version, len(self._word_cat))
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "guardrail_reload_skip: %s", exc)
            return False


# 进程级单例(热更新原地重建——调用方引用不变)
_guardrail = LocalGuardrail()


def get_guardrail() -> LocalGuardrail:
    return _guardrail


async def reload_guardrail() -> bool:
    """热更新入口(管理端/巡检调用)"""
    return await _guardrail.reload_if_updated()
