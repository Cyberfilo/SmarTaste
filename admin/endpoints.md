# Admin API Endpoints

All endpoints require admin authentication (is_admin=true).
Base URL: the main SmarTaste backend (e.g. `https://musicmind-production.up.railway.app`)

## System Status

### GET /api/admin/status
Returns system health + aggregate statistics.

**Response:**
```json
{
  "version": "3.240",
  "users": 3,
  "connections": 4,
  "calibrated_users": 2,
  "total_songs": 1250,
  "total_enriched": 980,
  "enrichment_pct": 78.4,
  "global_isrc_cache": 850,
  "listening_history_entries": 3200,
  "soundstat_configured": false
}
```

## Enrichment Progress

### GET /api/admin/progress
Returns per-user enrichment progress.

**Response:**
```json
{
  "users": [
    {
      "user_id": "019d3e3c-...",
      "email": "user@example.com",
      "display_name": "Filippo",
      "total_songs": 500,
      "enriched_songs": 420,
      "percentage": 84.0
    }
  ]
}
```

## Logs

### GET /api/admin/logs?limit=100
Returns recent log entries from the in-memory buffer.

### GET /api/admin/logs/stream
Server-Sent Events stream of live log entries.

### GET /api/admin/errors?limit=50
Returns recent 500 errors from the logging database.

**Response:**
```json
{
  "errors": [
    {
      "timestamp": "2026-04-08T10:30:00Z",
      "method": "GET",
      "path": "/api/taste/profile",
      "status_code": 500,
      "duration_ms": 1234,
      "user_id": "019d3e3c-...",
      "error_detail": "unhandled exception"
    }
  ],
  "source": "logs_db"
}
```

## Request Statistics

### GET /api/admin/request-stats
Returns today's request statistics from the logging database.

**Response:**
```json
{
  "stats": {
    "total_today": 1500,
    "errors_today": 3,
    "avg_duration_ms": 245.2,
    "slow_requests_today": 5
  },
  "source": "logs_db"
}
```

## Taste Profile (enrichment status per user)

### GET /api/taste/enrichment-status
Returns the current user's enrichment progress (no admin required).

**Response:**
```json
{
  "total_songs": 500,
  "enriched_songs": 420,
  "percentage": 84.0,
  "complete": false,
  "indexing": true
}
```

## Authentication

### POST /api/auth/login
Login with email/password. Sets httpOnly JWT cookies.

### GET /api/auth/me
Returns current user info including `is_admin` flag.

**Response:**
```json
{
  "user_id": "019d3e3c-...",
  "email": "admin@example.com",
  "display_name": "Admin",
  "is_admin": true
}
```
