"""zhuyi report 断链六段式报告验收测试（T1.5 + M1 qr 回放）"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import audit as au
import chains as ch
import report as rp


def _all_findings(registry=None):
    """跑四检拿全量产物(registry 可注入 fixture)。"""
    chs = ch.parse_chains()
    registry = registry if registry is not None else ch.load_registry()
    f1, total = au.check_endpoint_registry(chs, registry)
    f2 = au.check_auth_reachable(chs, registry)
    call_map, no_call = au.check_frontend_calls(chs)
    gaps = au.check_dataflow(chs)
    return chs, registry, f1, total, f2, call_map, no_call, gaps


def test_generate_six_sections(tmp_path):
    """六段式结构: 六段头全在+分级+只出不施铁律+归档路径返回"""
    chs, reg, f1, _t, f2, cm, nc, gaps = _all_findings()
    path = rp.generate(chs, reg, f1, f2, cm, nc, gaps, out_dir=str(tmp_path))
    assert os.path.exists(path)
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    for sec in ("一、链概览", "二、断点明细", "三、涉及文件",
                "四、复现指引", "五、关联案例", "六、处置建议"):
        assert sec in text
    assert "只出不施" in text
    # 当前态: 断链级应为空(cart=miss 归疑点级), 扫码登录链判定健康
    assert "### 断链级" in text and "无（当前审查面未发现链走不通实锤）" in text
    assert "扫码登录链 | 6" in text


def test_generate_qr_dead_fixture_m1(tmp_path):
    """M1 回放核心: 死条目 fixture → 报告检出扫码链断链级 3 处+死条目归因+关联案例"""
    dead = au.build_qr_dead_registry()
    chs, reg, f1, _t, f2, cm, nc, gaps = _all_findings(registry=dead)
    assert sum(1 for f in f2 if f["kind"] == "auth_401") == 3
    path = rp.generate(chs, reg, f1, f2, cm, nc, gaps, out_dir=str(tmp_path))
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    # 断链级非空: exchange 401 永拦在列
    assert "无（当前审查面" not in text
    assert "401 永拦" in text and "/api/entry/qr/{qr_id}/exchange" in text
    # 链概览: 扫码登录链判定=断链
    row = next(ln for ln in text.splitlines() if ln.startswith("| 扫码登录链 |"))
    assert "**断链**" in row
    # 涉及文件: 鉴权白名单在列 + 前端调用面
    assert "auth_middleware.py" in text and "js/entry-login.js" in text
    # 关联案例: qr 死条目案例命中
    assert "C-20261021-01" in text
    # 处置建议: auth_401 建议在列
    assert "白名单条目与 L0-端点清单" in text


def test_audit_run_archives_report(tmp_path, capsys, monkeypatch):
    """run() 集成: 报告归档+度量上报(降级态)——patch audit 持有的模块实例(实例无关)"""
    monkeypatch.setattr(au.report_mod, "REPORTS_DIR", str(tmp_path))
    monkeypatch.setattr(au.stats_mod, "_connect", lambda: None)  # 度量降级防真连 Redis
    assert au.run() == 0
    out = capsys.readouterr().out
    assert "T1.5 断链报告" in out and "T1.5 度量" in out
    assert "已归档" in out and "降级" in out
    assert any(n.startswith("断链报告-") for n in os.listdir(tmp_path))
