# -*- coding: utf-8 -*-
"""40号 P7 雷达2.0 全链交付总结报告 PDF 生成脚本

基于 docs/40号_P7_雷达2.0_全链交付总结报告.md 终态内容,
使用 reportlab Platypus 流式排版 + 内置 STSong-Light CID 中文字体。
用法: python generate_p7_report_pdf.py
"""
import sys

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageBreak, PageTemplate, Paragraph,
    Spacer, Table, TableStyle,
)

# 中文字体(CID 内置——无需外部字体文件)
pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
FONT = "STSong-Light"

PRIMARY = colors.HexColor("#1a5276")
ACCENT = colors.HexColor("#2874a6")
BORDER = colors.HexColor("#d5dde5")
ZEBRA = colors.HexColor("#f4f7fa")
OKGREEN = colors.HexColor("#1e8449")
WARN = colors.HexColor("#b9770e")

styles = getSampleStyleSheet()


def st(name, **kw):
    base = kw.pop("parent", styles["Normal"])
    return ParagraphStyle(name, parent=base, fontName=FONT, **kw)


S = {
    "title": st("title", fontSize=17, leading=26,
                textColor=colors.white, alignment=1),
    "subtitle": st("subtitle", fontSize=11.5,
                   leading=18, textColor=colors.HexColor("#d6e4f0"),
                   alignment=1),
    "h2": st("h2", fontSize=13.5, leading=20, textColor=PRIMARY,
             spaceBefore=14, spaceAfter=6),
    "h3": st("h3", fontSize=11, leading=16, textColor=ACCENT,
             spaceBefore=8, spaceAfter=3),
    "body": st("body", fontSize=9, leading=14.5),
    "bullet": st("bullet", fontSize=9, leading=14.5, leftIndent=10,
                 bulletIndent=2),
    "small": st("small", fontSize=7.8, leading=12,
                textColor=colors.HexColor("#5d6d7e")),
    "cell": st("cell", fontSize=7.6, leading=11),
    "cellb": st("cellb", fontSize=7.6, leading=11,
                textColor=colors.HexColor("#922b21")),
    "quote": st("quote", fontSize=8.6, leading=14),
    "arch": st("arch", fontSize=7.6, leading=12.5,
               textColor=colors.HexColor("#16222e")),
}

# 表格通用样式
TBL = TableStyle([
    ("FONT", (0, 0), (-1, -1), FONT, 7.6),
    ("FONT", (0, 0), (-1, 0), FONT, 8.2),
    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
    ("BACKGROUND", (0, 0), (-1, 0), PRIMARY),
    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ZEBRA]),
    ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ("TOPPADDING", (0, 0), (-1, -1), 3.5),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
    ("LEFTPADDING", (0, 0), (-1, -1), 5),
    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
])


def tbl(headers, rows, widths, bold_cols=()):
    data = [[Paragraph(str(h), S["cell"]) for h in headers]]
    for r in rows:
        row = []
        for ci, c in enumerate(r):
            sty = S["cellb"] if ci in bold_cols else S["cell"]
            row.append(Paragraph(str(c), sty))
        data.append(row)
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TBL)
    return t


def P(text, style="body"):
    return Paragraph(text, S[style])


def B(text):
    return Paragraph(f"&bull; {text}", S["bullet"])


def header_footer(canvas, doc):
    canvas.saveState()
    w, h = A4
    canvas.setFillColor(PRIMARY)
    canvas.rect(0, h - 12 * mm, w, 12 * mm, stroke=0, fill=1)
    canvas.setFillColor(colors.white)
    canvas.setFont(FONT, 8)
    canvas.drawString(15 * mm, h - 8 * mm,
                      "40号·AI智能雷达2.0 全网实时价值侦测中枢")
    canvas.drawRightString(w - 15 * mm, h - 8 * mm,
                           "P7 五期全链交付总结报告")
    canvas.setFillColor(colors.HexColor("#85929e"))
    canvas.setFont(FONT, 7.5)
    canvas.drawString(15 * mm, 8 * mm,
                     "竹香酒 zxjiu.com · 40号模块 P7 雷达2.0 · "
                     "提交链 9db1eec→4e012ee→2872f2c→5bf4855→"
                     "e46edef→a086034")
    canvas.drawRightString(w - 15 * mm, 8 * mm,
                           f"第 {doc.page} 页")
    canvas.setStrokeColor(BORDER)
    canvas.setLineWidth(0.5)
    canvas.line(15 * mm, 11 * mm, w - 15 * mm, 11 * mm)
    canvas.restoreState()


doc = BaseDocTemplate(
    "40号_P7_雷达2.0_全链交付总结报告.pdf", pagesize=A4,
    leftMargin=15 * mm, rightMargin=15 * mm,
    topMargin=18 * mm, bottomMargin=15 * mm,
    title="40号·AI智能雷达2.0 P7 五期全链交付总结报告",
    author="zhuxiang-jiu")
frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height,
              id="main")
doc.addPageTemplates(
    [PageTemplate(id="page", frames=[frame],
                  onPage=header_footer)])

E = []   # elements

# ---------- 封面标题区 ----------
E.append(Spacer(1, 6))
title_tbl = Table([
    [P("40号·AI智能雷达2.0 全网实时价值侦测中枢", "title")],
    [P("P7 五期全链交付总结报告", "title")],
    [Spacer(1, 4)],
    [P("定位升级：P0-P3 的雷达是“作品侦测器”；P7 升级为"
       "“价值信号预言机”——在流量洪峰形成前识别真实需求、"
       "情绪脉络与合规边界，为自主创作/合规转发/引流转化"
       "赢得“黄金窗口期”", "subtitle")],
    [P("架构铁律：“大模型”仍是确定性系统（聚类=指纹范式，"
       "情绪=词表密度，预测=历史周期统计，评分=三维公式）"
       "——LLM 禁入判定链不变", "subtitle")],
    [P("核心命题：速度服务于价值，而非取代价值——高热度但低"
       "契合/高风险的事件自动标记“观察区”或“禁区”，而非盲目"
       "推送", "subtitle")],
], colWidths=[doc.width])
title_tbl.setStyle(TableStyle([
    ("BACKGROUND", (0, 0), (-1, -1), PRIMARY),
    ("TOPPADDING", (0, 0), (-1, -1), 5),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ("LEFTPADDING", (0, 0), (-1, -1), 14),
    ("RIGHTPADDING", (0, 0), (-1, -1), 14),
]))
E.append(title_tbl)
E.append(Spacer(1, 8))

# 核心指标带
stats = [("5+1", "五引擎+收官期"), ("16", "API 端点"),
         ("6", "新数据表"), ("4,882", "交付代码行"),
         ("110", "专项断言"), ("33×3", "实机验收全绿")]
srow = [[Paragraph(f'<font size="15" color="#1a5276">'
                   f'<b>{n}</b></font>', S["cell"])
         for n, _l in stats],
        [Paragraph(l, S["small"]) for _n, l in stats]]
stbl = Table(srow, colWidths=[doc.width / 6.0] * 6)
stbl.setStyle(TableStyle([
    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#eaf2f8")),
    ("BOX", (0, 0), (-1, -1), 0.7, BORDER),
    ("INNERGRID", (0, 0), (-1, -1), 0.5, BORDER),
    ("ALIGN", (0, 0), (-1, 0), "CENTER"),
    ("ALIGN", (0, 1), (-1, 1), "CENTER"),
    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ("TOPPADDING", (0, 0), (-1, 0), 7),
    ("BOTTOMPADDING", (0, 1), (-1, 1), 6),
]))
E.append(stbl)

# ---------- 一、五期交付总览 ----------
E.append(P("一、五期交付总览", "h2"))
E.append(tbl(
    ["期", "交付", "核心验收", "断言", "提交"],
    [["P7a 感知与聚合",
      "12 种子频道池+事件流 mock（15min 槽）+聚类去重+情绪场域"
      "+刷量过滤",
      "12 频道惰性灌入/同指纹跨槽位聚合/聚簇&gt;50% 降权/"
      "时政军事在流", "24", "9db1eec"],
     ["P7b 三维价值评估",
      "契合知识映射+安全硬闸（五类风险）+转化统计+L1-L4 分级",
      "政治军事→L4 拦截留痕/暴雨→信值购高契合/安全&lt;0.6 "
      "短路不进乘法", "31", "4e012ee"],
     ["P7c 演化预测与干预",
      "生命周期分段+峰值拐点预警+跨平台关联（机会窗/叙事变异/"
      "联动造势）+合规预演沙盘",
      "爆发/发酵/峰值/衰退分段正确/下滑期预警/预案过合规校验"
      "（词表+授权库）", "21", "2872f2c"],
     ["P7d 自主响应触发",
      "L1 任务包（溯源 ID）+46号审批确认流+P6b 创作轨派发",
      "L1 确认后触发 P6b 脚本生成/派发 fail-soft 不回滚/"
      "自治暂停仲裁", "17", "5bf4855"],
     ["P7e 自治理与进化",
      "漏斗归因闭环+违规回流+阈值自适应收紧+效能周报+中枢看板",
      "溯源 ID→attract 漏斗→score 回填/违规激增自动收紧 "
      "75→85→95/周报聚合", "17", "e46edef"],
     ["P7f 收官三件套",
      "Docker 实机验证+生产部署+交付总结",
      "实机 33/33×2 轮幂等+生产公网 16 端点全通", "33",
      "a086034"]],
    [30 * mm, 52 * mm, 55 * mm, 11 * mm, 27 * mm], bold_cols=(4,)))

# ---------- 二、五引擎架构 ----------
E.append(P("二、五引擎架构（数据流）", "h2"))
arch_text = (
    "【全域感知层 P7a】12 种子频道（时政军事为主体=合规测试集）\n"
    "  → 事件流（15min 槽位确定性 mock：标题/ASR 转写稿/OCR 标签/"
    "BGM 指纹/弹幕样本）→ 事件指纹去重（SHA256 平台+频道+主题）\n"
    "  + 情绪场域（词表密度×群体聚合×刷量过滤——原文即用即弃）\n"
    "        ▼\n"
    "【三维价值评估 P7b】契合度（知识映射：暴雨85/节日80/互助78/"
    "美食75/消费70）\n"
    "  × 安全系数（硬闸&lt;0.6：类别硬映射/政策词/版权指纹/反转×0.5/"
    "价值观×0.6）\n"
    "  × 转化潜力（同类历史漏斗统计， 冷启动 0.5）→ L1-L4 分级\n"
    "        ▼\n"
    "【演化预测与干预 P7c】生命周期（爆发0-6h/发酵6-24h/峰值24-48h/"
    "衰退48h+ 拐点预警+早期信号）\n"
    "  + 跨平台（机会窗/叙事变异推荐/联动造势降权）\n"
    "  + 合规预演沙盘（L1 专用：推荐角度/禁用表述/授权素材/"
    "钩子四件套）\n"
    "        ▼\n"
    "【自主响应触发 P7d】L1 任务包（traceId 溯源+46号 submit_change "
    "留痕）\n"
    "  → 人工确认（46号 P0 人工通道范式：reject 驳回流/approve 终审）\n"
    "  → P6b 创作轨派发（generate_script 复用——零新增创作逻辑）\n"
    "        ▼\n"
    "【自治理与进化 P7e】归因闭环（traceId→script→shortCode→"
    "attract 漏斗→score 快照回填）\n"
    "  + 违规回流（仅留痕， 惩罚经 46号）\n"
    "  + 阈值收紧（×3 且样本≥20→+10 封顶 95， 只紧不松）\n"
    "  + 效能周报（触发/命中率/误报率/漏报案例库）+ 中枢看板四区")
arch_tbl = Table([[Paragraph(arch_text.replace("\n", "<br/>"),
                             S["arch"])]], colWidths=[doc.width])
arch_tbl.setStyle(TableStyle([
    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#16222e")),
    ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#d6e4f0")),
    ("TOPPADDING", (0, 0), (-1, -1), 9),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
    ("LEFTPADDING", (0, 0), (-1, -1), 12),
    ("RIGHTPADDING", (0, 0), (-1, -1), 12),
]))
E.append(arch_tbl)

# ---------- 三、核心交付明细 ----------
E.append(P("三、核心交付明细", "h2"))

E.append(P("P7a 感知与聚合（radar_hub_service.py）", "h3"))
for t in [
    "种子频道池：用户指定 12 频道（金梅煮酒/高志凯/陈虎点兵/金灿荣/"
    "震海会/今日蒋谈/包明说/任汉军财富故事会/保德全/听风的蚕/闫树军/"
    "黎建南台湾）——时政军事为主体构成 L4 合规分级天然测试集。",
    "事件流 mock：种子=radar|{channelId}|{date}|{slot} 槽位确定性"
    "（同槽内容一致可测聚类；跨槽推进演示“事件发酵”）。",
    "多模态字段：ASR 转写稿/OCR 标签/BGM 指纹（真实轨 "
    "radar_source_adapter 预留——P3b 限速+熔断范式，Mock-first "
    "产出不中断）。",
    "情绪场域：词表密度（负向/正向）→群体分类；刷量过滤（同 IP "
    "前缀聚簇&gt;50% 降权×0.3）；弹幕原文即用即弃（仅密度入库）。",
]:
    E.append(B(t))

E.append(P("P7b 三维价值评估（radar_score_service.py）", "h3"))
for t in [
    "信值契合度：确定性知识映射表（暴雨应急→信值购+叫帮 85/节日→"
    "礼品 80/互助→叫帮 78/美食→好店 75/消费→理性 70）；无映射 25"
    "（&lt;40 自动降级——不强行蹭热点）。",
    "安全硬闸（&lt;0.6 无条件拦截，短路不进价值乘法）：类别硬映射"
    "（时政/军事→L4）+一票否决词+政治军事扩展词表（台海/军演等 12 "
    "词）+封禁 BGM 指纹（P6b 库复用）+反转词×0.5+价值观词×0.6；"
    "RT-03 攻击归一（插符绕词表在归一后命中）。",
    "L1-L4 分级：L1（≥75 且契合≥70 且安全≥0.8 且爆发/发酵期）/L2"
    "（≥50）/L3 兜底/L4 屏蔽留痕备查永不静默丢弃。",
    "转化潜力：radar_scores 漏斗字段历史统计（(注册×0.4+激活×0.6)"
    "/点击归一化）；冷启动&lt;3 样本回退 0.5。",
]:
    E.append(B(t))

E.append(P("P7c 演化预测与干预（radar_forecast_service.py）", "h3"))
for t in [
    "生命周期分段：热度序列整数比较+时间窗；拐点预警优先于时间窗"
    "（连续 2 槽位下滑→衰退+“避免在下滑期投入”）；早期信号（槽位"
    "环比&gt;2×/类目热度&gt;2×均值）。",
    "跨平台关联：主题指纹（平台无关键）→机会窗（A 热议×B 沉默="
    "差异化切入窗）/叙事变异（词差集+站内调性对齐度推荐）/联动"
    "造势（≥3 平台同步陡增&gt;5×→疑似操纵标记+情绪×0.3 降权）。",
    "合规预演沙盘（L1 专用）：四件套（推荐角度=契合+情绪+时效模板/"
    "禁用表述=全量风险词护栏/素材建议=仅 active 授权库/钩子方向="
    "P5b 钩子库映射）；过校验（创作面词表扫描+素材⊆授权库）方回写。",
]:
    E.append(B(t))

E.append(P("P7d 自主响应触发（radar_task_service.py）", "h3"))
for t in [
    "任务包：预演通过自动入队（fail-soft 延迟创建——46号串行约束下"
    "留痕）；唯一溯源 ID RADAR-xxxxxx（P7e 归因锚）；46号 "
    "submit_change（config）审批总线留痕。",
    "人工确认流（46号轨，永不自动执行）：reject→46号干净驳回→任务 "
    "rejected（决策回流）；approve→46号 P0 人工通道范式（63号同款："
    "captured ValueError+reviewedBy 校验→模块终审）。",
    "派发：P6b generate_script 复用（钩子方向→hook_type 确定性映射"
    "+原创 IP 人设）；fail-soft 确认不回滚（自治暂停/无人设留痕"
    "待重试）。",
]:
    E.append(B(t))

E.append(P("P7e 自治理与进化（radar_govern_service.py）", "h3"))
for t in [
    "归因闭环：task→script→shortCode→attract 漏斗（点击/注册/下单）"
    "→score 快照回填（P7b 转化统计源——“高分是否真高转化”闭环）。",
    "阈值自适应：违规率&gt;3×基线 且样本≥20（三检测器范式）→L1 线"
    "+10 封顶 95；只紧不松（放宽须 46号建议书）；经 "
    "get_current_l1_line 即刻生效于后续评分。",
    "效能周报：触发数/命中率（转化≥0.5 一致率）/误报率（L1 被否决"
    "率）/漏报案例库（L3 事后高转化自动入库——46号校准轨）。",
    "中枢看板：事件/分级/任务/效能/阈值五区聚合（纯读取）。",
]:
    E.append(B(t))

# ---------- 四、数据资产 ----------
E.append(P("四、数据资产", "h2"))
E.append(P("6 新表（radar 前缀，双模式内存+Redis，bool 显式还原——"
           "P6g-4 教训）：", "body"))
E.append(tbl(
    ["表", "用途"],
    [["radar_channels", "种子频道池（12 频道+类别+状态）"],
     ["radar_events", "事件流（指纹/多模态/热度/情绪/生命周期/分级）"],
     ["radar_event_slots", "事件×槽位观测（热度/情绪时序）"],
     ["radar_scores", "三维评分快照+漏斗字段（转化统计源）"],
     ["radar_tasks", "L1 任务包（预案+决策依据+溯源+派发回执）"],
     ["radar_efficiency", "效能周报+阈值收紧留痕（kind 分型）"]],
    [45 * mm, 130 * mm], bold_cols=(0,)))
E.append(Spacer(1, 4))
E.append(P("16 端点（X-Role: admin）：channels GET/POST、events/"
           "collect、events GET、events/{id}、events/score、scores、"
           "events/{id}/predict、events/{id}/rehearse、tasks GET/POST、"
           "tasks/{id}/confirm、efficiency/weekly、efficiency、"
           "threshold/tighten、dashboard。", "body"))

# ---------- 五、验证体系 ----------
E.append(P("五、验证体系", "h2"))
E.append(tbl(
    ["层", "口径", "结果"],
    [["P7 专项", "5 套件 110 断言（24+31+21+17+17）", "全过"],
     ["40号全量回归", "P0-P6g 23 套件 687 断言", "全过"],
     ["Docker 实机", "verify_radar_p7_live.py 33 断言×2 轮幂等",
      "33/33"],
     ["生产实机", "容器内 33 断言", "33/33"],
     ["生产公网", "zxjiu.com 16 端点（channels/events/tasks/"
      "dashboard）", "全通"],
     ["语法", "py_compile 全部 P7 文件", "全绿"]],
    [32 * mm, 108 * mm, 35 * mm]))
E.append(Spacer(1, 4))
E.append(P("实机关键断言：12 频道惰性灌入→同槽幂等→时政军事 L4 拦截"
           "留痕→转化统计生效（L1 出现）→演化预测+跨平台→预演门槛→"
           "46号任务留痕→人工否决/重复确认 409→P6b 人设就位→延迟"
           "创建轨闭环→派发脚本回执→看板四区→周报聚合→阈值收紧"
           "（无异常不变）→幂等重跑（已裁决 409/补建返回既有 "
           "dispatched）。", "quote"))

# ---------- 六、红线对照表 ----------
E.append(P("六、红线对照表（宪法域）", "h2"))
E.append(tbl(
    ["红线", "工程实现", "验证"],
    [["政治军事类→L4 禁区", "类别硬映射+扩展词表双轨，屏蔽原因入库",
      "实机 L4≥6 留痕"],
     ["安全&lt;0.6 无条件拦截",
      "硬闸短路不进乘法（高契合×高热度不可稀释）", "专项+实机"],
     ["契合&lt;40 自动降级", "无映射 25 分（不强行蹭热点）", "专项"],
     ["L1 须经 46号确认", "submit_change 留痕+人工通道范式确认",
      "实机"],
     ["阈值只可自动收紧",
      "tighten 单调+10 封顶 95；放宽走 46号建议书",
      "专项 75→85→95"],
     ["情绪原文即用即弃", "仅聚合密度入库，弹幕不落查询面", "实机"],
     ["LLM 禁入判定链",
      "聚类=指纹/情绪=词表/预测=统计/评分=公式", "全链"],
     ["L4 屏蔽留痕备查", "blockedReasons 永不静默丢弃", "实机"],
     ["刷量过滤", "聚簇&gt;50% 情绪×0.3", "专项"],
     ["素材仅授权库", "预演素材⊆P6b active 授权", "专项"]],
    [42 * mm, 83 * mm, 50 * mm]))

# ---------- 七、大事记 ----------
E.append(P("七、大事记", "h2"))
E.append(tbl(
    ["日期", "事件"],
    [["2026-09-10", "P7 规划方案入库；P7a 感知与聚合交付（9db1eec）"],
     ["2026-09-10", "P7b 三维价值评估交付（4e012ee，含“节假日”"
      "子串映射修复）"],
     ["2026-09-11", "P7c 演化预测交付（2872f2c）；P7d 自主响应交付"
      "（5bf4855，含派发 DISPATCHED 状态修复）"],
     ["2026-09-11", "P7e 自治理交付（e46edef）；P7f 收官：Docker "
      "实机 33/33×2 轮幂等+生产 zxjiu.com 部署+公网验证+交付文档"
      "（a086034）"]],
    [25 * mm, 150 * mm]))
E.append(Spacer(1, 4))
E.append(P("提交链：9db1eec → 4e012ee → 2872f2c → 5bf4855 → "
           "e46edef → a086034", "body"))

# ---------- 八、代码资产 ----------
E.append(P("八、代码资产", "h2"))
E.append(tbl(
    ["类别", "文件", "行数"],
    [["Repository", "radar_repository.py（6 表）", "392"],
     ["Services", "radar_hub/score/forecast/task/govern_service.py",
      "1,902"],
     ["Routes", "radar_routes.py（16 端点）", "289"],
     ["测试", "test_radar_p7a~p7e.py（110 断言）", "2,065"],
     ["实机验收", "verify_radar_p7_live.py（33 断言）", "234"],
     ["合计", "12 文件", "4,882"]],
    [30 * mm, 115 * mm, 30 * mm]))

# ---------- 九、后续展望 ----------
E.append(P("九、后续展望", "h2"))
for t in [
    "真实源接入：radar_source_adapter 落地（平台 API/RSS——P3b 限速"
    "+熔断+契约归一范式），槽位 mock 轨切换 mock_fallback 灰度。",
    "预演→发布全链：L1 派发后的 P6c 渲染/P6d 发布调度联动（当前"
    "派发至脚本层，渲染发布走既有创作工坊人工轨）。",
    "阈值校准轨：漏报案例库积累后经 46号建议书校准契合映射表（雷达"
    "唯一可学习面——判定链常量永不自动改）。",
    "前端工作台：雷达中枢看板接入博主管理工作台（blogger.ts 25 端点"
    "→41 端点扩展）。",
]:
    E.append(B(t))

# ---------- 结语 ----------
E.append(Spacer(1, 10))
fin = Table([[Paragraph(
    "结语：P7 五引擎全链（感知→评估→预测→响应→自治理）收官，雷达 "
    "2.0 从“流量报警器”进化为“信值机会预言机”——速度服务于价值，"
    "高热度高风险事件自动屏蔽，高契合高转化机会黄金窗口直达创作管线，"
    "全程 LLM 禁入判定链，人工确认与留痕审计贯穿始终。",
    st("fin", fontSize=9, leading=15, textColor=colors.white))]],
    colWidths=[doc.width])
fin.setStyle(TableStyle([
    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#145a32")),
    ("TOPPADDING", (0, 0), (-1, -1), 10),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ("LEFTPADDING", (0, 0), (-1, -1), 14),
    ("RIGHTPADDING", (0, 0), (-1, -1), 14),
]))
E.append(fin)

doc.build(E)
print("PDF generated: 40号_P7_雷达2.0_全链交付总结报告.pdf")
print("pages OK, exit 0")
