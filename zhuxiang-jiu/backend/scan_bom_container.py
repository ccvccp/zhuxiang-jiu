"""容器内 py BOM 扫描(生产运行体)"""
import os

bad = []
for root in ("services", "routes", "repositories", "core"):
    if not os.path.isdir(root):
        continue
    for fn in os.listdir(root):
        if not fn.endswith(".py"):
            continue
        p = os.path.join(root, fn)
        with open(p, "rb") as f:
            head = f.read(3)
        if head == b"\xef\xbb\xbf":
            bad.append(p)
print("容器内带BOM py:", len(bad), "个")
for p in bad[:10]:
    print("  -", p)
