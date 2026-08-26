"""
CARB Power BI listing client.

Ported from the legacy pipeline (C:\\Users\\lee\\OneDrive\\Documents\\CARBSearch\\pipeline\\download_eos.py),
which uses this Power BI public API to resolve ~6,100 EO -> PDF URL mappings in production.
The request construction (`_build_query`) and response decoding (`_decode_rows`) are ported
as-is; the legacy URL-pattern fallback (`eo_to_pattern_url`) is preserved for callers that need
to guess a PDF URL for an EO not present in the Power BI listing.
"""
import time

import requests

RESOURCE_KEY = "2db31717-7ffa-4049-b5fa-3807d452c093"
MODEL_ID     = 901706
API_BASE     = "https://wabi-us-gov-virginia-api.analysis.usgovcloudapi.net"
PDF_BASE     = "https://ww2.arb.ca.gov/sites/default/files/aftermarket/devices/eo"

_PBI_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "X-PowerBI-ResourceKey": RESOURCE_KEY,
}


def eo_to_pattern_url(eo_number: str) -> str:
    """Derive the expected PDF URL from an EO number using the known pattern (legacy fallback)."""
    return f"{PDF_BASE}/{eo_number.lower()}.pdf"


def _build_query(restart_tokens=None) -> dict:
    window = {"Count": 500}
    if restart_tokens:
        window["RestartTokens"] = restart_tokens
    return {
        "version": "1.0.0",
        "queries": [{
            "Query": {
                "Commands": [{
                    "SemanticQueryDataShapeCommand": {
                        "Query": {
                            "Version": 2,
                            "From": [{"Name": "e", "Entity": "EO", "Type": 0}],
                            "Select": [
                                {
                                    "Column": {
                                        "Expression": {"SourceRef": {"Source": "e"}},
                                        "Property": "EO",
                                    },
                                    "Name": "EO.EO",
                                },
                                {
                                    "Column": {
                                        "Expression": {"SourceRef": {"Source": "e"}},
                                        "Property": "WebLink",
                                    },
                                    "Name": "EO.WebLink",
                                },
                            ],
                        },
                        "Binding": {
                            "Primary": {"Groupings": [{"Projections": [0, 1]}]},
                            "DataReduction": {
                                "DataVolume": 4,
                                "Primary": {"Window": window},
                            },
                            "Version": 1,
                        },
                    }
                }]
            },
            "QueryId": "",
            "ApplicationContext": {
                "DatasetId": RESOURCE_KEY,
                "Sources": [{"ReportId": RESOURCE_KEY}],
            },
        }],
        "cancelQueries": [],
        "modelId": MODEL_ID,
    }


def _extract_ds(payload: dict) -> dict:
    """Pull the single DataShape result (`dsr.DS[0]`) out of a Power BI querydata response."""
    return (
        payload.get("results", [{}])[0]
            .get("result", {})
            .get("data", {})
            .get("dsr", {})
            .get("DS", [{}])[0]
    )


def _decode_rows(ds: dict) -> dict:
    """Decode the Power BI DSR compressed row format into {eo_number: url}."""
    dicts = ds.get("ValueDicts", {})
    d0 = dicts.get("D0", [])  # EO number strings
    d1 = dicts.get("D1", [])  # WebLink URL strings
    rows = ds.get("PH", [{}])[0].get("DM0", [])

    result = {}
    prev_c = []

    for row in rows:
        if not isinstance(row, dict):
            # A malformed row (not a dict) must not crash the parse and must not
            # touch prev_c — the next good row still carries over from the last
            # row that decoded successfully.
            continue

        try:
            c = list(row.get("C") or [])
            r = row.get("R", 0)

            if r and prev_c:
                # R is a bitmask: bit N set means column N carries over from previous row
                merged = list(prev_c)
                new_idx = 0
                for col in range(len(merged)):
                    if not (r >> col & 1):
                        if new_idx < len(c):
                            merged[col] = c[new_idx]
                            new_idx += 1
                c = merged

            if len(c) >= 2:
                # c[i] is either an integer dict index or a raw string value
                eo = d0[c[0]] if isinstance(c[0], int) and c[0] < len(d0) else (c[0] if isinstance(c[0], str) else None)
                url = d1[c[1]] if isinstance(c[1], int) and c[1] < len(d1) else (c[1] if isinstance(c[1], str) else None)
                if eo and url:
                    result[eo] = url

            prev_c = list(c)
        except (TypeError, AttributeError, IndexError, KeyError):
            # Same rule as above: a row that blows up mid-decode must not update
            # prev_c — leave the last successfully-decoded row as the carry-over source.
            continue

    return result


def parse_listing(payload: dict) -> list[dict]:
    """Power BI querydata response JSON -> deduped [{"eo_number": ..., "pdf_url": ...}, ...].

    Rows without a resolvable eo_number + pdf_url pair are skipped.
    """
    decoded = _decode_rows(_extract_ds(payload))
    return [{"eo_number": eo, "pdf_url": url} for eo, url in decoded.items()]


class CarbClient:
    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()
        self.session.headers.update(_PBI_HEADERS)

    def _raw_listing(self, restart_tokens=None) -> dict:
        """One page of the raw Power BI querydata response (up to 500 rows)."""
        body = _build_query(restart_tokens)
        resp = self.session.post(
            f"{API_BASE}/public/reports/querydata?synchronous=true",
            json=body,
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()

    def list_all(self) -> list[dict]:
        """Page through the Power BI listing and return every EO -> PDF URL mapping."""
        all_rows = {}
        restart_tokens = None

        while True:
            payload = self._raw_listing(restart_tokens)
            for row in parse_listing(payload):
                all_rows[row["eo_number"]] = row["pdf_url"]

            ds = _extract_ds(payload)
            rows = ds.get("PH", [{}])[0].get("DM0", [])
            rt = ds.get("RT")

            if len(rows) < 500 or not rt:
                break
            restart_tokens = rt

        return [{"eo_number": eo, "pdf_url": url} for eo, url in all_rows.items()]

    def download_pdf(self, url: str) -> bytes:
        """Fetch a PDF's bytes. Sleeps >=1s before each request (politeness constraint)."""
        time.sleep(1)
        resp = self.session.get(url, timeout=60)
        resp.raise_for_status()
        return resp.content
