"""SmarTaste Admin Dashboard — standalone service.

Lightweight FastAPI app that:
1. Serves the admin dashboard HTML
2. Proxies API requests to the main SmarTaste backend
3. Uses its own simple password auth (ADMIN_PASSWORD env var)

Deployed on Railway at admin.music.menghi.dev.
"""

from __future__ import annotations

import hashlib
import os
import secrets

import httpx
from fastapi import Cookie, FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

BACKEND_URL = os.environ.get(
    "BACKEND_URL", "https://musicmind-production.up.railway.app"
)
NOCODB_URL = os.environ.get(
    "NOCODB_URL", "https://dbmanager.music.menghi.dev"
)
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "")
PORT = int(os.environ.get("PORT", "8080"))

# Simple session token store (in-memory, resets on restart)
_valid_tokens: set[str] = set()

app = FastAPI(title="SmarTaste Admin")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _check_admin_session(admin_token: str | None) -> bool:
    """Validate admin session token."""
    return admin_token is not None and admin_token in _valid_tokens


@app.get("/", response_class=HTMLResponse)
async def index(admin_token: str | None = Cookie(default=None)):
    if not _check_admin_session(admin_token):
        return RedirectResponse(url="/login")
    html_path = os.path.join(os.path.dirname(__file__), "templates", "dashboard.html")
    with open(html_path) as f:
        return HTMLResponse(content=f.read())


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    html_path = os.path.join(os.path.dirname(__file__), "templates", "login.html")
    with open(html_path) as f:
        return HTMLResponse(content=f.read())


@app.post("/auth/login")
async def login(request: Request):
    """Authenticate with admin password."""
    body = await request.json()
    password = body.get("password", "")

    if not ADMIN_PASSWORD:
        return JSONResponse(
            {"error": "ADMIN_PASSWORD not configured"}, status_code=500,
        )

    if password != ADMIN_PASSWORD:
        return JSONResponse({"error": "Wrong password"}, status_code=401)

    token = secrets.token_urlsafe(32)
    _valid_tokens.add(token)

    response = JSONResponse({"ok": True})
    response.set_cookie(
        "admin_token", token,
        httponly=True, samesite="lax", secure=True, max_age=86400,
    )
    return response


@app.post("/auth/logout")
async def logout(admin_token: str | None = Cookie(default=None)):
    if admin_token:
        _valid_tokens.discard(admin_token)
    response = JSONResponse({"ok": True})
    response.delete_cookie("admin_token")
    return response


@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy_api(
    request: Request,
    path: str,
    admin_token: str | None = Cookie(default=None),
):
    """Proxy /api/* to the main backend. Requires admin session."""
    if not _check_admin_session(admin_token):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    url = f"{BACKEND_URL}/api/{path}"
    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in ("host", "cookie")
    }
    # Authenticate with backend via shared secret
    if ADMIN_SECRET:
        headers["x-admin-secret"] = ADMIN_SECRET

    body = await request.body()

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.request(
            method=request.method,
            url=url,
            headers=headers,
            content=body if body else None,
            params=dict(request.query_params),
        )

    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type"),
    )


@app.get("/nocodb")
async def nocodb_redirect():
    return RedirectResponse(url=NOCODB_URL)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "smartaste-admin"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=PORT, reload=True)
