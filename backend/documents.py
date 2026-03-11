"""
Document extraction for Portfolio Guru — handles PDF, PPTX, DOCX files.
Uses markitdown for PPTX/DOCX and pypdf/pdfplumber for PDFs.
"""
import os
import tempfile
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


async def extract_from_document(file_path: str) -> str:
    """
    Extract text from a document file (PDF, PPTX, DOCX).
    Returns extracted text or empty string if extraction fails.
    """
    path = Path(file_path)
    suffix = path.suffix.lower()
    
    try:
        if suffix == ".pdf":
            return await _extract_pdf(file_path)
        elif suffix in (".pptx", ".ppt"):
            return await _extract_pptx(file_path)
        elif suffix in (".docx", ".doc"):
            return await _extract_docx(file_path)
        elif suffix in (".txt", ".md", ".rst"):
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        else:
            logger.warning(f"Unsupported file type: {suffix}")
            return ""
    except Exception as e:
        logger.error(f"Document extraction failed for {file_path}: {e}")
        return ""


async def _extract_pdf(file_path: str) -> str:
    """Extract text from PDF using pypdf."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(file_path)
        text_parts = []
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)
        return "\n\n".join(text_parts)
    except Exception as e:
        logger.error(f"PDF extraction failed: {e}")
        return ""


async def _extract_pptx(file_path: str) -> str:
    """Extract text from PowerPoint using python-pptx."""
    try:
        from pptx import Presentation
        prs = Presentation(file_path)
        text_parts = []
        
        for slide_num, slide in enumerate(prs.slides, 1):
            slide_texts = []
            for shape in slide.shapes:
                if hasattr(shape, "text") and shape.text.strip():
                    slide_texts.append(shape.text.strip())
            if slide_texts:
                text_parts.append(f"--- Slide {slide_num} ---\n" + "\n".join(slide_texts))
        
        return "\n\n".join(text_parts)
    except Exception as e:
        logger.error(f"PPTX extraction failed: {e}")
        return ""


async def _extract_docx(file_path: str) -> str:
    """Extract text from Word document using python-docx."""
    try:
        from docx import Document
        doc = Document(file_path)
        text_parts = []
        
        for para in doc.paragraphs:
            if para.text.strip():
                text_parts.append(para.text.strip())
        
        # Also extract text from tables
        for table in doc.tables:
            for row in table.rows:
                row_text = []
                for cell in row.cells:
                    if cell.text.strip():
                        row_text.append(cell.text.strip())
                if row_text:
                    text_parts.append(" | ".join(row_text))
        
        return "\n\n".join(text_parts)
    except Exception as e:
        logger.error(f"DOCX extraction failed: {e}")
        return ""


def get_supported_extensions() -> list:
    """Return list of supported file extensions."""
    return [".pdf", ".pptx", ".ppt", ".docx", ".doc", ".txt", ".md"]


def is_supported_document(filename: str) -> bool:
    """Check if a filename has a supported extension."""
    suffix = Path(filename).suffix.lower()
    return suffix in get_supported_extensions()
