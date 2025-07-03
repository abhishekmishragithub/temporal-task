# GitHub PR Bot

Temporal-powered GitHub automation that creates pull requests from issues with guaranteed cleanup and retry handling.

## Features

- **Resilient**: Automatic retries with exponential backoff
- **Reliable**: Guaranteed cleanup even on failures
- **Observable**: Full execution history in Temporal UI
- **Typed**: Complete type safety with dataclasses

## Quick Start

### Prerequisites

- Python 3.9+
- [uv](https://github.com/astral-sh/uv) package manager
- Docker for Temporal server
- GitHub Personal Access Token

### Setup

1. **Install dependencies**
   ```bash
   uv sync
   ```

2. **Start Temporal server**
   ```bash
   docker-compose up -d
   ```

3. **Set environment variables**
   ```bash
   export GITHUB_TOKEN="ghp_your_token_here"
   export TEMPORAL_ADDRESS="localhost:7233"  # optional
   export DEBUG="true"  # optional
   ```

### Usage

1. **Start the worker**
   ```bash
   uv run python after/run_worker.py
   ```

2. **Start a workflow** (in another terminal)
   ```bash
   uv run python after/start_workflow.py https://github.com/owner/repo/issues/123
   ```

3. **Monitor progress**
   - Worker logs show real-time progress
   - Temporal UI at [localhost:8080](http://localhost:8080)

## Development

**Format and lint**
```bash
uv run ruff format
uv run ruff check
```

**Type checking**
```bash
uv run mypy .
```

## Architecture

The workflow executes these steps with automatic retries:

1. **Parse** - Extract repo info from GitHub issue URL
2. **Clone** - Clone repository and create feature branch
3. **Commit** - Apply fix and commit changes
4. **Push** - Push branch to GitHub (with retry for rate limits)
5. **PR** - Create pull request (with retry for API limits)
6. **Cleanup** - Always remove local repository
