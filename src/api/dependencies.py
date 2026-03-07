"""Shared API-level dependencies and singleton services."""

import logging

from src.services import RAGService

logger = logging.getLogger(__name__)

rag_service = RAGService()
logger.info("RAG service initialized")
