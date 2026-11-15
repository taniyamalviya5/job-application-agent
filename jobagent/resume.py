"""Reads your resume as plain text for the AI."""
from __future__ import annotations

from pathlib import Path

from .config import ROOT


def resume_text(cfg: dict) -> str:
    p = cfg["profile"]
    md = ROOT / p["resume_text"]
    if md.exists() and md.read_text(encoding="utf-8").strip():
        return md.read_text(encoding="utf-8")
    pdf = ROOT / p["resume_pdf"]
    if pdf.exists():
        from pypdf import PdfReader
        return "\n".join((page.extract_text() or "") for page in PdfReader(str(pdf)).pages)
    return ""


def resume_pdf_path(cfg: dict) -> Path | None:
    pdf = ROOT / cfg["profile"]["resume_pdf"]
    return pdf if pdf.exists() else None
