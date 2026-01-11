# Multimodal Personal Finance Assistant

A professional, self-contained virtual assistant for managing personal finances. This application allows users to interact via text or voice, perform complex queries on their financial data, and manage transactions through a simple CLI.

## Features

- **Multimodal Interaction**: Supports text-based input and audio transcription (via `SpeechRecognition`).
- **MCP Architecture**: Uses a Model Context Protocol (MCP) simulation to decouple business logic from the conversational flow.
- **Intelligent NLU**: Powered by OpenAI's `gpt-4o-mini` to dynamically interpret user intent, categories, and timeframes.
- **Dynamic Transaction Management**:
  - Add expenses or income.
  - Delete transactions by ID.
  - Query historic spending with natural language (e.g., "last 3 days", "this week").
- **State Management**: Built with `LangGraph` to manage conversational history and execution nodes.
- **Debug Mode**: Includes a specialized logging mode to inspect LLM reasonings and system prompts.

## Prerequisites

- Python 3.10+
- OpenAI API Key

## Setup

1. **Install Dependencies**:

   ```bash
   pipenv install
   ```

   *Alternatively, if not using pipenv:*

   ```bash
   pip install langgraph pydantic openai python-dotenv SpeechRecognition
   ```

2. **Configure Environment**:
   Create a `.env` file in the project root:

   ```env
   OPENAI_API_KEY=your_actual_key_here
   ```

## Usage

Run the assistant in standard interactive mode:

```bash
python finance_assistant.py
```

### Commands Examples

- **Queries**: "How much did I spend on food this week?"
- **Additions**: "I spent 15.50 on a bus ticket today"
- **Deletions**: "Delete transaction 4"
- **Balance**: "What is my current total balance?"

### Audio Simulation

To simulate audio input (transcribing a `.wav` file):

```text
You: audio:path/to/voice_note.wav
```

### Debug Mode

To inspect the LLM's reasoning prompts and MCP data output:

```bash
python finance_assistant.py --debug
```
