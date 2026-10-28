import argparse
import json
import sys
import uuid
from pathlib import Path

import httpx

from .config import Settings


def cmd_serve(a):
    import uvicorn
    from .app import create_app
    settings = Settings()
    settings.check_secrets()
    print(f"vendor-guard on http://{a.host}:{a.port}   API docs: /docs")
    uvicorn.run(create_app(settings), host=a.host, port=a.port, log_level="warning")


def cmd_verify(a):
    from .store import Store
    s = Settings()
    s.check_secrets()
    ok, n, broken = Store(s.home, s.audit_key).verify_audit()
    print(f"{n} entries, chain {'intact' if ok else f'BROKEN at entry {broken}'}")
    sys.exit(0 if ok else 1)


def cmd_head(a):
    from .store import Store
    s = Settings()
    s.check_secrets()
    print(json.dumps(Store(s.home, s.audit_key).audit_head()))


def cmd_sanctions(a):
    from .sanctions import OFFICIAL_URL, Screening
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    print("Downloading the EU consolidated sanctions list ...")
    with httpx.stream("GET", OFFICIAL_URL, timeout=120, follow_redirects=True) as r:
        r.raise_for_status()
        with open(out, "wb") as fh:
            for chunk in r.iter_bytes():
                fh.write(chunk)
    print(f"Saved to {out}, {len(Screening(out).entries)} company names. Set SANCTIONS_FILE={out}")


def cmd_samples(a):
    from .samples import build
    names = build(a.out, a.stammdaten)
    print("Written:", *names, sep="\n  ")


def cmd_send(a):
    body = json.loads(Path(a.file).read_text(encoding="utf-8"))
    key = a.key or str(uuid.uuid4())
    r = httpx.post(a.webhook, json=body, headers={"Idempotency-Key": key}, timeout=60)
    print(r.status_code, r.text[:2000])


def main(argv=None):
    p = argparse.ArgumentParser(prog="vendor-guard")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="start API and approval pages")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8100)
    s.set_defaults(func=cmd_serve)

    sub.add_parser("verify-audit", help="recompute the audit chain").set_defaults(func=cmd_verify)
    sub.add_parser("audit-head", help="print the latest audit hash").set_defaults(func=cmd_head)

    d = sub.add_parser("sanctions-update", help="download the EU sanctions list")
    d.add_argument("--out", default="var/eu-sanctions.xml")
    d.set_defaults(func=cmd_sanctions)

    m = sub.add_parser("samples", help="build demo requests from the stammdaten-check sample run")
    m.add_argument("--out", default="samples")
    m.add_argument("--stammdaten", default="http://localhost:8000")
    m.set_defaults(func=cmd_samples)

    w = sub.add_parser("send", help="send one sample request to the n8n webhook")
    w.add_argument("file")
    w.add_argument("--webhook", default="http://localhost:5678/webhook/vendor-request")
    w.add_argument("--key", help="Idempotency-Key, random if not given")
    w.set_defaults(func=cmd_send)

    a = p.parse_args(argv)
    a.func(a)
