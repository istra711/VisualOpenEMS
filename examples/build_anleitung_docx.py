"""Build ANLEITUNG_DE.docx from docs/ANLEITUNG_DE.md (subset of Markdown)."""

from __future__ import annotations

import re
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

ROOT = Path(__file__).resolve().parents[1]
MD_PATH = ROOT / "docs" / "ANLEITUNG_DE.md"
DOCX_PATH = ROOT / "docs" / "ANLEITUNG_DE.docx"


def _inline(paragraph, text: str) -> None:
    """Render `code`, **bold**, *italic* into a paragraph."""
    # Split on code, bold, italic
    pattern = re.compile(r"(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*)")
    pos = 0
    for m in pattern.finditer(text):
        if m.start() > pos:
            paragraph.add_run(text[pos : m.start()])
        token = m.group(0)
        if token.startswith("`") and token.endswith("`"):
            run = paragraph.add_run(token[1:-1])
            run.font.name = "Consolas"
            run.font.size = Pt(9)
            rpr = run._element.get_or_add_rPr()
            rfonts = rpr.find(qn("w:rFonts"))
            if rfonts is None:
                rfonts = rpr.makeelement(qn("w:rFonts"), {})
                rpr.append(rfonts)
            rfonts.set(qn("w:ascii"), "Consolas")
            rfonts.set(qn("w:hAnsi"), "Consolas")
        elif token.startswith("**") and token.endswith("**"):
            run = paragraph.add_run(token[2:-2])
            run.bold = True
        elif token.startswith("*") and token.endswith("*"):
            run = paragraph.add_run(token[1:-1])
            run.italic = True
        else:
            paragraph.add_run(token)
        pos = m.end()
    if pos < len(text):
        paragraph.add_run(text[pos:])


def _add_table(doc: Document, rows: list[list[str]]) -> None:
    if not rows:
        return
    n_cols = max(len(r) for r in rows)
    table = doc.add_table(rows=len(rows), cols=n_cols)
    table.style = "Table Grid"
    for i, row in enumerate(rows):
        for j in range(n_cols):
            cell = table.rows[i].cells[j]
            cell.text = ""
            p = cell.paragraphs[0]
            val = row[j] if j < len(row) else ""
            # strip simple markdown in cells
            val = re.sub(r"\*\*(.+?)\*\*", r"\1", val)
            val = re.sub(r"`(.+?)`", r"\1", val)
            run = p.add_run(val)
            if i == 0:
                run.bold = True
    doc.add_paragraph()


def build() -> Path:
    md = MD_PATH.read_text(encoding="utf-8")
    lines = md.splitlines()

    doc = Document()
    # Narrow margins slightly for tables
    for section in doc.sections:
        section.left_margin = Inches(0.75)
        section.right_margin = Inches(0.75)

    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)

    i = 0
    n = len(lines)
    while i < n:
        raw = lines[i]
        line = raw.rstrip()
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        # Horizontal rule
        if stripped in {"---", "***", "___"}:
            doc.add_paragraph("─" * 60)
            i += 1
            continue

        # Code fence
        if stripped.startswith("```"):
            i += 1
            block: list[str] = []
            while i < n and not lines[i].strip().startswith("```"):
                block.append(lines[i])
                i += 1
            i += 1  # skip closing fence
            p = doc.add_paragraph()
            run = p.add_run("\n".join(block) if block else "")
            run.font.name = "Consolas"
            run.font.size = Pt(9)
            run._element.get_or_add_rPr()
            p.paragraph_format.left_indent = Inches(0.25)
            continue

        # Headings
        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            level = len(m.group(1))
            text = re.sub(r"\*\*(.+?)\*\*", r"\1", m.group(2))
            text = re.sub(r"`(.+?)`", r"\1", text)
            text = re.sub(r"\*(.+?)\*", r"\1", text)
            doc.add_heading(text, level=min(level, 4))
            i += 1
            continue

        # Table
        if stripped.startswith("|") and i + 1 < n and re.match(r"^\|[\s\-:|]+\|$", lines[i + 1].strip()):
            i += 1  # skip separator
            rows: list[list[str]] = []
            while i < n and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                rows.append(cells)
                i += 1
            _add_table(doc, rows)
            continue

        # Checklist / bullet
        if stripped.startswith("- [ ]") or stripped.startswith("- [x]"):
            checked = stripped.startswith("- [x]")
            text = stripped[5:].strip()
            p = doc.add_paragraph(style="List Bullet")
            run = p.add_run(("[x] " if checked else "[ ] ") + re.sub(r"\*\*(.+?)\*\*", r"\1", text))
            if checked:
                run.bold = True
            i += 1
            continue

        if stripped.startswith("- ") or stripped.startswith("* "):
            text = stripped[2:].strip()
            p = doc.add_paragraph(style="List Bullet")
            _inline(p, text)
            i += 1
            continue

        # Ordered list
        m_ord = re.match(r"^(\d+)\.\s+(.*)$", stripped)
        if m_ord:
            p = doc.add_paragraph(style="List Number")
            _inline(p, m_ord.group(2))
            i += 1
            continue

        # Normal paragraph
        p = doc.add_paragraph()
        _inline(p, stripped)
        i += 1

    # Footer note
    doc.add_paragraph()
    p = doc.add_paragraph()
    run = p.add_run("Generiert aus docs/ANLEITUNG_DE.md — FreeCAD OpenEMS GUI")
    run.italic = True
    run.font.size = Pt(9)
    run.font.color.rgb = RGBColor(0x66, 0x66, 0x66)

    doc.save(DOCX_PATH)
    return DOCX_PATH


if __name__ == "__main__":
    path = build()
    print(f"Wrote {path}")
    print(f"Size: {path.stat().st_size} bytes")
