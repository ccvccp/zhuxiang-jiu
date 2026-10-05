"""xhs 新号图文包生产器(2026-10-05)——零自动化铁律下的内容侧流程

用法: python xhs_pack.py "热点或主题标题" [平台来源]
      python xhs_pack.py "寒露养生酒单"
产出(目录 xhs_packs/<日期>_<hotword>/):
  - 4 张品牌大字卡 PNG(1080x1440, 3:4——xhs 图文规格; LLM 文案轨)
  - note_draft.txt(标题/正文/话题/AI 标注提醒——人工发布照抄)
流程铁律: 仅本地渲染产内容, 不接触 xhs 平台(发布=手机 App 人工,
AI 标注必勾——新号合规实验设计, 见总览 §五)
"""
import asyncio
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from services.sv73_script_service import Sv73ScriptService  # noqa: E402
from services.sv73_render_service import Sv73RenderService  # noqa: E402


def slug(s: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fa5]+", "_", s)[:24].strip("_")


async def main() -> int:
    title = sys.argv[1] if len(sys.argv) > 1 else "竹香酒品牌日常"
    src = sys.argv[2] if len(sys.argv) > 2 else "xiaohongshu"
    sb = await Sv73ScriptService().generate({
        "hotspotId": 0,
        "fingerprint": "fp-xhs-pack-" + slug(title),
        "platform": src,
        "title": title,
        "score": 70,
    }, template="vertical")

    out_dir = Path("xhs_packs") / (
        datetime.now().strftime("%m%d") + "_" + slug(title))
    out_dir.mkdir(parents=True, exist_ok=True)
    pages = []
    for p in Sv73RenderService().render_pages(sb):
        dest = out_dir / Path(p).name
        dest.write_bytes(Path(p).read_bytes())
        pages.append(dest)

    texts = [sc["text"] for sc in sb["scenes"]]
    draft = out_dir / "note_draft.txt"
    draft.write_text(
        f"# xhs 新号图文包 · {title}\n"
        f"# (生成 {datetime.now():%Y-%m-%d %H:%M}, LLM 轨: {sb['track']})\n\n"
        f"## 图片(按序上传 {len(pages)} 张)\n"
        + "".join(f"  {p.name}\n" for p in pages)
        + "\n## 标题(选一, 拟真口吻)\n"
        f"  A. 试着用AI做了组{sb['hotspot']['title'][:10]}海报, 打几分?\n"
        f"  B. {texts[0]}, 这个效果你们喜欢吗\n"
        "\n## 正文\n"
        f"  第一次用 AI 工具给竹香型白酒做品牌海报, "
        f"{'/'.join(texts[:2])}, 科技感和传统酒文化意外地搭。\n"
        "  纯练习作品, 图片均由 AI 生成。\n"
        "  理性饮酒, 未满18岁请勿饮酒。\n"
        "\n## 话题\n"
        "  #AI绘画 #AI海报 #竹香型白酒\n"
        "\n## 发布清单(人工·手机App)\n"
        "  1. 手机 App 发布(不用任何第三方工具)\n"
        "  2. 上传图片 → 发布页勾选「内容由 AI 生成」标注(关键!)\n"
        "  3. 无链接/无价格/无购买引导(实验期隔离营销变量)\n"
        "  4. 晚 19-21 点发布; 2h/24h/48h 三查(违规/可搜索/浏览量)\n",
        encoding="utf-8")

    print("pack:", out_dir.resolve())
    for p in pages:
        print("  ", p.name, p.stat().st_size, "B")
    print("draft:", draft.name)
    print("scenes:", texts)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
