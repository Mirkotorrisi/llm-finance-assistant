# PostgreSQL Persistence Implementation

This document describes the PostgreSQL persistence implementation for the LLM Finance Assistant.

## Overview

The application now supports persistent storage using PostgreSQL database through SQLAlchemy ORM. The implementation maintains backward compatibility with the in-memory storage option.

## Key Features

### 1. Dual Storage Mode
- **In-Memory Storage**: Fast, simple, no setup required (default when DB_PASSWORD not set)
- **PostgreSQL Storage**: Persistent data storage with automatic table creation in development mode

### 2. Automatic Migration
In development mode (`ENVIRONMENT=development`), database tables are automatically created on application startup using SQLAlchemy's `create_all()` method. No manual migration commands needed.

### 3. Configuration via Environment Variables
All database connection details are stored in `src/config/database.py`, with only the password read from environment variables for security.

## Database Schema

See full documentation for schema details, usage examples, and troubleshooting guides.
