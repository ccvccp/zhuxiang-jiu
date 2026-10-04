"""小竹本地合规引擎(local_guardrail_service, 2026-10-04)

用户方案落地: Guardrail 关键词过滤由词表 in 检查升级为
DFA(Trie) O(N) 引擎 + 酒类/招商场景专属词库(一级拦截/
二级替换/分类话术) + Redis 热更新。

2026-10-04 运营后台升级(用户方案二期):
    - 白名单豁免(allowlist): 命中敏感词后若上下文含豁免
      模式则放行("第一家店"豁免"第一"式误杀治理)
    - 命中日志(gr_hit_log): 输入拦截/输出替换 fire-and-forget
      留痕(sessionId/memberId/direction/原文脱敏)——Evolution
      Engine 数据源, 后台打标误杀/确认违规
    - 热更新双通道: Pub/Sub(zhuxiang:guardrail:update 即时
      重建) + 60s 版本轮询兜底(丢消息自愈)——此前 reload
      无调用点的洞一并补上

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
        卡号/身份证正则——落库前红线, 不在本引擎重复;
        命中日志原文仅做手机号快速脱敏)

热更新:
    Redis zhuxiang:xiaozhu:guardrail:rules = JSON
    {version, blocklist, replacemap, block_responses,
     allowlist}; 版本变更时进程内重建 DFA(构建 ~ms 级);
    无键用内置默认词库——运营后台改词库零重启生效。

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
_UPDATE_CHANNEL = "zhuxiang:guardrail:update"
_RELOAD_POLL_SEC = 60

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

# 命中日志原文快速脱敏(手机号; 完整 PII 脱敏在
# xiaozhu_service.mask_pii 落库红线, 此处仅日志面)
_PHONE_RE = re.compile(r"1[3-9]\d{9}")


class LocalGuardrail:
    """DFA 本地合规引擎(进程内单例语义; 词库热更新自动重建)"""

    def __init__(self, blocklist: dict | None = None,
                 replacemap: dict | None = None,
                 responses: dict | None = None,
                 allowlist: dict | None = None):
        self._blocklist = dict(blocklist
                               or DEFAULT_BLOCKLIST)
        self._replacemap = dict(replacemap
                                or DEFAULT_REPLACEMAP)
        self._responses = dict(responses
                               or DEFAULT_BLOCK_RESPONSES)
        # 白名单豁免: 敏感词→豁免模式(运营后台维护, 命中
        # 后上下文含豁免模式则放行——误杀治理)。
        # 双结构: CONTAINS(字符串, 变体穿透)+REGEX(预编译)
        self._allow_contains: dict[str, list[str]] = {}
        self._allow_regex: dict[str, list[re.Pattern]] = {}
        for w, patts in (allowlist or {}).items():
            for p in patts:
                if isinstance(p, dict):
                    # 发布快照新格式 {p, t}
                    if p.get("t") == "REGEX":
                        try:
                            self._allow_regex\
                                .setdefault(str(w), [])\
                                .append(re.compile(
                                    str(p.get("p", "")),
                                    re.IGNORECASE))
                        except re.error:
                            logger.warning(
                                "allowlist_regex_skip %s",
                                p.get("p"))
                    else:
                        self._allow_contains\
                            .setdefault(str(w), [])\
                            .append(str(p.get("p", "")))
                else:
                    # 旧格式纯字符串(全按 CONTAINS)
                    self._allow_contains\
                        .setdefault(str(w), []).append(str(p))
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

    def _dfa_search_all(self,
                        text: str) -> list[str]:
        """全部命中词(去重保序)——白名单逐词豁免判定用"""
        n = len(text)
        seen: list[str] = []
        for i in range(n):
            node = self._dfa
            j = i
            while j < n:
                node = node.get(text[j])
                if node is None:
                    break
                j += 1
                if "" in node:
                    w = text[i:j]
                    if w not in seen:
                        seen.append(w)
        return seen

    def _is_allowed(self, word: str,
                    original: str,
                    cleaned: str) -> bool:
        """白名单豁免(双模式):
        CONTAINS——豁免模式出现在原文或清洗文本中即放行
        (豁免词本身也做变体穿透, "第 一 家 店"同样豁免);
        REGEX——预编译正则 search 原文(运营自定义上下文模式,
        如"第[一二三]家店"一族一次配齐)"""
        for p in self._allow_contains.get(word, ()):
            p_clean = _STRIP_RE.sub("", p).lower()
            if (p in original
                    or (p_clean and p_clean in cleaned)):
                return True
        for pat in self._allow_regex.get(word, ()):
            if pat.search(original):
                return True
        return False

    # ------------------------------------------------------------
    # 命中日志(AsyncHitLogger 内存队列——put_nowait 非阻塞,
    # 后台定时/定量批量刷盘; 命中风暴时 QueueFull 丢弃背压,
    # 主链路 <1ms 不受日志面影响)
    # ------------------------------------------------------------

    def _log_hit(self, *, direction: str,
                 rule_type: str, word: str,
                 category: str, original: str,
                 processed: str,
                 context: dict | None) -> None:
        ctx = context or {}
        record = {
            "traceId": ctx.get("traceId", ""),
            "sessionId": ctx.get("sessionId", ""),
            "memberId": ctx.get("memberId", 0),
            "direction": direction,
            "ruleWord": word,
            "category": category,
            "ruleType": rule_type,
            "originalText": _PHONE_RE.sub(
                "1**********", str(original)[:500]),
            "processedText": str(processed)[:500],
        }
        _hit_logger.log(record)

    # ------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------

    def check_input(self, text: str,
                    context: dict | None = None) -> dict:
        """输入端前置校验(<1ms)

        context(可选, 命中日志归因用): {sessionId,
        memberId, traceId}

        Returns: {"blocked", "category", "word",
                  "response", "sanitized"}
            blocked=True → 拦截, response=分类话术
        """
        raw = str(text or "")
        cleaned = _STRIP_RE.sub("", raw).lower()
        # 全量命中→逐词白名单豁免→首个未豁免词拦截
        for hit in self._dfa_search_all(cleaned):
            if self._is_allowed(hit, raw, cleaned):
                continue
            cat = self._word_cat.get(hit, "default")
            logger.info(
                "voice48_guardrail_block category=%s "
                "word=%s", cat, hit)
            response = self._responses.get(
                cat, self._responses["default"])
            self._log_hit(
                direction="INPUT", rule_type="BLOCK",
                word=hit, category=cat, original=raw,
                processed=response, context=context)
            return {
                "blocked": True,
                "category": cat,
                "word": hit,
                "response": response,
                "sanitized": "",
            }
        return {"blocked": False, "category": "",
                "word": "", "response": "",
                "sanitized": raw}

    def filter_output(self, text: str,
                      context: dict | None = None) -> str:
        """输出端二级替换(广告法/价格/加盟夸大——不阻断)

        替换发生时留痕(direction=OUTPUT, ruleType=REPLACE)
        供运营审计输出面替换密度。
        """
        out = str(text or "")
        hit_words: list[str] = []
        for word, repl in self._replacemap.items():
            if word in out:
                out = out.replace(word, repl)
                hit_words.append(word)
        if hit_words:
            self._log_hit(
                direction="OUTPUT", rule_type="REPLACE",
                word="|".join(hit_words), category="",
                original=str(text or ""),
                processed=out, context=context)
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
            allowlist = data.get("allowlist")
            fresh = LocalGuardrail(
                blocklist=blocklist or DEFAULT_BLOCKLIST,
                replacemap=replacemap
                or DEFAULT_REPLACEMAP,
                responses=responses
                or DEFAULT_BLOCK_RESPONSES,
                allowlist=allowlist or {})
            fresh._rules_version = version
            # 原子替换 self 状态(引用互换——并发读者安全)
            self._blocklist = fresh._blocklist
            self._replacemap = fresh._replacemap
            self._responses = fresh._responses
            self._word_cat = fresh._word_cat
            self._dfa = fresh._dfa
            self._allow_contains = fresh._allow_contains
            self._allow_regex = fresh._allow_regex
            self._rules_version = version
            logger.info(
                "voice48_guardrail_rules_reloaded "
                "version=%s block_words=%s "
                "allow_contains=%s allow_regex=%s",
                version, len(self._word_cat),
                len(self._allow_contains),
                len(self._allow_regex))
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


# ------------------------------------------------------------
# 热更新监听(Pub/Sub 即时 + 轮询兜底; 惯例 A scheduler)
# ------------------------------------------------------------

_reload_task: asyncio.Task | None = None


async def _reload_loop() -> None:
    """订阅发布广播即时重建; 兜底每 60s 版本轮询自愈

    内存模式(测试)无 Redis——纯轮询跳过即可(引擎用内置
    词库, 测试自行构造实例)。
    """
    pubsub = None
    client = None
    from repositories.backend import (
        is_redis_mode, get_redis_client,
    )
    if is_redis_mode():
        try:
            client = await get_redis_client()
            pubsub = client.pubsub()
            await pubsub.subscribe(_UPDATE_CHANNEL)
            logger.info("guardrail_listener_subscribed "
                        "channel=%s", _UPDATE_CHANNEL)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "guardrail_listener_sub_fail: %s", exc)
            pubsub = None
    while True:
        try:
            if pubsub is not None:
                msg = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=_RELOAD_POLL_SEC)
                if msg:
                    await _guardrail.reload_if_updated()
                    continue
            else:
                await asyncio.sleep(_RELOAD_POLL_SEC)
            # 超时窗口(=轮询周期)兜底校验一次版本
            await _guardrail.reload_if_updated()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "guardrail_listener_tick_fail: %s", exc)
            await asyncio.sleep(_RELOAD_POLL_SEC)


def start_guardrail_listener() -> None:
    """幂等启动热更新监听(main startup 挂载)"""
    global _reload_task
    if _reload_task is not None \
            and not _reload_task.done():
        return
    _reload_task = asyncio.get_event_loop()\
        .create_task(_reload_loop())
    logger.info("guardrail_listener_started")


# ------------------------------------------------------------
# AsyncHitLogger——命中日志高性能异步写入器
# (用户方案二期: 内存队列+批量刷盘; 主链路 put_nowait
#  非阻塞 <1ms, 后台定时 2s/定量 200 条批量落库, 命中
#  风暴时 QueueFull 丢弃背压保主业务)
# ------------------------------------------------------------

class AsyncHitLogger:
    """内存队列 + 定时/定量批量刷盘(Redis pipeline 批量
    或内存批量; 优雅停机 flush 残留)"""

    def __init__(self, batch_size: int = 200,
                 flush_interval: float = 2.0,
                 maxsize: int = 10000):
        self._queue: asyncio.Queue = asyncio.Queue(
            maxsize=maxsize)
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._dropped = 0
        self._worker: asyncio.Task | None = None

    # -- 主链路接口(同步非阻塞) --------------------------

    def log(self, record: dict) -> None:
        """非阻塞入队; 队列满(命中风暴)丢弃并计数告警"""
        try:
            self._queue.put_nowait(record)
        except asyncio.QueueFull:
            self._dropped += 1
            if self._dropped % 100 == 1:
                logger.warning(
                    "guardrail_hit_queue_full dropped=%s",
                    self._dropped)

    # -- 刷盘 --------------------------------------------

    async def flush(self) -> int:
        """批量落库(定量取一批; 返回写入条数)"""
        batch: list[dict] = []
        while len(batch) < self._batch_size:
            try:
                batch.append(
                    self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        if not batch:
            return 0
        try:
            from repositories.guardrail_repository \
                import get_guardrail_repo
            await get_guardrail_repo()\
                .log_hits_batch(batch)
            return len(batch)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "guardrail_hit_flush_fail n=%s: %s",
                len(batch), exc)
            return 0

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(self._flush_interval)
            try:
                await self.flush()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "guardrail_hit_worker_fail: %s", exc)

    # -- 生命周期 ----------------------------------------

    def start(self) -> None:
        if self._worker is not None \
                and not self._worker.done():
            return
        self._worker = asyncio.get_event_loop()\
            .create_task(self._flush_loop())
        logger.info("guardrail_hit_logger_started "
                    "batch=%s interval=%ss",
                    self._batch_size,
                    self._flush_interval)

    async def stop(self) -> int:
        """优雅停机: 停 worker + 刷入残留"""
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except (asyncio.CancelledError,
                    Exception):  # noqa: BLE001
                pass
            self._worker = None
        return await self.flush()


# 模块级单例(_log_hit 调用方零感知)
_hit_logger = AsyncHitLogger()


def get_hit_logger() -> AsyncHitLogger:
    return _hit_logger


async def flush_hits() -> int:
    """手动刷盘(测试/巡检用——即时落库不等工作周期)"""
    return await _hit_logger.flush()
