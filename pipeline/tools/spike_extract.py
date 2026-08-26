"""Day-1 spike: real Gemini extraction on N local PDFs. Usage:
   py -3 tools/spike_extract.py d-269-30 d-57-12 ...  (EO numbers, lowercase file style)"""
import sys, json, time
from pathlib import Path
from google import genai
from google.genai import types
from config import settings
from schemas.extraction import Extraction
from prompts.extractor import EXTRACTOR_PROMPT

PDF_DIR = Path(r"C:\Users\lee\OneDrive\Documents\CARBSearch\output\pdfs")

def main(eos: list[str]):
    client = genai.Client(vertexai=True, project=settings.project_id, location=settings.genai_location)
    from google.cloud import storage
    bucket = storage.Client(project=settings.project_id).bucket(settings.bucket)
    rows = []
    for eo in eos:
        pdf = PDF_DIR / f"{eo.lower()}.pdf"
        blob = bucket.blob(f"spike/{pdf.name}")
        blob.upload_from_filename(str(pdf))
        uri = f"gs://{settings.bucket}/spike/{pdf.name}"
        t0 = time.time()
        try:
            resp = client.models.generate_content(
                model=settings.model_id,
                contents=[EXTRACTOR_PROMPT, types.Part.from_uri(file_uri=uri, mime_type="application/pdf")],
                config=types.GenerateContentConfig(temperature=0, response_mime_type="application/json",
                                                   response_schema=Extraction),
            )
            u = resp.usage_metadata
            data = json.loads(resp.text)
            rows.append(dict(eo=eo, ok=True, secs=round(time.time()-t0,1),
                             tok_in=u.prompt_token_count, tok_out=u.candidates_token_count,
                             fitment_rows=len(data.get("fitment", [])),
                             pns=len(data.get("part_numbers", [])),
                             conf=data.get("confidence"),
                             supersedes=data.get("supersedes")))
            Path(f"spike_{eo}.json").write_text(json.dumps(data, indent=2))
        except Exception as e:
            rows.append(dict(eo=eo, ok=False, err=str(e)[:200]))
    cin, cout = settings.price_in_per_mtok/1e6, settings.price_out_per_mtok/1e6
    ok = [r for r in rows if r["ok"]]
    if ok:
        avg_out = sum(r["tok_out"] for r in ok)/len(ok)
        avg_in = sum(r["tok_in"] for r in ok)/len(ok)
        est = 6055*(avg_in*cin + avg_out*cout)
        print(f"\navg in={avg_in:.0f} out={avg_out:.0f} tokens; extraction-only backfill ≈ ${est:.0f}")
    for r in rows: print(r)

if __name__ == "__main__":
    main(sys.argv[1:])
