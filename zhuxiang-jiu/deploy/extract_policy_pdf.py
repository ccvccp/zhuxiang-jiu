# -*- coding: utf-8 -*-
"""提取《关于加快推进竹产业创新发展的意见》PDF 全文到 txt"""
import sys
from pypdf import PdfReader

SRC = r"D:\网站素材图\关于加快推进竹产业创新发展的意见\关于加快推进竹产业创新发展的意见.pdf"
DST = r"D:\网站素材图\关于加快推进竹产业创新发展的意见\意见全文.txt"

reader = PdfReader(SRC)
parts = []
for i, page in enumerate(reader.pages, 1):
    text = page.extract_text() or ""
    parts.append(f"--- 第{i}页 ---\n{text}")

full = "\n".join(parts)
with open(DST, "w", encoding="utf-8") as f:
    f.write(full)
print(f"页数: {len(reader.pages)} | 字符: {len(full)}")
