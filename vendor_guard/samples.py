"""Demo requests: PDFs plus the JSON n8n receives.

Four cases, built around vendors that exist in the stammdaten-check sample run:
  1 new vendor, clean
  2 new vendor that already exists under another spelling
  3 bank change the vendor really asked for
  4 bank change letter of the kind used in payment diversion fraud
All companies and people are invented.
"""
import base64
import io
import json
import random
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

import httpx


def _iban(country, bban):
    tmp = bban + country + "00"
    check = 98 - int("".join(str(int(c, 36)) for c in tmp)) % 97
    return f"{country}{check:02d}{bban}"


def _pdf(lines, title):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle(title)
    y = 270 * mm
    for line in lines:
        if line.startswith("# "):
            c.setFont("Helvetica-Bold", 14)
            line = line[2:]
        else:
            c.setFont("Helvetica", 10.5)
        c.drawString(22 * mm, y, line)
        y -= 7 * mm
    c.showPage()
    c.save()
    return buf.getvalue()


def _doc(name, pdf):
    return {"filename": name, "contentBase64": base64.b64encode(pdf).decode()}


def build(out, stammdaten_url="http://localhost:8000", vendor_ids=("300001", "300002"), requester=None):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    r = random.Random(7)
    requester = requester or {"name": "Lena Brandt", "email": "lena.brandt@example.com"}

    def vendor(lifnr):
        resp = httpx.get(f"{stammdaten_url.rstrip('/')}/api/v1/vendors/{lifnr}", timeout=10)
        resp.raise_for_status()
        return resp.json()

    existing = [vendor(v) for v in vendor_ids]
    files = {}

    # 1 clean new vendor
    new_iban = _iban("DE", "37040044" + str(r.randint(10**9, 10**10 - 1)))
    form = _pdf([
        "# Lieferantenstammblatt",
        "Firma: Rheinauen Kaffeetechnik GmbH",
        "Anschrift: Friedrich-Ebert-Allee 71, 53113 Bonn",
        "USt-IdNr.: DE136695976",
        "Handelsregister: Amtsgericht Bonn HRB 21877",
        f"Bankverbindung IBAN: {new_iban}   BIC: COBADEFFXXX",
        "Kontoinhaber: Rheinauen Kaffeetechnik GmbH",
        "Telefon: +49 228 9137740",
    ], "Lieferantenstammblatt")
    files["1-new-vendor-clean.json"] = {
        "type": "onboarding", "requester": requester, "sourceEmail": "info@rheinauen-kaffee.example.de",
        "vendor": {"name": "Rheinauen Kaffeetechnik GmbH", "street": "Friedrich-Ebert-Allee 71",
                   "postalCode": "53113", "city": "Bonn", "country": "DE", "email": "info@rheinauen-kaffee.example.de"},
        "documents": [_doc("lieferantenstammblatt.pdf", form)],
    }

    # 2 new vendor that is already in the master, written differently
    v = existing[0]
    form2 = _pdf([
        "# Lieferantenstammblatt",
        f"Firma: {v['name'].upper()}",
        f"Anschrift: {v['street']}, {v['postalCode']} {v['city']}",
        f"Bankverbindung IBAN: {_iban('DE', '37040044' + str(r.randint(10**9, 10**10 - 1)))}",
    ], "Lieferantenstammblatt")
    files["2-new-vendor-duplicate.json"] = {
        "type": "onboarding", "requester": requester, "sourceEmail": v["email"],
        "vendor": {"name": v["name"].upper(), "street": v["street"], "postalCode": v["postalCode"],
                   "city": v["city"], "country": v["country"]},
        "documents": [_doc("stammblatt.pdf", form2)],
    }

    # 3 genuine bank change, letter from the vendor's own domain, same phone
    v = existing[1]
    genuine_iban = _iban("DE", "50010517" + str(r.randint(10**9, 10**10 - 1)))
    letter = _pdf([
        f"# {v['name']}",
        f"{v['street']}, {v['postalCode']} {v['city']}   Telefon: {v['phone']}",
        "",
        "Änderung unserer Bankverbindung",
        "",
        "Sehr geehrte Damen und Herren,",
        "wir haben unsere Hausbank gewechselt. Bitte überweisen Sie Rechnungen ab dem 1. des",
        "kommenden Monats auf unser neues Konto:",
        f"IBAN: {genuine_iban}   BIC: INGDDEFFXXX",
        f"Kontoinhaber: {v['name']}",
        "Die Bestätigung der Bank liegt bei. Für Rückfragen erreichen Sie uns unter der bekannten Nummer.",
        "Mit freundlichen Grüßen, Buchhaltung",
    ], "Bankverbindung")
    files["3-bank-change-genuine.json"] = {
        "type": "bank_change", "requester": requester, "existingId": v["id"], "sourceEmail": v["email"],
        "vendor": {"iban": genuine_iban, "accountHolder": v["name"]},
        "documents": [_doc("bankwechsel.pdf", letter)],
    }

    # 4 the fraud pattern: other domain, foreign account, other holder, pressure, other phone
    v = existing[0]
    fraud_iban = _iban("NL", "INGB" + str(r.randint(10**8, 10**10 - 1)).zfill(10))
    sender = "buchhaltung@" + v["email"].split("@")[1].replace(".example.", "-finance.example.")
    letter4 = _pdf([
        f"# {v['name']}",
        "Telefon: +49 1577 3920114",
        "",
        "DRINGEND: Neue Bankverbindung ab sofort",
        "",
        "Sehr geehrte Damen und Herren,",
        "aufgrund einer Betriebsprüfung ist unser bisheriges Konto vorübergehend gesperrt.",
        "Bitte veranlassen Sie die offenen Zahlungen umgehend auf das folgende Konto:",
        f"IBAN: {fraud_iban}",
        "Kontoinhaber: M. de Vries Trading",
        "Bitte behandeln Sie diese Mitteilung vertraulich. Rückfragen bitte nur per E-Mail.",
    ], "Bankverbindung")
    files["4-bank-change-fraud.json"] = {
        "type": "bank_change", "requester": requester, "existingId": v["id"], "sourceEmail": sender,
        "vendor": {"iban": fraud_iban},
        "documents": [_doc("neue-bankverbindung.pdf", letter4)],
    }

    for name, body in files.items():
        (out / name).write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    return sorted(files)
