"""Extract text and images from manual PDFs using PyMuPDF."""

from __future__ import annotations

import base64
import io
import sys
from typing import Optional

import fitz  # PyMuPDF
from PIL import Image


_MAX_HEADING_CHARS = 60
_FONT_FLAG_BOLD = 16


def _is_bold_span(span: dict) -> bool:
    flags = span.get("flags", 0)
    if flags & _FONT_FLAG_BOLD:
        return True
    font = span.get("font", "")
    return "Bold" in font or "bold" in font


def _extract_section_heading(page: "fitz.Page") -> Optional[str]:
    """Return the first heading-like line on the page, if any.

    Heuristic: a line is a heading if it is shorter than ~60 chars and
    either entirely bold or in ALL CAPS.
    """
    try:
        blocks = page.get_text("dict").get("blocks", [])
    except Exception as exc:  # noqa: BLE001
        print(f"  [warn] heading extraction failed: {exc}", file=sys.stderr)
        return None

    for block in blocks:
        if block.get("type") != 0:  # 0 = text block
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            if not spans:
                continue
            parts = []
            all_bold = True
            for span in spans:
                text = span.get("text", "").strip()
                if not text:
                    continue
                parts.append(text)
                if not _is_bold_span(span):
                    all_bold = False
            line_text = " ".join(parts).strip()
            if not line_text or len(line_text) >= _MAX_HEADING_CHARS:
                continue
            is_all_caps = line_text.isupper() and any(c.isalpha() for c in line_text)
            if all_bold or is_all_caps:
                return line_text
    return None


def _image_bytes_to_png_b64(img_bytes: bytes, ext: str) -> str:
    """Convert raw image bytes (any format PIL can read) to base64 PNG."""
    if ext.lower() == "png":
        return base64.b64encode(img_bytes).decode("ascii")
    with Image.open(io.BytesIO(img_bytes)) as pil_img:
        if pil_img.mode not in ("RGB", "RGBA", "L"):
            pil_img = pil_img.convert("RGB")
        buf = io.BytesIO()
        pil_img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")


def _extract_page_images(page: "fitz.Page", doc: "fitz.Document") -> list[str]:
    images: list[str] = []
    try:
        image_refs = page.get_images(full=True)
    except Exception as exc:  # noqa: BLE001
        print(f"  [warn] image listing failed: {exc}", file=sys.stderr)
        return images

    for img_info in image_refs:
        xref = img_info[0]
        try:
            base_image = doc.extract_image(xref)
            img_bytes = base_image["image"]
            ext = base_image.get("ext", "png")
            images.append(_image_bytes_to_png_b64(img_bytes, ext))
        except Exception as exc:  # noqa: BLE001
            print(
                f"  [warn] failed to extract image xref={xref}: {exc}",
                file=sys.stderr,
            )
            continue
    return images


def parse_manual(pdf_path: str) -> list[dict]:
    """Parse a PDF manual into a list of per-page dicts.

    Each dict contains: page_num, section_heading, text, images (base64 PNG
    strings). If a page has no extractable text but does contain images, the
    dict additionally carries image_only=True.
    """
    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:  # noqa: BLE001
        print(f"[error] failed to open PDF {pdf_path}: {exc}", file=sys.stderr)
        raise

    pages: list[dict] = []
    try:
        for page_index, page in enumerate(doc):
            page_num = page_index + 1
            try:
                raw_text = (page.get_text() or "").strip()
                heading = _extract_section_heading(page)
                images = _extract_page_images(page, doc)
                page_data: dict = {
                    "page_num": page_num,
                    "section_heading": heading,
                    "text": raw_text,
                    "images": images,
                }
                if not raw_text and images:
                    page_data["image_only"] = True
                pages.append(page_data)
            except Exception as exc:  # noqa: BLE001
                print(
                    f"[warn] failed to process page {page_num}: {exc}",
                    file=sys.stderr,
                )
                continue
    finally:
        doc.close()
    return pages
