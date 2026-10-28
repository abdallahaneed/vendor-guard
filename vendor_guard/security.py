"""Signed approval links and the signed audit log.

Approval link: one per approver and case, signed with LINK_SECRET, valid for a
limited time, usable once. The approver's identity comes from the fact that
the link was sent only to their mailbox.

Audit log: every entry stores the hash of the one before, signed with
AUDIT_KEY (HMAC). Changing or deleting an old entry breaks the chain from
that point on. Limit: someone with the database AND the key can rebuild the
whole chain. That is why the key does not live in the database, and why the
latest hash is sent out once a day (see the n8n workflow audit-anchor).
"""
import base64
import hashlib
import hmac
import json
import secrets
import time


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make_token(secret: str, case_id: int, approver: str, hours: int) -> tuple[str, str, int]:
    nonce = secrets.token_urlsafe(12)
    exp = int(time.time()) + hours * 3600
    body = _b64(json.dumps({"c": case_id, "a": approver, "n": nonce, "e": exp}, separators=(",", ":")).encode())
    sig = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}", nonce, exp


class TokenError(Exception):
    pass


def read_token(secret: str, token: str) -> dict:
    try:
        body, sig = token.split(".", 1)
    except ValueError:
        raise TokenError("link is damaged") from None
    good = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, good):
        raise TokenError("link is not valid")
    data = json.loads(_unb64(body))
    if data["e"] < time.time():
        raise TokenError("link has expired, ask for a new one")
    return data


def chain_hash(key: str, prev: str, entry: dict) -> str:
    canonical = json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hmac.new(key.encode(), (prev + canonical).encode(), hashlib.sha256).hexdigest()
