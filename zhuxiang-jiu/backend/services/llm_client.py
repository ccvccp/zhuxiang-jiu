"""LLM provider 客户端(P3.3: llm 轨统一接入点; P3.5: embedding 统一接入点)

设计对齐项目惯例(attract D-11 / knowledge D-14/D-18 的 llm 轨预留):
    - 纯标准库: urllib.request POST OpenAI 兼容 /chat/completions
      端点(智谱 GLM / DeepSeek / 通义等均兼容该协议), 不引入 SDK
    - 模块级单例 provider_client, 未配置 key 时整体不可用
    - 调用方降级: client 不吞异常语义, 返回 None 表示"本次不可用",
      由各调用方按既有惯例回退 rule 轨并打 *_llm_*_fallback_rule 日志

环境变量(对齐 AI_ 前缀动态读取惯例):
    LLM_API_KEY    必填, 缺失即 llm/embedding 轨关闭(默认 "")
    LLM_BASE_URL   OpenAI 兼容端点(默认智谱)
    LLM_MODEL      模型名(默认 glm-4-flash)
    LLM_TIMEOUT    请求超时秒(默认 15, 对齐 crawl_run)
    LLM_ENABLED    总开关, off 强制走 rule(默认 on)

P3.5 embedding 语义向量(检索升级):
    KNOWLEDGE_EMBEDDING  开关(默认 off, on 时检索走语义路径)
    EMBEDDING_MODEL      向量模型名(默认 embedding-3, 智谱)

多模态 LLM 视觉理解(图片 GLM-4V / 视频 GLM-4V-Plus):
    KNOWLEDGE_MEDIA_LLM  开关(默认 off, on 时媒体入库走视觉理解)
    VISION_MODEL         视觉模型名(默认 glm-4v-flash, 智谱)

抓取智能清洗(网页正文提炼):
    KNOWLEDGE_CRAWL_LLM  开关(默认 off, on 时 crawl/run 走 LLM 清洗)

语音转写 ASR(P3.6 视频抽帧+ASR):
    ASR_MODEL            转写模型名(默认 glm-asr-2512, 智谱;
                         单文件 ≤25MB/≤30s, 由调用方分段)

检索重排 Rerank(P3.7):
    KNOWLEDGE_RERANK     开关(默认 off, on 时检索结果经重排模型精排)
    RERANK_MODEL         重排模型名(默认 rerank, 智谱;
                         候选 ≤128 条/单条 ≤4096 字符)

用法:
    from services.llm_client import provider_client
    text = provider_client.chat("system prompt", "user prompt")
    if text is None:  # 未配置/失败 → 调用方回退 rule
        ...
    vecs = provider_client.embed(["文本1", "文本2"])
    if vecs is None:  # 未配置/失败 → 调用方回退 2-gram
        ...
    text = provider_client.vision("描述这张图", "https://.../a.jpg")
    if text is None:  # 未配置/失败 → 调用方回退 rule(人工描述)
        ...
"""

import json
import logging
import os
import urllib.request

logger = logging.getLogger(__name__)

_TIMEOUT = int(os.environ.get("LLM_TIMEOUT", "15"))

# 视觉理解超时秒(图片/视频理解显著慢于纯文本, 单独放宽)
_VISION_TIMEOUT = int(os.environ.get("LLM_VISION_TIMEOUT", "60"))

# embedding 批量请求单批上限(对齐知识库 rebuild 回填的分批粒度)
EMBED_BATCH_SIZE = 16


def llm_enabled() -> bool:
    """llm 轨总开关(LLM_ENABLED=off 或未配置 API key 时关闭)"""
    if os.environ.get("LLM_ENABLED", "on").strip().lower() == "off":
        return False
    return bool(os.environ.get("LLM_API_KEY", "").strip())


def embedding_enabled() -> bool:
    """embedding 语义检索开关(P3.5)

    KNOWLEDGE_EMBEDDING=on 且配置 API key 时开启;
    LLM_ENABLED=off 总开关关闭时同样关闭(llm/embedding 共用 key)。
    """
    if not llm_enabled():
        return False
    return os.environ.get(
        "KNOWLEDGE_EMBEDDING", "off").strip().lower() == "on"


def media_llm_enabled() -> bool:
    """多模态视觉理解开关(图片/视频入库 llm 轨)

    KNOWLEDGE_MEDIA_LLM=on 且配置 API key 时开启;
    LLM_ENABLED=off 总开关关闭时同样关闭。
    """
    if not llm_enabled():
        return False
    return os.environ.get(
        "KNOWLEDGE_MEDIA_LLM", "off").strip().lower() == "on"


def crawl_llm_enabled() -> bool:
    """抓取智能清洗开关(crawl/run llm 轨)

    KNOWLEDGE_CRAWL_LLM=on 且配置 API key 时开启;
    LLM_ENABLED=off 总开关关闭时同样关闭。
    """
    if not llm_enabled():
        return False
    return os.environ.get(
        "KNOWLEDGE_CRAWL_LLM", "off").strip().lower() == "on"


def rerank_enabled() -> bool:
    """检索重排开关(P3.7)

    KNOWLEDGE_RERANK=on 且配置 API key 时开启;
    LLM_ENABLED=off 总开关关闭时同样关闭。
    """
    if not llm_enabled():
        return False
    return os.environ.get(
        "KNOWLEDGE_RERANK", "off").strip().lower() == "on"


def log_feature_status() -> None:
    """启动时输出各轨开关状态(P4.1 部署加固)

    一目了然: 哪些 LLM 轨生效、哪些回退 rule——容器化部署
    后环境变量遗漏/密钥未注入可即时发现, 不再静默降级。
    """
    key_set = bool(os.environ.get("LLM_API_KEY", "").strip())
    master = "on" if llm_enabled() else "off"
    logger.info("=" * 60)
    logger.info("LLM 功能开关状态(P4.1):")
    logger.info("  LLM_API_KEY       : %s",
                "已配置" if key_set else "未配置(llm 轨全部关闭)")
    logger.info("  LLM_ENABLED       : %s (总开关)", master)
    if key_set:
        tracks = [
            ("RAG llm 合成", "KNOWLEDGE_CHAT_LLM",
             os.environ.get("KNOWLEDGE_CHAT_LLM", "off")),
            ("Embedding 语义检索", "KNOWLEDGE_EMBEDDING",
             os.environ.get("KNOWLEDGE_EMBEDDING", "off")),
            ("多模态视觉理解", "KNOWLEDGE_MEDIA_LLM",
             os.environ.get("KNOWLEDGE_MEDIA_LLM", "off")),
            ("抓取智能清洗", "KNOWLEDGE_CRAWL_LLM",
             os.environ.get("KNOWLEDGE_CRAWL_LLM", "off")),
            ("Rerank 重排", "KNOWLEDGE_RERANK",
             os.environ.get("KNOWLEDGE_RERANK", "off")),
        ]
        for name, env, val in tracks:
            effective = "on" if val.strip().lower() == "on" and \
                master == "on" else "off(回退 rule)"
            logger.info("  %-18s: %s (%s=%s)", name, effective, env, val)
        logger.info("  模型: chat=%s vision=%s embed=%s asr=%s rerank=%s",
                     os.environ.get("LLM_MODEL", "glm-4-flash"),
                     os.environ.get("VISION_MODEL", "glm-4v-flash"),
                     os.environ.get("EMBEDDING_MODEL", "embedding-3"),
                     os.environ.get("ASR_MODEL", "glm-asr-2512"),
                     os.environ.get("RERANK_MODEL", "rerank"))
    logger.info("=" * 60)


class LLMProviderClient:
    """OpenAI 兼容 /chat/completions 与 /embeddings 客户端(urllib, 纯标准库)"""

    def chat(self, system: str, user: str,
             temperature: float = 0.3,
             model: str = "") -> str | None:
        """单轮对话补全, 失败/未配置返回 None(调用方回退 rule)

        Args:
            model: 指定模型档位(空则用 LLM_MODEL 默认档)。
                36号智能推广模块传 LLM_MODEL_PROMO(glm-5.3 Agent 链)。

        Returns:
            模型回复文本; 未配置 key、请求失败、响应异常均返回 None。
        """
        if not llm_enabled():
            return None
        from core.metrics import llm_timer
        with llm_timer("chat"):
            api_key = os.environ["LLM_API_KEY"].strip()
            base_url = os.environ.get(
                "LLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"
            ).rstrip("/")
            model = model or os.environ.get("LLM_MODEL", "glm-4-flash")
            payload = json.dumps({
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": temperature,
            }, ensure_ascii=False).encode("utf-8")
            request = urllib.request.Request(
                f"{base_url}/chat/completions", data=payload,
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {api_key}"},
                method="POST")
            try:
                with urllib.request.urlopen(
                        request, timeout=_TIMEOUT) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                content = (body.get("choices") or [{}])[0].get(
                    "message", {}).get("content")
                if not content or not str(content).strip():
                    logger.warning("llm_chat_empty_response model=%s", model)
                    return None
                return str(content).strip()
            except Exception as exc:
                logger.warning("llm_chat_failed(回退rule): %s", exc)
                return None

    def classify_dialog_intent(
            self, user_text: str,
            context_desc: str) -> dict | None:
        """LLM 对话意图分类(智能应答轨——规则 miss 时兜底)

        输入: 用户话语 + 最近对话上下文描述(商品语境/反问)
        输出: {"intent", "qty", "reply"} 或 None(解析失败回退
        规则轨 general 兜底)。防幻觉红线: chat 类 reply 由
        system prompt 禁数字(价格/金额由系统卡片展示)。

        intent 集: affirm(要买/加购) | negate(不要) | next(换
        一款) | command:<action>(对应规则轨 action) | chat(导
        购闲聊) | unknown(听不懂)
        """
        system = (
            "你是语音购物助手的意图分类器。根据对话上下文与用户"
            "话语, 只输出一个 JSON 对象, 不要任何其他文字:\n"
            '{"intent": "affirm|negate|next|setqty'
            '|command:<action>|chat|unknown", '
            '"qty": <1-9整数, 仅affirm/setqty时>, '
            '"reply": "<仅chat时: 导购回复, 30字内, 口语亲切, '
            '绝不提价格金额数字>"}\n'
            "分类规则:\n"
            "- affirm: 用户同意购买/加购(需要/要/买/来/加 N 件/"
            "就要这个/就它了)\n"
            "- negate: 拒绝当前推荐(不要/不喜欢/太贵了换换/算了"
            "这款)\n"
            "- next: 换下一款(换一款/还有吗/下一个/再看看)\n"
            "- setqty: 修正数量为指定总量(只要两件/一共三瓶/"
            "改为两件/两件就够了——设总数; '来两件/再要两件'"
            "是追加归 affirm)\n"
            "- command:<action>: 明确功能指令, action 从这些里选"
            " product.new|order.query|promo.query|trust.balance"
            "|xiaozhu.help\n"
            "- chat: 购物闲聊/咨询(问口感/度数/怎么喝/你是谁/"
            "你是真人吗/你叫什么)——reply 给出导购回应;\n"
            "  想结束对话(退出/再见/就到这/不用了谢谢)也归"
            " chat——reply 引导用户点左上角返回, 表示随时再唤\n"
            "- unknown: 无法理解\n"
            "注意: '太贵了'归 negate(拒绝这款); '怎么买'归 "
            "affirm; 身份类问题(你是谁/真人吗)一律归 chat。\n"
            "command 仅当话语明确对应那个功能才选——找不到"
            "匹配就归 chat 或 unknown, 绝不硬选最接近的。\n"
            "纯语气词(啊/嗯/哦)或听不清的话一律归 unknown。\n"
            "reply 铁律(导购人设):\n"
            "1. 称呼用「您」——禁用「亲」(不符合品牌人设);\n"
            "2. 商品信息(度数/香型/口感/原料/工艺)只能依据"
            "上下文提供的商品数据回答, 数据里没有的就诚实说"
            "『这个我帮您确认下』并引导看商品卡——绝不编造,"
            "绝不输出 XX 等占位符。\n"
            "礼貌用语(谢谢/多谢/辛苦了/不用了谢谢)是结束语"
            "不是购买应答——一律归 chat, 客气回应即可。\n"
            "qty 只依据本轮话语: 明确说了数量才填(如'来两件'填"
            "2); 模糊表述(几个/多来点)填 2; 未提数量填 1——"
            "忽略上文出现过的数量。"
        )
        user = (str(context_desc or "").strip()
                + "\n用户说: " + str(user_text or "").strip())
        raw = self.chat(system, user, temperature=0.1)
        if not raw:
            return None
        try:
            import re as _re
            m = _re.search(r"\{.*\}", raw, _re.S)
            data = json.loads(m.group(0)) if m else None
            if not isinstance(data, dict) \
                    or not data.get("intent"):
                return None
            qty = data.get("qty")
            return {
                "intent": str(data["intent"]).strip(),
                "qty": (int(qty) if str(qty).isdigit()
                        and 1 <= int(qty) <= 9 else 1),
                "reply": (str(data.get("reply") or "")
                          .strip()[:60] or None),
            }
        except Exception as exc:
            logger.warning("intent_classify_parse_fail: %s", exc)
            return None

    def embed(self, texts: list[str]) -> list[list[list[float]]] | None:
        """批量文本向量化(P3.5), 失败返回 None(调用方回退 2-gram)

        单批上限 EMBED_BATCH_SIZE, 超出自动分批串行请求;
        任一批失败整体返回 None(部分成功无意义, 全量回退)。

        Returns:
            与入参等长的向量列表; 未配置 key、请求失败、
            响应异常、数量不匹配均返回 None。
        """
        if not embedding_enabled() or not texts:
            return None
        from core.metrics import llm_timer
        api_key = os.environ["LLM_API_KEY"].strip()
        base_url = os.environ.get(
            "LLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"
        ).rstrip("/")
        model = os.environ.get("EMBEDDING_MODEL", "embedding-3")
        vectors: list[list[float]] = []
        for start in range(0, len(texts), EMBED_BATCH_SIZE):
            batch = [t for t in texts[start:start + EMBED_BATCH_SIZE] if t]
            if not batch:
                continue
            with llm_timer("embed"):
                payload = json.dumps({"model": model, "input": batch},
                                     ensure_ascii=False).encode("utf-8")
                request = urllib.request.Request(
                    f"{base_url}/embeddings", data=payload,
                    headers={"Content-Type": "application/json",
                             "Authorization": f"Bearer {api_key}"},
                    method="POST")
                try:
                    with urllib.request.urlopen(
                            request, timeout=_TIMEOUT) as resp:
                        body = json.loads(resp.read().decode("utf-8"))
                    data = body.get("data") or []
                    data.sort(key=lambda d: d.get("index", 0))
                    batch_vecs = [d.get("embedding") for d in data]
                    if len(batch_vecs) != len(batch) or any(
                            not v for v in batch_vecs):
                        logger.warning(
                            "llm_embed_count_mismatch model=%s", model)
                        return None
                    vectors.extend(batch_vecs)
                except Exception as exc:
                    logger.warning("llm_embed_failed(回退2-gram): %s", exc)
                    return None
        return vectors or None

    def vision(self, prompt: str, url: str,
               media_type: str = "image") -> str | None:
        """多模态视觉理解(图片 GLM-4V / 视频 GLM-4V-Plus)

        OpenAI 兼容 content 数组格式: text + image_url/video_url;
        失败/未配置返回 None(调用方回退 rule 轨人工描述)。

        Args:
            prompt: 视觉理解指令(如"客观描述图片内容")
            url: 媒体地址(http/https 公网可达)
            media_type: "image" | "video"

        Returns:
            模型回复文本; 未配置 key、URL 为空、请求失败、
            响应异常均返回 None。
        """
        if not media_llm_enabled() or not (url or "").strip():
            return None
        from core.metrics import llm_timer
        api_key = os.environ["LLM_API_KEY"].strip()
        base_url = os.environ.get(
            "LLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"
        ).rstrip("/")
        model = os.environ.get("VISION_MODEL", "glm-4v-flash")
        media_key = "image_url" if media_type == "image" else "video_url"
        payload = json.dumps({
            "model": model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": media_key, media_key: {"url": url.strip()}},
                ],
            }],
        }, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{base_url}/chat/completions", data=payload,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {api_key}"},
            method="POST")
        try:
            with llm_timer("vision"), urllib.request.urlopen(
                    request, timeout=_VISION_TIMEOUT) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            content = (body.get("choices") or [{}])[0].get(
                "message", {}).get("content")
            if not content or not str(content).strip():
                logger.warning("llm_vision_empty_response model=%s", model)
                return None
            return str(content).strip()
        except Exception as exc:
            logger.warning("llm_vision_failed(回退rule): %s", exc)
            return None

    def transcribe(self, audio_path: str) -> str | None:
        """语音转文本(GLM-ASR), multipart 上传本地音频文件

        纯标准库手工构造 multipart/form-data(不引入 requests);
        单文件限制 ≤25MB/≤30s 由调用方分段(对齐智谱约束);
        失败/未配置返回 None(调用方回退/跳过)。

        Args:
            audio_path: 本地音频文件路径(wav/mp3)

        Returns:
            转写文本; 未配置 key、文件读取失败、请求失败、
            响应异常、空转写均返回 None。
        """
        if not llm_enabled():
            return None
        import uuid
        from core.metrics import llm_timer
        model = os.environ.get("ASR_MODEL", "glm-asr-2512")
        api_key = os.environ["LLM_API_KEY"].strip()
        base_url = os.environ.get(
            "LLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"
        ).rstrip("/")
        try:
            with open(audio_path, "rb") as f:
                audio = f.read()
        except OSError as exc:
            logger.warning("llm_asr_read_failed: %s", exc)
            return None
        if not audio:
            return None
        boundary = "zhuxiang" + uuid.uuid4().hex
        fields = [("model", model), ("stream", "false")]
        parts = []
        for name, value in fields:
            parts.append(
                f"--{boundary}\r\nContent-Disposition: form-data; "
                f'name="{name}"\r\n\r\n{value}\r\n'.encode())
        fname = os.path.basename(audio_path) or "audio.wav"
        parts.append(
            (f"--{boundary}\r\nContent-Disposition: form-data; "
             f'name="file"; filename="{fname}"\r\n'
             f"Content-Type: audio/wav\r\n\r\n").encode())
        parts.append(audio)
        parts.append(f"\r\n--{boundary}--\r\n".encode())
        body = b"".join(parts)
        request = urllib.request.Request(
            f"{base_url}/audio/transcriptions", data=body,
            headers={"Content-Type":
                     f"multipart/form-data; boundary={boundary}",
                     "Authorization": f"Bearer {api_key}"},
            method="POST")
        try:
            with llm_timer("transcribe"), urllib.request.urlopen(
                    request, timeout=_TIMEOUT) as resp:
                result = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            logger.warning("llm_asr_failed(跳过): %s", exc)
            return None
        text = result.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()
        # 分段响应兼容: [{"text": ...}, ...]
        segments = result.get("segments") or []
        joined = "".join(str(s.get("text") or "")
                         for s in segments if isinstance(s, dict))
        if joined.strip():
            return joined.strip()
        logger.warning("llm_asr_empty_response model=%s", model)
        return None

    def rerank(self, query: str,
               documents: list[str]) -> list[tuple[int, float]] | None:
        """检索重排(P3.7): query 与候选文本的相关性打分排序

        返回 (原始下标, 相关性得分) 按得分降序; 候选超 128 条
        截断(智谱约束), 失败/未配置返回 None(调用方保持原序)。

        Returns:
            [(index, relevance_score), ...] 降序; 未配置 key、
            请求失败、响应异常、空结果均返回 None。
        """
        if not rerank_enabled() or not documents:
            return None
        from core.metrics import llm_timer
        api_key = os.environ["LLM_API_KEY"].strip()
        base_url = os.environ.get(
            "LLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"
        ).rstrip("/")
        model = os.environ.get("RERANK_MODEL", "rerank")
        docs = [d[:4096] for d in documents[:128]]
        payload = json.dumps({"model": model, "query": query[:4096],
                              "documents": docs, "top_n": len(docs)},
                             ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{base_url}/rerank", data=payload,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {api_key}"},
            method="POST")
        try:
            with llm_timer("rerank"), urllib.request.urlopen(
                    request, timeout=_TIMEOUT) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            logger.warning("llm_rerank_failed(保持原序): %s", exc)
            return None
        results = body.get("results") or []
        ranked = []
        for r in results:
            idx = r.get("index")
            score = r.get("relevance_score")
            if isinstance(idx, int) and 0 <= idx < len(docs) \
                    and isinstance(score, (int, float)):
                ranked.append((idx, float(score)))
        ranked.sort(key=lambda x: x[1], reverse=True)
        return ranked or None

    def synthesize(self, text: str, speed: float = None,
                   voice: str = None) -> bytes | None:
        """语音合成 TTS(智谱 cogtts, WAV 返回)

        微信 X5 无系统 TTS 引擎(speechSynthesis 入队无声)
        ——服务端合成兜底: 电脑走浏览器 TTS, 微信走本接口。
        失败/未配置返回 None(前端静默降级不播报)。
        v2 G: speed(0.5-2.0, spike 实证 cogtts 语速生效)
        /voice per-call 透传——None 走环境变量默认。
        """
        t = str(text or "").strip()
        if not t:
            return None
        if "LLM_API_KEY" not in os.environ:
            return None
        from core.metrics import llm_timer
        api_key = os.environ["LLM_API_KEY"].strip()
        base_url = os.environ.get(
            "LLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"
        ).rstrip("/")
        payload_dict = {
            "model": os.environ.get("TTS_MODEL", "cogtts"),
            "input": t[:200],
            "voice": voice
            or os.environ.get("TTS_VOICE", "tongtong"),
            "response_format": "wav"}
        if speed is not None:
            payload_dict["speed"] = max(
                0.5, min(2.0, float(speed)))
        payload = json.dumps(
            payload_dict, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{base_url}/audio/speech", data=payload,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {api_key}"},
            method="POST")
        try:
            with llm_timer("synthesize"), urllib.request.urlopen(
                    request, timeout=_TIMEOUT) as resp:
                data = resp.read()
                ctype = resp.headers.get("Content-Type", "")
            if (ctype.startswith("audio/")
                    and isinstance(data, bytes)
                    and len(data) > 500):
                return data
            logger.warning("llm_tts_bad_response ct=%s len=%s",
                           ctype, len(data) if data else 0)
            return None
        except Exception as exc:
            logger.warning("llm_tts_failed(跳过播报): %s", exc)
            return None

    def synthesize_mp3(self, text: str, speed: float = None,
                       voice: str = None) -> bytes | None:
        """语音合成 MP3(智谱 cogtts 仅支持 wav → lameenc 转码)

        小程序 InnerAudioContext 对 wav 兼容差(Android 无声)
        ——mp3 双端兼容(小程序原生 / H5 WebAudio decodeAudioData)。
        lameenc 未安装/转码失败 → 回退 wav(调用方按 wav 处理)。
        v2 G: speed/voice 透传(synthesize 同口径)。
        """
        wav = self.synthesize(text, speed=speed, voice=voice)
        if not wav:
            return None
        return _wav_to_mp3(wav) or wav


def _wav_to_mp3(wav_bytes: bytes) -> bytes | None:
    """WAV(16-bit PCM) → MP3(lameenc 64kbps)

    cogtts wav 为标准 PCM 头; 非 16-bit/解析失败返回 None。
    """
    import io
    import wave
    try:
        import lameenc
    except ImportError:
        return None
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as w:
            nch = w.getnchannels()
            rate = w.getframerate()
            width = w.getsampwidth()
            frames = w.readframes(w.getnframes())
        if width != 2 or nch not in (1, 2) or not frames:
            return None
        enc = lameenc.Encoder()
        enc.set_channels(nch)
        enc.set_in_sample_rate(rate)
        enc.set_bit_rate(64)
        enc.set_quality(2)
        mp3 = enc.encode(bytes(frames)) + enc.flush()
        return bytes(mp3) if len(mp3) > 500 else None
    except Exception as exc:
        logger.warning("wav_to_mp3_failed: %s", exc)
        return None


provider_client = LLMProviderClient()
