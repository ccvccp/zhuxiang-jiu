# -*- coding: utf-8 -*-
"""抓取《十部门关于加快推进竹产业创新发展的意见》政府网全文并提取正文"""
import re
import urllib.request

URL = ("https://www.gov.cn/zhengce/zhengceku/2021-12/07/"
       "content_5658570.htm")
UA = "ZhuxiangKnowledgeBot/1.0 (+https://zxjiu.com)"
DST = r"D:\网站素材图\关于加快推进竹产业创新发展的意见\意见全文.txt"

req = urllib.request.Request(URL, headers={"User-Agent": UA})
with urllib.request.urlopen(req, timeout=20) as r:
    html = r.read().decode("utf-8", "ignore")
print("HTML 字符:", len(html))

# 提取正文区(gov.cn 政策库正文在 pages_content / #UCAP-CONTENT)
m = re.search(
    r'(?:id="UCAP-CONTENT"|class="pages_content")[^>]*>(.*?)</div>',
    html, re.S)
body = m.group(1) if m else html
# 去标签
text = re.sub(r"<script.*?</script>|<style.*?</style>", "", body, flags=re.S)
text = re.sub(r"<[^>]+>", "\n", text)
text = re.sub(r"&nbsp;?", " ", text)
text = re.sub(r"[ \t]+", " ", text)
lines = [ln.strip() for ln in text.splitlines()]
lines = [ln for ln in lines if ln]
full = "\n".join(lines)
with open(DST, "w", encoding="utf-8") as f:
    f.write(full)
print("正文行数:", len(lines), "| 字符:", len(full))
print("首3行:", lines[:3])
print("竹食品提及:", full.count("竹食品"), "| 竹医药提及:",
      full.count("竹医药"), "| 竹笋提及:", full.count("竹笋"))
