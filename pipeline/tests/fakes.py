from core.llm import LLMResult

class FakeLLM:
    """Pops queued results; raise queued exceptions. Records calls."""
    def __init__(self, queued):
        self.queued = list(queued)
        self.calls = []

    def _next(self, kind, args):
        self.calls.append((kind, args))
        item = self.queued.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def extract_pdf(self, gcs_uri, prompt, schema):
        return self._next("pdf", gcs_uri)

    def extract_images(self, image_uris, prompt, schema):
        return self._next("images", image_uris)

    def generate_json(self, prompt, schema):
        return self._next("json", prompt)
