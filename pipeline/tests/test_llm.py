import json
from types import SimpleNamespace
from core.llm import LLM, LLMResult
from schemas.extraction import Extraction

class StubClient:  # mimics google-genai client surface
    def __init__(self, text, tin=100, tout=50):
        self.models = SimpleNamespace(generate_content=lambda **kw: SimpleNamespace(
            text=text, usage_metadata=SimpleNamespace(prompt_token_count=tin, candidates_token_count=tout)))

def test_parse_and_usage():
    payload = json.dumps({"eo_number": "D-1-1", "confidence": 0.8})
    llm = LLM(client=StubClient(payload))
    r = llm.extract_pdf("gs://b/x.pdf", "prompt", Extraction)
    assert r.data["eo_number"] == "D-1-1" and r.tok_in == 100 and r.tok_out == 50

class EnumFinishReasonClient:  # mimics google-genai response with enum finish_reason
    def __init__(self, text):
        self.models = SimpleNamespace(generate_content=lambda **kw: SimpleNamespace(
            text=text, usage_metadata=SimpleNamespace(prompt_token_count=100, candidates_token_count=50),
            candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name="MAX_TOKENS"))]))

def test_finish_reason_enum_derivation():
    payload = json.dumps({"eo_number": "D-2-2", "confidence": 0.9})
    llm = LLM(client=EnumFinishReasonClient(payload))
    r = llm.extract_pdf("gs://b/x.pdf", "prompt", Extraction)
    assert r.finish_reason == "MAX_TOKENS"
    # Also test that no-candidates stub yields "STOP"
    llm2 = LLM(client=StubClient(payload))
    r2 = llm2.extract_pdf("gs://b/x.pdf", "prompt", Extraction)
    assert r2.finish_reason == "STOP"
