import hashlib, time

def diff_listings(known: set[str], listing: list[dict]) -> list[dict]:
    return [e for e in listing if e["eo_number"] not in known]

def discover(repo, carb, gcs, run_id) -> int:
    known = repo.known_eo_numbers()
    new = diff_listings(known, carb.list_all())
    success_count = 0
    for e in new:
        eo = e["eo_number"]
        try:
            # Bucket-first: CARB's CDN intermittently serves empty 200 bodies
            # to cloud egress IPs, so vetted bytes already in the bucket beat
            # a re-download of the same document.
            pdf = gcs.cached_pdf(eo)
            if pdf is None:
                pdf = carb.download_pdf(e["pdf_url"])
                uri = gcs.upload_pdf(eo, pdf)
            else:
                uri = gcs.pdf_uri(eo)
            repo.upsert_eo(eo, {"state": "discovered", "pdf_url": e["pdf_url"], "gcs_uri": uri,
                                "sha256": hashlib.sha256(pdf).hexdigest(), "first_seen": time.time()})
            repo.create_work_item(eo, run_id)
            repo.add_event(run_id, {"agent": "scout", "eo": eo, "action": "discovered"})
            success_count += 1
        except Exception as exc:
            repo.add_event(run_id, {"agent": "scout", "eo": eo, "action": "discover_failed",
                                    "error": str(exc)[:300]})
            continue
    return success_count
