"""zhudun index——资产图谱重建（T1.2: 吸收 P0 四件临时扫描器）

按序执行 docs/registry/tools/ 四件生成器, 重建:
  scan_endpoints.py   -> L0-端点清单.md
  scan_modules.py     -> L0-模块清单.md
  scan_pages.py       -> L0-页面清单.md
  build_registry_json.py -> registry.json (MD 为源, 脚本生成)

吸收原则: 复用不改写(扫描逻辑九形态实证过, 禁复制漂移);
P3 收口时把四件工具正式内化为本包模块并移除临时文件。
"""
import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
TOOLS = os.path.join(REPO, "docs", "registry", "tools")
SCRIPTS = ("scan_endpoints.py", "scan_modules.py", "scan_pages.py", "build_registry_json.py")


def _load(name):
    path = os.path.join(TOOLS, name)
    spec = importlib.util.spec_from_file_location(name[:-3], path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run(args=None):
    """重建三份 L0 MD + registry.json; 输出各件工具的统计行供人工校对"""
    for name in SCRIPTS:
        mod = _load(name)
        mod.main()
    print("[zhudun index] 重建完成: 三份 L0 MD + registry.json (人工校对后 git diff 应为 0)")
    return 0
