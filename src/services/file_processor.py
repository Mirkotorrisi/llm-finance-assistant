"""File processing service for bank statements."""

import csv
import io
import logging
from typing import List, Dict, Any, BinaryIO
from PyPDF2 import PdfReader
import openpyxl
import pandas as pd

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
    
    @staticmethod
    def extract_from_pdf(file_content: BinaryIO) -> List[Dict[str, Any]]:
        """Extract full text from PDF file for LLM-based parsing.
        
        Args:
            file_content: Binary content of the PDF file
            
        Returns:
            List containing a single dict with the full PDF text
        """
        try:
            pdf_reader = PdfReader(file_content)
            all_text = []
            
            # Extract text from all pages
            for page in pdf_reader.pages:
                text = page.extract_text()
                if text:
                    all_text.append(text)
            
            # Join all text
            full_text = "\n".join(all_text)
            
            logger.info(f"Extracted {len(full_text)} characters from PDF")
            
            # Return full text for LLM-based parsing
            return [{"pdf_text": full_text}]
            
        except Exception as e:
            logger.error(f"Error extracting PDF content: {str(e)}")
            raise FileValidationError(f"Failed to extract PDF content: {str(e)}")
    
    @staticmethod
    def extract_from_excel(file_content: BinaryIO) -> List[Dict[str, Any]]:
        """Extract rows and columns from Excel file.
        
        Args:
            file_content: Binary content of the Excel file
            
        Returns:
            List of rows as dictionaries
        """
        try:
            # Read Excel file using pandas
            df = pd.read_excel(file_content, engine='openpyxl')
            
            # Convert to list of dictionaries
            data = df.to_dict('records')
            
            logger.info(f"Extracted {len(data)} rows from Excel file")
            
            return data
            
        except Exception as e:
            logger.error(f"Error extracting Excel content: {str(e)}")
            raise FileValidationError(f"Failed to extract Excel content: {str(e)}")
    
    @staticmethod
    def extract_from_csv(file_content: BinaryIO) -> List[Dict[str, Any]]:
        """Extract rows from CSV file.
        
        Args:
            file_content: Binary content of the CSV file
            
        Returns:
            List of rows as dictionaries
        """
        try:
            # Decode binary content to text
            text_content = file_content.read().decode('utf-8')
            
            # Read CSV using csv.DictReader
            csv_reader = csv.DictReader(io.StringIO(text_content))
            data = list(csv_reader)
            
            logger.info(f"Extracted {len(data)} rows from CSV file")
            
            return data
            
        except Exception as e:
            logger.error(f"Error extracting CSV content: {str(e)}")
            raise FileValidationError(f"Failed to extract CSV content: {str(e)}")
    
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
        elif filename_lower.endswith(('.xls', '.xlsx')):
            return FileProcessor.extract_from_excel(file_content)
        elif filename_lower.endswith('.csv'):
            return FileProcessor.extract_from_csv(file_content)
        else:
            raise FileValidationError(f"Unsupported file format: {filename}")
