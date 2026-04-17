"""SmarTaste Admin Dashboard — standalone service.

Lightweight FastAPI app that:
1. Serves the React admin dashboard (static bundle built from admin/ui/)
2. Proxies /api/* to the main SmarTaste backend with x-admin-secret injected
3. Gates both with a single-password HMAC cookie (survives restarts)

Deployed on Railway at admin.music.menghi.dev.
"""

from __future__ import annotations

import hashlib
import hmac
import os

import httpx
from fastapi import Cookie, FastAPI, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import StreamingResponse

BACKEND_URL = os.environ.get(
    "BACKEND_URL", "https://musicmind-production.up.railway.app"
)
NOCODB_URL = os.environ.get(
    "NOCODB_URL", "https://dbmanager.music.menghi.dev"
)
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "")
PORT = int(os.environ.get("PORT", "8080"))

_COOKIE_KEY = (
    hashlib.sha256(f"staste-admin-{ADMIN_PASSWORD}".encode()).hexdigest()
    if ADMIN_PASSWORD
    else ""
)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
NEXT_ASSETS_DIR = os.path.join(STATIC_DIR, "_next")
INDEX_HTML = os.path.join(STATIC_DIR, "index.html")

app = FastAPI(title="SmarTaste Admin")

# Next.js hashed bundle — safe to serve unauthenticated (no data, shell only)
if os.path.isdir(NEXT_ASSETS_DIR):
    app.mount(
        "/_next",
        StaticFiles(directory=NEXT_ASSETS_DIR),
        name="next-assets",
    )


def _sign_token(password: str) -> str:
    return hmac.new(
        _COOKIE_KEY.encode(), password.encode(), hashlib.sha256,
    ).hexdigest()


def _check_admin_session(admin_token: str | None) -> bool:
    if not admin_token or not ADMIN_PASSWORD or not _COOKIE_KEY:
        return False
    expected = _sign_token(ADMIN_PASSWORD)
    return hmac.compare_digest(admin_token, expected)


@app.get("/")
async def index(admin_token: str | None = Cookie(default=None)):
    if not _check_admin_session(admin_token):
        return RedirectResponse(url="/login")
    if not os.path.isfile(INDEX_HTML):
        return HTMLResponse(
            "<h1>Admin UI bundle missing</h1>"
            "<p>static/index.html not found — the Node build stage may have failed.</p>",
            status_code=500,
        )
    return FileResponse(INDEX_HTML)


@app.get("/login", response_class=HTMLResponse)
async def login_page(admin_token: str | None = Cookie(default=None)):
    if _check_admin_session(admin_token):
        return RedirectResponse(url="/")
    html_path = os.path.join(os.path.dirname(__file__), "templates", "login.html")
    with open(html_path) as f:
        return HTMLResponse(content=f.read())


@app.post("/auth/login")
async def login(request: Request):
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


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    p = os.path.join(STATIC_DIR, "favicon.ico")
    if os.path.isfile(p):
        return FileResponse(p)
    return Response(status_code=204)


@app.get("/api/admin/logs/stream")
async def proxy_log_stream(
    request: Request,
    admin_token: str | None = Cookie(default=None),
):
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
