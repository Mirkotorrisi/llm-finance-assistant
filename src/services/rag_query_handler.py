"""RAG query handler for conversational financial queries.

This service handles user queries by:
1. Performing semantic search on narrative documents
2. Injecting retrieved narratives into LLM context
3. Generating grounded answers based on narratives
"""

import logging
import os
from typing import Dict, List, Optional, Any
from openai import OpenAI

from src.services.narrative_vectorization import NarrativeRAGService
from src.services.aggregation_service import AggregationService

logger = logging.getLogger(__name__)


class RAGQueryHandler:
    """Handler for RAG-based conversational queries.
    
    This service:
    - Performs semantic search on narrative documents
    - Retrieves relevant context
    - Uses LLM to generate answers grounded in narratives
    - Never allows LLM to compute or infer from raw data
    """
    
    def __init__(
        self, 
        narrative_rag_service: Optional[NarrativeRAGService] = None,
        aggregation_service: Optional[AggregationService] = None,
        currency_symbol: str = "€"
    ):
        """Initialize the query handler.
        
        Args:
            narrative_rag_service: Narrative RAG service. If None, creates new one.
            aggregation_service: Aggregation service for live queries. If None, creates new one.
            currency_symbol: Currency symbol to use in responses (default: €)
        """
        self.narrative_rag = narrative_rag_service or NarrativeRAGService()
        self.aggregation_service = aggregation_service or AggregationService()
        self.currency_symbol = currency_symbol
        
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            logger.warning("OPENAI_API_KEY not set. Query handling will be disabled.")
            self.client = None
        else:
            self.client = OpenAI(api_key=api_key)
        
        self.model = "gpt-4o-mini"
    
    def close(self):
        """Close underlying services."""
        self.aggregation_service.close()
    
    def answer_query(
        self, 
        user_query: str, 
        year: Optional[int] = None, 
        month: Optional[int] = None,
        top_k: int = 5
    ) -> Dict[str, Any]:
        """Answer a user query using narrative RAG.
        
        Args:
            user_query: User's question
            year: Optional year filter for context
            month: Optional month filter for context
            top_k: Number of relevant documents to retrieve
            
        Returns:
            Dict with answer, sources, confidence, and metadata
        """
        if not self.client:
            return {
                "query": user_query,
                "answer": "OpenAI API is not configured. Cannot process query.",
                "sources": [],
                "confidence": "none",
                "error": "OpenAI client not initialized"
            }
        
        try:
            # Step 1: Retrieve relevant narrative documents
            logger.info(f"Retrieving relevant narratives for query: {user_query}")
            relevant_docs = self.narrative_rag.query(user_query, top_k=top_k)
            
            if not relevant_docs:
                # Fallback: try to get live data if year/month specified
                if year and month:
                    return self._answer_with_live_data(user_query, year, month)
                
                return {
                    "query": user_query,
                    "answer": "I don't have enough information to answer this question. "
                              "Please ensure financial data has been processed and narrative summaries generated.",
                    "sources": [],
                    "confidence": "none"
                }
            
            # Step 2: Build context from retrieved documents
            context_parts = []
            sources = []
            
            for i, doc in enumerate(relevant_docs, 1):
                context_parts.append(f"[Document {i}]")
                context_parts.append(doc["text"])
                context_parts.append("")
                
                sources.append({
                    "type": doc["type"],
                    "metadata": doc["metadata"],
                    "similarity": doc["similarity"],
                    "text_preview": doc["text"][:200] + "..." if len(doc["text"]) > 200 else doc["text"]
                })
            
            context = "\n".join(context_parts)
            
            # Step 3: Create prompt for LLM
            system_prompt = """You are a financial assistant helping users understand their financial data.

CRITICAL RULES:
- You MUST base your answers ONLY on the provided narrative documents
- NEVER compute, calculate, or infer numbers from the narratives
- If a number is mentioned in a narrative, you can quote it
- If a number is NOT in the narratives, say you don't have that information
- Always cite which documents support your answer
- Be conversational but accurate

The documents provided are pre-computed summaries from the SQL database.
They are the ONLY source of truth for your answers."""

            user_prompt = f"""User Question: {user_query}

Retrieved Financial Narratives:
{context}

Based ONLY on the narratives above, answer the user's question. Be specific and cite relevant details from the narratives. If the narratives don't contain the information needed, say so clearly."""

            # Step 4: Get LLM response
            logger.info("Generating answer with LLM...")
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3,  # Lower temperature for factual answers
                max_tokens=500
            )
            
            answer = response.choices[0].message.content
            
            # Step 5: Determine confidence based on similarity scores
            avg_similarity = sum(doc["similarity"] for doc in relevant_docs) / len(relevant_docs)
            
            if avg_similarity > 0.8:
                confidence = "high"
            elif avg_similarity > 0.6:
                confidence = "medium"
            else:
                confidence = "low"
            
            logger.info(f"Query answered with confidence: {confidence}")
            
            return {
                "query": user_query,
                "answer": answer,
                "sources": sources,
                "confidence": confidence,
                "avg_similarity": avg_similarity,
                "documents_retrieved": len(relevant_docs)
            }
            
        except Exception as e:
            logger.error(f"Error answering query: {str(e)}")
            return {
                "query": user_query,
                "answer": f"An error occurred while processing your query: {str(e)}",
                "sources": [],
                "confidence": "none",
                "error": str(e)
            }
    
    def _answer_with_live_data(
        self, 
        user_query: str, 
        year: int, 
        month: int
    ) -> Dict[str, Any]:
        """Fallback: answer using live SQL data when no narratives available.
        
        Args:
            user_query: User's question
            year: Year
            month: Month
            
        Returns:
            Dict with answer and sources
        """
        try:
            logger.info(f"No narratives found, using live SQL data for {year}-{month}")
            
            # Get live data from SQL
            totals = self.aggregation_service.get_monthly_totals(year, month)
            net_worth = self.aggregation_service.get_net_worth(year, month)
            
            # Create a simple answer
            from datetime import datetime
            month_name = datetime(year, month, 1).strftime("%B")
            
            answer = (
                f"Based on live data for {month_name} {year}: "
                f"Total income was {self.currency_symbol}{totals['total_income']:.2f}, "
                f"total expenses were {self.currency_symbol}{abs(totals['total_expense']):.2f}, "
                f"resulting in net savings of {self.currency_symbol}{totals['net_savings']:.2f}. "
                f"Net worth was {self.currency_symbol}{net_worth:.2f}."
            )
            
            return {
                "query": user_query,
                "answer": answer,
                "sources": [{
                    "type": "live_sql_query",
                    "metadata": {
                        "year": year,
                        "month": month
                    },
                    "text_preview": "Live SQL aggregation"
                }],
                "confidence": "high",
                "note": "Answer generated from live SQL data (no narratives available)"
            }
        except Exception as e:
            logger.error(f"Error with live data fallback: {str(e)}")
            return {
                "query": user_query,
                "answer": "I don't have enough information to answer this question.",
                "sources": [],
                "confidence": "none",
                "error": str(e)
            }
    
    def get_service_status(self) -> Dict[str, Any]:
        """Get status of the query handler and underlying services.
        
        Returns:
            Dict with service status information
        """
        rag_stats = self.narrative_rag.get_stats()
        
        return {
            "query_handler_ready": self.client is not None,
            "narrative_rag_ready": rag_stats["service_ready"],
            "total_documents": rag_stats["total_documents"],
            "documents_by_type": rag_stats["by_type"],
            "llm_model": self.model
        }
