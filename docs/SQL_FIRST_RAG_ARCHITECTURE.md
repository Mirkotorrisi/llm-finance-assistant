# SQL-First RAG Architecture

## Overview

This document describes the SQL-first RAG (Retrieval-Augmented Generation) architecture implemented for the LLM Finance Assistant. This architecture ensures that SQL is the single source of truth for all numerical data, while vector embeddings are used only for semantic search over pre-computed narrative summaries.

## Core Principles

### 1. SQL is the Single Source of Truth
- All numerical calculations are performed by SQL queries
- The UI always displays data directly from the database
- No computations are ever derived from embeddings

### 2. Vector Store Contains Only Narratives
- Raw transactions are **NEVER** embedded
- Only pre-generated narrative summaries are embedded
- Narratives are derived artifacts that can be regenerated at any time

### 3. LLM Never Computes from Embeddings
- The LLM receives narrative context, not raw data
- All numbers in LLM responses come from pre-computed narratives
- The LLM provides explanations, not calculations

## Architecture Components

### 1. AggregationService
**Purpose:** Performs all SQL-based aggregations and computations.

**Key Methods:**
- `get_monthly_totals(year, month)` - Returns income, expenses, net savings
- `get_net_worth(year, month)` - Returns total account balances
- `get_category_aggregates(year, month?)` - Returns spending by category
- `get_month_over_month_delta(year, month)` - Returns comparative metrics
- `detect_anomalies(year, month)` - Identifies unusual spending patterns

**Data Sources:**
- `MonthlyAccountSnapshot` table (primary source of truth for balances)
- `Transaction` table (for granular category breakdowns)

### 2. NarrativeGenerator
**Purpose:** Converts SQL aggregates into human-readable narrative documents.

**Document Types Generated:**
1. **Monthly Summaries** - "In March 2024, total expenses were €4,720 and total income was €5,724, resulting in net savings of €1,001."
2. **Category Summaries** - "In 2024, the Home category accounted for €8,400 in expenses, representing 18% of total yearly spending."
3. **Anomaly Summaries** - "June 2024 was the highest spending month (€17,669), driven by mortgage accruals, taxes, and exceptional household costs."
4. **Yearly Overviews** - High-level annual summaries

**Key Methods:**
- `generate_monthly_summary(year, month)` - Creates monthly narrative
- `generate_category_summary(year, category)` - Creates category narrative
- `generate_anomaly_summary(year, month)` - Creates anomaly narrative (if detected)
- `generate_all_documents(year)` - Generates complete set of narratives for a year

### 3. NarrativeRAGService
**Purpose:** Manages the vector store with strict validation to prevent raw data embedding.

**Document Type Whitelist:**
- `monthly_summary`
- `category_summary`
- `anomaly`
- `yearly_overview`
- `note` (user annotations)

**Forbidden Patterns:**
- `transaction` (raw transactions are explicitly forbidden)
- `raw` (no raw data)
- `individual` (no individual records)

**Key Methods:**
- `add_documents(documents)` - Validates and embeds narratives
- `query(query_text, top_k, doc_type?)` - Semantic search over narratives
- `clear()` - Clears vector store (safe, can regenerate)
- `regenerate_from_narratives(documents)` - Full regeneration

**Validation:**
Every document is validated before embedding:
1. Must have `text` and `type` fields
2. Type must be in the whitelist
3. Type must not contain forbidden patterns
4. Text must be non-empty and reasonably long

### 4. RAGQueryHandler
**Purpose:** Handles conversational queries by retrieving narratives and using LLM to answer.

**Query Flow:**
1. User asks: "Why did I spend so much in June?"
2. Semantic search retrieves relevant narrative documents
3. Narratives are injected into LLM context
4. LLM generates answer based on narratives
5. Response includes sources and confidence level

**Key Methods:**
- `answer_query(user_query, year?, month?, top_k)` - Main query handler
- `get_service_status()` - Returns service readiness status

**Confidence Levels:**
- **High** (avg similarity > 0.8) - Very relevant narratives found
- **Medium** (avg similarity > 0.6) - Moderately relevant narratives
- **Low** (avg similarity ≤ 0.6) - Less relevant narratives

## API Endpoints (v2)

### POST /api/v2/narratives/generate
Generate and embed narrative documents for a year.

**Request:**
```json
{
  "year": 2024
}
```

**Response:**
```json
{
  "year": 2024,
  "documents_generated": 15,
  "documents_embedded": 15,
  "rejected": 0,
  "status": "success",
  "details": {
    "embedding_stats": {...},
    "total_in_store": 15
  }
}
```

### POST /api/v2/chat
Conversational query using narrative RAG.

**Request:**
```json
{
  "query": "Why did I spend so much in June?",
  "year": 2024,
  "month": 6,
  "top_k": 5
}
```

**Response:**
```json
{
  "query": "Why did I spend so much in June?",
  "answer": "June 2024 was your highest spending month with €17,669 in expenses. This was driven by several factors...",
  "sources": [
    {
      "type": "anomaly",
      "metadata": {...},
      "similarity": 0.92,
      "text_preview": "June 2024 was the highest spending month..."
    }
  ],
  "confidence": "high",
  "metadata": {
    "avg_similarity": 0.89,
    "documents_retrieved": 3
  }
}
```

### GET /api/v2/narratives/stats
Get vector store statistics.

**Response:**
```json
{
  "total_documents": 15,
  "documents_by_type": {
    "monthly_summary": 12,
    "category_summary": 2,
    "anomaly": 1
  },
  "service_ready": true,
  "query_handler_ready": true
}
```

### DELETE /api/v2/narratives
Clear all narratives from vector store (safe operation).

### POST /api/v2/narratives/regenerate
Clear and regenerate narratives from SQL.

## Regeneration Strategy

Narratives should be regenerated when:

1. **New month closes** - Generate narratives for the new month
2. **Significant data updates** - User edits historical data or imports new transactions
3. **Manual trigger** - User requests regeneration via API

**Why regeneration is safe:**
- Narratives are derived artifacts from SQL
- SQL remains the source of truth
- Regeneration simply re-computes narratives from current SQL state
- No data loss possible

## Example Usage Workflow

### Initial Setup
```bash
# 1. Import financial data (transactions, snapshots)
POST /statements/upload

# 2. Generate narratives for the year
POST /api/v2/narratives/generate
{
  "year": 2024
}
```

### Conversational Queries
```bash
# Query about spending
POST /api/v2/chat
{
  "query": "What were my biggest expenses in 2024?",
  "top_k": 5
}

# Month-specific query
POST /api/v2/chat
{
  "query": "Did I save money in June?",
  "year": 2024,
  "month": 6
}
```

### Maintenance
```bash
# Check vector store status
GET /api/v2/narratives/stats

# Regenerate after data updates
POST /api/v2/narratives/regenerate
{
  "year": 2024
}

# Clear narratives (if needed)
DELETE /api/v2/narratives
```

## Data Flow Diagram

```
┌─────────────────┐
│   User Input    │
│  (Transactions) │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   PostgreSQL    │◄── Single Source of Truth
│   (SQL Tables)  │
└────────┬────────┘
         │
         ├──────────────────────────┐
         │                          │
         ▼                          ▼
┌─────────────────┐        ┌──────────────┐
│ AggregationSvc  │        │      UI      │
│  (SQL Queries)  │        │ (Direct SQL) │
└────────┬────────┘        └──────────────┘
         │
         ▼
┌─────────────────┐
│NarrativeGenerator│
│ (Text Summaries)│
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│NarrativeRAGSvc  │
│(Vector Store)   │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│RAGQueryHandler  │
│  (LLM + RAG)    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  User Answer    │
└─────────────────┘
```

## Benefits

### 1. Consistency
- UI and LLM always show the same numbers
- No discrepancies between different data sources
- Easy to debug and verify

### 2. Explainability
- All LLM answers are grounded in specific narrative documents
- Sources are traceable back to SQL queries
- Clear audit trail for all responses

### 3. Scalability
- Narratives can be regenerated independently
- Vector store size grows with narratives, not transactions
- Efficient for large transaction volumes

### 4. Maintainability
- Clear separation of concerns
- SQL layer remains simple and testable
- RAG layer is stateless and regenerable

## Migration from Old RAG

The old `RAGService` in `src/services/vectorization.py` embedded raw transactions. To migrate:

1. **Continue using old service** for transaction search (if needed)
2. **Use new v2 endpoints** for conversational queries
3. **Eventually deprecate** old transaction embedding approach

The two systems can coexist:
- Old: `/api/transactions/search` - Transaction-level semantic search
- New: `/api/v2/chat` - Narrative-based conversational queries

## Configuration

### Environment Variables
- `OPENAI_API_KEY` - Required for embeddings and LLM
- `DB_*` - Database configuration (see DATABASE_IMPLEMENTATION.md)

### Constants
- `MIN_CATEGORY_AMOUNT = 100.0` - Minimum spending to generate category summary (EUR)
- `ALLOWED_DOCUMENT_TYPES` - Whitelist of embeddable document types
- `FORBIDDEN_PATTERNS` - Patterns that must not appear in document types

## Testing

Comprehensive test coverage includes:
- `tests/test_aggregation_service.py` - SQL aggregation logic
- `tests/test_narrative_generator.py` - Narrative generation
- `tests/test_narrative_rag.py` - Vector store validation
- `tests/test_rag_query_handler.py` - Query handling

Run tests with:
```bash
pytest tests/test_aggregation_service.py
pytest tests/test_narrative_generator.py
pytest tests/test_narrative_rag.py
pytest tests/test_rag_query_handler.py
```

## Security Considerations

1. **No SQL Injection** - All queries use SQLAlchemy ORM
2. **Input Validation** - Document types validated before embedding
3. **API Authentication** - Should be added for production use
4. **Rate Limiting** - Should be implemented for LLM endpoints

## Future Enhancements

1. **Multi-currency support** - Configurable currency symbols and conversions
2. **User annotations** - Allow users to add notes that get embedded
3. **Smart regeneration** - Only regenerate affected months
4. **Batch operations** - Generate narratives for multiple years
5. **Caching** - Cache frequently accessed narratives

## Troubleshooting

### No narratives returned
- Check if narratives were generated: `GET /api/v2/narratives/stats`
- Regenerate if needed: `POST /api/v2/narratives/regenerate`

### Low confidence answers
- More specific queries often work better
- Check if relevant narratives exist for the time period
- Generate narratives if missing

### Numbers don't match UI
- UI should always be correct (from SQL)
- LLM answers come from narratives
- Regenerate narratives to sync: `POST /api/v2/narratives/regenerate`
