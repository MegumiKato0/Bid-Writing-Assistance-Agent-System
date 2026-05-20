from __future__ import annotations

"""
OCR 仅作兜底：依赖可选包 pytesseract 与系统 Tesseract 可执行文件。
未安装时 ocr_service_available() 为 False，上层不得把 OCR 当默认路径。
"""

from pathlib import Path

_ocr_checked: bool | None = None
_ocr_ok: bool = False


def ocr_service_available() -> bool:
    global _ocr_checked, _ocr_ok
    if _ocr_checked is not None:
        return _ocr_ok
    _ocr_checked = True
    try:
        import pytesseract  # noqa: F401
        from PIL import Image  # noqa: F401

        _ocr_ok = True
    except ImportError:
        _ocr_ok = False
    return _ocr_ok


def ocr_image_file(path: str) -> str:
    if not ocr_service_available():
        return ""
    import pytesseract
    from PIL import Image

    im = Image.open(path)
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    try:
        return pytesseract.image_to_string(im, lang="chi_sim+eng") or ""
    except Exception:
        return ""


def ocr_pdf_page_raster(pdf_path: str, page_index_zero_based: int, dpi: int = 150) -> str:
    if not ocr_service_available():
        return ""
    import fitz
    import pytesseract
    from PIL import Image
    import io

    doc = fitz.open(pdf_path)
    try:
        if page_index_zero_based < 0 or page_index_zero_based >= doc.page_count:
            return ""
        page = doc.load_page(page_index_zero_based)
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        return pytesseract.image_to_string(img, lang="chi_sim+eng") or ""
    finally:
        doc.close()
