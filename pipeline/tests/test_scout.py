import hashlib
from agents.scout import diff_listings, discover
from tests.fakes import FakeRepo

def test_diff_only_new():
    known = {"D-1-1"}
    listing = [{"eo_number": "D-1-1", "pdf_url": "u1"}, {"eo_number": "D-2-2", "pdf_url": "u2"}]
    assert [e["eo_number"] for e in diff_listings(known, listing)] == ["D-2-2"]

class FakeCarb:
    def list_all(self):
        return [{"eo_number": "D-9-9", "pdf_url": "http://x/d-9-9.pdf"}]
    def download_pdf(self, url):
        return b"%PDF-fake"

class FakeGCS:
    def upload_pdf(self, eo, data):
        return f"gs://b/pdfs/{eo.lower()}.pdf"

def test_discover_creates_registry_and_work():
    repo = FakeRepo()
    run = repo.create_run("test")
    n = discover(repo, FakeCarb(), FakeGCS(), run)
    assert n == 1
    eo = repo.get_eo("D-9-9")
    assert eo["state"] == "discovered"
    assert eo["sha256"] == hashlib.sha256(b"%PDF-fake").hexdigest()
    assert repo.claim_next("w", now=1.0)["eo_number"] == "D-9-9"
