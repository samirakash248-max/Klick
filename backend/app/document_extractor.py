"""Automated PDF & Document Extraction Engine for Klick Browser Agent.

Handles downloaded invoices, statements, CSVs, and receipts from browser sessions,
extracting tabular data, plain text, document metadata, and structured entities
(totals, currency, invoice numbers, dates, contacts) directly into structured JSON/CSV.
"""

from __future__ import annotations

import base64
import csv
from datetime import datetime, timezone
import io
import json
import logging
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple, Union

from .schemas import DocumentExtractionResult, DocumentParseRequest, ExtractedTable

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DOWNLOADS_DIR = DATA_DIR / "downloads"
EXTRACTED_DIR = DATA_DIR / "extracted_documents"


class DocumentExtractor:
    """Parser and entity extraction engine for documents and downloads."""

    def __init__(self, downloads_dir: Path = DOWNLOADS_DIR, extracted_dir: Path = EXTRACTED_DIR):
        self.downloads_dir = downloads_dir
        self.extracted_dir = extracted_dir
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        self.extracted_dir.mkdir(parents=True, exist_ok=True)

    def extract_document(
        self,
        file_content: Union[bytes, Path, str],
        filename: str = "document.pdf",
        extract_tables: bool = True,
    ) -> DocumentExtractionResult:
        """Parse document content into structured extraction result."""
        # Normalize content to bytes and filename
        if isinstance(file_content, Path):
            filename = file_content.name
            raw_bytes = file_content.read_bytes()
        elif isinstance(file_content, str):
            p = Path(file_content)
            try:
                if p.exists() and p.is_file():
                    filename = p.name
                    raw_bytes = p.read_bytes()
                else:
                    raise ValueError("Not a file path")
            except Exception:
                cleaned_str = file_content.strip()
                if cleaned_str.startswith("data:") and "," in cleaned_str:
                    cleaned_str = cleaned_str.split(",", 1)[1]
                    try:
                        raw_bytes = base64.b64decode(cleaned_str)
                    except Exception:
                        raw_bytes = file_content.encode("utf-8")
                elif "\n" in cleaned_str or " " in cleaned_str or len(cleaned_str) < 100:
                    raw_bytes = file_content.encode("utf-8")
                else:
                    try:
                        raw_bytes = base64.b64decode(cleaned_str)
                    except Exception:
                        raw_bytes = file_content.encode("utf-8")
        else:
            raw_bytes = file_content

        ext = Path(filename).suffix.lower()

        if ext == ".pdf":
            result = self._parse_pdf(raw_bytes, filename, extract_tables=extract_tables)
        elif ext in (".csv", ".tsv"):
            result = self._parse_csv(raw_bytes, filename)
        elif ext in (".json",):
            result = self._parse_json(raw_bytes, filename)
        else:
            result = self._parse_text(raw_bytes, filename)

        # Save parsed result to extracted cache
        self._save_extraction(result)
        return result

    def _parse_pdf(self, raw_bytes: bytes, filename: str, extract_tables: bool = True) -> DocumentExtractionResult:
        """Extract text, tables, and metadata from PDF bytes."""
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(raw_bytes))
            page_count = len(reader.pages)
            full_text_parts = []
            tables: List[ExtractedTable] = []

            for i, page in enumerate(reader.pages):
                txt = page.extract_text() or ""
                full_text_parts.append(txt)

                if extract_tables:
                    page_tables = self._extract_tables_from_text(txt)
                    tables.extend(page_tables)

            full_text = "\n\n".join(full_text_parts)
            meta = {}
            if reader.metadata:
                for k, v in reader.metadata.items():
                    clean_k = str(k).lstrip("/").lower()
                    meta[clean_k] = str(v)

            entities = self._extract_entities(full_text)

            return DocumentExtractionResult(
                filename=filename,
                file_type="pdf",
                page_count=page_count,
                total_characters=len(full_text),
                text_content=full_text,
                tables=tables,
                entities=entities,
                metadata=meta,
            )
        except Exception as e:
            logger.error(f"Failed parsing PDF {filename}: {e}")
            # Fallback to plain text search on raw bytes
            decoded = raw_bytes.decode("latin1", errors="ignore")
            entities = self._extract_entities(decoded)
            return DocumentExtractionResult(
                filename=filename,
                file_type="pdf",
                page_count=1,
                total_characters=len(decoded),
                text_content=decoded[:2000],
                tables=[],
                entities=entities,
                metadata={"error": str(e)},
            )

    def _parse_csv(self, raw_bytes: bytes, filename: str) -> DocumentExtractionResult:
        """Parse CSV or TSV file into structured tables and text."""
        decoded = raw_bytes.decode("utf-8", errors="replace")
        delimiter = "\t" if filename.lower().endswith(".tsv") else ","

        reader = list(csv.reader(io.StringIO(decoded), delimiter=delimiter))
        tables = []
        if reader:
            headers = [str(c).strip() for c in reader[0]]
            rows = [[str(c).strip() for c in row] for row in reader[1:]]
            table = ExtractedTable(
                headers=headers,
                rows=rows,
                row_count=len(rows),
                column_count=len(headers),
            )
            tables.append(table)

        entities = self._extract_entities(decoded)
        return DocumentExtractionResult(
            filename=filename,
            file_type="csv",
            page_count=1,
            total_characters=len(decoded),
            text_content=decoded,
            tables=tables,
            entities=entities,
            metadata={"row_count": len(reader)},
        )

    def _parse_json(self, raw_bytes: bytes, filename: str) -> DocumentExtractionResult:
        """Parse JSON document into structured entities."""
        decoded = raw_bytes.decode("utf-8", errors="replace")
        try:
            parsed = json.loads(decoded)
            entities = self._extract_entities(decoded)
            entities["json_keys"] = list(parsed.keys()) if isinstance(parsed, dict) else len(parsed)
            return DocumentExtractionResult(
                filename=filename,
                file_type="json",
                page_count=1,
                total_characters=len(decoded),
                text_content=decoded,
                tables=[],
                entities=entities,
                metadata={"type": type(parsed).__name__},
            )
        except Exception as e:
            return self._parse_text(raw_bytes, filename)

    def _parse_text(self, raw_bytes: bytes, filename: str) -> DocumentExtractionResult:
        """Parse general plain text document."""
        decoded = raw_bytes.decode("utf-8", errors="replace")
        entities = self._extract_entities(decoded)
        tables = self._extract_tables_from_text(decoded)
        return DocumentExtractionResult(
            filename=filename,
            file_type="text",
            page_count=1,
            total_characters=len(decoded),
            text_content=decoded,
            tables=tables,
            entities=entities,
            metadata={},
        )

    def _extract_tables_from_text(self, text: str) -> List[ExtractedTable]:
        """Heuristic table extraction from column-aligned text."""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        candidate_rows = []

        for line in lines:
            # If line has multiple column dividers like tabs, pipes, or 2+ spaces
            if "|" in line:
                parts = [p.strip() for p in line.split("|") if p.strip()]
                if len(parts) >= 2:
                    candidate_rows.append(parts)
            elif "\t" in line:
                parts = [p.strip() for p in line.split("\t") if p.strip()]
                if len(parts) >= 2:
                    candidate_rows.append(parts)
            else:
                parts = re.split(r"\s{2,}", line)
                if len(parts) >= 2:
                    candidate_rows.append(parts)

        if len(candidate_rows) >= 2:
            headers = candidate_rows[0]
            rows = candidate_rows[1:]
            return [
                ExtractedTable(
                    headers=headers,
                    rows=rows,
                    row_count=len(rows),
                    column_count=len(headers),
                )
            ]
        return []

    def _extract_entities(self, text: str) -> Dict[str, Any]:
        """Extract structured business entities using regex heuristics."""
        entities: Dict[str, Any] = {}

        # 1. Total amounts & currency values
        total_patterns = [
            r"(?:total|amount due|balance due|grand total|subtotal)\s*[:\s]*([\$€£₹]?\s*[0-9,]+(?:\.[0-9]{2})?)",
            r"([\$€£₹]\s*[0-9,]+(?:\.[0-9]{2})?)",
        ]
        amounts = []
        for pat in total_patterns:
            matches = re.findall(pat, text, flags=re.IGNORECASE)
            for m in matches:
                clean_m = m.strip()
                if clean_m and clean_m not in amounts:
                    amounts.append(clean_m)
        if amounts:
            entities["currency_amounts"] = amounts[:10]
            entities["probable_total"] = amounts[0]

        # 2. Invoice / Order numbers
        inv_patterns = [
            r"\b(?:invoice|inv|order|receipt|bill)\s*(?:#|no\.?|id|num)?\s*[:\s]*([A-Za-z0-9\-_]{3,20})\b",
            r"\b(INV-[A-Za-z0-9]+)\b",
            r"\b(ORD-[A-Za-z0-9]+)\b",
        ]
        invoices = []
        stop_words = {"date", "number", "due", "oice", "customer", "amount", "email", "total", "subtotal", "table"}
        for pat in inv_patterns:
            matches = re.findall(pat, text, flags=re.IGNORECASE)
            for m in matches:
                clean_m = m.strip()
                if clean_m and clean_m.lower() not in stop_words and clean_m not in invoices:
                    invoices.append(clean_m)
        if invoices:
            entities["invoice_numbers"] = invoices[:10]

        # 3. Dates
        date_patterns = [
            r"\b(?:\d{4}[-/.]\d{2}[-/.]\d{2}|\d{2}[-/.]\d{2}[-/.]\d{4})\b",
            r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2},? \d{4}\b",
        ]
        dates = []
        for pat in date_patterns:
            matches = re.findall(pat, text, flags=re.IGNORECASE)
            for m in matches:
                if m not in dates:
                    dates.append(m)
        if dates:
            entities["dates"] = dates[:5]

        # 4. Email addresses
        emails = re.findall(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", text)
        if emails:
            entities["emails"] = list(set(emails))[:5]

        # 5. Phone numbers
        phones = re.findall(r"\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b", text)
        if phones:
            entities["phone_numbers"] = list(set(phones))[:5]

        return entities

    def _save_extraction(self, result: DocumentExtractionResult):
        """Save structured JSON extraction to disk."""
        try:
            safe_name = re.sub(r"[^\w\-.]", "_", result.filename)
            out_file = self.extracted_dir / f"extracted_{safe_name}.json"
            out_file.write_text(json.dumps(result.model_dump(), indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to persist document extraction: {e}")

    def list_downloads(self) -> List[Dict[str, Any]]:
        """List files currently downloaded in the downloads directory."""
        items = []
        for p in sorted(self.downloads_dir.glob("*"), key=lambda x: x.stat().st_mtime, reverse=True):
            if p.is_file():
                items.append({
                    "filename": p.name,
                    "size_bytes": p.stat().st_size,
                    "modified": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat(),
                    "path": str(p),
                })
        return items

    def get_latest_download(self) -> Optional[Path]:
        """Return the most recently saved file in the downloads folder."""
        files = [p for p in self.downloads_dir.glob("*") if p.is_file()]
        if not files:
            return None
        return max(files, key=lambda x: x.stat().st_mtime)


# Singleton document extractor instance
document_extractor = DocumentExtractor()
