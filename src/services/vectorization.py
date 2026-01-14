"""RAG service for transaction semantic search and retrieval."""

import logging
import os
from typing import List, Dict, Any, Optional
from openai import OpenAI

logger = logging.getLogger(__name__)


class RAGService:
    """Service for RAG-based transaction search with semantic embeddings.
    
    This service provides:
    1. Transaction chunking and embedding generation
    2. In-memory vector storage
    3. Semantic search for transaction retrieval
    """
    
    def __init__(self):
        """Initialize the RAG service."""
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            logger.warning("OPENAI_API_KEY not set. RAG functionality will be disabled.")
            self.client = None
        else:
            self.client = OpenAI(api_key=api_key)
        
        self.model = "text-embedding-3-small"
        
        # In-memory vector store: list of (transaction_data, embedding) tuples
        self.vector_store: List[Dict[str, Any]] = []
    
    @staticmethod
    def create_transaction_text(transaction: Dict[str, Any]) -> str:
        """Create text representation of a transaction for embedding.
        
        Args:
            transaction: Transaction data
            
        Returns:
            Text representation
        """
        return (
            f"Date: {transaction['date']}, "
            f"Description: {transaction['description']}, "
            f"Amount: {transaction['amount']} {transaction.get('currency', 'EUR')}, "
            f"Category: {transaction['category']}"
        )
    
    def add_transactions(self, transactions: List[Dict[str, Any]]) -> bool:
        """Add transactions to the vector store.
        
        Args:
            transactions: List of transactions to add
            
        Returns:
            True if successful, False otherwise
        """
        if not self.client:
            logger.warning("OpenAI client not initialized. Cannot add transactions to vector store.")
            return False
        
        if not transactions:
            return True
        
        try:
            # Create text representations
            texts = [self.create_transaction_text(txn) for txn in transactions]
            
            # Generate embeddings
            response = self.client.embeddings.create(
                model=self.model,
                input=texts
            )
            
            # Store transaction data with embeddings
            for i, transaction in enumerate(transactions):
                self.vector_store.append({
                    "transaction": transaction,
                    "text": texts[i],
                    "embedding": response.data[i].embedding
                })
            
            logger.info(f"Added {len(transactions)} transactions to vector store (total: {len(self.vector_store)})")
            return True
            
        except Exception as e:
            logger.error(f"Error adding transactions to vector store: {str(e)}")
            return False
    
    def query(self, query_text: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """Query the vector store for similar transactions.
        
        Args:
            query_text: Natural language query
            top_k: Number of results to return
            
        Returns:
            List of most relevant transactions with similarity scores
        """
        if not self.client:
            logger.warning("OpenAI client not initialized. Cannot query vector store.")
            return []
        
        if not self.vector_store:
            logger.info("Vector store is empty")
            return []
        
        try:
            # Generate query embedding
            response = self.client.embeddings.create(
                model=self.model,
                input=[query_text]
            )
            query_embedding = response.data[0].embedding
            
            # Calculate cosine similarity with all stored embeddings
            similarities = []
            for item in self.vector_store:
                similarity = self._cosine_similarity(query_embedding, item["embedding"])
                similarities.append({
                    "transaction": item["transaction"],
                    "text": item["text"],
                    "similarity": similarity
                })
            
            # Sort by similarity (descending) and take top k
            similarities.sort(key=lambda x: x["similarity"], reverse=True)
            results = similarities[:top_k]
            
            logger.info(f"Query '{query_text}' returned {len(results)} results")
            
            return results
            
        except Exception as e:
            logger.error(f"Error querying vector store: {str(e)}")
            return []
    
    @staticmethod
    def _cosine_similarity(vec1: List[float], vec2: List[float]) -> float:
        """Calculate cosine similarity between two vectors.
        
        Args:
            vec1: First vector
            vec2: Second vector
            
        Returns:
            Cosine similarity score (-1 to 1, where 1 is most similar)
        """
        # Dot product
        dot_product = sum(a * b for a, b in zip(vec1, vec2))
        
        # Magnitudes
        magnitude1 = sum(a * a for a in vec1) ** 0.5
        magnitude2 = sum(b * b for b in vec2) ** 0.5
        
        # Cosine similarity
        if magnitude1 == 0 or magnitude2 == 0:
            return 0.0
        
        return dot_product / (magnitude1 * magnitude2)
    
    def clear(self) -> None:
        """Clear the vector store."""
        self.vector_store = []
        logger.info("Vector store cleared")
    
    def size(self) -> int:
        """Get the number of items in the vector store.
        
        Returns:
            Number of stored transactions
        """
        return len(self.vector_store)
