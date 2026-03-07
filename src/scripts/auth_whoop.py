import os
import urllib.parse
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.request
import json
from datetime import datetime, timedelta
from pathlib import Path
import re
from utils import get_state_root

# Load existing environment to get client ID & Secret implicitly or declare them here
# We'll parse the .env file directly to be safe
ENV_FILE = Path(__file__).parent.parent.parent / '.env'

def get_env_var(var_name):
    if not ENV_FILE.exists():
        return None
    content = ENV_FILE.read_text()
    match = re.search(f"^{var_name}=(.*)$", content, re.MULTILINE)
    if match:
        return match.group(1).strip()
    return None

CLIENT_ID = get_env_var("WHOOP_CLIENT_ID")
CLIENT_SECRET = get_env_var("WHOOP_CLIENT_SECRET")
REDIRECT_URI = "http://localhost:8888/callback"
AUTH_URL = "https://api.prod.whoop.com/oauth/oauth2/auth"
TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
SCOPES = "offline read:recovery read:cycles read:sleep read:profile read:workout read:body_measurement"

# Global to store the received code
auth_code = None

class OAuthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        parsed_path = urllib.parse.urlparse(self.path)
        
        if parsed_path.path == '/callback':
            query = urllib.parse.parse_qs(parsed_path.query)
            if 'code' in query:
                auth_code = query['code'][0]
                self.send_response(200)
                self.send_header('Content-type', 'text/html')
                self.end_headers()
                self.wfile.write(b"<html><body><h1>Successfully authenticated!</h1><p>You can close this window and return to the terminal.</p></body></html>")
            else:
                self.send_response(400)
                self.send_header('Content-type', 'text/html')
                self.end_headers()
                self.wfile.write(b"<html><body><h1>Authentication failed. No code found.</h1></body></html>")
        else:
            self.send_response(404)
            self.end_headers()
            
    def log_message(self, format, *args):
        pass # Suppress logging

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
    state_file.write_text(json.dumps(state, indent=2))
    print(f"✅ WHOOP token state seeded at {state_file}")

def main():
    if not CLIENT_ID or not CLIENT_SECRET:
        print("❌ Could not find WHOOP_CLIENT_ID or WHOOP_CLIENT_SECRET in .env file.")
        return

    # 1. Start local server
    server = HTTPServer(('localhost', 8888), OAuthHandler)
    
    # 2. Build Authorization URL
    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "state": "random_secure_string_123"
    }
    url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"
    
    # 3. Open browser
    print(f"Opening browser to authorize WHOOP...\nIf the browser doesn't open, visit this URL:\n{url}\n")
    webbrowser.open(url)
    
    # 4. Wait for the callback
    print("Waiting for you to authorize the application...")
    while auth_code is None:
        server.handle_request()
        
    print(f"✅ Authorization code received! Exchanging for tokens...")
    
    # 5. Exchange code for tokens
    data = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": auth_code,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI
    }).encode('utf-8')
    
    req = urllib.request.Request(TOKEN_URL, data=data)
    req.add_header('Content-Type', 'application/x-www-form-urlencoded')
    req.add_header('User-Agent', 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
    req.add_header('Accept', 'application/json')
    req.add_header('Accept-Language', 'en-US,en;q=0.9')
    
    try:
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read().decode('utf-8'))
            access_token = result.get('access_token')
            refresh_token = result.get('refresh_token')
            expires_in = result.get('expires_in', 3600)
            
            if access_token and refresh_token:
                seed_private_token_state(access_token, refresh_token, expires_in)
            else:
                print("❌ Failed to parse tokens from response:")
                print(result)
    except urllib.error.HTTPError as e:
        print(f"❌ HTTP Error exchanging token: {e.code}")
        print(e.read().decode('utf-8'))
    except Exception as e:
        print(f"❌ Error exchanging token: {e}")

if __name__ == "__main__":
    main()
