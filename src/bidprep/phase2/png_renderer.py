from __future__ import annotations

from pathlib import Path

from bidprep.phase2.file_profile_schemas import ClientDocumentProfile
from bidprep.phase2.schemas import ClientFile, Requirement

PNG_REQUIRED_MATERIALS = frozenset(
    {
        "身份证",
        "营业执照",
        "执业许可证",
        "证书类",
        "授权书",
        "声明函",
        "承诺函",
        "盖章页",
        "签字页",
    }
)


def _safe_segment(text: str, max_len: int = 48) -> str:
    chars: list[str] = []
    for ch in text or "":
        if ch.isalnum() or "\u4e00" <= ch <= "\u9fff" or ch in "._-()（）":
            chars.append(ch)
        else:
            chars.append("_")
    value = "".join(chars).strip("_")
    return (value[:max_len] or "file").strip("_")


def preview_basename_for_requirement(
    ordinal: int,
    standard_material_name: str,
    source_original_stem: str,
    *,
    name_max: int = 48,
    stem_max: int = 24,
) -> str:
    return f"{ordinal:02d}_{_safe_segment(standard_material_name, name_max)}_{_safe_segment(source_original_stem, stem_max)}"


def render_pdf_pages_to_png(
    pdf_abs: str,
    out_dir: Path,
    basename: str,
    *,
    dpi: int = 120,
    selected_pages: list[int] | None = None,
) -> list[Path]:
    import fitz

    path = Path(pdf_abs)
    if not path.is_file():
        return []

    out_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf_abs)
    try:
        pages = selected_pages or list(range(1, doc.page_count + 1))
        scale = dpi / 72
        matrix = fitz.Matrix(scale, scale)
        out: list[Path] = []
        for page_number in pages:
            if page_number < 1 or page_number > doc.page_count:
                continue
            page = doc.load_page(page_number - 1)
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            dest = out_dir / f"{basename}_p{page_number:03d}.png"
            pix.save(str(dest))
            out.append(dest)
        return out
    finally:
        doc.close()


def render_image_to_png(src_abs: str, dest: Path) -> Path:
    from PIL import Image

    dest.parent.mkdir(parents=True, exist_ok=True)
    image = Image.open(src_abs)
    if image.mode == "RGBA":
        bg = Image.new("RGB", image.size, (255, 255, 255))
        bg.paste(image, mask=image.split()[3])
        image = bg
    elif image.mode != "RGB":
        image = image.convert("RGB")
    dest = dest.with_suffix(".png")
    image.save(str(dest), format="PNG")
    return dest


def render_docx_placeholder_png(client: ClientFile, dest: Path, text: str) -> list[Path]:
    from PIL import Image, ImageDraw, ImageFont

    dest.parent.mkdir(parents=True, exist_ok=True)
    lines = (text or "").splitlines()[:36] or ["(DOCX with no extracted text)"]
    image = Image.new("RGB", (1000, 1200), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("msyh.ttc", 18)
    except OSError:
        font = ImageFont.load_default()

    y = 24
    draw.text((24, y), f"DOCX: {client.original_filename}", fill=(20, 20, 20), font=font)
    y += 36
    for line in lines:
        draw.text((24, y), line[:90], fill=(20, 20, 20), font=font)
        y += 24
        if y > 1140:
            break
    image.save(str(dest), format="PNG")
    return [dest]


def should_render_png_for_requirement(requirement: Requirement, profile: ClientDocumentProfile | None) -> bool:
    req_text = f"{requirement.name} {requirement.category} {requirement.remark}"
    if any(material in req_text for material in PNG_REQUIRED_MATERIALS):
        return True
    if profile is None:
        return False
    if profile.should_render_png:
        return True
    if any(material in profile.detected_material_types for material in PNG_REQUIRED_MATERIALS):
        return True
    return Path(profile.file_path).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}


def default_selected_pages(client: ClientFile, profile: ClientDocumentProfile | None, *, render_all: bool) -> list[int] | None:
    if render_all:
        if client.page_count > 0:
            return list(range(1, client.page_count + 1))
        return None
    if profile and profile.preferred_png_pages:
        return profile.preferred_png_pages
    return [1]


def render_client_file_pngs(
    client: ClientFile,
    previews_dir: Path,
    basename: str,
    *,
    selected_pages: list[int] | None = None,
    render_all: bool = False,
) -> list[Path]:
    path = Path(client.abs_path)
    if not path.is_file():
        return []

    ext = path.suffix.lower()
    base = _safe_segment(basename)
    if ext == ".pdf":
        return render_pdf_pages_to_png(str(path), previews_dir, base, selected_pages=selected_pages)
    if ext in {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}:
        return [render_image_to_png(str(path), previews_dir / f"{base}_p001.png")]
    if ext == ".docx":
        return render_docx_placeholder_png(client, previews_dir / f"{base}_p001.png", client.extracted_text)
    return []
