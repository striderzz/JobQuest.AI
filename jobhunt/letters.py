"""Cover-letter helpers: read extra context files (a LinkedIn PDF export, notes, project docs) and lay a finished
letter out as a clean one-page PDF."""
import os
import tempfile
from datetime import date
from pathlib import Path

from . import resume

CONTEXT_TYPES = (".pdf", ".docx", ".txt", ".md")
MAX_CONTEXT_CHARS = 6000


class LetterError(Exception):
    pass


def context_text(data, filename):
    """Plain text from an uploaded file, trimmed so a few files still fit in one prompt."""
    ext = Path(filename).suffix.lower()
    if ext not in CONTEXT_TYPES:
        raise LetterError("Use a PDF, DOCX, TXT or MD file.")
    if len(data) > 8 * 1024 * 1024:
        raise LetterError("That file is over 8 MB.")
    fd, tmp = tempfile.mkstemp(suffix=ext)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        try:
            text, _ = resume.extract(tmp)
        except resume.ResumeError as e:
            raise LetterError(str(e).replace(Path(tmp).name, filename)) from e
    finally:
        os.unlink(tmp)
    text = " ".join(text.split())
    return text[:MAX_CONTEXT_CHARS]


def _fonts(pdf):
    """Calibri from Windows when present (full Unicode); otherwise the built-in Helvetica."""
    win = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    if (win / "calibri.ttf").exists() and (win / "calibrib.ttf").exists():
        pdf.add_font("Body", "", str(win / "calibri.ttf"))
        pdf.add_font("Body", "B", str(win / "calibrib.ttf"))
        return "Body", False
    return "Helvetica", True


def _latin1(s):
    swaps = {"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-", "\u2014": "-", "\u2026": "...",
             "\u2022": "-", "\u20b9": "Rs."}
    for k, v in swaps.items():
        s = s.replace(k, v)
    return s.encode("latin-1", "replace").decode("latin-1")


def letter_pdf(body, name="", contact="", company="", role=""):
    """A4, one column, 11pt with generous margins - the layout recruiters expect. Returns PDF bytes."""
    from fpdf import FPDF
    pdf = FPDF(format="A4", unit="mm")
    pdf.set_margins(25, 22, 25)
    pdf.set_auto_page_break(True, margin=20)
    pdf.set_title(f"Cover letter - {company}" if company else "Cover letter")
    pdf.set_author(name or "")
    pdf.add_page()
    family, legacy = _fonts(pdf)
    fix = _latin1 if legacy else (lambda s: s)
    w = pdf.epw
    if name:
        pdf.set_font(family, "B", 16)
        pdf.cell(w, 8, fix(name), new_x="LMARGIN", new_y="NEXT")
    if contact:
        pdf.set_font(family, "", 9.5)
        pdf.set_text_color(90, 90, 96)
        pdf.cell(w, 5, fix(contact), new_x="LMARGIN", new_y="NEXT")
    pdf.set_draw_color(200, 200, 205)
    pdf.ln(3)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + w, pdf.get_y())
    pdf.ln(6)
    pdf.set_text_color(20, 20, 24)
    pdf.set_font(family, "", 11)
    pdf.cell(w, 5.5, date.today().strftime("%d %B %Y"), new_x="LMARGIN", new_y="NEXT")
    if company or role:
        pdf.ln(2)
        if company:
            pdf.cell(w, 5.5, fix(company), new_x="LMARGIN", new_y="NEXT")
        if role:
            pdf.cell(w, 5.5, fix(f"Re: {role}"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)
    for para in [p.strip() for p in body.replace("\r", "").split("\n\n") if p.strip()]:
        pdf.multi_cell(w, 5.6, align="L", text=fix(" ".join(para.split("\n")) if len(para) > 80 else para), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)
    return bytes(pdf.output())


# ---------------------------------------------------------------- Coach drafts (CV, email, letter) as files

def _blocks(md):
    """Light Markdown -> [(kind, text)]: h1, h2, bullet, para, gap. Bold/italic markers are dropped."""
    out = []
    for raw in md.replace("\r", "").split("\n"):
        line = raw.strip().replace("**", "").replace("__", "")
        if not line:
            out.append(("gap", ""))
        elif line.startswith("# "):
            out.append(("h1", line[2:].strip()))
        elif line.startswith(("## ", "### ")):
            out.append(("h2", line.lstrip("#").strip()))
        elif line[:2] in ("- ", "* ", "• "):
            out.append(("bullet", line[2:].strip()))
        elif set(line) <= set("-_=*"):
            out.append(("gap", ""))
        else:
            out.append(("para", line))
    return out


def document_pdf(md, title="Document"):
    from fpdf import FPDF
    pdf = FPDF(format="A4", unit="mm")
    pdf.set_margins(20, 18, 20)
    pdf.set_auto_page_break(True, margin=18)
    pdf.set_title(title)
    pdf.add_page()
    family, legacy = _fonts(pdf)
    fix = _latin1 if legacy else (lambda s: s)
    w = pdf.epw
    pdf.set_text_color(20, 20, 24)
    for kind, text in _blocks(md):
        if kind == "h1":
            pdf.set_font(family, "B", 18)
            pdf.multi_cell(w, 8, text=fix(text), new_x="LMARGIN", new_y="NEXT")
        elif kind == "h2":
            pdf.ln(2.5)
            pdf.set_font(family, "B", 11.5)
            pdf.cell(w, 6, fix(text.upper()), new_x="LMARGIN", new_y="NEXT")
            pdf.set_draw_color(190, 190, 196)
            pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + w, pdf.get_y())
            pdf.ln(1.5)
        elif kind == "bullet":
            pdf.set_font(family, "", 10.5)
            pdf.set_x(pdf.l_margin + 3)
            pdf.cell(4, 5.2, fix("•") if not legacy else "-")
            pdf.multi_cell(w - 7, 5.2, text=fix(text), new_x="LMARGIN", new_y="NEXT")
        elif kind == "para":
            pdf.set_font(family, "", 10.5)
            pdf.multi_cell(w, 5.2, text=fix(text), new_x="LMARGIN", new_y="NEXT")
        else:
            pdf.ln(2)
    return bytes(pdf.output())


def document_docx(md, title="Document"):
    import io
    import docx
    from docx.shared import Pt
    d = docx.Document()
    d.core_properties.title = title
    style = d.styles["Normal"]
    style.font.name, style.font.size = "Calibri", Pt(10.5)
    for kind, text in _blocks(md):
        if kind == "h1":
            d.add_heading(text, level=0)
        elif kind == "h2":
            d.add_heading(text, level=1)
        elif kind == "bullet":
            d.add_paragraph(text, style="List Bullet")
        elif kind == "para":
            d.add_paragraph(text)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()
