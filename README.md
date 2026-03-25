# MusicMind MCP

An MCP server that gives Claude intelligent access to your Apple Music account — taste profiling, smart recommendations, and playlist generation.

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager
- Apple Developer account with a MusicKit key (.p8 file)

## Setup

```bash
# 1. Install dependencies
uv sync --all-extras

# 2. Run the setup wizard (creates config + OAuth)
uv run python -m musicmind.setup

# 3. Verify the server starts
uv run python -m musicmind
```

The setup wizard will:
- Ask for your Apple Developer Team ID, Key ID, and .p8 key path
- Open a browser for Apple Music OAuth authorization
- Save everything to `~/.config/musicmind/config.json`

## Claude Desktop Integration

Add to `~/.config/claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "musicmind": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/musicmind-mcp", "python", "-m", "musicmind"]
    }
  }
}
```

## Claude Code Integration

Add to `.mcp.json` in any project:

```json
{
  "mcpServers": {
    "musicmind": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/musicmind-mcp", "python", "-m", "musicmind"]
    }
  }
}
```

## Tools (28)

| Domain | Tools | Description |
|--------|-------|-------------|
| Library | 6 | Browse songs, albums, artists, playlists, search |
| Catalog | 7 | Search, lookup, charts, activities, genres |
| Playback | 3 | Recently played, heavy rotation, recommendations |
| Manage | 4 | Create playlists, add tracks, rate songs |
| Taste | 4 | Profile, compare, stats, explain |
| Recommend | 3 | Discover, smart playlist, refresh cache |
| System | 2 | Health check, help guide |

## Quick Start with Claude

After setup, try these prompts:

- "Show me my music taste profile"
- "What have I been listening to lately?"
- "Find me new songs similar to what I already like"
- "Create a playlist called 'Late Night Drive' with underground drill vibes"
- "Why would I like this song?" (after looking one up)

## Development

```bash
uv run pytest           # Run tests
uv run ruff check src/  # Lint
```
