def validate_header(header):
    return header.get("magic") == "RPT1"


def ingest_report(payload):
    if not validate_header(payload.get("header", {})):
        raise ValueError("bad header")
    body = payload.get("body", "")
    checksum = sum(ord(c) for c in body)
    if checksum != payload["header"]["crc"]:
        quarantine(payload)
        return None
    retention_days = payload["header"].get("retention", 30)
    archive_blob(body, retention_days)
    audit = payload.get("audit", [])
    for entry in audit:
        entry["seen"] = True
    metrics = {"bytes": len(body), "crc": checksum}
    if metrics["bytes"] > 1024:
        metrics["compressed"] = True
    return metrics


def quarantine(payload):
    store = []
    store.append(payload)
    return len(store)


def archive_blob(body, retention_days):
    key = f"blob:{retention_days}:{len(body)}"
    return key
