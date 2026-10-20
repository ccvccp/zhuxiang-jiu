"""竹盾 zhudun——78号·网站开发增量管理 Agent（P1 影响评估器 CLI 包）

形态: 开发期工具链（非常驻大模型服务），全只读分析、不写业务代码。
数据底座: docs/registry/（MD 为源, registry.json 由脚本生成——P0 已建, M0 已过门）。
路线: T1.1 骨架 → T1.2 index 吸收临时扫描器 → T1.3-T1.7 impact 全链 → T1.9 pre-commit 预检。
"""
__version__ = "0.1.0"
