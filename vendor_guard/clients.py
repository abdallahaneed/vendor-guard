"""Calls to other systems, each with retries and a hard timeout.

stammdaten-check  field checks, duplicates, IBAN owners, current vendor record
n8n               resume a waiting workflow after a decision
VIES              EU VAT check, optional
"""
import logging
import time

import httpx

log = logging.getLogger("vendor_guard")

RETRY_STATUS = {429, 500, 502, 503, 504}


def call(method, url, tries=3, backoff=1.0, timeout=10, **kw):
    """Retry on network errors and 5xx/429. 4xx answers are returned, they will not get better."""
    last = None
    for attempt in range(tries):
        try:
            r = httpx.request(method, url, timeout=timeout, **kw)
            if r.status_code not in RETRY_STATUS:
                return r
            last = f"HTTP {r.status_code}"
        except httpx.HTTPError as e:
            last = f"{type(e).__name__}: {e}"
        if attempt < tries - 1:
            time.sleep(backoff * 2 ** attempt)  # 1s, 2s
    log.warning("%s %s failed after %d tries: %s", method, url, tries, last)
    return None


class Stammdaten:
    def __init__(self, base_url, api_key="", tries=3, backoff=1.0):
        self.base = base_url.rstrip("/")
        self.headers = {"X-API-Key": api_key} if api_key else {}
        self.tries, self.backoff = tries, backoff

    def partner_check(self, vendor: dict, existing_id=None):
        body = {"object": "vendor", "existingId": existing_id, **{
            k: vendor.get(k, "") or "" for k in ("name", "name2", "street", "postalCode", "city", "country",
                                                 "vatId", "iban")}}
        r = call("POST", f"{self.base}/api/v1/partner-checks", json=body, headers=self.headers,
                 tries=self.tries, backoff=self.backoff)
        return r.json() if r is not None and r.status_code == 200 else None

    def vendor(self, lifnr):
        """Returns (record, reachable). record is None when the vendor does not exist."""
        r = call("GET", f"{self.base}/api/v1/vendors/{lifnr}", headers=self.headers,
                 tries=self.tries, backoff=self.backoff)
        if r is None:
            return None, False
        if r.status_code == 404:
            return None, True
        return (r.json(), True) if r.status_code == 200 else (None, False)


def resume_n8n(url, payload, tries=3, backoff=1.0):
    """n8n's Wait node continues when its resume URL is called."""
    if not url:
        return False
    r = call("POST", url, json=payload, tries=tries, backoff=backoff)
    return r is not None and r.status_code < 300


def vies_check(vat, tries=2):
    vat = vat.replace(" ", "").upper()
    if len(vat) < 4:
        return None
    cc = "EL" if vat[:2] == "GR" else vat[:2]
    r = call("GET", f"https://ec.europa.eu/taxation_customs/vies/rest-api/ms/{cc}/vat/{vat[2:]}",
             tries=tries, timeout=8)
    if r is None or r.status_code != 200:
        return "unavailable"
    data = r.json()
    if data.get("userError") in ("MS_UNAVAILABLE", "TIMEOUT", "MS_MAX_CONCURRENT_REQ", "SERVICE_UNAVAILABLE"):
        return "unavailable"
    return "valid" if data.get("isValid") else "invalid"
