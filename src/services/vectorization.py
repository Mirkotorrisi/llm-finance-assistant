"""Vectorization service for chunking and embedding transaction data."""

import logging
import os
from typing import List, Dict, Any
from openai import OpenAI

logger = logging.getLogger(__name__)


class VectorizationService:
    """Service for chunking and vectorizing transaction data."""
    
    def __init__(self):
        """Initialize the vectorization service."""
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            logger.warning("OPENAI_API_KEY not set. Vectorization will be skipped.")
            self.client = None
        else:
            self.client = OpenAI(api_key=api_key)
        self.model = "text-embedding-3-small"
    
    @staticmethod
    def chunk_transactions(transactions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Split transactions into logical chunks (one per transaction).
        
        Args:
            transactions: List of transactions
            
        Returns:
            List of chunks with transaction data
        """
        chunks = []
        
        for transaction in transactions:
            # Create a text representation of the transaction
            text = (
                f"Date: {transaction['date']}, "
                f"Description: {transaction['description']}, "
                f"Amount: {transaction['amount']} {transaction.get('currency', 'EUR')}, "
                f"Category: {transaction['category']}"
            )
            
            chunk = {
                "text": text,
                "metadata": transaction
            }
            chunks.append(chunk)
        
        logger.info(f"Created {len(chunks)} chunks from {len(transactions)} transactions")
        
        return chunks
    
    def generate_embeddings(self, chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Generate embeddings for transaction chunks.
        
        Args:
            chunks: List of transaction chunks
            
        Returns:
            List of chunks with embeddings added
        """
        if not chunks:
            return []
        
        if not self.client:
            logger.info("OpenAI client not initialized. Skipping embeddings generation.")
            return chunks
        
        try:
            # Extract texts for embedding
            texts = [chunk["text"] for chunk in chunks]
            
            # Generate embeddings using OpenAI
            response = self.client.embeddings.create(
                model=self.model,
                input=texts
            )
            
            # Add embeddings to chunks
            for i, chunk in enumerate(chunks):
                chunk["embedding"] = response.data[i].embedding
            
            logger.info(f"Generated embeddings for {len(chunks)} chunks")
            
            return chunks
            
        except Exception as e:
            logger.error(f"Error generating embeddings: {str(e)}")
            # Return chunks without embeddings if OpenAI fails
            logger.warning("Returning chunks without embeddings due to error")
            return chunks
    
    def process_transactions(self, transactions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Process transactions: chunk and generate embeddings.
        
        Args:
            transactions: List of transactions
            
        Returns:
            List of processed chunks with embeddings
        """
        chunks = self.chunk_transactions(transactions)
        chunks_with_embeddings = self.generate_embeddings(chunks)
        
        return chunks_with_embeddings
