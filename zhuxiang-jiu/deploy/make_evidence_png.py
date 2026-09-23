"""生成 BOM 污染证据总览图(官方反馈附件用)

全部使用真实取证数据:
- 波3 mtime 秒级序列(2026-09-23 凌晨, 71 文件字母序推进)
- CPU 双快照 Top5(写入轮活跃进程, 均为 Trae CN)
- 十六进制对比(HEAD 单层 BOM vs 污染后双层)
- 哨兵实验(14 新文件 0 触碰 vs 索引清单 71 命中)
输出: bom_evidence.png (PIL 手绘, 零外部依赖)
"""
from PIL import Image, ImageDraw, ImageFont

W, H = 1600, 2160
BG = (248, 247, 244)
INK = (35, 35, 38)
ACC = (53, 92, 68)      # 竹青主色
WARN = (181, 69, 60)
GRID = (215, 212, 205)

img = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(img)

F_T = "C:/Windows/Fonts/msyh.ttc"        # 雅黑(标题/正文)
F_M = "C:/Windows/Fonts/consola.ttf"      # 等宽(hex/代码)
ft = lambda z: ImageFont.truetype(F_T, z)
fm = lambda z: ImageFont.truetype(F_M, z)

f_title, f_h2, f_body = ft(40), ft(26), ft(19)
f_small, f_mono = ft(15), fm(19)


def sec(y, text):
    d.rectangle([80, y, 108, y + 30], fill=ACC)
    d.text((124, y - 3), text, font=f_h2, fill=INK)
    return y + 56


y = 70
d.text((80, y), "Trae CN 索引/缓存重建进程 BOM 污染 · 取证总览",
       font=f_title, fill=INK)
d.text((80, y + 56), "环境: Windows 11 ｜ Trae CN 1.107.1 (commit 11ec4a1, stable)"
       "  ｜ 取证时间: 2026-09-22 ~ 09-23  ｜ 全部数据来自实测, 复核脚本随附",
       font=f_small, fill=(120, 118, 112))
y = 170

# ---------- 区块 1: 十六进制对比 ----------
y = sec(y, "证据 1 ｜ 文件首字节十六进制对比 —— 每轮重建叠加一层 UTF-8 BOM")

hex_clean = "EF BB BF 69 6D 70 6F 72 74 20 52 65 61 63 74 ..."
hex_dirty = "EF BB BF EF BB BF 69 6D 70 6F 72 74 20 52 65 61 ..."
for label, hx, col, note in (
        ("HEAD 干净版(单层 BOM):", hex_clean, ACC, "git 提交的原始内容"),
        ("污染后(实测抓取):    ", hex_dirty, WARN, "每轮重建再叠加一层 EF BB BF")):
    d.text((100, y), label, font=f_body, fill=INK)
    d.text((100, y + 30), hx, font=f_mono, fill=col)
    d.text((100, y + 58), "# " + note, font=f_small, fill=(120, 118, 112))
    y += 92
d.text((100, y), "git diff 首行(实际抓取):  -\\ufeffimport React, ...    "
       "→    +\\ufeff\\ufeffimport React, ...", font=f_small, fill=INK)
y += 34
d.text((100, y), "受影响最重文件曾叠加至 10 层 BOM = 同一文件历经 10 轮重建写回",
       font=f_body, fill=WARN)
y += 46

# ---------- 区块 2: mtime 时间线 ----------
y = sec(y, "证据 2 ｜ 71 文件 mtime 按目录树字母序逐秒推进(2026-09-23 02:57-03:02)")

# 真实 mtime 序列(取证记录): (秒偏移, 文件名序号, 字母序段)
events = [
    (0, "services/xx64_anchor_service.py"), (2, "services/xx64_redteam_service.py"),
    (23, "test_ab63_p0.py"), (25, "test_ai_feedback_hooks.py"),
    (26, "test_ai_governance_p1.py"), (27, "test_ai_governance_p4.py"),
    (28, "test_ai_governance_scheduler.py"), (29, "test_ai_learning_routes.py"),
    (37, "test_av62_p0.py"), (38, "test_av62_p1.py"), (39, "test_av62_p2.py"),
    (41, "test_av62_p4.py"), (42, "test_av62_p5.py"), (43, "test_batch3_ai.py"),
    (44, "test_batch4_ai.py"), (45, "test_batch5_ai.py"), (46, "test_batch6_ai.py"),
    (56, "test_credit_ai.py"), (58, "test_dm61_p0.py"), (60, "test_dm61_p1.py"),
    (61, "test_dm61_p2.py"), (62, "test_dm61_p3.py"), (63, "test_dm61_p4.py"),
    (64, "test_dm61_p5.py"), (68, "test_ii58_p0.py"), (70, "test_ii58_p5.py"),
    (72, "test_ii59_p0.py"), (74, "test_kb57_p0.py"), (76, "test_kb57_p5.py"),
    (97, "test_payment_expire_scheduler.py"), (136, "test_xiaozhu_p4.py"),
    (142, "test_xiaozhu_v2.py"), (147, "test_xinzhi_platform.py"),
    (149, "test_xx64_p0.py"), (150, "test_xx64_p1.py"), (151, "test_xx64_p2.py"),
    (152, "test_xx64_p3.py"), (154, "test_xx64_p5.py"), (155, "test_xx65_p0.py"),
    (168, "verify_ab63_p0_live.py"), (182, "verify_av62_p0_live.py"),
    (183, "verify_av62_p1_live.py"), (184, "verify_av62_p2_live.py"),
    (185, "verify_av62_p3_live.py"), (186, "verify_av62_p4_live.py"),
    (187, "verify_av62_p5_live.py"), (188, "verify_batch2_ai_live.py"),
    (189, "verify_batch3_ai_live.py"), (190, "verify_batch4_ai_live.py"),
    (193, "verify_credit_ai_live.py"), (194, "verify_dm61_p0_live.py"),
    (195, "verify_dm61_p1_live.py"), (196, "verify_dm61_p2_live.py"),
    (197, "verify_dm61_p3_live.py"), (198, "verify_dm61_p4_live.py"),
    (199, "verify_dm61_p5_live.py"), (228, "verify_trust_value_p2_live.py"),
    (241, "verify_xx64_p0_live.py"), (242, "verify_xx64_p1_live.py"),
    (243, "verify_xx64_p2_live.py"), (244, "verify_xx64_p3_live.py"),
    (245, "verify_xx64_p4_live.py"), (246, "verify_xx64_p5_live.py"),
    (247, "verify_xx65_p0_live.py"), (248, "verify_xx65_p1_live.py"),
    (249, "verify_xx65_p2_live.py"), (250, "verify_xx65_p3_live.py"),
    (252, "verify_xx65_p4_live.py"), (257, "deploy/add-dns-records.ps1"),
    (321, "docs/67号_AI智能叫帮模块用户操作指南.md"),
    (327, "docs/会员等级与升级_用户操作指南.md"),
]
x0, x1 = 100, 1520
span = 340  # 秒
gx = lambda t: x0 + (x1 - x0) * t / span


def draw_timeline(y, base_off, base_label, col, evs, show_axis):
    top, bot = y + 26, y + 190
    for t in range(0, span + 1, 60):
        d.line([gx(t), top, gx(t), bot], fill=GRID)
        if show_axis:
            d.text((gx(t) - 18, bot + 6),
                   f"+{base_off + t // 60 * 60//60}min" if False else
                   base_label.format(t // 60),
                   font=f_small, fill=(120, 118, 112))
    n = len(evs)
    for i, (t, name) in enumerate(evs):
        px = gx(t)
        py = top + (bot - top) * i / max(1, n - 1)
        r = 4
        d.ellipse([px - r, py - r, px + r, py + r], fill=col)
    # 字母序段标注
    segs = [(0, 34, "backend/test_*（字母序）"), (34, 60, "backend/verify_*"),
            (60, 65, "deploy/"), (65, 67, "docs/")]
    for a, b, lab in segs:
        ya = top + (bot - top) * a / n
        yb = top + (bot - top) * b / n
        if b > a:
            d.line([x0 - 14, ya, x0 - 14, yb], fill=col, width=2)
            d.text((x0 + 8, (ya + yb) / 2 - 10), lab, font=f_small, fill=INK)
    return bot + 34


# 段A: 02:54:30-02:55:06 (taro-app 段, 5 文件)
d.text((100, y), "轮 A ｜ 02:54:30 - 02:55:06  (taro-app/ 段, 无人时段):",
       font=f_body, fill=INK)
y += 8
ev_a = [(0, "taro-app/src/pages/activity"), (6, "taro-app/src/pages/index"),
        (8, "taro-app/src/pages/mine"), (21, "taro-app/test/test-promo"),
        (36, "backend/prod_groupbuy_e2e.py")]
y = draw_timeline(y, 54, "+{}min", ACC, ev_a, True) + 10

# 段B: 02:57:14-03:02:41 (zhuxiang-jiu 段, 71 文件)
d.text((100, y), "轮 B ｜ 02:57:14 - 03:02:41  (zhuxiang-jiu/ 段, 71 文件与轮 A 首尾衔接):",
       font=f_body, fill=INK)
y += 8
y = draw_timeline(y, 117, "+{}min", WARN, events, True) + 6
d.text((100, y), "横轴=经过秒数(自段起点), 纵轴=文件字母序位次。散点单调右移 = "
       "索引器按目录树字母序逐文件写入, 非用户编辑行为。",
       font=f_small, fill=(120, 118, 112))
y += 44

# ---------- 区块 3: CPU 双快照 ----------
y = sec(y, "证据 3 ｜ 写入轮进行中 全进程 CPU 2 秒增量快照 Top5(全部为 Trae CN 进程)")
cpu = [
    ("Trae CN.exe  pid 28328  --type=renderer (02:53:41 启动)", 2312),
    ("Trae CN.exe  pid 21448  tsserver TypeScript LSP (02:54:38 启动)", 1343),
    ("Trae CN.exe  pid 22780  --utility-sub-type=node.mojom.NodeService", 1313),
    ("Trae CN.exe  pid 23500  --utility-sub-type=node.mojom.NodeService", 345),
    ("Trae CN.exe  pid 17480  NativeExtensionService", 218),
]
bx0, bx1 = 100, 1180
for i, (name, ms) in enumerate(cpu):
    yy = y + i * 58
    d.text((100, yy), f"{ms:>5} ms   " + name, font=f_small, fill=INK)
    bar_y = yy + 24
    ln = bx0 + int((bx1 - bx0) * ms / 2400)
    d.rectangle([bx0, bar_y, ln, bar_y + 18], fill=ACC if i < 3 else (140, 158, 148))
y += 5 * 58 + 2
d.text((100, y), "快照时刻: 2026-09-23 03:01:11(写入事件触发后 2 秒窗口)。"
       "02:53:41-02:54:38 为 Trae 凌晨自动维护拉起的进程, 其启动时间与污染窗口精确重合。",
       font=f_small, fill=(120, 118, 112))
y += 40

# ---------- 区块 4: 哨兵实验 + 波次 ----------
y = sec(y, "证据 4 ｜ 哨兵实验 + 波次汇总 —— 写入者遍历 Trae 索引缓存清单")
# 哨兵对比条
d.text((100, y), "在项目各目录新建 14 个哨兵文件(py/js/md/tsx):", font=f_body, fill=INK)
d.rectangle([100, y + 34, 620, y + 60], fill=(200, 199, 194))
d.text((116, y + 38), "哨兵命中 0 / 14   (新建文件不在索引清单)", font=f_small, fill=INK)
d.text((700, y + 34 - 0, ), "", font=f_small, fill=INK)
d.rectangle([700, y + 34, 1500, y + 60], fill=WARN)
d.text((716, y + 38), "Trae 索引清单命中 71 / 71 (含从未被编辑的文件)",
       font=f_small, fill=(255, 255, 255))
y += 82
waves = [
    ("波 1  09-22 白天(编辑会话后)", "79 个文件", "首次发现, docs 已叠至 10 层"),
    ("波 2  09-22 22:49-22:57", "76 个文件", "agent 检索/测试活动时段"),
    ("波 3  09-23 02:54-03:02", "5+71 个文件", "深夜无人时段, Trae 02:53 自动维护重启后"),
]
for i, (w, n, note) in enumerate(waves):
    yy = y + i * 36
    d.text((100, yy), w, font=f_small, fill=INK)
    d.text((480, yy), n, font=f_small, fill=WARN)
    d.text((660, yy), note, font=f_small, fill=(120, 118, 112))
y += 3 * 36 + 14
d.text((100, y), "已排除: git filter/autocrlf ｜ Python LSP jedi(重启实测无写入) ｜"
       " 百度网盘 ｜ 计划任务(孤儿) ｜ 系统维护任务",
       font=f_small, fill=(120, 118, 112))
y += 30

# footer
d.line([80, H - 96, W - 80, H - 96], fill=GRID)
d.text((80, H - 80), "取证工具链: 高频 mtime watcher + 全进程 CPU 双快照(ctypes) + "
       "14 哨兵阵列  ｜ 数据可提供原始日志复核  ｜ 2026-09-23",
       font=f_small, fill=(120, 118, 112))

img.save("d:/网站架构设计/bom_evidence.png")
print("saved bom_evidence.png", img.size)
