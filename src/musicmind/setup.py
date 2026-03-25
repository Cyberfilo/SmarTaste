"""One-time Apple Music OAuth setup wizard.

Serves a local HTML page that uses MusicKit JS to obtain a Music User Token,
then saves it to ~/.config/musicmind/config.json.
"""

from __future__ import annotations

import json
import logging
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from musicmind.config import CONFIG_FILE, save_config

logger = logging.getLogger(__name__)
logger.addHandler(logging.StreamHandler(sys.stderr))

SETUP_PORT = 5555

OAUTH_HTML_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
    <title>MusicMind — Apple Music Authorization</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            display: flex; justify-content: center; align-items: center;
            min-height: 100vh; margin: 0;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
            color: #e0e0e0;
        }}
        .container {{
            text-align: center; padding: 3rem;
            background: rgba(255,255,255,0.05);
            border-radius: 20px; backdrop-filter: blur(10px);
            border: 1px solid rgba(255,255,255,0.1);
            max-width: 500px;
        }}
        h1 {{ color: #fa586a; margin-bottom: 0.5rem; }}
        p {{ color: #a0a0b0; line-height: 1.6; }}
        button {{
            background: #fa586a; color: white; border: none;
            padding: 14px 40px; font-size: 16px; border-radius: 25px;
            cursor: pointer; margin-top: 1rem; font-weight: 600;
            transition: all 0.2s;
        }}
        button:hover {{ background: #e8475a; transform: scale(1.02); }}
        button:disabled {{ background: #555; cursor: not-allowed; transform: none; }}
        #status {{ margin-top: 1.5rem; font-size: 14px; min-height: 20px; }}
        .success {{ color: #4ade80 !important; }}
        .error {{ color: #f87171 !important; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>MusicMind</h1>
        <p>Click below to authorize access to your Apple Music account.<br>
        This grants MusicMind read/write access to your library and listening history.</p>
        <button id="authBtn" onclick="authorize()">Authorize Apple Music</button>
        <p id="status"></p>
    </div>

    <script src="https://js-cdn.music.apple.com/musickit/v3/musickit.js"
            data-web-components
            crossorigin></script>
    <script>
        const DEVELOPER_TOKEN = "{developer_token}";

        async function authorize() {{
            const btn = document.getElementById('authBtn');
            const status = document.getElementById('status');
            btn.disabled = true;
            status.textContent = 'Initializing MusicKit...';
            status.className = '';

            try {{
                await MusicKit.configure({{
                    developerToken: DEVELOPER_TOKEN,
                    app: {{ name: 'MusicMind', build: '1.0.0' }}
                }});

                const music = MusicKit.getInstance();
                status.textContent = 'Waiting for Apple Music authorization...';
                const userToken = await music.authorize();

                status.textContent = 'Saving token...';
                const resp = await fetch('/callback', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ music_user_token: userToken }})
                }});

                if (resp.ok) {{
                    status.textContent = 'Authorization successful! You can close this tab.';
                    status.className = 'success';
                }} else {{
                    throw new Error('Failed to save token');
                }}
            }} catch (err) {{
                status.textContent = 'Error: ' + err.message;
                status.className = 'error';
                btn.disabled = false;
            }}
        }}
    </script>
</body>
</html>"""


class OAuthHandler(BaseHTTPRequestHandler):
    """HTTP handler for the local OAuth callback server."""

    developer_token: str = ""
    received_token: str | None = None

    def log_message(self, format: str, *args: object) -> None:
        """Route HTTP logs to stderr."""
        logger.info(format, *args)

    def do_GET(self) -> None:
        if self.path == "/" or self.path == "/index.html":
            html = OAUTH_HTML_TEMPLATE.format(developer_token=self.developer_token)
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html.encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:
        if self.path == "/callback":
            content_length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_length))
            token = body.get("music_user_token", "")

            if token:
                OAuthHandler.received_token = token
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok": true}')
            else:
                self.send_response(400)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()


def run_setup() -> None:
    """Run the interactive setup wizard."""
    print("=" * 50, file=sys.stderr)
    print("  MusicMind — Apple Music Setup Wizard", file=sys.stderr)
    print("=" * 50, file=sys.stderr)

    # Load or create config
    if CONFIG_FILE.exists():
        existing = json.loads(CONFIG_FILE.read_text())
        print(f"\nExisting config found at {CONFIG_FILE}", file=sys.stderr)
    else:
        existing = {}
        print(f"\nNo config found. Creating {CONFIG_FILE}", file=sys.stderr)

    # Prompt for missing fields
    team_id = existing.get("team_id") or input("Apple Developer Team ID: ").strip()
    key_id = existing.get("key_id") or input("Apple Music Key ID: ").strip()
    private_key_path = existing.get("private_key_path") or input(
        "Path to .p8 private key file: "
    ).strip()
    storefront = existing.get("storefront", "it")

    # Verify key file exists
    key_path = Path(private_key_path).expanduser()
    if not key_path.exists():
        print(f"\nERROR: Key file not found: {key_path}", file=sys.stderr)
        sys.exit(1)

    # Save config (without user token for now)
    config_data = {
        "team_id": team_id,
        "key_id": key_id,
        "private_key_path": str(key_path),
        "storefront": storefront,
        "music_user_token": existing.get("music_user_token", ""),
    }
    save_config(config_data)

    # Generate developer token for MusicKit JS
    from musicmind.auth import AuthManager
    from musicmind.config import MusicMindConfig

    cfg = MusicMindConfig(**config_data)
    auth = AuthManager(cfg)
    dev_token = auth.developer_token

    OAuthHandler.developer_token = dev_token

    # Start local server
    print(f"\nStarting OAuth server on http://localhost:{SETUP_PORT}", file=sys.stderr)
    print("Opening browser for Apple Music authorization...\n", file=sys.stderr)

    server = HTTPServer(("localhost", SETUP_PORT), OAuthHandler)
    webbrowser.open(f"http://localhost:{SETUP_PORT}")

    # Serve until we get the token
    while OAuthHandler.received_token is None:
        server.handle_request()

    server.server_close()

    # Save the user token
    config_data["music_user_token"] = OAuthHandler.received_token
    save_config(config_data)

    print("\nSetup complete!", file=sys.stderr)
    print(f"Music User Token saved to {CONFIG_FILE}", file=sys.stderr)
    print(
        "\nYou can now start the MCP server with: uv run python -m musicmind",
        file=sys.stderr,
    )


if __name__ == "__main__":
    run_setup()
