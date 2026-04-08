"""SmarTaste Admin Dashboard — standalone service.

Lightweight FastAPI app that:
1. Serves the admin dashboard HTML
2. Proxies API requests to the main SmarTaste backend
3. Handles auth via the same JWT cookies

Deployed on Railway at admin.music.menghi.dev.
"""

from __future__ import annotations

import os

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

BACKEND_URL = os.environ.get(
    "BACKEND_URL", "https://musicmind-production.up.railway.app"
)
NOCODB_URL = os.environ.get(
    "NOCODB_URL", "https://admin.music.menghi.dev"
)
PORT = int(os.environ.get("PORT", "8080"))

app = FastAPI(title="SmarTaste Admin")

# Serve static assets
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the admin dashboard HTML."""
    html_path = os.path.join(os.path.dirname(__file__), "templates", "dashboard.html")
    with open(html_path) as f:
        return HTMLResponse(content=f.read())


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    """Serve the admin login page."""
    html_path = os.path.join(os.path.dirname(__file__), "templates", "login.html")
    with open(html_path) as f:
        return HTMLResponse(content=f.read())


@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy_api(request: Request, path: str):
    """Proxy all /api/* requests to the main SmarTaste backend.

    Forwards cookies for auth, returns response as-is.
    """
    url = f"{BACKEND_URL}/api/{path}"
    headers = dict(request.headers)
    headers.pop("host", None)

    # Forward cookies
    cookies = dict(request.cookies)

    body = await request.body()

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.request(
            method=request.method,
            url=url,
            headers=headers,
            cookies=cookies,
            content=body if body else None,
            params=dict(request.query_params),
        )

    # Build response with forwarded cookies
    response = Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=dict(resp.headers),
    )

    # Forward set-cookie headers from backend
    for cookie_header in resp.headers.get_list("set-cookie"):
        response.headers.append("set-cookie", cookie_header)

    return response


@app.get("/nocodb")
async def nocodb_redirect():
    """Redirect to NocoDB."""
    return RedirectResponse(url=NOCODB_URL)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "smartaste-admin"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=PORT, reload=True)
