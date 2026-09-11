"""68号 信值·臻选购物平台 收官三册 PDF 生成脚本

基于 docs/ 三册 Markdown 终态内容, 使用 reportlab Platypus
流式排版 + 内置 STSong-Light CID 中文字体(40号 P7 范式)。

三册:
    ① 68号_信值臻选购物平台_五期全链交付总结报告.pdf
    ② 68号_信值臻选购物平台_运行报告.pdf
    ③ 68号_信值臻选购物平台_用户操作指南.pdf

用法: python generate_xinzhi_trilogy_pdf.py
"""
import os
import sys

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate, Paragraph,
    Spacer, Table, TableStyle,
)

# 中文字体(CID 内置——无需外部字体文件)
pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
FONT = "STSong-Light"

PRIMARY = colors.HexColor("#6b3410")
ACCENT = colors.HexColor("#935116")
BORDER = colors.HexColor("#e2d5c5")
ZEBRA = colors.HexColor("#f7f1e8")
OKGREEN = colors.HexColor("#1e8449")
WARN = colors.HexColor("#b9770e")

styles = getSampleStyleSheet()


def st(name, **kw):
    base = kw.pop("parent", styles["Normal"])
    return ParagraphStyle(name, parent=base, fontName=FONT, **kw)


S = {
    "title": st("title", fontSize=16, leading=24,
                textColor=colors.white, alignment=1),
    "subtitle": st("subtitle", fontSize=10.5, leading=16.5,
                   textColor=colors.HexColor("#f0d9b5"),
                   alignment=1),
    "h2": st("h2", fontSize=13, leading=19, textColor=PRIMARY,
             spaceBefore=14, spaceAfter=6),
    "h3": st("h3", fontSize=10.5, leading=15.5, textColor=ACCENT,
             spaceBefore=8, spaceAfter=3),
    "body": st("body", fontSize=9, leading=14.5),
    "bullet": st("bullet", fontSize=9, leading=14.5, leftIndent=10,
                 bulletIndent=2),
    "small": st("small", fontSize=7.8, leading=12,
                textColor=colors.HexColor("#7d6a55")),
    "cell": st("cell", fontSize=7.6, leading=11),
    "arch": st("arch", fontSize=7.4, leading=12,
               textColor=colors.HexColor("#2c1f14")),
    "note": st("note", fontSize=8.4, leading=13.5),
}

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


def tbl(headers, rows, widths):
    data = [[Paragraph(str(h), S["cell"]) for h in headers]]
    for r in rows:
        data.append([Paragraph(str(c), S["cell"]) for c in r])
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TBL)
    return t


def P(text, style="body"):
    return Paragraph(text, S[style])


def B(text):
    return Paragraph(f"&bull; {text}", S["bullet"])


def note_box(text):
    t = Table([[P(text, "note")]], colWidths=[None])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fef9e7")),
        ("LINEBEFORE", (0, 0), (0, -1), 2.5, WARN),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ]))
    return t


def cover(title_lines, subtitle_lines, header_text):
    rows = [[P(t, "title")] for t in title_lines]
    rows.append([Spacer(1, 4)])
    rows += [[P(s, "subtitle")] for s in subtitle_lines]
    t = Table(rows, colWidths=[None])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PRIMARY),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 14),
    ]))
    return t


def make_doc(path, header_text, footer_text, elements):
    doc = BaseDocTemplate(
        path, pagesize=A4,
        leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=18 * mm, bottomMargin=15 * mm,
        title=os.path.splitext(os.path.basename(path))[0],
        author="zhuxiang-jiu")
    frame = Frame(doc.leftMargin, doc.bottomMargin,
                  doc.width, doc.height, id="main")

    def header_footer(canvas, d):
        canvas.saveState()
        w, h = A4
        canvas.setFillColor(PRIMARY)
        canvas.rect(0, h - 12 * mm, w, 12 * mm, stroke=0, fill=1)
        canvas.setFillColor(colors.white)
        canvas.setFont(FONT, 8)
        canvas.drawString(15 * mm, h - 8 * mm, header_text[0])
        canvas.drawRightString(w - 15 * mm, h - 8 * mm,
                               header_text[1])
        canvas.setFillColor(colors.HexColor("#85929e"))
        canvas.setFont(FONT, 7.5)
        canvas.drawString(15 * mm, 8 * mm, footer_text)
        canvas.drawRightString(w - 15 * mm, 8 * mm,
                               f"第 {d.page} 页")
        canvas.setStrokeColor(BORDER)
        canvas.setLineWidth(0.5)
        canvas.line(15 * mm, 11 * mm, w - 15 * mm, 11 * mm)
        canvas.restoreState()

    doc.addPageTemplates(
        [PageTemplate(id="page", frames=[frame],
                      onPage=header_footer)])
    doc.build(elements)
    return path


W = 180 * mm   # A4 内容宽(210-2×15mm)

# ============================================================
# ① 交付总结报告
# ============================================================
E = []
E.append(Spacer(1, 6))
E.append(cover(
    ["68号·信值·臻选购物平台", "五期全链交付总结报告"],
    ["定位升级：zxjiu.com 从企业展示站升维为信值驱动的购物平台"
     "——「模块即货架， 信值即货币， AI 即导购」",
     "核心创新命题：零重复建设的编排式创新——文档描绘的五维雷达/"
     "三维匹配/动态定价/导购助手/邻里臻选/反作弊, 本项目 60+ 既有"
     "模块已建成约 80%, 68号只做「聚合编排前台」, 不重建任何信用资产",
     "架构铁律：LLM 禁入判定链不变——评分=公式/排序=加权/定价=三因子"
     "/反作弊=确定性检测器; 一切惩罚经 46号审批（宪法域）"],
    ("68号·信值·臻选购物平台", "五期全链交付总结报告")))
E.append(Spacer(1, 8))

E.append(P("一、五期交付总览", "h2"))
E.append(tbl(
    ["期", "交付", "核心验收", "断言", "提交"],
    [["P0 信值账户聚合层",
      "五维雷达（诚信30/互助25/专业20/活跃15/成长10——分段"
      "Sigmoid+熔断+衰减）",
      "47/67/44/62号只读聚合/新用户30天冷启动权重切换/"
      "全计算审计留痕", "26", "7a98166"],
     ["P1 臻选货架引擎",
      "商品三维评分（契合×安全硬闸×转化→L1-L4）+信值加权排序",
      "P7b 范式移植商品域/L4 短路不进乘法/排序公式 "
      "base×(1+α×雷达总分)", "24", "fdb3a40"],
     ["P2 透明定价与导购",
      "价格构成拆解（60号三因子+α抵扣+0.7地板+杀熟审计）+"
      "反馈闭环（L1/L2/L3）+小竹导购人格（SOP五步法）",
      "原价-信值抵扣-折扣=实付强制公示/价差&gt;20%留痕/"
      "LLM 禁数字（全查询层直出）", "29", "7e2772f"],
     ["P3 互助购物生态",
      "邻里臻选（品类聚合+匿名门槛K=5）+邻里求购（67号范式）"
      "+碳积分联动",
      "零个体数据红线/三单上限/紧急→距离→新单排序/碳档案"
      "不可交易", "23", "eb64788"],
     ["P4 商家信值体系",
      "4+2 认证+预演沙盘（P7c 范式）+S-A-B-C-D 动态评级",
      "四必查缺一确定性拒绝/1000单推演+启航报告/降级仅生成 "
      "46号建议书", "23", "b075122"],
     ["P5 灰度上线收官",
      "三态灰度 XINZHI_MODE+A/B 护栏（三指标恶化&gt;3%自动暂停）"
      "+年度信值白皮书",
      "读取链 guard-pause&gt;override&gt;env/6 决策面 off=409/"
      "观测面永不关停/PII 扫描零命中", "21", "951e83d"],
     ["P5f Docker 实机",
      "实机验收 56 断言×3 轮幂等+XINZHI_MODE compose 脚手架",
      "Redis 容器全链/40号 P7 回归 33/33 共存零影响", "56",
      "016a2e5"]],
    [26 * mm, 55 * mm, 55 * mm, 11 * mm, 24 * mm]))
E.append(Spacer(1, 4))

E.append(P("二、五层架构（数据流）", "h2"))
arch1 = (
    "【账户层·信值五维雷达 P0】xinzhi_radar_service（新）\n"
    "  用户信值五维 = 聚合 47号tier（诚信）×67号信任指数（互助）"
    "×44号评分（专业）×登录/订单流水（活跃）×62号估值斜率（成长）\n"
    "  → 分段Sigmoid归一化 → 硬熔断（任一维&lt;40→总分≤59）→ 软奖励（+5）"
    "→ 负面半衰期90天  ★ 零新建计算：全部只读聚合\n"
    "      ▼\n"
    "【决策层·臻选货架 P1】商品三维评分 = 信值契合度（知识映射表）"
    "×体验确定性（硬闸&lt;0.6）×转化潜力（历史漏斗）\n"
    "  → L1臻选位（≥75）/L2优选（≥50）/L3普通/L4风险 → "
    "信值加权排序 base×(1+α×总分/100)\n"
    "      ▼\n"
    "【体验层·透明定价与导购 P2】价格构成 = 60号三因子（≤30%封顶）"
    "→ 68号 α 信值抵扣（S.15/A.12/B.08, 单笔≤30%）→ 0.7 地板保护\n"
    "  → 详情页强制公示（杀熟价差&gt;20%留痕）; 导购 = 小竹臻选导购"
    "人格（SOP五步法）★ 数字永远来自查询层\n"
    "      ▼\n"
    "【生态层·互助购物 P3-P4】邻里臻选（品类聚合+匿名门槛K=5+零个体数据）\n"
    "  + 邻里求购（67号范式： 三单上限/违禁词/LBS排序）+ 碳积分（67号只读"
    "+求购碳500g×人数, 不可交易）\n"
    "  + 商家体系（4+2 认证+预演沙盘+S-A-B-C-D 评级——降级走 46号"
    "建议书, 处罚永不自动）\n"
    "      ▼\n"
    "【治理层·灰度与宪法 P5】三态灰度（读取链： 护栏暂停&gt;override"
    "&gt;env）+ A/B 护栏（三指标恶化&gt;3%自动暂停, 人工恢复）\n"
    "  + 年度信值白皮书（四章节+PII 扫描+样本门+67号联动）"
    "  观测面永不关停（宪法口径）")
arch_tbl = Table([[P(arch1, "arch")]], colWidths=[None])
arch_tbl.setStyle(TableStyle([
    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f5ecdd")),
    ("BOX", (0, 0), (-1, -1), 0.7, BORDER),
    ("TOPPADDING", (0, 0), (-1, -1), 8),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ("LEFTPADDING", (0, 0), (-1, -1), 10),
    ("RIGHTPADDING", (0, 0), (-1, -1), 10),
]))
E.append(arch_tbl)
E.append(Spacer(1, 4))

E.append(P("三、核心交付明细", "h2"))
for title, items in [
    ("P0 信值账户聚合层（xinzhi_radar_service.py）", [
        "五维口径（文档《信值五维雷达图》）：诚信度30%/互助值25%/专业度"
        "20%/活跃度15%/成长力10%；分段 Sigmoid 归一化（及格线 50 分）",
        "修正层：硬熔断（任一维&lt;40→总分≤59）+软奖励（+5）+负面半衰期 "
        "90 天",
        "新用户保护：30 天冷启动权重切换（活跃15→25, 诚信30→20）",
        "审计留痕：每次计算入库快照（computedFingerprint 防重复计算）"]),
    ("P1 臻选货架引擎（xinzhi_prime_service.py）", [
        "信值契合度：确定性知识映射表（珍藏→专业度/宴请→互助值/热销→"
        "活跃度；基准分×(0.6+0.4×维度值/100) 用户加成）；无映射中性 50",
        "安全硬闸（&lt;0.6 无条件拦截， 短路不进价值乘法）：下架→L4/"
        "违禁词（40号复用+商品域扩展）/差评观察×0.5",
        "L1-L4 分级：L4 屏蔽留痕（blockedReasons 永不静默丢弃）",
        "信值加权排序（文档公式）：最终分 = 基础分×(1+α×雷达总分/100)"]),
    ("P2 透明定价与导购（xinzhi_pricing/guide_service.py）", [
        "定价管线（全确定性， 60号 compute_price 复用）：①三因子（封顶 "
        "0.7）→②α 信值抵扣（单笔≤30%）→③地板保护→④杀熟审计（价差"
        "&gt;20% 留痕, 处置经 46号）",
        "公示行：原价¥X - 信值抵扣¥Y（等级G α=Z）- 三因子折扣¥W = 实付¥V",
        "反馈闭环：L1安抚自动回复/L2工单24h/L3紧急15分钟；路由（物流→"
        "物流运营/分数→信值产品/客服→客服主管）；人工轨不可重复",
        "导购人格：SOP五步法；LLM 禁数字铁律（话术=模板拼接, 数字 100% "
        "查询层插值）；L4 硬闸商品如实告知不静默"]),
    ("P3 互助购物生态（xinzhi_neighbor_service.py）", [
        "邻里臻选频道（零个体数据红线——P6f-3 口径）：品类聚合；匿名门槛 "
        "K=5（&lt;5 人品类不展示防群体反推个体）",
        "邻里求购（67号叫帮范式直接复用, 67号模块零改动）：三单上限+违禁"
        "词预检+LBS 20km 排序（紧急→距离→新单）+响应仅计数脱敏",
        "碳积分联动：求购关闭折算 500g×(1+响应人数)；碳档案=67号互助碳"
        "只读引用+68号求购碳；不可交易（宪法口径防金融化）"]),
    ("P4 商家信值体系（xinzhi_merchant_service.py）", [
        "4+2 认证（38号审核范式）：四必查（主体资质/履约能力/服务保障/"
        "后台系统）缺一确定性拒绝留痕；两加分（生态贡献+8/外部背书+6）",
        "预演沙盘（P7c 范式移植）：申报配置→模拟 1000 单→信值轨迹 10 段"
        "爬坡→《启航报告》（TOP3 风险+成长路线图三杠杆分数弹性）",
        "动态评级 S-A-B-C-D：认证分+履约分（37号订单结算率×28+评价好评率"
        "×18 只读聚合）；升级自动生效留痕；降级仅生成 46号 pending 建议书"
        "（处罚永不自动——宪法域）"]),
    ("P5 灰度上线收官（xinzhi_mode/whitepaper_service.py）", [
        "三态灰度（全站范式）：off（默认零影响）/shadow（放行+留痕标记）"
        "/assist（生产）；读取链：护栏暂停&gt;运行时 override&gt;环境变量",
        "6 决策面门槛（off 时 409）：求购发布/响应/关闭+商家认证/评级重算"
        "+导购应答；观测面永不关停（宪法口径）",
        "A/B 护栏：三指标任一相对基线恶化&gt;3% 自动暂停（保护机制非处罚）；"
        "恢复须人工 resume；指标滚动留痕（截断 50 条）",
        "年度信值白皮书（四章节）：信值体系框架/年度数据/红线工程化案例"
        "（6 条）/开放倡议（CC BY-NC-SA）；PII 扫描兜底+样本门+67号联动"]),
]:
    E.append(P(title, "h3"))
    for it in items:
        E.append(B(it))
E.append(Spacer(1, 4))

E.append(P("四、数据资产", "h2"))
E.append(tbl(
    ["表", "用途", "期"],
    [["xinzhi_radar_snapshots", "用户五维快照（总分/分维/归因/审计指纹）", "P0"],
     ["xinzhi_product_scores", "商品三维评分快照（契合/安全/转化/分级）", "P1"],
     ["xinzhi_price_details", "价格构成拆解留痕（杀熟审计源）", "P2"],
     ["xinzhi_feedback", "反馈工单（标签/路由/状态/时效）", "P2"],
     ["xinzhi_groupbuys", "邻里求购（67号联动关联键）", "P3"],
     ["xinzhi_merchants", "商家信值档案（认证/评级/沙盘报告）", "P4"],
     ["xinzhi_grayscale", "灰度运行时态（override/护栏暂停/指标留痕）", "P5"]],
    [55 * mm, 95 * mm, 30 * mm]))
E.append(P("25 端点（X-Member-Id 会员域；off 态观测面照常）；46号审批总线"
           "集成：xinzhi_merchant 评分器入册 SCORER_REGISTRY（batch 41）"
           "——商家降档建议书走 submit_change pending 人工裁决轨。"))
E.append(Spacer(1, 4))

E.append(P("五、验证体系", "h2"))
E.append(tbl(
    ["层", "口径", "结果"],
    [["专项测试", "6 套件 146 断言（26+24+29+23+23+21）", "全过"],
     ["跨期回归", "每期交付后 P0 起全量回归", "全绿"],
     ["Docker 实机", "verify_xinzhi_p5_live.py 56 断言×3 轮幂等", "56/56"],
     ["共存零影响", "40号 P7 实机回归（同一容器）", "33/33"],
     ["67号零改动", "git diff 确认（help_service 等零变更）", "确认"],
     ["语法+lint", "py_compile+ruff 全部 68号文件", "全绿"]],
    [35 * mm, 100 * mm, 45 * mm]))
E.append(note_box(
    "实机关键断言链：会员注册→雷达即时计算→播种 S/D 等级+trusted/"
    "restricted tier→商品三维评分→价格构成（trusted+S ¥216.41 vs "
    "restricted+D ¥281.4 价差 23%→auditFlag 留痕）→反馈 L1/L2/L3 分流"
    "→灰度默认 off（三决策面 409/观测面 200）→切档 assist（导购 SOP "
    "五步+求购全链+碳折算）→46号 sync 入册→4+2 认证+沙盘→护栏恶化自动"
    "暂停（决策面随 guard_pause 关闭）→人工恢复→白皮书四章节 PII 零命中"
    "→override 清除回落 off（零影响铁律闭环）。"))
E.append(Spacer(1, 4))

E.append(P("六、红线对照表（宪法域）", "h2"))
E.append(tbl(
    ["红线", "工程实现", "验证"],
    [["抵扣系数 α 只紧不松", "S.15/A.12/B.08 硬编码；放宽走 46号建议书", "专项+实机"],
     ["同商品用户间价差≤20%+公示", "杀熟审计留痕+价格构成卡强制", "实机（trusted vs restricted）"],
     ["抵扣≤30%/实付≥地板", "ALPHA_CAP+0.7 地板双闸", "实机（promo 0.5 触地板）"],
     ["降级永不自动", "商家降档仅 46号 pending 建议书", "专项（changeId 留痕）"],
     ["邻里频道零个体数据", "品类聚合+匿名门槛 K=5", "专项+实机"],
     ["LLM 禁入判定链", "评分/排序/定价/分流/评级全确定性；导购数字全查询层", "专项（数字一致性）"],
     ["观测面永不关停", "mode 只挡 6 决策面", "实机（off 态雷达 200）"],
     ["护栏暂停非处罚", "功能开关（自动）+人工恢复（留痕）", "实机"],
     ["碳积分不可交易", "只读观测口径（methodology 公示）", "专项"],
     ["灰度默认零影响", "XINZHI_MODE=off 时 67 模块零改动", "实机（40号回归）"]],
    [42 * mm, 88 * mm, 50 * mm]))
E.append(Spacer(1, 4))

E.append(P("七、大事记与提交链", "h2"))
E.append(P("2026-09-11：规划方案入库（2492f45）→P0 五维雷达（7a98166）→"
           "P1 臻选货架（fdb3a40）→P2 透明定价与导购（7e2772f）→P3 互助"
           "生态（eb64788）→P4 商家体系（b075122）→P5 灰度收官（951e83d）"
           "→Docker 实机（016a2e5）。"))
E.append(Spacer(1, 4))

E.append(P("八、代码资产", "h2"))
E.append(tbl(
    ["类别", "文件", "行数"],
    [["Repository", "xinzhi_repository.py（7 表）", "500"],
     ["Services", "radar/prime/pricing/guide/neighbor/merchant/mode/whitepaper_service.py", "2,931"],
     ["Routes", "xinzhi_routes.py（25 端点）", "623"],
     ["测试", "test_xinzhi_p0~p5.py（146 断言）", "2,376"],
     ["实机验收", "verify_xinzhi_p5_live.py（56 断言）", "544"],
     ["配置", "docker-compose.yml（XINZHI_MODE 脚手架）", "+7"],
     ["合计", "12 后端文件", "6,974"]],
    [30 * mm, 115 * mm, 35 * mm]))
E.append(Spacer(1, 4))

E.append(P("九、与巨头差异化", "h2"))
E.append(tbl(
    ["巨头", "本项目落地"],
    [["京东供应链确定性", "37号同盟+41号代驾履约 → 商家 S-A-D 评级驱动臻选位"],
     ["天猫品牌种草", "40号 P5b 创作工坊 → 种草内容经信值深审（拒虚假种草）"],
     ["淘宝长尾公平曝光", "信值加权排序含互助贡献——诚信小商家公平曝光"],
     ["美团本地生活", "67号叫帮 LBS 互助大厅零成本联动——全站最大差异点"]],
    [45 * mm, 135 * mm]))
E.append(note_box(
    "独有护城河：购物+互助一体（买酒可叫邻里代驾/求购可唤邻里互助/"
    "消费累积碳积分）——「15 分钟信值生活圈」在既有 67号上的零成本实现。"))

DOC1 = make_doc(
    "../docs/68号_信值臻选购物平台_五期全链交付总结报告.pdf",
    ("68号·信值·臻选购物平台", "五期全链交付总结报告"),
    "竹香酒 zxjiu.com · 68号模块 · 提交链 2492f45→016a2e5 · "
    "五期 146 专项断言+56 实机断言×3 轮幂等",
    list(E))
print(f"PDF generated: {DOC1}")

# ============================================================
# ② 运行报告
# ============================================================
E2 = []
E2.append(Spacer(1, 6))
E2.append(cover(
    ["68号·信值·臻选购物平台", "运行报告"],
    ["范围：五期交付（P0-P5）的运行时行为、实机验证证据与灰度运行口径",
     "环境：Docker 容器（Redis 模式, LOCK_MODE=redis / STORE_MODE=redis）",
     "报告时间：2026-09-11（提交 016a2e5 实机验证轮）"],
    ("68号·信值·臻选购物平台", "运行报告")))
E2.append(Spacer(1, 8))

E2.append(P("一、运行环境与部署拓扑", "h2"))
E2.append(P("宿主机 docker-compose（项目名 zhuxiang-jiu）：backend 容器"
            "（端口 8000, uvicorn 单 worker, XINZHI_MODE 环境变量默认 off——"
            "68号灰度总开关）+ redis 7-alpine（appendonly 持久化, 7 张 "
            "xinzhi_* Hash 表）。健康检查 /api/decision/health（10s 间隔）。"))
E2.append(B("部署命令（中文目录必须带 -p）：docker compose -p "
            "zhuxiang-jiu -f docker-compose.yml up -d --build backend"))
E2.append(B("验收脚本（宿主机执行, 直达容器网络）：python "
            "backend/verify_xinzhi_p5_live.py（56 断言全链）"))
E2.append(Spacer(1, 4))

E2.append(P("二、灰度运行口径（P5）", "h2"))
E2.append(P("2.1 三态与读取链", "h3"))
E2.append(tbl(
    ["态", "语义", "生效范围"],
    [["off（默认）", "决策面关闭（409）", "6 决策面拒绝； 观测面照常"],
     ["shadow", "观察学习期", "决策放行, 响应携带 xinzhiMode: shadow 留痕"],
     ["assist", "辅助生产期", "决策生效, 响应携带 xinzhiMode: assist"]],
    [30 * mm, 45 * mm, 105 * mm]))
E2.append(note_box("读取优先级：护栏暂停（guard_pause） &gt; 运行时 "
                   "override &gt; 环境变量 XINZHI_MODE &gt; 默认 off。"
                   "运行时切换不需重建容器：POST /api/xinzhi/mode/override"
                   "（?mode=assist 切档 / ?mode= 清除回落 env）；GET "
                   "/api/xinzhi/mode 总览。"))
E2.append(P("2.2 决策面/观测面清单", "h3"))
E2.append(tbl(
    ["类别", "端点", "off 态行为"],
    [["决策面（6）", "groupbuy 发布/响应/关闭、merchant apply/regrade、guide", "409（决策面关闭）"],
     ["观测面（永不关停）", "radar、radar/history、products/score、prime、price、feedback、"
      "guide/personas、neighbor、groupbuy GET、carbon、merchant simulate/level、"
      "mode、whitepaper", "200 正常"]],
    [35 * mm, 115 * mm, 30 * mm]))
E2.append(P("2.3 A/B 护栏（自动暂停机制）", "h3"))
for it in [
    "监控三指标：退款率 / 客诉进线率 / 卸载率（基线 0.05/0.02/0.01）",
    "触发条件：任一指标 (当期-基线)/基线 &gt; 3% → 自动暂停（等效 off）",
    "性质：保护机制（功能开关非处罚）——暂停可自动, 恢复须人工 POST "
    "/api/xinzhi/mode/resume（决策留痕）",
    "实机证据：退款率 0.05→0.10（+100%&gt;3%）→自动暂停→求购 409→"
    "人工 resume→回 assist 档→override 清除→回落 env=off",
]:
    E2.append(B(it))
E2.append(Spacer(1, 4))

E2.append(P("三、实机验证证据（56 断言×3 轮幂等）", "h2"))
E2.append(tbl(
    ["链路", "实测", "断言"],
    [["雷达即时计算", "五维产出（诚信30/互助25/专业20/活跃15/成长10）", "5"],
     ["播种等级控制", "S 级生效（radar GET 返回 grade=S）", "1"],
     ["商品三维评分", "ZX42-2026L07 批次评分+可解释（fit/safety/conversion）", "5"],
     ["价格构成（杀熟链）", "trusted+S 实付 ¥216.41（α=0.15 抵扣 ¥38.19）；"
      "restricted+D 实付 ¥281.4（零抵扣）；价差 23%&gt;20% → auditFlag 留痕", "4"],
     ["地板保护", "promo=0.5 时 floored=true, finalPrice=¥187.6（268×0.7）", "1"],
     ["反馈闭环", "L2 信值产品组路由/L3 紧急 15 分钟/L1 自动回复/越权 404", "6"],
     ["灰度门槛", "默认 off; 三决策面 409; 观测面 200", "6"],
     ["assist 放行", "导购 SOP 五步（steps=5+xinzhiMode=assist）/求购全链", "12"],
     ["46号总线", "registry/sync 入册 41+ 档案（xinzhi_merchant batch 41）", "1"],
     ["商家体系", "4+2 认证 A 档（certScore=54）/缺必查拒绝/沙盘 1000 单/评级 hold", "6"],
     ["邻里频道", "匿名门槛 K=5 公示/零个体数据（无 memberId/nickname/phone）", "2"],
     ["护栏", "正常不暂停/+100% 暂停/暂停后决策面关闭/guard_pause 总览", "4"],
     ["白皮书", "四章节/PII 零命中/年度聚合含 67号联动", "3"],
     ["收官复位", "override 清除回落 env=off（零影响铁律闭环）", "1"]],
    [38 * mm, 112 * mm, 30 * mm]))
E2.append(P("3.2 幂等性：同脚本连续 3 轮全绿（Redis 残留数据不破坏断言——"
            "唯一手机号注册/快照累加取最新/灰度态复位键重建）；业务表只增"
            "不删， 验证脚本零破坏。"))
E2.append(P("3.3 共存零影响：40号 P7 实机回归（同一容器）33/33 全绿；67号"
            "模块 git diff 零改动（只读引用 BANNED_KEYWORDS/carbon_profile/"
            "whitepaper）；46号 SCORER_REGISTRY 新增 1 档案， 既有 40 档案"
            "零影响。"))
E2.append(Spacer(1, 4))

E2.append(P("四、运行时数据资产口径", "h2"))
E2.append(tbl(
    ["表", "写入时机", "增长特性"],
    [["xinzhi_radar_snapshots", "每次雷达计算", "每人可多快照（历史曲线 90 日）"],
     ["xinzhi_product_scores", "每次评分批次", "商品×会员组合幂等锚"],
     ["xinzhi_price_details", "每次价格拆解", "留痕（杀熟审计源）"],
     ["xinzhi_feedback", "每次反馈提交", "L1 自动闭环/L2/L3 工单"],
     ["xinzhi_groupbuys", "求购发布", "三单上限控制"],
     ["xinzhi_merchants", "商家认证", "一人一档幂等锚"],
     ["xinzhi_grayscale", "运行时态", "单例（stateId=1）"]],
    [55 * mm, 50 * mm, 75 * mm]))
E2.append(P("序列化防护（P6g-4 实机教训）：bool 显式还原（12 字段）；复杂"
            "字段注册序列化清单（24 字段）；_list() 通配排除 :seq 序列键"
            "（36号 attract 教训）。"))
E2.append(Spacer(1, 4))

E2.append(P("五、宪法域红线运行时验证", "h2"))
E2.append(tbl(
    ["红线", "运行时行为", "实机证据"],
    [["杀熟零容忍", "价差&gt;20% → auditFlag 留痕（处置经 46号, 永不自动）", "trusted vs restricted 实测"],
     ["α 只紧不松", "S.15/A.12/B.08 硬编码（XINZHI_ALPHA 常量）", "实机 α=0.15"],
     ["地板不击穿", "α 抵扣后 max(final, 268×0.7)", "promo 0.5 实测"],
     ["降级永不自动", "降档仅 46号 pending 建议书, 等级恒定", "专项 changeId 留痕"],
     ["零个体数据", "邻里频道响应无任何身份字段", "实机 flat 扫描"],
     ["观测面永不关停", "off 态雷达/白皮书 200", "实机"],
     ["碳不可交易", "methodology 公示「只读观测不可交易」", "实机"],
     ["LLM 禁数字", "导购话术数字与查询层一致", "实机（¥/等级直出）"]],
    [38 * mm, 92 * mm, 50 * mm]))
E2.append(Spacer(1, 4))

E2.append(P("六、运维操作速查", "h2"))
E2.append(tbl(
    ["场景", "操作"],
    [["灰度放量", "POST /api/xinzhi/mode/override?mode=shadow → 观察 → assist"],
     ["紧急回退", "POST /api/xinzhi/mode/override?mode=off（或清除回落 env）"],
     ["护栏触发后", "确认指标回落 → POST /api/xinzhi/mode/resume（留痕）"],
     ["状态巡检", "GET /api/xinzhi/mode（态+护栏+检查/违规计数）"],
     ["年度报告", "GET /api/xinzhi/whitepaper（四章节, 零个体数据）"],
     ["降档裁决", "46号审批总线（xinzhi_merchant 档案 pending 建议书）"],
     ["实机验收", "python backend/verify_xinzhi_p5_live.py（56 断言）"]],
    [40 * mm, 140 * mm]))
E2.append(Spacer(1, 4))

E2.append(P("七、遗留与后续", "h2"))
E2.append(tbl(
    ["项", "状态", "说明"],
    [["生产部署（zxjiu.com）", "待执行", "代码已具备生产条件（compose 脚手架就位）"],
     ["前端「臻选」频道", "待实施", "规划含 taro-app 一级频道（首页入口）"],
     ["XINZHI_MODE 生产值", "建议", "首次上线 shadow（观察期）→数据达标后 assist"]],
    [45 * mm, 30 * mm, 105 * mm]))
E2.append(Spacer(1, 6))
E2.append(note_box(
    "结论：68号五期在 Docker Redis 容器实机环境完成 56 断言×3 轮幂等全绿："
    "五维雷达→臻选货架→透明定价（杀熟审计实测）→导购 SOP→邻里生态→"
    "商家体系（46号总线）→灰度护栏→白皮书全链贯通；与 40号（33/33）、"
    "67号（零改动）共存零影响；零影响铁律（off 默认）与观测面永不关停"
    "（宪法口径）在实机环境逐项验证通过。具备生产部署条件。"))

DOC2 = make_doc(
    "../docs/68号_信值臻选购物平台_运行报告.pdf",
    ("68号·信值·臻选购物平台", "运行报告"),
    "竹香酒 zxjiu.com · 68号模块运行报告 · 实机 56 断言×3 轮幂等 · "
    "提交 016a2e5",
    list(E2))
print(f"PDF generated: {DOC2}")

# ============================================================
# ③ 用户操作指南
# ============================================================
E3 = []
E3.append(Spacer(1, 6))
E3.append(cover(
    ["信值·臻选购物平台", "用户操作指南"],
    ["在这里, 您的诚信不是口号, 而是看得见的资产——它决定您看到什么好物、"
     "享受多少抵扣、被邻里如何信任。",
     "我们承诺：价格构成全透明, 永不大数据杀熟。"],
    ("信值·臻选购物平台", "用户操作指南")))
E3.append(Spacer(1, 8))

E3.append(P("一、这是什么？", "h2"))
E3.append(tbl(
    ["概念", "说明"],
    [["信值雷达", "您的五维信用画像（诚信/互助/专业/活跃/成长）, 0-100 分"],
     ["信值等级", "S（≥90）/A（80-89）/B（70-79）/C（60-69）/D（&lt;60）五档"],
     ["臻选货架", "只展示通过安全硬闸的好物, 高契合商品优先"],
     ["信值抵扣", "等级越高, 购物抵扣越多（S 级享基准价 15% 信值抵扣）"],
     ["邻里臻选", "看看同城市民在买什么（仅聚合数据, 无人可见您的购买）"]],
    [40 * mm, 140 * mm]))
E3.append(note_box(
    "核心承诺（红线公示）：① 价格构成强制公示：原价−信值抵扣−平台折扣="
    "实付, 每一项可追溯；② 同一商品对不同信值用户的价差不超过 20%, 超线"
    "自动进入审计通道；③ 信值抵扣永远受地板保护, 单笔抵扣不超过商品价的 "
    "30%；④ 观测与反馈通道永不关停——您的意见随时可提。"))
E3.append(Spacer(1, 4))

E3.append(P("二、信值雷达：您的信用画像", "h2"))
E3.append(P("2.1 五个维度怎么来？（零手工申报, 全部真实行为）", "h3"))
E3.append(tbl(
    ["维度", "权重", "数据来源"],
    [["诚信度", "30%", "风险档案（违规记录按 90 天半衰期衰减）"],
     ["互助值", "25%", "67号叫帮互助信值 + 帮助质量评价"],
     ["专业度", "20%", "44号评分体系（您贡献的评分质量）"],
     ["活跃度", "15%", "登录与订单流水（自然使用）"],
     ["成长力", "10%", "资产估值斜率（62号）"]],
    [30 * mm, 25 * mm, 125 * mm]))
E3.append(P("2.2 看懂您的雷达", "h3"))
for it in [
    "总分 0-100：分段曲线计分（及格线 50, 卓越线趋近 100）",
    "硬熔断：任一维 &lt; 40 → 总分封顶 59（单维短板会拖累整体）",
    "软奖励：连续 6 个月无违规 + 互助活跃前 10% → 总分 +5",
    "新用户保护：注册 30 天内冷启动权重（活跃度权重上调, 诚信度下调）"
    "——新人不因「没记录」被低估",
]:
    E3.append(B(it))
E3.append(P("2.3 每维「影响因素 TOP3」", "h3"))
E3.append(P("雷达页每一维都展示最具体的影响因素, 例如：诚信度 ↓——因上周 "
            "1 笔订单超时 / 风险信号命中 2 次。"))
E3.append(note_box(
    "如何提分（确定性路径, 无捷径）：守约（诚信）/ 参与邻里互助（互助"
    "提升最快）/ 发布高质量评价（专业）/ 自然活跃（活跃）/ 持续使用"
    "（成长）。"))
E3.append(Spacer(1, 4))

E3.append(P("三、臻选货架：选好物", "h2"))
E3.append(tbl(
    ["维度", "说明"],
    [["契合度", "商品特征×您的画像（您常参与互助→宴请/礼品类更契合）"],
     ["安全系数", "硬闸检查（在售状态/违禁词/差评观察）——低于 0.6 直接拦截"],
     ["转化潜力", "同类商品的历史成交表现"]],
    [30 * mm, 150 * mm]))
E3.append(P("分级：L1 臻选位（≥75, 货架前排）/ L2 优选（≥50）/ L3 普通 / "
            "L4 风险（拦截留痕, 详情页如实告知拦截原因——永不静默丢弃）。"
            "商品详情页含三维分明细（契合×安全×转化=价值分+解释文本）"
            "与信值加权排序分。"))
E3.append(Spacer(1, 4))

E3.append(P("四、透明定价：看清每一分钱", "h2"))
E3.append(P("4.1 价格构成卡（每个商品详情页强制公示）", "h3"))
price_arch = ("原价 ¥268\n"
              "- 信值抵扣 ¥38.19（等级S α=0.15）\n"
              "- 三因子折扣 ¥13.40（信任×贡献×活动）\n"
              "─────────────────\n"
              "= 实付 ¥216.41")
price_tbl = Table([[P(price_arch, "arch")]], colWidths=[None])
price_tbl.setStyle(TableStyle([
    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#2c1f14")),
    ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#ead9c2")),
    ("TOPPADDING", (0, 0), (-1, -1), 8),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ("LEFTPADDING", (0, 0), (-1, -1), 12),
    ("RIGHTPADDING", (0, 0), (-1, -1), 12),
]))
E3.append(price_tbl)
E3.append(Spacer(1, 4))
E3.append(P("您的等级越高, 信值抵扣越多：", "h3"))
E3.append(tbl(
    ["等级", "抵扣系数 α", "例（¥268 基准价）"],
    [["S", "15%", "约抵 ¥38"], ["A", "12%", "约抵 ¥30"],
     ["B", "8%", "约抵 ¥20"], ["C/D", "0", "无抵扣"]],
    [50 * mm, 50 * mm, 80 * mm]))
E3.append(P("4.2 三重保护（平台红线）", "h3"))
for it in [
    "抵扣上限：单笔信值抵扣 ≤ 商品价 30%（防恶意套现）",
    "地板保护：折上折再深, 实付也不低于基准价 7 折（68号 α 不可击穿 "
    "60号三因子地板）",
    "杀熟审计：同商品跨用户价差 &gt;20% 自动留痕进入审计通道（大数据"
    "杀熟零容忍）",
]:
    E3.append(B(it))
E3.append(Spacer(1, 4))

E3.append(P("五、小竹导购：问不踩坑", "h2"))
E3.append(P("在商品页向小竹提问, 她按 SOP 五步法回答："))
for i, it in enumerate([
    "意图锚定——听懂您问价格/求推荐/还是有疑问",
    "信值佐证——「您当前信值等级 S（雷达总分 95）, 等级决定臻选抵扣权益」",
    "透明解释——逐项拆解价格构成, 数字全部实时查询（无预测性数字）",
    "履约跟进——商品口碑（月销/评分）如实展示, 风险商品直接告知",
    "价值沉淀——指出您最弱维度, 给出确定性成长建议",
], 1):
    E3.append(Paragraph(f"{i}. {it}", S["bullet"]))
E3.append(note_box(
    "边界公示（导购人格卡片 GET /api/xinzhi/guide/personas）：小竹是"
    "解释者而非推销者——不承诺降价、不诱导消费；一切数字来自实时查询层。"))
E3.append(Spacer(1, 4))

E3.append(P("六、邻里臻选与求购：社区一起买", "h2"))
E3.append(P("6.1 邻里臻选频道：展示同城市民在买的品类聚合（多少人买过/"
            "多少单）。隐私红线：仅平台×品类聚合, 单品类不足 5 人不展示"
            "（您的个人购买任何人不可见）。"))
E3.append(P("6.2 邻里求购（三步）：① 发布（限 3 单在途；违禁内容直接拒绝）"
            "→② 响应（仅显示脱敏昵称, 不留任何联系方式——安全靠平台撮合）"
            "→③ 关闭（发起人确认, 记录邻里拼单碳减排 500g×参与人数, "
            "并入您的碳档案）。"))
E3.append(P("6.3 碳积分档案：67号互助碳（公益 100%/有偿 50%/接力均摊）"
            "+ 求购拼单碳。只读观测, 不可交易——它是荣誉记录, 不是货币。"))
E3.append(Spacer(1, 4))

E3.append(P("七、商家入驻：信值认证", "h2"))
E3.append(P("7.1 4+2 认证", "h3"))
E3.append(tbl(
    ["项目", "性质", "说明"],
    [["主体资质", "必查", "营业执照/许可证"],
     ["履约能力", "必查", "仓储/物流/供货证明"],
     ["服务保障", "必查", "售后/客服体系"],
     ["后台系统", "必查", "库存/订单系统对接"],
     ["生态贡献", "加分（+8）", "平台生态共建"],
     ["外部背书", "加分（+6）", "第三方权威认证"]],
    [40 * mm, 35 * mm, 105 * mm]))
E3.append(P("四必查缺一即拒（确定性规则, 提前补齐再申请, 省时间）。"))
E3.append(P("7.2 预演沙盘（入驻前先「试运营」）", "h3"))
E3.append(P("申报预期指标（日均单量/履约率/客诉率/时效达标率）→ 系统模拟 "
            "1000 单履约 → 生成《启航报告》：信值轨迹预测（10 段爬坡至"
            "稳态）/ TOP3 风险（按模拟绝对量排序：违约/客诉/迟发）/ 成长"
            "路线图（每项提升的分数弹性）。建议履约率 98%/客诉 ≤1%/时效 "
            "95% 即可稳定进入 A-S 档。沙盘为确定性推演（非承诺）。"))
E3.append(P("7.3 商家评级 S-A-B-C-D", "h3"))
E3.append(P("评级分 = 认证分 + 履约分（订单结算率×28 + 好评率×18, 全平台"
            "只读聚合——刷不出来）。升级：履约表现提升自动生效（历史留痕）；"
            "降级：永不自动——仅生成平台内部建议书, 经人工审批总线裁决"
            "（给商家申诉与改进窗口, 处罚透明）。"))
E3.append(Spacer(1, 4))

E3.append(P("八、反馈通道：意见直达（永不关停）", "h2"))
E3.append(tbl(
    ["级别", "触发", "响应承诺"],
    [["L3 紧急", "涉及诈骗/隐私泄露/人身安全等关键词", "15 分钟专人响应"],
     ["L2 工单", "物流/分数/客服/价格/商品/互助类明确问题", "24 小时内"],
     ["L1 安抚", "情绪/无效表达", "即时自动回复（已记录）"]],
    [30 * mm, 100 * mm, 50 * mm]))
E3.append(P("提交后可随时查询进度（GET /api/xinzhi/feedback/{id}, 仅本人"
            "可见）。"))
E3.append(Spacer(1, 4))

E3.append(P("九、灰度说明", "h2"))
E3.append(P("平台新功能按三态灰度渐进开放：观察（shadow）→ 辅助（assist）。"
            "若提示「决策面关闭」（409）：功能处于灰度观察期, 数据在积累, "
            "观察达标后开放。观测面永不关停：您的雷达/评分/价格构成/反馈/"
            "碳档案/白皮书在任何阶段都可正常查看——信用透明是宪法, 不是功能。"))
E3.append(Spacer(1, 4))

E3.append(P("十、常见问题", "h2"))
for q, a in [
    ("Q1：我的信值分为什么比朋友低？",
     "五维来源不同（互助活跃度/评价质量/守约记录）, 雷达页每维 TOP3 "
     "影响因素写得清清楚楚——全部可解释, 无黑箱。"),
    ("Q2：同一商品我和朋友价格不一样, 是杀熟吗？",
     "信值抵扣因等级而异（这是透明激励, 构成卡公示每一项）；但价差超过 "
     "20% 会自动触发审计——平台对大数据杀熟零容忍, 超线记录会被核查。"),
    ("Q3：信值能买卖吗？",
     "不能。信值与碳积分均为只读信用资产, 不可交易不可转让（防金融化"
     "红线）。"),
    ("Q4：商家被降级有救济吗？",
     "有。降级永不自动执行——必须经人工审批, 且全程留痕可申诉。"),
    ("Q5：邻里能看到我买了什么吗？",
     "永远不能。邻里频道仅展示品类聚合（&lt;5 人品类不展示）, 个体购买"
     "数据对任何人不可见。"),
]:
    E3.append(Paragraph(f"<b>{q}</b>", S["body"]))
    E3.append(Paragraph(f"A：{a}", S["bullet"]))
E3.append(Spacer(1, 4))

E3.append(P("附录：端点速查（技术读者）", "h2"))
E3.append(tbl(
    ["功能", "端点", "鉴权"],
    [["我的雷达", "GET /api/xinzhi/radar", "X-Member-Id"],
     ["90 日曲线", "GET /api/xinzhi/radar/history", "X-Member-Id"],
     ["臻选货架", "GET /api/xinzhi/prime", "X-Member-Id"],
     ["商品评分明细", "GET /api/xinzhi/products/{id}/score", "X-Member-Id"],
     ["价格构成", "GET /api/xinzhi/price/{id}", "X-Member-Id"],
     ["导购问答", "POST /api/xinzhi/guide", "X-Member-Id"],
     ["导购人格卡", "GET /api/xinzhi/guide/personas", "公开"],
     ["邻里臻选", "GET /api/xinzhi/neighbor", "公开"],
     ["求购大厅", "GET /api/xinzhi/groupbuy", "公开"],
     ["求购发布/响应/关闭", "POST /api/xinzhi/groupbuy + /{id}/respond + /{id}/close", "X-Member-Id"],
     ["我的碳档案", "GET /api/xinzhi/carbon/{member_id}", "公开只读"],
     ["反馈提交/进度", "POST /api/xinzhi/feedback + GET /{id}", "X-Member-Id"],
     ["商家认证/沙盘/评级", "POST /api/xinzhi/merchant/apply + /simulate + GET /{id}/level + POST /{id}/regrade", "X-Member-Id"],
     ["年度白皮书", "GET /api/xinzhi/whitepaper", "公开"]],
    [40 * mm, 110 * mm, 30 * mm]))
E3.append(Spacer(1, 6))
E3.append(note_box(
    "结语：信值·臻选——让每一次守约、每一次互助、每一条真实评价, 都成为"
    "您看得见的购物权益。价格透明是底线, 信用资产是您自己的。"))

DOC3 = make_doc(
    "../docs/68号_信值臻选购物平台_用户操作指南.pdf",
    ("信值·臻选购物平台", "用户操作指南"),
    "竹香酒 zxjiu.com · 68号模块用户操作指南 · "
    "价格透明/信用资产/邻里互助",
    list(E3))
print(f"PDF generated: {DOC3}")

print("All 3 PDFs generated OK")
sys.exit(0)
