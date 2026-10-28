"""Pull the fields we care about out of supplier documents.

Supplier PDFs are letters, forms and bank confirmations with no fixed layout.
Rules work well here because the fields have strict formats: an IBAN has a
checksum, a German VAT ID has a check digit, an HRB number starts with HRB.
Every value found gets a confidence so a person knows what to double-check.
"""
import base64
import io
import re
from dataclasses import dataclass, field

import pdfplumber

IBAN_RE = re.compile(r"\b([A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,4})?)\b")
BIC_RE = re.compile(r"\b(?:BIC|SWIFT)[:\s]*([A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?)\b")
VAT_RE = re.compile(r"\b(DE\s?\d{3}\s?\d{3}\s?\d{3}|ATU\d{8}|NL\d{9}B\d{2}|BE[01]\d{9})\b")
HRB_RE = re.compile(r"\b(HR[AB])\s?(\d{2,6})(?:\s?([A-Z]))?\b")
COURT_RE = re.compile(r"Amtsgericht\s+([A-ZÄÖÜ][\wäöüß\-]+(?:\s(?!HR[AB]\b)[A-ZÄÖÜ][\wäöüß\-]+)?)")
HOLDER_RE = re.compile(r"(?:Kontoinhaber|Account holder|Inhaber)[:\s]+(.+)")
PHONE_RE = re.compile(r"(?:Tel\.?|Telefon|Phone)[:\s]+(\+?[\d ()/\-]{7,})")

# Words that show up in payment diversion letters far more than in real ones
PRESSURE = ["dringend", "sofort", "umgehend", "ab sofort", "vertraulich", "eilt", "urgent",
            "immediately", "confidential", "letzte mahnung", "keine rückfragen"]


@dataclass
class Extracted:
    text: str = ""
    fields: dict = field(default_factory=dict)       # name -> value
    confidence: dict = field(default_factory=dict)   # name -> high / medium / low
    pressure_words: list = field(default_factory=list)
    pages: int = 0

    def as_dict(self):
        return {"fields": self.fields, "confidence": self.confidence,
                "pressureWords": self.pressure_words, "pages": self.pages}


def pdf_text(data: bytes) -> tuple[str, int]:
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        pages = [p.extract_text() or "" for p in pdf.pages]
    return "\n".join(pages), len(pages)


def from_base64(content: str) -> bytes:
    return base64.b64decode(content, validate=True)


def fields_from_text(text: str) -> Extracted:
    from .checks import iban_ok  # local import, checks imports this module too

    out = Extracted(text=text)
    ibans = [m.replace(" ", "") for m in IBAN_RE.findall(text)]
    valid = [i for i in ibans if iban_ok(i)]
    if valid:
        # A letter may show the old and the new IBAN. The last valid one is
        # usually the new one ("bitte ab sofort auf folgendes Konto").
        out.fields["iban"] = valid[-1]
        out.confidence["iban"] = "high" if len(set(valid)) == 1 else "medium"
        if len(set(valid)) > 1:
            out.fields["ibansFound"] = list(dict.fromkeys(valid))
    elif ibans:
        out.fields["iban"] = ibans[-1]
        out.confidence["iban"] = "low"  # looks like an IBAN, checksum fails

    if m := BIC_RE.search(text):
        out.fields["bic"], out.confidence["bic"] = m.group(1), "high"
    if m := VAT_RE.search(text):
        out.fields["vatId"], out.confidence["vatId"] = m.group(1).replace(" ", ""), "high"
    if m := HRB_RE.search(text):
        out.fields["register"] = f"{m.group(1)} {m.group(2)}" + (f" {m.group(3)}" if m.group(3) else "")
        out.confidence["register"] = "high"
        if c := COURT_RE.search(text):
            out.fields["registerCourt"] = c.group(1).strip()
    if m := HOLDER_RE.search(text):
        out.fields["accountHolder"] = m.group(1).strip().split("  ")[0]
        out.confidence["accountHolder"] = "medium"
    if m := PHONE_RE.search(text):
        out.fields["phoneInDocument"] = " ".join(m.group(1).split())
        out.confidence["phoneInDocument"] = "medium"

    lowered = text.lower()
    out.pressure_words = [w for w in PRESSURE if w in lowered]
    return out


def from_documents(documents: list[dict]) -> Extracted:
    """documents: [{"filename": ..., "contentBase64": ...}]. Text of all PDFs is combined."""
    texts, pages = [], 0
    for d in documents:
        data = from_base64(d["contentBase64"])
        if not data.startswith(b"%PDF"):
            raise ValueError(f"{d.get('filename', 'document')} is not a PDF")
        t, n = pdf_text(data)
        texts.append(t)
        pages += n
    ex = fields_from_text("\n".join(texts))
    ex.pages = pages
    return ex
