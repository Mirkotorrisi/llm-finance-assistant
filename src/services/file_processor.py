"""File processing service for bank statements."""

import csv
import io
import logging
from html.parser import HTMLParser
from typing import List, Dict, Any, BinaryIO, Optional
from pypdf import PdfReader
import pandas as pd

from src.services.columns import header_kinds

logger = logging.getLogger(__name__)

# Maximum file size in bytes (10 MB)
MAX_FILE_SIZE = 10 * 1024 * 1024

# Supported file formats
SUPPORTED_FORMATS = {".pdf", ".xls", ".xlsx", ".csv"}


class FileValidationError(Exception):
    """Exception raised for file validation errors."""
    pass


class FileProcessor:
    """Service for processing bank statement files."""
    
    @staticmethod
    def validate_file(filename: str, file_size: int) -> None:
        """Validate file format and size.
        
        Args:
            filename: Name of the file
            file_size: Size of the file in bytes
            
        Raises:
            FileValidationError: If file is invalid
        """
        # Check file size
        if file_size > MAX_FILE_SIZE:
            raise FileValidationError(
                f"File size exceeds maximum limit of {MAX_FILE_SIZE / 1024 / 1024}MB"
            )
        
        # Check file format
        file_ext = filename.lower()
        if not any(file_ext.endswith(fmt) for fmt in SUPPORTED_FORMATS):
            raise FileValidationError(
                f"Unsupported file format. Supported formats: {', '.join(SUPPORTED_FORMATS)}"
            )
        
        logger.info(f"File validation passed: {filename}, size: {file_size} bytes")
    
    # Number of PDF pages to send to the LLM in a single call.
    # ~10 pages ≈ 15-25 k characters — well within GPT-4o-mini's context window.
    PAGES_PER_CHUNK = 10

    @staticmethod
    def extract_from_pdf(file_content: BinaryIO) -> List[Dict[str, Any]]:
        """Extract text from a PDF and split it into page chunks for LLM parsing.

        Returns one dict per chunk so that large PDFs are processed page-by-page
        rather than truncated to the first N characters.
        """
        try:
            pdf_reader = PdfReader(file_content)
            pages_text = []

            for page in pdf_reader.pages:
                text = page.extract_text()
                if text and text.strip():
                    pages_text.append(text)

            if not pages_text:
                logger.warning("No extractable text found in PDF")
                return []

            # statements with a known layout are read with rules (no model): rows plus closing balance and holdings
            from src.services.broker_statements import parse as parse_known_statement

            known = parse_known_statement("\n".join(pages_text))
            if known is not None:
                rows, meta = known
                logger.info("PDF: known %s layout, %d row(s) read locally", meta.get("kind"), len(rows))
                if rows:
                    rows[0] = {**rows[0], "__meta__": meta}
                    return rows
                return [{"__meta__": meta, "date": None}]

            chunk_size = FileProcessor.PAGES_PER_CHUNK
            chunks = []
            for i in range(0, len(pages_text), chunk_size):
                chunk_pages = pages_text[i : i + chunk_size]
                first = i + 1
                last = min(i + chunk_size, len(pages_text))
                chunks.append({
                    "pdf_text": "\n\n--- page break ---\n\n".join(chunk_pages),
                    "chunk_index": i // chunk_size,
                    "pages": f"{first}-{last}",
                })

            logger.info(
                f"PDF: {len(pages_text)} pages → {len(chunks)} chunk(s) "
                f"of up to {chunk_size} pages each"
            )
            return chunks

        except Exception as e:
            logger.error(f"Error extracting PDF content: {str(e)}")
            raise FileValidationError(f"Failed to extract PDF content: {str(e)}")
    
    # ------------------------------------------------------------------ spreadsheets and text tables

    # Banks often name a file ".xls" or ".csv" whatever it really is (HTML, tab-separated text, the old binary Excel
    # format, a real .xlsx). The format is therefore read from the content, never from the extension.
    @staticmethod
    def sniff_format(raw: bytes) -> str:
        """'pdf' | 'xlsx' | 'xls' | 'html' | 'text', from the first bytes."""
        if raw[:5] == b"%PDF-":
            return "pdf"
        if raw[:2] == b"PK":
            return "xlsx"  # a zip container: .xlsx
        if raw[:4] == b"\xd0\xcf\x11\xe0":
            return "xls"  # OLE2 container: the old binary Excel format
        head = raw[:4096].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
        if head.startswith((b"<!doctype html", b"<html", b"<table", b"<?xml")) or b"<table" in head:
            return "html"
        return "text"

    @staticmethod
    def _decode(raw: bytes) -> str:
        if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
            return raw.decode("utf-16")
        try:
            return raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            return raw.decode("cp1252", errors="replace")  # Windows exports by Italian banks

    @staticmethod
    def _text_grid(raw: bytes) -> List[List[Any]]:
        text = FileProcessor._decode(raw)
        lines = [l for l in text.splitlines() if l.strip()]
        sample = lines[:30]
        delimiter = max((";", "\t", ",", "|"), key=lambda d: sum(l.count(d) for l in sample))
        return [[c.strip() for c in row] for row in csv.reader(io.StringIO("\n".join(lines)), delimiter=delimiter)]

    @staticmethod
    def _html_grid(raw: bytes) -> List[List[Any]]:
        class Tables(HTMLParser):
            def __init__(self) -> None:
                super().__init__()
                self.tables: List[List[List[str]]] = []
                self._depth = 0
                self._cell: Optional[List[str]] = None
                self._row: Optional[List[str]] = None

            def handle_starttag(self, tag, attrs):
                if tag == "table":
                    self._depth += 1
                    self.tables.append([])
                elif tag == "tr" and self.tables:
                    self._row = []
                elif tag in ("td", "th") and self._row is not None:
                    self._cell = []

            def handle_endtag(self, tag):
                if tag in ("td", "th") and self._cell is not None and self._row is not None:
                    self._row.append(" ".join("".join(self._cell).split()))
                    self._cell = None
                elif tag == "tr" and self._row is not None and self.tables:
                    self.tables[-1].append(self._row)
                    self._row = None
                elif tag == "table":
                    self._depth -= 1

            def handle_data(self, data):
                if self._cell is not None:
                    self._cell.append(data)

        parser = Tables()
        parser.feed(FileProcessor._decode(raw))
        return max(parser.tables, key=len) if parser.tables else []

    @staticmethod
    def _excel_grid(raw: bytes, kind: str) -> List[List[Any]]:
        engine = "openpyxl" if kind == "xlsx" else "xlrd"
        df = pd.read_excel(io.BytesIO(raw), header=None, engine=engine, dtype=object)
        df = df.astype(object).where(df.notna(), None)
        return df.values.tolist()

    @staticmethod
    def find_header_row(grid: List[List[Any]], scan: int = 50) -> int:
        """Index of the heading row of the transactions table.

        Statements start with rows about the account (holder, IBAN, period, balance) before the table. The heading row
        is the first one with at least two kinds of heading (date, description, amount). Falls back to the first
        non-empty row, which is what a plain CSV has.
        """
        for i, row in enumerate(grid[:scan]):
            kinds = set()
            for cell in row:
                if cell is not None:
                    kinds |= header_kinds(cell)
            if len(kinds) >= 2:
                return i
        return next((i for i, row in enumerate(grid) if any(c not in (None, "") for c in row)), 0)

    @staticmethod
    def _records(grid: List[List[Any]]) -> List[Dict[str, Any]]:
        if not grid:
            return []
        start = FileProcessor.find_header_row(grid)
        names: List[str] = []
        for j, cell in enumerate(grid[start]):
            name = str(cell).strip() if cell not in (None, "") else f"col{j + 1}"
            while name in names:  # duplicate headings get a suffix
                name += "_2"
            names.append(name)
        records = []
        for row in grid[start + 1:]:
            if all(c in (None, "") for c in row):
                continue
            records.append({names[j]: (row[j] if j < len(row) and row[j] != "" else None) for j in range(len(names))})
        return records

    @staticmethod
    def read_grid(raw: bytes) -> List[List[Any]]:
        """Every cell of a spreadsheet-like file (real .xls, .xlsx, HTML or delimited text), row by row."""
        kind = FileProcessor.sniff_format(raw)
        try:
            if kind in ("xlsx", "xls"):
                return FileProcessor._excel_grid(raw, kind)
            if kind == "html":
                return FileProcessor._html_grid(raw)
            if kind == "text":
                return FileProcessor._text_grid(raw)
        except Exception as e:
            logger.error(f"Error reading {kind} content: {str(e)}")
            raise FileValidationError(f"Failed to read the file as a {kind} table: {str(e)}")
        raise FileValidationError("This looks like a PDF but has a spreadsheet extension. Rename it to .pdf.")

    @staticmethod
    def extract_tabular(raw: bytes) -> List[Dict[str, Any]]:
        """Rows of a statement table from any spreadsheet-like content, as dicts keyed by the heading row."""
        kind = FileProcessor.sniff_format(raw)
        try:
            grid = FileProcessor.read_grid(raw)
            data = FileProcessor._records(grid)
        except FileValidationError:
            raise
        except Exception as e:
            logger.error(f"Error reading {kind} content: {str(e)}")
            raise FileValidationError(f"Failed to read the file as a {kind} table: {str(e)}")
        logger.info(f"Extracted {len(data)} rows from a {kind} file")
        return data

    @staticmethod
    def extract_from_excel(file_content: BinaryIO) -> List[Dict[str, Any]]:
        """Extract rows from an Excel-like file (.xls, .xlsx, or an HTML/text export named like one)."""
        return FileProcessor.extract_tabular(file_content.read())

    @staticmethod
    def extract_from_csv(file_content: BinaryIO) -> List[Dict[str, Any]]:
        """Extract rows from a CSV-like file (any delimiter among ; , tab |, UTF-8 or Windows encoding)."""
        return FileProcessor.extract_tabular(file_content.read())

    @staticmethod
    def process_file(filename: str, file_content: BinaryIO, file_size: int) -> List[Dict[str, Any]]:
        """Process a file and extract data.
        
        Args:
            filename: Name of the file
            file_content: Binary content of the file
            file_size: Size of the file in bytes
            
        Returns:
            List of extracted data rows
            
        Raises:
            FileValidationError: If file validation or processing fails
        """
        # Validate file
        FileProcessor.validate_file(filename, file_size)
        
        # Extract data based on file type
        filename_lower = filename.lower()
        
        if filename_lower.endswith('.pdf'):
            return FileProcessor.extract_from_pdf(file_content)
        elif filename_lower.endswith(('.xls', '.xlsx', '.csv')):
            return FileProcessor.extract_tabular(file_content.read())
        else:
            raise FileValidationError(f"Unsupported file format: {filename}")
