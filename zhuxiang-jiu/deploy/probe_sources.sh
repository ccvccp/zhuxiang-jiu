#!/bin/bash
# 种子源候选探测(从生产机房发起, UA 同爬虫)
UA="ZhuxiangKnowledgeBot/1.0 (+https://zxjiu.com)"
probe() {  # probe <名称> <url>
  code=$(curl -s -o /tmp/_p.html -w "%{http_code}" -A "$UA" --max-time 12 "$2")
  size=$(wc -c < /tmp/_p.html 2>/dev/null)
  kw=$(grep -c "白酒\|竹\|酿造\|发酵" /tmp/_p.html 2>/dev/null)
  echo "$1: HTTP $code, ${size}B, 关键词命中行 $kw"
}
probe "维基-白酒-页面" "https://zh.wikipedia.org/wiki/%E7%99%BD%E9%85%92"
probe "维基-API摘要" "https://zh.wikipedia.org/api/rest_v1/page/summary/%E7%99%BD%E9%85%92"
probe "维基-action全文" "https://zh.wikipedia.org/w/api.php?action=query&prop=extracts&explaintext=1&format=json&titles=%E7%99%BD%E9%85%92"
probe "百科-白酒" "https://baike.baidu.com/item/%E7%99%BD%E9%85%92"
probe "百科-竹子" "https://baike.baidu.com/item/%E7%AB%B9%E5%AD%90"
probe "百科-竹林七贤" "https://baike.baidu.com/item/%E7%AB%B9%E6%9E%97%E4%B8%83%E8%B4%A4"
rm -f /tmp/_p.html
