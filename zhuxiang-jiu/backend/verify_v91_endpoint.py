"""v91 failures 端点容器内验证"""
import urllib.request

r = urllib.request.urlopen(
    "http://localhost:8000/api/xiaozhu/evolution/failures")
print("status:", r.status)
print("body:", r.read()[:200].decode("utf-8", "ignore"))
