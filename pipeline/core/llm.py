import json
from dataclasses import dataclass
from config import settings

@dataclass
class LLMResult:
    data: dict
    tok_in: int
    tok_out: int
    finish_reason: str = "STOP"

def _default_client():
    from google import genai
    return genai.Client(vertexai=True, project=settings.project_id, location=settings.genai_location)

class LLM:
    def __init__(self, client=None, model: str | None = None):
        self.client = client or _default_client()
        self.model = model or settings.model_id

    def _call(self, contents, schema) -> LLMResult:
        from google.genai import types
        resp = self.client.models.generate_content(
            model=self.model, contents=contents,
            config=types.GenerateContentConfig(temperature=0, response_mime_type="application/json",
                                               response_schema=schema, max_output_tokens=65535))
        u = resp.usage_metadata
        try:
            fr = resp.candidates[0].finish_reason
            fr = getattr(fr, "name", str(fr)) if fr is not None else "STOP"
        except (AttributeError, IndexError, TypeError):
            fr = "STOP"
        return LLMResult(json.loads(resp.text), u.prompt_token_count or 0, u.candidates_token_count or 0, fr)

    def extract_pdf(self, gcs_uri: str, prompt: str, schema) -> LLMResult:
        from google.genai import types
        return self._call([prompt, types.Part.from_uri(file_uri=gcs_uri, mime_type="application/pdf")], schema)

    def extract_images(self, image_uris: list[str], prompt: str, schema) -> LLMResult:
        from google.genai import types
        parts = [prompt] + [types.Part.from_uri(file_uri=u, mime_type="image/png") for u in image_uris]
        return self._call(parts, schema)

    def generate_json(self, prompt: str, schema) -> LLMResult:
        return self._call([prompt], schema)
