"""Screening against the EU consolidated financial sanctions list.

Reads the official XML export (download with `vendor-guard sanctions-update`)
or a CSV with a name column. Matching is fuzzy on purpose: listed companies
appear with spelling variants. That also means false hits, so a hit only
sends the case to a person, it never blocks on its own.
"""
import csv
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from rapidfuzz import fuzz, process

OFFICIAL_URL = ("https://webgate.ec.europa.eu/fsd/fsf/public/files/"
                "xmlFullSanctionsList_1_1/content?token=dG9rZW4tMjAxNw")

LEGAL = r"\b(gmbh|ag|kg|ug|ohg|llc|ltd|limited|inc|corp|co|jsc|ojsc|pjsc|ooo|sa|bv|nv|plc|holding|group)\b"


def norm(name):
    n = str(name or "").lower()
    for k, v in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        n = n.replace(k, v)
    n = re.sub(LEGAL, " ", n)
    return re.sub(r"[^a-z0-9]+", " ", n).strip()


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def load_xml(path):
    """Names of listed enterprises from the EU FSF XML. Persons are skipped,
    vendors are companies. Each alias becomes its own entry."""
    names = []
    for _, el in ET.iterparse(path, events=("end",)):
        if _local(el.tag) != "sanctionEntity":
            continue
        kind = ""
        aliases = []
        for child in el:
            t = _local(child.tag)
            if t == "subjectType":
                kind = (child.get("code") or child.get("classificationCode") or "").lower()
            elif t == "nameAlias" and child.get("wholeName"):
                aliases.append(child.get("wholeName"))
        if kind in ("enterprise", "e"):
            ref = el.get("euReferenceNumber") or el.get("logicalId") or ""
            names += [(a, ref) for a in aliases]
        el.clear()
    return names


def load_csv(path):
    with open(path, encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    out = []
    for r in rows:
        kind = (r.get("schema") or r.get("type") or "").lower()
        if kind in ("person",):
            continue
        out.append((r["name"], r.get("id", "")))
        for alias in (r.get("aliases") or "").split(";"):
            if alias.strip():
                out.append((alias.strip(), r.get("id", "")))
    return out


class Screening:
    def __init__(self, path=None, threshold=90):
        self.threshold = threshold
        self.source = "none"
        self.entries = []
        if path and Path(path).exists():
            p = Path(path)
            self.entries = load_xml(p) if p.suffix.lower() == ".xml" else load_csv(p)
            self.source = p.name
        self._keys = [norm(n) for n, _ in self.entries]

    @property
    def loaded(self):
        return bool(self.entries)

    def check(self, name, limit=3):
        key = norm(name)
        if not key or not self._keys:
            return []
        hits = process.extract(key, self._keys, scorer=fuzz.token_sort_ratio, limit=limit,
                               score_cutoff=self.threshold)
        return [{"name": self.entries[i][0], "ref": self.entries[i][1], "score": s, "source": self.source}
                for _, s, i in hits]
