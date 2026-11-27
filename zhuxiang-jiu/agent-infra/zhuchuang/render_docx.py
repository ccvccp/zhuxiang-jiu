import json
import zipfile
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

from contracts import load_json, source_hash

TITLES = (("planning", "第一章　创新规划"), ("design", "第二章　设计"), ("deployment", "第三章　部署方案"), ("acceptance", "第四章　验收计划／报告"))


class RenderError(RuntimeError):
    pass


def _flatten(value):
    if isinstance(value, dict):
        lines = []
        for key, item in value.items():
            nested = _flatten(item)
            lines.extend(f"{key}: {line}" for line in nested)
        return lines
    if isinstance(value, list):
        lines = []
        for item in value:
            lines.extend(_flatten(item))
        return lines
    return [str(value)]


def _markdown(manifest, chapters, evidence, review):
    lines = [f"# {manifest['topic']}创新规划设计部署验收", "", f"- 仓库提交：{manifest['repo_commit']}", f"- 生成方式：{manifest['generation']}", f"- 审查状态：{'通过' if review['word_ready'] else '阻断'}", ""]
    for stage, title in TITLES:
        lines.extend([f"## {title}", ""])
        for line in _flatten(chapters[stage]["content"]):
            lines.append(f"- {line}")
        lines.append("")
    lines.extend(["## 附录", "", "### 证据索引"])
    lines.extend(f"- {item['id']}：{item['path']} {item['locator']} — {item['summary']}" for item in evidence)
    lines.extend(["", "### 独立审查摘要", f"- 阻断项：{len(review['blockers'])}", f"- 警告：{len(review['warnings'])}"])
    return "\n".join(lines) + "\n"


def write_markdown(run_dir):
    run_dir = Path(run_dir)
    manifest = load_json(run_dir / "manifest.json")
    evidence = load_json(run_dir / "evidence.json")
    chapters = load_json(run_dir / "chapters.json")
    review = load_json(run_dir / "review.json")
    path = run_dir / "document.md"
    path.write_text(_markdown(manifest, chapters, evidence, review), encoding="utf-8")
    return path


def _font(run, size=10.5, bold=False):
    run.font.name = "Microsoft YaHei"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")
    run.font.size = Pt(size)
    run.bold = bold


def _add_value(document, value):
    if isinstance(value, dict):
        for key, item in value.items():
            paragraph = document.add_paragraph()
            _font(paragraph.add_run(f"{key}："), bold=True)
            if isinstance(item, (dict, list)):
                _add_value(document, item)
            else:
                _font(paragraph.add_run(str(item)))
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, (dict, list)):
                _add_value(document, item)
            else:
                paragraph = document.add_paragraph(style="List Bullet")
                _font(paragraph.add_run(str(item)))
    else:
        paragraph = document.add_paragraph()
        _font(paragraph.add_run(str(value)))


def render_document(run_dir):
    run_dir = Path(run_dir)
    manifest = load_json(run_dir / "manifest.json")
    evidence = load_json(run_dir / "evidence.json")
    chapters = load_json(run_dir / "chapters.json")
    review = load_json(run_dir / "review.json")
    if not review.get("word_ready") or review.get("blockers"):
        raise RenderError("审查存在阻断项，不生成正式 Word")
    if review.get("source_hash") != source_hash(chapters):
        raise RenderError("源稿已变化，必须重新审查")
    write_markdown(run_dir)
    document = Document()
    section = document.sections[0]
    section.page_width, section.page_height = Cm(21), Cm(29.7)
    section.top_margin = section.bottom_margin = Cm(2.5)
    section.left_margin = section.right_margin = Cm(2.6)
    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _font(title.add_run(f"{manifest['topic']}\n创新规划设计部署验收"), 22, True)
    control = document.add_table(rows=5, cols=2)
    control.style = "Table Grid"
    for row, (key, value) in zip(control.rows, (("目标项目", "ccvccp/zhuxiang-jiu"), ("仓库提交", manifest["repo_commit"]), ("生成日期", manifest["created_at"]), ("生成方式", manifest["generation"]), ("审查状态", "通过"))):
        row.cells[0].text, row.cells[1].text = key, str(value)
    document.add_heading("内容导航", level=1)
    for _, title_text in TITLES:
        document.add_paragraph(title_text, style="List Number")
    document.add_paragraph("附录")
    for stage, title_text in TITLES:
        document.add_heading(title_text, level=1)
        _add_value(document, chapters[stage]["content"])
    document.add_heading("附录", level=1)
    document.add_heading("证据索引", level=2)
    table = document.add_table(rows=1, cols=3)
    table.style = "Table Grid"
    table.rows[0].cells[0].text, table.rows[0].cells[1].text, table.rows[0].cells[2].text = "编号", "来源", "摘要"
    repeat_header = OxmlElement("w:tblHeader")
    repeat_header.set(qn("w:val"), "true")
    table.rows[0]._tr.get_or_add_trPr().append(repeat_header)
    for item in evidence:
        cells = table.add_row().cells
        cells[0].text, cells[1].text, cells[2].text = item["id"], f"{item['path']} {item['locator']}", item["summary"]
    document.add_heading("独立审查摘要", level=2)
    document.add_paragraph(f"阻断项：{len(review['blockers'])}；警告：{len(review['warnings'])}")
    document.add_heading("运行记录摘要", level=2)
    document.add_paragraph(json.dumps({"run_id": manifest["run_id"], "dirty": manifest["dirty"], "model": manifest["model"]}, ensure_ascii=False))
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), "PAGE")
    footer._p.append(field)
    path = run_dir / "创新规划设计部署验收.docx"
    document.save(path)
    errors = validate_docx(path)
    if errors:
        raise RenderError("；".join(errors))
    manifest["artifacts"]["docx"] = str(path)
    from contracts import atomic_write_json
    atomic_write_json(run_dir / "manifest.json", manifest)
    return path


def validate_docx(path):
    errors = []
    path = Path(path)
    if not path.is_file() or path.stat().st_size == 0:
        return ["DOCX 不存在或为空"]
    with zipfile.ZipFile(path) as package:
        if package.testzip() is not None:
            errors.append("DOCX ZIP 包损坏")
    document = Document(path)
    text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    for _, title in TITLES:
        if title not in text:
            errors.append(f"缺少 {title}")
    if "附录" not in text:
        errors.append("缺少附录")
    if "{{" in text or "}}" in text:
        errors.append("Word 含未解析模板变量")
    return errors
