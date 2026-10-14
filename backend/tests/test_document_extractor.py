"""Unit tests for Automated PDF & Document Extraction Engine."""

import csv
import io
from pathlib import Path
import pytest

from backend.app.document_extractor import DocumentExtractor
from backend.app.schemas import DocumentExtractionResult


def test_document_extractor_csv(tmp_path: Path):
    """Verify parsing CSV tables and extracting entities."""
    extractor = DocumentExtractor(downloads_dir=tmp_path / "downloads", extracted_dir=tmp_path / "extracted")

    csv_content = """Invoice,Customer,Amount,Date,Email
INV-1001,Apex Systems,$1500.00,2026-10-14,billing@apex.com
INV-1002,Wayne Enterprises,$3200.50,2026-10-15,bruce@wayne.com
"""
    csv_file = tmp_path / "test_invoice.csv"
    csv_file.write_text(csv_content, encoding="utf-8")

    res = extractor.extract_document(csv_file)
    assert isinstance(res, DocumentExtractionResult)
    assert res.file_type == "csv"
    assert len(res.tables) == 1
    table = res.tables[0]
    assert table.row_count == 2
    assert table.headers == ["Invoice", "Customer", "Amount", "Date", "Email"]
    assert table.rows[0][0] == "INV-1001"

    # Entities check
    entities = res.entities
    assert any("1500" in a for a in entities.get("currency_amounts", []))
    assert "INV-1001" in entities.get("invoice_numbers", [])
    assert any("apex.com" in e for e in entities.get("emails", []))


def test_document_extractor_pdf(tmp_path: Path):
    """Verify parsing PDF document and metadata using pypdf."""
    from pypdf import PdfWriter

    extractor = DocumentExtractor(downloads_dir=tmp_path / "downloads", extracted_dir=tmp_path / "extracted")

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    pdf_file = tmp_path / "test_doc.pdf"
    with open(pdf_file, "wb") as f:
        writer.write(f)

    res = extractor.extract_document(pdf_file)
    assert isinstance(res, DocumentExtractionResult)
    assert res.file_type == "pdf"
    assert res.page_count == 1
    assert res.filename == "test_doc.pdf"


def test_document_extractor_text_entity_heuristics(tmp_path: Path):
    """Verify regex entity extraction for invoice numbers, totals, and dates."""
    extractor = DocumentExtractor(downloads_dir=tmp_path / "downloads", extracted_dir=tmp_path / "extracted")

    text_sample = """
    OFFICIAL RECEIPT
    Invoice #: INV-99882
    Date: 2026-10-14
    Grand Total: $4,999.00
    Contact: support@klick.ai | (555) 123-4567
    """
    res = extractor.extract_document(text_sample, filename="receipt.txt")
    entities = res.entities

    assert "INV-99882" in entities.get("invoice_numbers", [])
    assert any("4,999" in a for a in entities.get("currency_amounts", []))
    assert any("2026-10-14" in d for d in entities.get("dates", []))
    assert "support@klick.ai" in entities.get("emails", [])
