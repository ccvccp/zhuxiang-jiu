"""竹医 zhuyi 包（79号·逻辑审查与修正 Agent）

三层校验（对齐 79号方案）:
  audit     L1 业务链完整性审查——5 条核心链逐步验证(端点在册/鉴权可达/前端调用点/数据流衔接)
  contract  L2 前后端契约对齐——前端 JS 调用面 vs registry 路由登记表(断契/死条目/未用端点)
  consult   L3 修正处方——检索 cases 故障复盘库 → 处方报告(Patch+回归建议+Blast Radius), 只出不施
"""
