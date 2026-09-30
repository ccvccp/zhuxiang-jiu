"""73号(sv)·短视频智能模型 P0——全链编排服务 v1.0

职责: 热点 → 剧本(73号 sv73_script) → 渲染+合成(73号 sv73_render)
      → 内容登记(36号 content 惯例) 一键编排
链路: 36号雷达热点 → [本编排] → mp4 产物 + content(pending)
      → 36号人工三审 review → approve → publish → rpa_pending
      (channels/douyin-bot RPA 拉单发布)

发布链铁律(三审闸门不动):
    73号编排止步于 content 登记(status=pending 待人工 review)——
    发布资格决策全部留在 36号人工三审位(approve→publish_content
    →process_publish_queue→rpa_pending 既有链路, 零新审端点)

SV73_MODE 三态:
    off: 决策面 409(路由层门控)
    shadow: 影子观察期——全链产出+content 留痕(sv73.shadow=true,
            不建 attract 短码不占归因通道, 仅审计)
    real: 辅助生产期——完整登记(短码+产物关联), content 待人工
          review 后走 36号 rpa_pending 惯例发布

幂等: 同热点同品类重复 run → scriptId 同 → mp4 覆盖; content 按
      sv73.storyboardId 查重复用(不重复登记)

变更链:
    - 2026-09-30 P0 立项(docs/73号_短视频智能模型_创新规划方案.md)
"""

from __future__ import annotations

import asyncio
import logging
import os

from services.sv73_script_service import (
    Sv73ScriptService, current_mode, DEFAULT_CATEGORY,
    DEFAULT_TEMPLATE,
)
from services.sv73_render_service import Sv73RenderService
from services.sv73_match_service import Sv73MatchService
from services.promo_service import PromoService

logger = logging.getLogger(__name__)

# 发布平台(36号 RPA_PLATFORMS 成员; 2026-09-30 P2 平台扩展
# 四平台全开——36号 rpa_pending 清单/回执闭环平台无关)
SV73_PLATFORMS = ("wechat_channels", "douyin",
                  "xiaohongshu", "weibo")
DEFAULT_PLATFORM = "wechat_channels"

# 平台内容形态注册表(发布执行层口径)
#   video: mp4 直传(bot 实证: channels/douyin; xhs 视频笔记
#          ——发布流待登录态联调, 形态支持)
#   card : 平台无视频通道——降级图文卡(封面 PNG+文案);
#          weibo-cli C 系无视频命令(2026-09-27 实证), 走
#          36号 coverUrl 封面 + upload_pic/upload_url_text 三步
SV73_PLATFORM_FORMS = {
    "wechat_channels": "video",
    "douyin": "video",
    "xiaohongshu": "video",
    "weibo": "card",
}

# 品类自动档(P1: category="auto" 经匹配引擎推荐)
CATEGORY_AUTO = "auto"


def render_mode_enabled(mode: str | None = None) -> bool:
    """渲染步开关(SV73_RENDER_MODE, 默认 off——铁律)

    生产灰度实证(2026-09-30): 2 核小机 ffmpeg zoompan 合成
    300s 超时(load 峰值 75 压死 sshd)——渲染+合成对齐 36号
    产线定位**留在开发机跑**; 生产 pipeline 只跑"剧本+登记",
    mp4 由开发机渲染后经 /api/sv73/render/attach 挂载元数据
    (RPA 发布本就在开发机执行, 文件无需上生产)。
    显式 mode 参数覆盖 env(单次控制; 测试/开发机全链用)。
    """
    m = (mode if mode is not None
         else os.environ.get("SV73_RENDER_MODE", "off"))
    return str(m).strip().lower() in ("on", "1", "true")


def _now_iso() -> str:
    from datetime import datetime, UTC
    return datetime.now(UTC).isoformat(timespec="seconds")


class Sv73PipelineService:
    """73号全链编排(剧本→渲染→登记; 发布决策留 36号人工三审)"""

    def __init__(self, script: Sv73ScriptService = None,
                 render: Sv73RenderService = None,
                 promo: PromoService = None):
        self.script = script or Sv73ScriptService()
        self.render = render or Sv73RenderService()
        self.promo = promo or PromoService()
        self.repo = self.promo.repo

    # ---------- content 登记(36号惯例模板) ----------

    async def _find_content_by_script(self, script_id: str) -> dict | None:
        """幂等查重: sv73.storyboardId 已登记则复用(list 后自筛)"""
        for c in await self.repo.list_contents(limit=200):
            if (c.get("sv73") or {}).get("storyboardId") == script_id:
                return c
        return None

    async def _register_content(self, hotspot: dict, storyboard: dict,
                                built: dict, platform: str,
                                match_result: dict = None) -> dict:
        """产物登记 36号 content(状态判定与 36号生成即预审同口径:
        hardFail/低于人工线 → rejected; 否则 pending 待人工 review)"""
        from repositories.promo_repository import (
            CONTENT_STATUS_REJECTED, CONTENT_STATUS_PENDING,
            PROMO_HITL_FLOOR,
        )
        gate = storyboard["compliance"]
        mode = current_mode()
        status = (CONTENT_STATUS_REJECTED
                  if (gate["hardFail"]
                      or gate["score"] < PROMO_HITL_FLOOR)
                  else CONTENT_STATUS_PENDING)
        content = {
            "contentId": await self.repo.next_id("content"),
            "contentGroupId": 0,
            "hotspotId": int(hotspot.get("hotspotId") or 0),
            "platform": platform,
            "title": storyboard["hotspot"]["title"],
            "body": " ".join(sc["voiceover"]
                             for sc in storyboard["scenes"]),
            "hashtags": "",
            "cta": next((sc["text"] for sc in storyboard["scenes"]
                         if sc["role"] == "action"), ""),
            "coverHint": "sv73 短视频(大字卡+运镜)",
            "complianceScore": gate["score"],
            "complianceViolations": gate["violations"],
            "hardFail": gate["hardFail"],
            "requiresManualReview": gate["requiresManualReview"],
            "status": status,
            "shortCode": "",
            "receipt": {},
            "scheduledAt": "",
            "publishedAt": "",
            "createdAt": _now_iso(),
            # 73号 扩展段(产物关联——36号 save_content 整 dict 落库容忍;
            # P2 数据闭环快照: 风格六因子决策时留痕, 供回流构造)
            "sv73": {
                "storyboardId": storyboard["scriptId"],
                "video": built["video"],
                "pages": built["pages"],
                "audioTrack": built["audioTrack"],
                "totalDuration": built["durationSeconds"],
                "track": storyboard["track"],
                "bgmMood": storyboard["bgm"]["mood"],
                "persona": storyboard["persona"],
                "shadow": mode == "shadow",
                # P2 闭环快照(风格因子源)
                "template": storyboard["template"]["name"],
                "complianceScore": gate["score"],
                "matchTop": ((match_result or {})
                             .get("topSeries", "")),
                "matchFallback": bool(
                    (match_result or {}).get("fallback")),
            },
        }
        # real 态 + 过闸 → 建归因短码(36号 best-effort 同款);
        # shadow 影子期不占归因通道
        if (status == CONTENT_STATUS_PENDING
                and mode == "real"):
            content["shortCode"] = (
                await self.promo._create_attract_code(
                    content["contentId"]))
        return await self.repo.save_content(content)

    # ---------- 编排入口 ----------

    async def run(self, hotspot: dict,
                  category: str = DEFAULT_CATEGORY,
                  platform: str = DEFAULT_PLATFORM,
                  persona: str = "zhuxiaomei",
                  template: str = DEFAULT_TEMPLATE,
                  render_mode: str | None = None) -> dict:
        """一键全链: 剧本→(渲染)→content 登记(幂等)

        category="auto"(P1): 经匹配引擎推荐主推品类(确定性规则)。
        render_mode(分段语义): None=读 SV73_RENDER_MODE(默认
        off——生产只跑"剧本+登记", 渲染留开发机); "on"=全链
        (开发机/强机); "off"=显式跳过。
        Returns:
            {mode, match, storyboard, render, content, nextSteps}
        """
        if platform not in SV73_PLATFORMS:
            raise ValueError(f"不支持的平台: {platform}")

        # 0. 品类匹配(P1: auto 档——确定性规则, LLM 禁入)
        match_result = None
        if category == CATEGORY_AUTO:
            match_result = await Sv73MatchService().match(hotspot)
            category = match_result["topSeries"]

        # 1. 分镜剧本(LLM 轨/Mock-first, 前置闸门, 模板驱动)
        storyboard = await self.script.generate(
            hotspot, category, persona, template)

        # 2+3. 渲染+合成(分段: render off 跳过——生产小机
        #      ffmpeg 300s 超时实证, 对齐 36号"渲染在开发机")
        if render_mode_enabled(render_mode):
            built = await asyncio.to_thread(
                self.render.build, storyboard)
        else:
            built = {
                "scriptId": storyboard["scriptId"],
                "skipped": True,
                "pages": [], "video": "", "audioTrack": "",
                "sizeBytes": 0,
                "durationSeconds":
                    storyboard["totalDuration"],
                "note": ("渲染步跳过(SV73_RENDER_MODE=off)——"
                         "开发机渲染后 POST /api/sv73/render/"
                         "attach 挂载产物元数据"),
            }

        # 4. content 登记(幂等: 同 storyboardId 复用)
        content = await self._find_content_by_script(
            storyboard["scriptId"])
        reused = content is not None
        if not reused:
            content = await self._register_content(
                hotspot, storyboard, built, platform,
                match_result)

        logger.info(
            "sv73_pipeline_done script=%s video=%s content=%s"
            " reused=%s mode=%s",
            storyboard["scriptId"], built["video"],
            content.get("contentId"), reused, current_mode())
        return {
            "mode": current_mode(),
            "match": match_result,
            "storyboard": storyboard,
            "render": built,
            "content": {
                "contentId": content.get("contentId"),
                "status": content.get("status"),
                "shortCode": content.get("shortCode", ""),
                "sv73": content.get("sv73", {}),
            },
            "reused": reused,
            "platformForm": SV73_PLATFORM_FORMS.get(
                platform, "video"),
            "nextSteps": self._next_steps(content, platform),
        }

    @staticmethod
    def _next_steps(content: dict, platform: str) -> str:
        """发布链指引(按平台形态注入执行层口径)"""
        if content.get("status") != "pending":
            return (f"content 状态 {content.get('status')}"
                    "(发布链未开——三审闸门不动)")
        form = SV73_PLATFORM_FORMS.get(platform, "video")
        base = ("content 待 36号人工三审: POST /api/promo/contents/"
                f"{content.get('contentId')}/review → approve 后 "
                "publish_content → process_publish_queue 挂 "
                "rpa_pending")
        if form == "card":
            return (base
                    + f"(微博 card 形态: weibo-cli C 系无视频"
                      "命令——36号 coverUrl 封面下载 + "
                      "upload_pic/upload_url_text 三步图文卡, "
                      "--mblog_statement 1 必带)")
        if platform == "xiaohongshu":
            return (base
                    + "(xhs-bot.js 视频笔记——发布流 DOM 待"
                      "登录态联调)")
        return base + f"(channels/douyin-bot RPA {form} 直传)"

    # ---------- 产物挂载(分段语义: 开发机渲染 → 元数据回填) ----------

    async def attach_render(self, script_id: str,
                            video: str,
                            pages: list | None = None,
                            audio_track: str = "",
                            size_bytes: int = 0) -> dict:
        """开发机渲染产物挂载(幂等: 覆盖回填 content.sv73)

        分段链路: 生产 run(渲染跳过) → 开发机取 storyboard 渲染
        (build) → 本端点回填产物元数据。video 为**开发机本地路径**
        (RPA 发布本就在开发机执行, 文件无需上生产)。
        """
        content = await self._find_content_by_script(script_id)
        if content is None:
            raise KeyError(
                f"scriptId={script_id} 无登记 content"
                "(先 pipeline/run)")
        if not str(video or "").strip():
            raise ValueError("video 路径不可为空")
        sv = dict(content.get("sv73") or {})
        sv.update({
            "storyboardId": script_id,
            "video": str(video),
            "pages": list(pages or []),
            "audioTrack": str(audio_track or ""),
            "sizeBytes": int(size_bytes or 0),
            "renderSource": "devmachine",
            "attachedAt": _now_iso(),
        })
        content["sv73"] = sv
        await self.repo.save_content(content)
        logger.info("sv73_render_attached script=%s content=%s "
                    "video=%s", script_id,
                    content.get("contentId"), video)
        return {
            "success": True,
            "scriptId": script_id,
            "contentId": content.get("contentId"),
            "sv73": sv,
            "note": "开发机产物元数据已挂载(RPA 发布在开发机"
                    "本地取文件)",
        }
