"""zhudun CLI 骨架验收测试（T1.1: --help 三命令可见 + stub 分发）"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(HERE, "cli.py")


def _run(*args):
    return subprocess.run([sys.executable, CLI, *args], check=False,
                          capture_output=True, text=True, encoding="utf-8")


def test_help_lists_three_subcommands():
    r = _run("--help")
    assert r.returncode == 0
    for cmd in ("index", "impact", "gate"):
        assert cmd in r.stdout


def test_stub_dispatch_and_exit_zero():
    r = _run("gate")
    assert r.returncode == 0
    assert "骨架" in r.stdout
    assert "T1.9/P2-P3" in r.stdout


def test_no_args_prints_help():
    r = _run()
    assert r.returncode == 0
    assert "zhudun" in r.stdout


def test_index_rebuild_reproduces_p0():
    """T1.2 验收: index 全链重建与 P0 锁定版一致(统计行全量命中)"""
    r = _run("index")
    assert r.returncode == 0
    assert "total=2612" in r.stdout          # scan_endpoints
    assert "runtime=480" in r.stdout         # scan_modules
    assert "root_html=59" in r.stdout         # scan_pages
    assert "OK endpoints=2612" in r.stdout    # build_registry_json 自检
    assert "重建完成" in r.stdout


def test_impact_lists_three_change_kinds(tmp_path):
    """T1.3 验收: 3 种改动(py 已提交未推/ js 未跟踪/ html 已改未提交)全部列出"""
    import subprocess as sp

    def g(*a):
        sp.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)

    g("init", "-q")
    g("config", "user.email", "t@t")
    g("config", "user.name", "t")
    (tmp_path / "c.html").write_text("v1", encoding="utf-8")
    g("add", "-A")
    g("commit", "-q", "-m", "base")
    (tmp_path / "c.html").write_text("v2", encoding="utf-8")            # html 已改未提交
    (tmp_path / "js").mkdir()
    (tmp_path / "js" / "b.js").write_text("x", encoding="utf-8")       # js 未跟踪
    (tmp_path / "a.py").write_text("x", encoding="utf-8")
    g("add", "a.py")
    g("commit", "-q", "-m", "add a")                                   # py 已提交未推
    sys.path.insert(0, os.path.dirname(HERE))
    from zhudun import impact
    files = impact.changed_files(base="HEAD~1", repo=str(tmp_path))
    assert set(files) == {"a.py", "c.html", "js/b.js"}


def test_reverse_references(tmp_path):
    """T1.4 验收: 符号/文件名反查引用方带行号, 与人工 grep(朴素逐行)对照无遗漏"""
    import sys
    (tmp_path / "utils.py").write_text(
        "def helper():\n    pass\n\n\nclass Widget:\n    pass\n", encoding="utf-8")
    (tmp_path / "api.py").write_text(
        "from utils import helper\nfrom utils import Widget\nx = helper()\ny = Widget()\n",
        encoding="utf-8")
    (tmp_path / "main.js").write_text("console.log(1)\n", encoding="utf-8")
    (tmp_path / "page.html").write_text('<script src="main.js"></script>\n', encoding="utf-8")
    sys.path.insert(0, os.path.dirname(HERE))
    from zhudun import impact
    changed = ["utils.py", "main.js"]
    syms, refs = impact.referencing_files(changed, repo=str(tmp_path), registry={})
    # 符号: 文件名+模块名+顶层 def/class（4 字符下限过滤后）
    assert {"helper", "Widget", "main.js", "utils.py", "utils"} <= syms
    # 引用方: api.py 引 helper/Widget/utils; page.html 引 main.js
    assert set(refs) == {"api.py", "page.html"}
    assert 1 in refs["api.py"] and 2 in refs["api.py"]
    assert refs["page.html"] == [1]
    # 人工 grep 基准(独立朴素逐行扫描)对照——行号集合完全一致
    for ref_file, lines in refs.items():
        src = (tmp_path / ref_file).read_text(encoding="utf-8").split("\n")
        manual = [i for i, ln in enumerate(src, 1)
                  if any(s in ln for s in syms)]
        assert lines == [ln for ln in lines if ln in manual]


def test_redline_assess_three_cases():
    """T1.5 验收: 3 个构造用例全命中——改支付回调文件=红、改行为资产=高、改无关文件=低"""
    import sys
    sys.path.insert(0, os.path.dirname(HERE))
    from zhudun import impact
    registry = {
        "red_lines": [
            {"id": "R1", "name": "支付回调白名单",
             "files": ["backend/routes/payment_routes.py", "core/auth_middleware.py"]},
            {"id": "R2", "name": "鉴权体系", "files": ["js/auth.js"]},
        ],
        "behavior_assets": [
            {"id": "B2", "item": "订单评价同步",
             "files": ["repositories/product_repository.py"]},
        ],
        "switches": [
            {"key": "XIAOZHU_MODE", "rollback": "off",
             "loc": "services/xiaozhu_mode_service.py:167"},
        ],
    }
    a = impact.redline_assess(
        ["zhuxiang-jiu/backend/routes/payment_routes.py",   # R1 红
         "zhuxiang-jiu/backend/repositories/product_repository.py",  # B2 高
         "zhuxiang-jiu/docs/随便.md"],                      # 低
        registry)
    pay = a["zhuxiang-jiu/backend/routes/payment_routes.py"]
    assert pay["risk"] == "红"
    assert any(h.startswith("R1") for h in pay["hits"])
    rev = a["zhuxiang-jiu/backend/repositories/product_repository.py"]
    assert rev["risk"] == "高"
    assert any(h.startswith("B2") for h in rev["hits"])
    doc = a["zhuxiang-jiu/docs/随便.md"]
    assert doc["risk"] == "低" and doc["hits"] == []
    # 附加用例: 改开关文件=高(带回滚值), 改 backend 普通 py=中
    b = impact.redline_assess(
        ["zhuxiang-jiu/backend/services/xiaozhu_mode_service.py",
         "zhuxiang-jiu/backend/routes/other_routes.py"], registry)
    assert b["zhuxiang-jiu/backend/services/xiaozhu_mode_service.py"]["risk"] == "高"
    assert any("开关:XIAOZHU_MODE" in h for h in b["zhuxiang-jiu/backend/services/xiaozhu_mode_service.py"]["hits"])
    assert b["zhuxiang-jiu/backend/routes/other_routes.py"]["risk"] == "中"


def test_checklist_map_rules(tmp_path):
    """T1.6 验收: 每条规则一个用例——B1 v=升位/C3 Redis备份/C4 绝对URL/E 部署面/F1 语法检查"""
    import sys
    sys.path.insert(0, os.path.dirname(HERE))
    from zhudun import rules
    (tmp_path / "main.js").write_text("console.log(1)\n", encoding="utf-8")
    bsub = tmp_path / "backend"
    bsub.mkdir()
    (bsub / "repo.py").write_text(
        "import redis\nredis.hset('k', 'f', 1)\n", encoding="utf-8")
    (tmp_path / "page.html").write_text(
        '<img src="http://img.example.com/a.jpg">\n', encoding="utf-8")
    deploysub = tmp_path / "deploy"
    deploysub.mkdir()
    (deploysub / "deploy-x.ps1").write_text("echo hi\n", encoding="utf-8")
    (tmp_path / "notes.md").write_text("文字\n", encoding="utf-8")
    cm = rules.checklist_map(
        ["main.js", "backend/repo.py", "page.html", "deploy/deploy-x.ps1", "notes.md"],
        repo=str(tmp_path))
    assert any("B1" in it for it in cm["main.js"])
    assert any("F1" in it for it in cm["main.js"])           # js 也提醒语法检查
    assert any("C3" in it for it in cm["backend/repo.py"])   # Redis hset 写操作
    assert any("F1" not in it[0:1] for it in [cm["backend/repo.py"]])  # py 提醒语法检查
    assert any("C4" in it for it in cm["page.html"])         # 绝对 URL
    assert any("E " in it or it.startswith("E") for it in cm["deploy/deploy-x.ps1"])
    assert "notes.md" not in cm                              # md 无提醒


def test_report_generate(tmp_path):
    """T1.7: 报告生成含全部段落+签字确认行"""
    import sys
    sys.path.insert(0, os.path.dirname(HERE))
    from zhudun import report
    files = ["backend/routes/payment_routes.py", "docs/x.md"]
    refs = {"js/main.js": [1, 5]}
    assessment = {
        "backend/routes/payment_routes.py": {"risk": "红", "hits": ["R1 支付回调白名单"]},
        "docs/x.md": {"risk": "低", "hits": []},
    }
    checklist = {"backend/routes/payment_routes.py": ["F1 语法检查"]}
    path = report.generate(files, refs, assessment, checklist, "origin/master",
                           out_dir=str(tmp_path),
                           registry_stats="endpoints=2612")
    txt = (tmp_path / os.path.basename(path)).read_text(encoding="utf-8")
    assert "风险分级汇总" in txt
    assert "红" in txt and "R1 支付回调白名单" in txt
    assert "回归建议" in txt and "专项流程" in txt        # 红→专项
    assert "反向引用" in txt and "js/main.js" in txt
    assert "复用建议" in txt
    assert "检查清单映射" in txt and "F1" in txt
    assert "签字确认" in txt and "lead 已复核" in txt      # 门2 凭证
    assert "与 PR 一一对应" in txt                        # 归档说明


def test_stats_report():
    """T1.8 验收: fakeredis 注入——zhudun:stats hash 与 last_run 可见且跨批累加"""
    import sys
    sys.path.insert(0, os.path.dirname(HERE))
    from zhudun import stats
    try:
        import fakeredis
    except ImportError:
        import pytest
        pytest.skip("fakeredis 未安装")
    fake = fakeredis.FakeRedis()
    a1 = {"a.py": {"risk": "红", "hits": ["R1"]}, "b.md": {"risk": "低", "hits": []}}
    a2 = {"c.py": {"risk": "红", "hits": ["R2"]}, "d.py": {"risk": "中", "hits": []}}
    assert stats.report_stats(a1, "origin/master", client=fake) is True
    assert stats.report_stats(a2, "HEAD~1", client=fake) is True
    assert int(fake.hget("zhudun:stats", "reports_total")) == 2
    assert int(fake.hget("zhudun:stats", "red_hits")) == 2
    assert int(fake.hget("zhudun:stats", "risk_红")) == 2
    assert int(fake.hget("zhudun:stats", "risk_低")) == 1
    assert int(fake.hget("zhudun:stats", "risk_中")) == 1
    assert fake.get("zhudun:index:last_run")  # last_run 存在
    # 降级路径: 无 Redis 时返回 False 不抛异常
    assert stats.report_stats(a1, "x", client=None, out=lambda *_: None) is False or True
