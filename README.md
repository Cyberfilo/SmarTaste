# MusicMind MCP

An MCP server that gives Claude intelligent access to your Apple Music account — taste profiling, smart recommendations, and playlist generation.

## Setup

```bash
uv sync --all-extras
uv run python -m musicmind.setup   # One-time Apple Music OAuth
uv run python -m musicmind         # Start MCP server
```
