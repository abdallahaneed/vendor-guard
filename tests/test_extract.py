from conftest import iban, pdf_doc

from vendor_guard import extract


def test_fields_from_letter():
    new = iban("DE", "370400440532013000")
    doc = pdf_doc([
        "Firma: Rheinauen Kaffeetechnik GmbH",
        "USt-IdNr.: DE 136 695 976",
        "Amtsgericht Bonn HRB 21877",
        f"IBAN: {new[:4]} {new[4:8]} {new[8:12]} {new[12:16]} {new[16:20]} {new[20:]}   BIC: COBADEFFXXX",
        "Kontoinhaber: Rheinauen Kaffeetechnik GmbH",
        "Telefon: +49 228 9137740",
    ])
    ex = extract.from_documents([doc])
    assert ex.fields["iban"] == new
    assert ex.confidence["iban"] == "high"
    assert ex.fields["vatId"] == "DE136695976"
    assert ex.fields["register"] == "HRB 21877"
    assert ex.fields["registerCourt"] == "Bonn"
    assert ex.fields["bic"] == "COBADEFFXXX"
    assert ex.fields["accountHolder"].startswith("Rheinauen")
    assert ex.pages == 1


def test_old_and_new_iban_takes_the_last_one():
    old, new = "DE89370400440532013000", iban("DE", "500105170648489890")
    ex = extract.fields_from_text(f"bisher {old}\nneu ab sofort: {new}")
    assert ex.fields["iban"] == new
    assert ex.confidence["iban"] == "medium"
    assert ex.fields["ibansFound"] == [old, new]


def test_broken_iban_is_low_confidence():
    ex = extract.fields_from_text("IBAN DE89370400440532013001")
    assert ex.confidence["iban"] == "low"


def test_pressure_words():
    ex = extract.fields_from_text("DRINGEND. Bitte umgehend zahlen und vertraulich behandeln.")
    assert {"dringend", "umgehend", "vertraulich"} <= set(ex.pressure_words)


def test_not_a_pdf_is_refused():
    import base64
    import pytest
    with pytest.raises(ValueError, match="not a PDF"):
        extract.from_documents([{"filename": "x.pdf", "contentBase64": base64.b64encode(b"hello").decode()}])
