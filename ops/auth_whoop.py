#!/usr/bin/env python3

import json
import os
import re
import secrets
import sys
import tempfile
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "src" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from utils import get_state_root, load_project_dotenv


ENV_FILE = PROJECT_ROOT / ".env"
REDIRECT_URI = "http://localhost:8888/callback"
AUTH_URL = "https://api.prod.whoop.com/oauth/oauth2/auth"
TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
SCOPES = "offline read:recovery read:cycles read:sleep read:profile read:workout read:body_measurement"

auth_code = None
expected_oauth_state = None

load_project_dotenv()


def get_env_var(var_name):
    env_value = (os.getenv(var_name) or "").strip()
    if env_value:
        return env_value
    if not ENV_FILE.exists():
        return None
    content = ENV_FILE.read_text(encoding="utf-8")
    match = re.search(f"^{var_name}=(.*)$", content, re.MULTILINE)
    if match:
        return match.group(1).strip()
    return None


CLIENT_ID = get_env_var("WHOOP_CLIENT_ID")
CLIENT_SECRET = get_env_var("WHOOP_CLIENT_SECRET")


class OAuthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        parsed_path = urllib.parse.urlparse(self.path)

        if parsed_path.path == "/callback":
            query = urllib.parse.parse_qs(parsed_path.query)
            state = query.get("state", [""])[0]
            if "code" in query and is_valid_oauth_state(state):
                auth_code = query["code"][0]
                self.send_response(200)
                self.send_header("Content-type", "text/html")
                self.end_headers()
                self.wfile.write(
                    b"<html><body><h1>Successfully authenticated!</h1><p>You can close this window and return to the terminal.</p></body></html>"
                )
            else:
                self.send_response(400)
                self.send_header("Content-type", "text/html")
                self.end_headers()
                self.wfile.write(b"<html><body><h1>Authentication failed.</h1><p>Invalid OAuth response.</p></body></html>")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


def seed_private_token_state(access_token, refresh_token, expires_in):
    state_file = get_state_root() / "whoop_token_state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)

    now = datetime.now()
    state = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_expires_at": (now + timedelta(seconds=int(expires_in or 3600))).isoformat(),
        "last_refresh_at": now.isoformat(),
        "last_refresh_attempt_at": now.isoformat(),
        "last_refresh_attempt_result": "success: seeded by auth_whoop bootstrap",
        "access_token_prefix": access_token[:20] + "..." if access_token else None,
        "refresh_token_prefix": refresh_token[:20] + "..." if refresh_token else None,
    }
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=state_file.parent,
            delete=False,
        ) as handle:
            os.chmod(handle.name, 0o600)
            json.dump(state, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
            tmp_path = handle.name
        os.replace(tmp_path, state_file)
        os.chmod(state_file, 0o600)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    print(f"WHOOP token state seeded at {state_file}")


def is_valid_oauth_state(state):
    return bool(state and expected_oauth_state and secrets.compare_digest(state, expected_oauth_state))


def main():
    global expected_oauth_state
    if not CLIENT_ID or not CLIENT_SECRET:
        print("Could not find WHOOP_CLIENT_ID or WHOOP_CLIENT_SECRET in .env file.")
        return

    server = HTTPServer(("localhost", 8888), OAuthHandler)
    expected_oauth_state = secrets.token_urlsafe(32)

    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "state": expected_oauth_state,
    }
    url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"

    print(f"Opening browser to authorize WHOOP...\nIf the browser doesn't open, visit this URL:\n{url}\n")
    webbrowser.open(url)

    print("Waiting for you to authorize the application...")
    while auth_code is None:
        server.handle_request()

    print("Authorization code received. Exchanging for tokens...")

    data = urllib.parse.urlencode(
        {
            "grant_type": "authorization_code",
            "code": auth_code,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "redirect_uri": REDIRECT_URI,
        }
    ).encode("utf-8")

    req = urllib.request.Request(TOKEN_URL, data=data)
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header(
        "User-Agent",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    )
    req.add_header("Accept", "application/json")
    req.add_header("Accept-Language", "en-US,en;q=0.9")

    try:
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read().decode("utf-8"))
            access_token = result.get("access_token")
            refresh_token = result.get("refresh_token")
            expires_in = result.get("expires_in", 3600)

            if access_token and refresh_token:
                seed_private_token_state(access_token, refresh_token, expires_in)
            else:
                print("Failed to parse tokens from response:")
                print(result)
    except urllib.error.HTTPError as error:
        print(f"HTTP error exchanging token: {error.code}")
        print(error.read().decode("utf-8"))
    except Exception as error:
        print(f"Error exchanging token: {error}")


if __name__ == "__main__":
    main()
