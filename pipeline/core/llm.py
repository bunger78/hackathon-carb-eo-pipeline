import json
from dataclasses import dataclass
from config import settings

@dataclass
class LLMResult:
    data: dict
    tok_in: int
    tok_out: int

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
                                               response_schema=schema))
        u = resp.usage_metadata
        return LLMResult(json.loads(resp.text), u.prompt_token_count or 0, u.candidates_token_count or 0)

    def extract_pdf(self, gcs_uri: str, prompt: str, schema) -> LLMResult:
        from google.genai import types
        return self._call([prompt, types.Part.from_uri(file_uri=gcs_uri, mime_type="application/pdf")], schema)

    def extract_images(self, image_uris: list[str], prompt: str, schema) -> LLMResult:
        from google.genai import types
        parts = [prompt] + [types.Part.from_uri(file_uri=u, mime_type="image/png") for u in image_uris]
        return self._call(parts, schema)

    def generate_json(self, prompt: str, schema) -> LLMResult:
        return self._call([prompt], schema)
