"""SmarTaste Admin Dashboard — standalone service.

Lightweight FastAPI app that:
1. Serves the admin dashboard HTML
2. Proxies API requests to the main SmarTaste backend
3. Uses cookie-based password auth (survives restarts)

Deployed on Railway at admin.music.menghi.dev.
"""

from __future__ import annotations

import hashlib
import hmac
import os

import httpx
from fastapi import Cookie, FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.responses import StreamingResponse
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

# Derive a stable signing key from the password (survives restarts)
_COOKIE_KEY = hashlib.sha256(
    f"staste-admin-{ADMIN_PASSWORD}".encode()
).hexdigest() if ADMIN_PASSWORD else ""

app = FastAPI(title="SmarTaste Admin")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _sign_token(password: str) -> str:
    """Create an HMAC signature of the password using the derived key."""
    return hmac.new(
        _COOKIE_KEY.encode(), password.encode(), hashlib.sha256,
    ).hexdigest()


def _check_admin_session(admin_token: str | None) -> bool:
    """Validate admin session — compare cookie against expected HMAC."""
    if not admin_token or not ADMIN_PASSWORD or not _COOKIE_KEY:
        return False
    expected = _sign_token(ADMIN_PASSWORD)
    return hmac.compare_digest(admin_token, expected)


@app.get("/", response_class=HTMLResponse)
async def index(admin_token: str | None = Cookie(default=None)):
    if not _check_admin_session(admin_token):
        return RedirectResponse(url="/login")
    html_path = os.path.join(os.path.dirname(__file__), "templates", "dashboard.html")
    with open(html_path) as f:
        return HTMLResponse(content=f.read())


@app.get("/login", response_class=HTMLResponse)
async def login_page(admin_token: str | None = Cookie(default=None)):
    # If already logged in, redirect to dashboard
    if _check_admin_session(admin_token):
        return RedirectResponse(url="/")
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

    token = _sign_token(password)

    response = JSONResponse({"ok": True})
    response.set_cookie(
        "admin_token", token,
        httponly=True, samesite="lax", secure=True, max_age=86400 * 30,
    )
    return response


@app.post("/auth/logout")
async def logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie("admin_token")
    return response


@app.get("/api/admin/logs/stream")
async def proxy_log_stream(
    request: Request,
    admin_token: str | None = Cookie(default=None),
):
    """Proxy SSE log stream from backend. Requires admin session."""
    if not _check_admin_session(admin_token):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    url = f"{BACKEND_URL}/api/admin/logs/stream"
    headers = {}
    if ADMIN_SECRET:
        headers["x-admin-secret"] = ADMIN_SECRET

    async def stream_generator():
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("GET", url, headers=headers) as resp:
                async for line in resp.aiter_lines():
                    if await request.is_disconnected():
                        break
                    yield line + "\n"

    return StreamingResponse(
        stream_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


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
