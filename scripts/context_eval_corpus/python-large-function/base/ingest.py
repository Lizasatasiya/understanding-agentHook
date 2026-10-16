def validate_header(header):
    return header.get("magic") == "RPT1"


def ingest_report(payload):
    if not validate_header(payload.get("header", {})):
        raise ValueError("bad header")
    body = payload.get("body", "")
    checksum = sum(ord(c) for c in body)
    return {"checksum": checksum}
