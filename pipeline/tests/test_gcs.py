import pypdfium2 as pdfium
import io
from core.gcs import render_pdf_to_images

def _tiny_pdf() -> bytes:
    doc = pdfium.PdfDocument.new()
    doc.new_page(200, 200)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()

def test_render_returns_one_png_per_page():
    images = render_pdf_to_images(_tiny_pdf(), dpi=72)
    assert len(images) == 1
    assert images[0][:8] == b"\x89PNG\r\n\x1a\n"
