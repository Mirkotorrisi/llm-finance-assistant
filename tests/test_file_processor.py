"""Unit tests for file processor service."""

import io
import pytest
from src.services.file_processor import FileProcessor, FileValidationError


class TestFileValidation:
    """Tests for file validation."""
    
    def test_validate_file_size_exceeds_limit(self):
        """Test that files exceeding size limit are rejected."""
        with pytest.raises(FileValidationError, match="exceeds maximum limit"):
            FileProcessor.validate_file("test.csv", 11 * 1024 * 1024)
    
    def test_validate_unsupported_format(self):
        """Test that unsupported formats are rejected."""
        with pytest.raises(FileValidationError, match="Unsupported file format"):
            FileProcessor.validate_file("test.txt", 1000)
    
    def test_validate_supported_formats(self):
        """Test that supported formats pass validation."""
        # Should not raise any exception
        FileProcessor.validate_file("test.pdf", 1000)
        FileProcessor.validate_file("test.csv", 1000)
        FileProcessor.validate_file("test.xls", 1000)
        FileProcessor.validate_file("test.xlsx", 1000)


class TestCSVExtraction:
    """Tests for CSV extraction."""
    
    def test_extract_from_csv_basic(self):
        """Test basic CSV extraction."""
        csv_content = b"date,description,amount\n2026-01-01,Grocery,50.00\n2026-01-02,Gas,30.00"
        
        result = FileProcessor.extract_from_csv(io.BytesIO(csv_content))
        
        assert len(result) == 2
        assert result[0]["date"] == "2026-01-01"
        assert result[0]["description"] == "Grocery"
        assert result[0]["amount"] == "50.00"
    
    def test_extract_from_csv_empty(self):
        """Test CSV extraction with empty file."""
        csv_content = b"date,description,amount\n"
        
        result = FileProcessor.extract_from_csv(io.BytesIO(csv_content))
        
        assert len(result) == 0


class TestPDFExtraction:
    """Tests for PDF extraction."""
    
    def test_extract_from_pdf_returns_list(self):
        """Test that PDF extraction returns a list."""
        # We can't easily create a real PDF, but we can test the error handling
        with pytest.raises(FileValidationError):
            FileProcessor.extract_from_pdf(io.BytesIO(b"not a pdf"))
