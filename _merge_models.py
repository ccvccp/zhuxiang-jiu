# -*- coding: utf-8 -*-
"""models.py 合并恢复: 决策模块42类(git c444dfb) + 85号ORM四表(当前版)"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

site = open(r"d:\网站架构设计\_models_site_hist.py", encoding="utf-8-sig").read()
cur = open(r"d:\网站架构设计\zhuxiang-jiu\backend\models.py", encoding="utf-8").read()
i = site.index("# ============================================================")
body_site = site[i:]
j = cur.index("from sqlalchemy import (")
body85 = cur[j:]
header = '''# -*- coding: utf-8 -*-
"""模型定义(合并版 2026-10-16 恢复)

上段: AI决策筹划模块(29号) Pydantic 模型——git c444dfb 版, 2026-10-15 10:37 被 85号 ORM 覆盖丢失, 本文件合并恢复;
下段: 85号·持久化层 ORM 四表(Drama/DramaRankSnapshot/CrawlJob, 2026-10-15 Day1 版)。
两段类名零冲突(已核)。主站 8000 因覆盖后重启失败, 恢复后可正常启动。
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

'''
sep = '''

# ============================================================
#  85号·持久化层 ORM 模型(Drama/DramaRankSnapshot/CrawlJob)
# ============================================================
'''
merged = header + body_site.rstrip() + "\n" + sep + "\n" + body85
open(r"d:\网站架构设计\zhuxiang-jiu\backend\models.py", "w", encoding="utf-8").write(merged)
print("merged size", len(merged))
