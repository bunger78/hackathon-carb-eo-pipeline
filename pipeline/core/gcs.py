import io
import pypdfium2 as pdfium

def render_pdf_to_images(pdf_bytes: bytes, dpi: int = 200) -> list[bytes]:
    doc = pdfium.PdfDocument(pdf_bytes)
    out = []
    for page in doc:
        bitmap = page.render(scale=dpi / 72)
        pil = bitmap.to_pil()
        buf = io.BytesIO()
        pil.save(buf, format="PNG")
        out.append(buf.getvalue())
    return out

class GCSStore:
    def __init__(self, bucket_name: str):
        from google.cloud import storage
        self.bucket = storage.Client().bucket(bucket_name)
        self.name = bucket_name

    def upload_pdf(self, eo: str, data: bytes) -> str:
        path = f"pdfs/{eo.lower()}.pdf"
        self.bucket.blob(path).upload_from_string(data, content_type="application/pdf")
        return f"gs://{self.name}/{path}"

    def pdf_uri(self, eo: str) -> str:
        return f"gs://{self.name}/pdfs/{eo.lower()}.pdf"

    def cached_pdf(self, eo: str) -> bytes | None:
        """Bytes already in the bucket for this EO, or None. Only a payload
        with the %PDF magic counts -- an empty or garbage object (e.g. a WAF
        error body stored by an earlier fetch) must not satisfy the cache."""
        blob = self.bucket.blob(f"pdfs/{eo.lower()}.pdf")
        if not blob.exists():
            return None
        data = blob.download_as_bytes()
        return data if data.startswith(b"%PDF") else None

    def download(self, gs_uri: str) -> bytes:
        path = gs_uri.split(f"gs://{self.name}/", 1)[1]
        return self.bucket.blob(path).download_as_bytes()

    def upload_page_images(self, eo: str, images: list[bytes]) -> list[str]:
        uris = []
        for i, img in enumerate(images):
            path = f"pages/{eo.lower()}/{i}.png"
            self.bucket.blob(path).upload_from_string(img, content_type="image/png")
            uris.append(f"gs://{self.name}/{path}")
        return uris
