"""84号·短剧智能生产工厂 P0(连续剧本引擎, 2026-10-09)

外部方案《短剧智能生产工厂大模型》七模块甄别后真缺口 P0:
复用 36号雷达(热点)/73号(渲染发布)/44号(学习)/76号(消费引流)
做底座, 84号新建**连续剧本**能力——多集 bible+节拍表+多角色
分镜剧本, 为 76号消费端持续供剧集内容, 为网站引流服务。

核心设计:
- 规格: 6-80 集×60-180s; 每集三拍(钩子 hook/反转 twist/
  悬念 cliffhanger)——外部方案「前3秒钩子/每30秒反转/结尾
  悬念」的工程化收敏(单集 3-6 句对话, 反转 1 次足矣, 硬套
  30s 反转在 60s 集内不可行)。
- 角色一致性: cast 挂接 IP_PERSONAS 八人设(数字人基准图=
  角色一致性, 对 LoRA 三重锁的替代)——allow_alcohol=False
  人设拒入(广告法§40); 服装维度留给 P1 --outfit。
- LLM 三轨: PROMO_LLM_MODEL 主档→glm-4-flash 备→rule 兜底
  (73号同款降级链); 输出 JSON assert_schema fail-hard,
  lint_series 机器复核。
- 落盘: backend/series84/d84_<hash>.json(幂等覆盖)。

P1(生产编排)/P2(题材研判+评分回流)按观察窗排期。
"""

import hashlib
import json
import logging
import os
import re
from pathlib import Path

from services.sv73_script_service import IP_PERSONAS

logger = logging.getLogger(__name__)

# 落盘根(backend/series84 → 容器 /app/series84——与 73号
# storyboards 同级; compose 绑定挂载防 rebuild 清空)
SERIES_DIR = Path(__file__).resolve().parent.parent / "series84"
DEFAULT_EPISODES = 6
MIN_EPISODES, MAX_EPISODES = 4, 80
# 口播节奏上限(对话剧单句; 73号独白 25 字更严)
LINE_MAX_CHARS = 30
MIN_LINES_PER_EP = 4

# 时长双档(2026-10-17 对标懒趴趴短剧): 第 1/5/9…集为长集
# (建世界观+充值锚点), 其余短集(爆发集抢完播率)——
# 长档 90-150s/台词 5-8 句, 短档 45-90s/台词 4-6 句
LONG_DURATION = (90, 150)
SHORT_DURATION = (45, 90)


def episode_length_plan(ep_no: int) -> str:
    """集号 → 长集(long)/短集(short): 1,5,9,13… 长档"""
    return "long" if (int(ep_no) - 1) % 4 == 0 else "short"


# 合规: 酒类连续剧角色白名单(IP 双主角+四中老年——儿童禁入)
DEFAULT_CAST = ("zhuxiaomei", "xiaozhushuaige")
ALLOWED_CAST = ("zhuxiaomei", "xiaozhushuaige",
                "role_zhongnian_f", "role_zhongnian_m",
                "role_laonian_f", "role_laonian_m")

# P2 剧集评分五因子(44号治理档案型, batch 55——单一事实源)
D84_WEIGHTS = {
    "theme_appeal": 25.0,   # 题材吸引力(热点分值归一)
    "cast_fit": 20.0,       # 人设匹配(IP 主角满配/角色数)
    "pacing": 20.0,         # 节奏(单集时长 45-120s 达标率)
    "script_quality": 15.0, # 剧本质量(lint 零瑕疵+LLM 轨)
    "traffic_value": 20.0,  # 引流值(76号解锁转化, 累计观测)
}


def _clip(s, n):
    return str(s or "").strip()[:n]


def _now_iso() -> str:
    import datetime as _dt
    return _dt.datetime.now(
        _dt.timezone.utc).isoformat()


def drama84_mode() -> str:
    """三态开关(off=路由 409 拒绝, shadow/on 常开)"""
    return os.environ.get("DRAMA84_MODE", "on").strip().lower()


def is_kill() -> bool:
    """一键回退(DRAMA84_KILL=on 全端点 409)"""
    return os.environ.get(
        "DRAMA84_KILL", "off").strip().lower() in ("on", "1", "true")


# --------------------------------------------------------
# lint_series: 机器复核(A2 同款理念——LLM 轨先过机器再落盘)
# --------------------------------------------------------

def lint_series(series: dict) -> list:
    """剧本瑕疵清单(空=通过)——结构/句长/角色在册/节拍齐/叠字"""
    flaws = []
    eps = series.get("episodes") or []
    if not (MIN_EPISODES <= len(eps) <= MAX_EPISODES):
        flaws.append(f"集数越界({len(eps)}, 须 {MIN_EPISODES}"
                     f"-{MAX_EPISODES})")
    for i, ep in enumerate(eps, 1):
        if int(ep.get("ep") or 0) != i:
            flaws.append(f"第{i}集 ep 序号错({ep.get('ep')})")
        beats = ep.get("beats") or {}
        for k in ("hook", "twist", "cliffhanger"):
            if not _clip(beats.get(k), 60):
                flaws.append(f"第{i}集缺节拍 {k}")
        dur = int(ep.get("duration") or 0)
        if not (45 <= dur <= 180):
            flaws.append(f"第{i}集时长越界({dur}s, 须 45-180)")
        lines = ep.get("lines") or []
        if len(lines) < MIN_LINES_PER_EP:
            flaws.append(f"第{i}集台词不足({len(lines)}<"
                         f"{MIN_LINES_PER_EP})")
        for ln in lines:
            text = _clip(ln.get("text"), 200)
            persona = ln.get("persona") or ""
            if persona not in ALLOWED_CAST:
                flaws.append(
                    f"第{i}集角色未在册({persona})")
            if len(text) > LINE_MAX_CHARS:
                flaws.append(f"第{i}集台词超长"
                             f"({len(text)}>{LINE_MAX_CHARS})")
            if re.search(r"(.)\1{2,}", text):
                flaws.append(f"第{i}集叠字瑕疵({text[:12]})")
    return flaws


class Drama84ScriptService:
    """连续剧本引擎(bible+分集+多角色分镜)"""

    def series_path(self, sid: str) -> Path:
        return SERIES_DIR / f"{sid}.json"

    def list_series(self) -> list:
        SERIES_DIR.mkdir(parents=True, exist_ok=True)
        rows = []
        for p in sorted(SERIES_DIR.glob("d84_*.json")):
            try:
                rows.append(json.loads(p.read_text("utf-8")))
            except Exception:  # noqa: BLE001
                continue
        return rows

    def get_series(self, sid: str) -> dict | None:
        p = self.series_path(sid)
        return (json.loads(p.read_text("utf-8"))
                if p.is_file() else None)

    # ---------- scriptId 确定性派生(73号同款幂等) ----------

    @staticmethod
    def series_id(hotspot: dict, episodes: int,
                  cast: tuple) -> str:
        fp = str(hotspot.get("fingerprint")
                 or hashlib.sha256(str(hotspot.get("title") or
                                       "").encode(
                     "utf-8")).hexdigest()[:16])
        raw = f"{fp}|{episodes}|{','.join(sorted(cast))}"
        return "d84_" + hashlib.sha256(
            raw.encode("utf-8")).hexdigest()[:12]

    # ---------- 主入口 ----------

    async def generate_series(self, hotspot: dict,
                              episodes: int = DEFAULT_EPISODES,
                              cast: tuple = DEFAULT_CAST,
                              title: str = "") -> dict:
        """热点+阵容 → 连续剧 bible+分集剧本(落盘幂等)"""
        episodes = max(MIN_EPISODES,
                       min(MAX_EPISODES, int(episodes or 6)))
        bad = [c for c in cast if c not in IP_PERSONAS]
        if bad:
            raise ValueError(f"人设未注册: {bad}")
        alc = [c for c in cast
               if not IP_PERSONAS[c].get("allow_alcohol", True)]
        if alc:
            raise ValueError(
                f"人设不适用于酒类内容(广告法§40): {alc}")
        sid = self.series_id(hotspot, episodes, tuple(cast))
        series = None
        for model in ("", "glm-4-flash"):
            series = self._llm_series(hotspot, episodes, cast,
                                      title, model)
            if series is not None \
                    and not lint_series(series):
                series["track"] = (model or "main")
                self._normalize_durations(series)
                break
            series = None
        if series is None:
            series = self._rule_series(hotspot, episodes, cast,
                                       title)
            series["track"] = "rule"
        self._assert_schema(series)
        series["seriesId"] = sid
        series["lintFlaws"] = lint_series(series)
        SERIES_DIR.mkdir(parents=True, exist_ok=True)
        self.series_path(sid).write_text(
            json.dumps(series, ensure_ascii=False, indent=1),
            encoding="utf-8")
        return series

    # ---------- 时长档位校正(LLM 轨后处理, 确定性保证) ----------

    @staticmethod
    def _normalize_durations(series: dict) -> None:
        """按集号双档 clamp duration(LLM 出界时拉回档内;
        缺失给档中值)——长 90-150s / 短 45-90s"""
        for ep in series.get("episodes") or []:
            lo, hi = (LONG_DURATION if episode_length_plan(
                ep.get("ep")) == "long" else SHORT_DURATION)
            try:
                d = int(ep.get("duration") or 0)
            except (TypeError, ValueError):
                d = 0
            ep["duration"] = min(hi, max(lo, d)) if d > 0 \
                else (lo + hi) // 2

    # ---------- LLM 轨(bible+episodes 一次成形) ----------

    def _llm_series(self, hotspot: dict, episodes: int,
                    cast: tuple, title: str,
                    model: str) -> dict | None:
        try:
            from services.llm_client import provider_client
            persona_lines = "\n".join(
                f"- {c}({IP_PERSONAS[c]['name']}): "
                f"{_clip(IP_PERSONAS[c].get('tagline'), 40)}"
                for c in cast)
            system = (
                "你是连续短剧编剧。根据热点与角色阵容, 只输出一个"
                " JSON 对象, 不要任何其他文字。结构:\n"
                '{"title": "<剧名≤10字>", "theme": "<题材>", '
                '"world": "<世界观≤80字>", '
                '"goldenFinger": "<金手指/爽点设定≤40字>", '
                '"arc": ["<第1集阶段>", ...共' + str(episodes) + "个], "
                '"cast": [{"persona": "<人设键>", "character": '
                '"<剧内角色名>", "role": "<主角/对手/长辈等>", '
                '"traits": "<性格标签≤12字>"}], '
                '"episodes": [{"ep": <集号从1起>, "title": '
                '"<本集题≤12字>", "duration": <60-120整数秒>, '
                '"beats": {"hook": "<开场钩子≤40字>", "twist": '
                '"<中段反转≤40字>", "cliffhanger": '
                '"<结尾悬念≤40字>"}, "lines": [{"persona": '
                '"<人设键>", "character": "<剧内名>", "emotion": '
                '"<情绪>", "scene": "<场景≤8字>", "text": '
                '"<台词≤30字>"}]}]}\n'
                "铁律:\n"
                "1. 共 " + str(episodes) + " 集, 每集 lines 4-6 句, "
                "多角色交替对话(同一角色不连说两句);\n"
                "2. 第1句台词必须演 hook, 最后一句留 cliffhanger "
                "悬念(未完待续感);\n"
                "3. 台词口语化≤30字, 禁叠字瑕疵;\n"
                "4. 角色只能从阵容选: \n" + persona_lines + "\n"
                "5. 剧情积极向上可连载, 可自然融入竹香酒品牌"
                "(不硬广), 每集至少一处品牌或产品自然出现;\n"
                "6. 禁暴力低俗/未成年相关/功效宣称/绝对化用语;\n"
                "7. 时长双档: 第1/5/9…集为长集(90-150秒, lines "
                "5-8句, 建世界观+情绪锚点), 其余短集(45-90秒, "
                "lines 4-6句, 抢完播率)——本条覆盖铁律1的句数。"
            )
            user = (f"热点: {hotspot.get('title')} "
                    f"(平台{hotspot.get('platform')}, "
                    f"分值{hotspot.get('score')})\n"
                    f"集数: {episodes}\n"
                    + (f"剧名倾向: {title}\n" if title else ""))
            raw = provider_client.chat(
                system, user, temperature=0.5, model=model,
                # 大 JSON 单次成形(6 集全量)——全局默认超时不够,
                # 生产实证 glm 主档 read timeout 降级; 专用 90s
                timeout=90)
            if not raw:
                return None
            m = re.search(r"\{[\s\S]*\}", raw)
            if not m:
                return None
            series = json.loads(m.group(0))
            series["hotspot"] = {
                "title": _clip(hotspot.get("title"), 60),
                "platform": _clip(hotspot.get("platform"), 20),
                "score": hotspot.get("score") or 0,
            }
            return series
        except Exception as exc:  # noqa: BLE001
            logger.info("drama84_llm_skip(model=%s): %s",
                        model or "main", exc)
            return None

    # ---------- rule 兜底(确定性三幕循环) ----------

    def _rule_series(self, hotspot: dict, episodes: int,
                     cast: tuple, title: str) -> dict:
        ht = _clip(hotspot.get("title"), 20) or "竹林故事"
        a, b = cast[0], cast[1] if len(cast) > 1 else cast[0]
        na = IP_PERSONAS[a]["name"]
        nb = IP_PERSONAS[b]["name"]
        stages = ("困境", "转机", "前行", "考验", "翻盘", "圆梦")
        # 台词随 stage 变化(每集有差异——固定模板硬循环会让
        # 每集逐字相同, 观感不可接受)
        beat_lines = {
            "困境": [("焦急", "这事比想象中难办"),
                     ("委屈", "难道就这样认输了")],
            "转机": [("惊喜", "原来转机就在眼前"),
                     ("振奋", "这一步走对了")],
            "前行": [("坚定", "认准的路就走下去"),
                     ("感概", "一路走来真不容易")],
            "考验": [("紧张", "这次的难关不简单"),
                     ("沉着", "越乱越要稳得住")],
            "翻盘": [("扬眉", "终于等到这一天"),
                     ("欣喜", "苦日子熬出头了")],
            "圆梦": [("欣慰", "竹香酒馆守住了"),
                     ("感恩", "谢谢一路撑住的人")],
        }
        eps = []
        for i in range(1, episodes + 1):
            stage = stages[min(i - 1, len(stages) - 1)]
            va = beat_lines[stage][0]
            vb = beat_lines[stage][1]
            eps.append({
                "ep": i,
                "title": f"{ht}·{stage}",
                "duration": (120 if episode_length_plan(i) == "long"
                             else 70),
                "beats": {
                    "hook": f"第{i}集: {ht}风波又起",
                    "twist": f"{na}发现转机藏在竹香酒里",
                    "cliffhanger": "更大的考验正在靠近",
                },
                "lines": [
                    {"persona": a, "character": na,
                     "emotion": va[0], "scene": "竹林小院",
                     "text": va[1]},
                    {"persona": b, "character": nb,
                     "emotion": "沉稳", "scene": "竹林小院",
                     "text": "别慌, 我有一坛竹香老酒"},
                    {"persona": a, "character": na,
                     "emotion": vb[0], "scene": "竹林小院",
                     "text": vb[1]},
                    {"persona": b, "character": nb,
                     "emotion": "笃定", "scene": "竹林小院",
                     "text": "竹香入喉, 办法就有了, 且看下回"},
                ],
            })
        return {
            "title": _clip(title, 12) or f"竹香酒馆·{ht}",
            "theme": "逆袭成长",
            "world": "江南竹镇上一家传承三代的竹香酒馆, "
                     "年轻一代用真诚与手艺守住家业",
            "goldenFinger": "一坛祖传竹香老酒的味觉记忆",
            "arc": [stages[min(i, len(stages) - 1)]
                    for i in range(episodes)],
            "cast": [
                {"persona": c, "character": IP_PERSONAS[c]["name"],
                 "role": "主角" if c == a else "搭档",
                 "traits": _clip(
                     IP_PERSONAS[c].get("tagline"), 12)}
                for c in dict.fromkeys((a, b))],
            "episodes": eps,
            "hotspot": {
                "title": _clip(hotspot.get("title"), 60),
                "platform": _clip(hotspot.get("platform"), 20),
                "score": hotspot.get("score") or 0,
            },
        }

    # ---------- P1: 集成片登记 36号 content(76号组剧消费) ----------

    async def register_episode(self, sid: str, ep_no: int,
                               video: str,
                               duration: float) -> dict:
        """84号集成片 → 36号 content(episode_id 供 76号组剧;
        状态恒 pending 走三审, sv73.storyboardId={sid}_ep{n})
        """
        series = self.get_series(sid)
        if series is None:
            raise KeyError(f"剧本不存在({sid})")
        eps = series.get("episodes") or []
        if not (1 <= ep_no <= len(eps)):
            raise ValueError(f"集号越界(ep={ep_no})")
        ep = eps[ep_no - 1]
        # 幂等: 同 storyboardId 已登记直接返回
        from repositories.promo_repository import PromoRepository
        repo = PromoRepository()
        rows = await repo.list_contents()
        sbid = f"{sid}_ep{ep_no}"
        for c in rows or []:
            if ((c.get("sv73") or {}).get("storyboardId")
                    == sbid):
                return c
        flaws = lint_series(series)
        # 简介复读机(2026-10-17 对标夫仔): 标题拼入金手指卖点句
        # ——单条视频即完整钩子, 脱离合集上下文也能转化
        gf = _clip(series.get("goldenFinger"), 40)
        base = f"{series['title']}·第{ep_no}集 " \
               f"{ep.get('title') or ''}".strip()
        import datetime as _dt
        content = {
            "contentId": await repo.next_id("content"),
            "contentGroupId": 0,
            "hotspotId": int((series.get("hotspot") or {})
                             .get("hotspotId") or 0),
            "platform": "douyin",
            "title": (f"{base} | {gf}" if gf else base)[:60],
            "body": " ".join(ln["text"] for ln in ep["lines"]),
            "hashtags": "#独白短剧 #AI短剧",
            "cta": "",
            "coverHint": "84号连续短剧(数字人分镜轮切)",
            "complianceScore": (100 if not flaws else 60),
            "complianceViolations": flaws,
            "hardFail": [],
            "requiresManualReview": bool(flaws),
            "status": "pending",
            "shortCode": "",
            "receipt": {},
            "scheduledAt": "",
            "publishedAt": "",
            "createdAt": _dt.datetime.now(
                _dt.timezone.utc).isoformat(),
            "sv73": {
                "storyboardId": sbid,
                "video": video,
                "pages": [],
                "audioTrack": "",
                "totalDuration": duration,
                "track": series.get("track") or "rule",
                "bgmMood": "warm",
                "persona": ",".join(sorted({
                    ln["persona"] for ln in ep["lines"]})),
                "shadow": False,
                "template": "drama84_series",
                "complianceScore": (100 if not flaws else 60),
                "matchTop": series.get("title"),
                "matchFallback": False,
            },
        }
        return await repo.save_content(content)

    # ---------- P2-A: 题材研判(热点→选题包) ----------

    async def derive_topics(self, limit: int = 8) -> dict:
        """36号雷达热点 → 短剧题材化选题包(LLM 研判+落盘)

        选题包: 每热点 {题材/人群/爽点结构/风险标签/适配人设/
        建议集数}; fail-soft——LLM 失败给热点直通占位包。
        """
        from repositories.promo_repository import PromoRepository
        rows = await PromoRepository().list_hotspots(limit=limit) \
            or []
        picks = [h for h in rows
                 if _clip(h.get("title"), 4)][:limit]
        topics = self._llm_topics(picks) or [
            {"hotspotId": int(h.get("hotspotId") or 0),
             "hotspot": _clip(h.get("title"), 60),
             "theme": "品牌向剧情",
             "audience": "25-45 白酒兴趣人群",
             "hookPattern": "热点开场+竹香酒转机",
             "risk": "无",
             "cast": list(DEFAULT_CAST),
             "episodes": DEFAULT_EPISODES,
             "score": float(h.get("score") or 0)}
            for h in picks]
        out = {"generatedAt": _now_iso(),
               "count": len(topics), "topics": topics}
        SERIES_DIR.mkdir(parents=True, exist_ok=True)
        (SERIES_DIR / "d84_topics.json").write_text(
            json.dumps(out, ensure_ascii=False, indent=1),
            encoding="utf-8")
        return out

    def _llm_topics(self, picks: list) -> list | None:
        if not picks:
            return []
        try:
            from services.llm_client import provider_client
            hs_lines = "\n".join(
                f"- id={int(h.get('hotspotId') or 0)} "
                f"[{h.get('platform')}] {_clip(h.get('title'), 40)}"
                f" (分值{h.get('score') or 0})"
                for h in picks)
            system = (
                "你是短剧选题策划。对每个热点输出一个 JSON 数组"
                "(仅数组, 无其他文字), 每元素: "
                '{"hotspotId": <原id>, "hotspot": "<原题>", '
                '"theme": "<题材≤8字>", "audience": "<人群≤16字>", '
                '"hookPattern": "<爽点结构≤20字>", "risk": '
                '"<风险标签, 无则填无>", "cast": '
                '["zhuxiaomei", "xiaozhushuaige"]子集, '
                '"episodes": <6-12>, "score": <原分值>}\n'
                "铁律: cast 只能从 ['zhuxiaomei', "
                "'xiaozhushuaige', 'role_zhongnian_f', "
                "'role_zhongnian_m', 'role_laonian_f', "
                "'role_laonian_m'] 选; 剧情须能自然融入竹香酒"
                "品牌; 题材积极无违规。\n热点清单:\n" + hs_lines
            )
            raw = provider_client.chat(
                system, "开始研判", temperature=0.4,
                timeout=60)
            m = re.search(r"\[[\s\S]*\]", raw or "")
            if not m:
                return None
            topics = json.loads(m.group(0))
            ok = all(
                isinstance(t.get("cast"), list)
                and set(t["cast"]) <= set(ALLOWED_CAST)
                for t in topics)
            return topics if ok else None
        except Exception as exc:  # noqa: BLE001
            logger.info("drama84_topics_llm_skip: %s", exc)
            return None

    # ---------- P2-D: 评分回流(76号解锁漏斗→44号) ----------

    async def score_series(self, sid: str,
                           unlocks: int = 0,
                           online_days: float = 1.0) -> dict:
        """剧集五因子评分 → 44号治理档案回流+快照落盘

        unlocks/online_days: 76号 drama76_unlocks 计数与上线
        天数(调用方路由侧聚合); 评分=决策时快照, actualAction
        固定 released(治理档案型不参与 Hedge 周期)。
        """
        series = self.get_series(sid)
        if series is None:
            raise KeyError(f"剧本不存在({sid})")
        eps = series.get("episodes") or []
        flaws = lint_series(series)
        hs_score = float(
            (series.get("hotspot") or {}).get("score") or 0)
        cast = {c.get("persona") for c in
                (series.get("cast") or [])}
        ip_full = bool(cast & set(DEFAULT_CAST))
        cast_n = len(cast)

        def _pacing_ok(ep: dict) -> bool:
            """双档达标(2026-10-17): 长集 90-180s / 短集 45-120s"""
            dur = int(ep.get("duration") or 0)
            return (LONG_DURATION[0] <= dur <= 180 if
                    episode_length_plan(ep.get("ep")) == "long"
                    else 45 <= dur <= 120)

        pacing_ok = sum(1 for e in eps if _pacing_ok(e))
        factors = [
            {"name": "theme_appeal",
             "score": round(min(100.0, hs_score), 1),
             "weight": D84_WEIGHTS["theme_appeal"],
             "detail": f"热点分值 {hs_score}"},
            {"name": "cast_fit",
             "score": round(
                 (100 if ip_full else 60)
                 * min(1.0, cast_n / 2), 1),
             "weight": D84_WEIGHTS["cast_fit"],
             "detail": f"角色 {cast_n} 个/IP 主角 "
                       f"{'在列' if ip_full else '缺位'}"},
            {"name": "pacing",
             "score": round(
                 100 * pacing_ok / max(1, len(eps)), 1),
             "weight": D84_WEIGHTS["pacing"],
             "detail": f"{pacing_ok}/{len(eps)} 集达标"
                       f"(长集90-180s/短集45-120s)"},
            {"name": "script_quality",
             "score": (100.0 if not flaws and
                       (series.get("track") or "")
                       .startswith(("main", "glm")) else
                       (70.0 if not flaws else 40.0)),
             "weight": D84_WEIGHTS["script_quality"],
             "detail": f"track={series.get('track')} "
                       f"lint={len(flaws)} 瑕疵"},
            {"name": "traffic_value",
             "score": round(min(100.0,
                                unlocks * 20.0
                                / max(1.0, online_days)), 1),
             "weight": D84_WEIGHTS["traffic_value"],
             "detail": f"{unlocks} 解锁/上线 "
                       f"{online_days:.1f} 天"},
        ]
        total = round(sum(
            f["score"] * f["weight"] / 100.0
            for f in factors), 1)
        snapshot = {"seriesId": sid,
                    "title": series.get("title"),
                    "score": total, "factors": factors,
                    "unlocks": unlocks,
                    "onlineDays": online_days,
                    "scoredAt": _now_iso()}
        # 44号治理档案回流(best-effort——管道不可用时快照
        # 仍落盘, 评分不因回流失败而丢)
        try:
            from services.ai_learning_service import (
                submit_feedback,
            )
            await submit_feedback({
                "scorerId": "drama84_series",
                "factors": factors,
                "scoreAtDecision": total,
                "actualAction": "released",
                "correct": True,
                "note": f"84号剧集评分 {sid} "
                        f"{series.get('title')}"})
        except Exception as exc:  # noqa: BLE001
            logger.info("drama84_feedback_skip: %s", exc)
        SERIES_DIR.mkdir(parents=True, exist_ok=True)
        (SERIES_DIR / f"{sid}_score.json").write_text(
            json.dumps(snapshot, ensure_ascii=False,
                       indent=1), encoding="utf-8")
        return snapshot

    # ---------- schema fail-hard(73号 assert 同款纪律) ----------

    @staticmethod
    def _assert_schema(s: dict) -> None:
        for k in ("title", "theme", "world", "goldenFinger"):
            if not _clip(s.get(k), 200):
                raise ValueError(f"bible 字段缺失: {k}")
        if not (s.get("arc") and s.get("cast")):
            raise ValueError("bible 缺 arc/cast")
        eps = s.get("episodes") or []
        if not eps:
            raise ValueError("episodes 为空")
        for ep in eps:
            for k in ("ep", "title", "duration", "beats",
                      "lines"):
                if k not in ep:
                    raise ValueError(f"第{ep.get('ep')}集缺 {k}")
            for ln in ep["lines"]:
                for k in ("persona", "character", "text"):
                    if not _clip(ln.get(k), 200):
                        raise ValueError(
                            f"第{ep.get('ep')}集台词缺 {k}")
