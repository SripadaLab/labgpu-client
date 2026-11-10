#!/usr/bin/env python3
"""labgpu: your personal on-demand GPU machine in the Sripada Lab AWS account.

  labgpu login     sign in (opens a page in your browser); lasts 12 hours
  labgpu up        wake your machine and wait until it's ready (~2 minutes)
  labgpu status    asleep or awake, time left, this month's cost
  labgpu down      put it to sleep now (your files are kept)
  labgpu hf-token  save your Hugging Face token on the machine (opens your browser)

Then use it like any server:  ssh labgpu 'nvidia-smi',  scp/rsync to labgpu:,
or ssh -L 8000:localhost:8000 labgpu for a web app on the machine.

Installed by install.sh into ~/.labgpu (see AGENT.md). Exit code 3 means
"not signed in": run `labgpu login` and ask the person to finish in the browser.
"""

import argparse
import base64
import datetime as dt
import hashlib
import html
import http.server
import json
import os
import pathlib
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

HOME = pathlib.Path(__file__).resolve().parent  # ~/.labgpu
CONFIG = json.loads((HOME / "config.json").read_text())
REGION = CONFIG["region"]
BASE = os.environ.get("LABGPU_BASE", "https://raw.githubusercontent.com/SripadaLab/labgpu-client/main")
TOKENS = HOME / "tokens.json"
CREDENTIALS = HOME / "credentials.json"
KEY = HOME / "id_ed25519"
PLUGIN = HOME / "bin" / "session-manager-plugin"
NOT_SIGNED_IN = 3


def fail(message, code=1):
    print(message, file=sys.stderr)
    sys.exit(code)


def write_private(path, data):
    """Write a file only you can read (tokens, credentials)."""
    path.touch(mode=0o600, exist_ok=True)
    os.chmod(path, 0o600)
    path.write_text(json.dumps(data))


# ---------------------------------------------------------------- sign-in
class CognitoError(Exception):
    pass


def cognito(service, action, body, region=None):
    """Call one of Cognito's public sign-in APIs (they need no AWS credentials)."""
    prefix = {"cognito-idp": "AWSCognitoIdentityProviderService",
              "cognito-identity": "AWSCognitoIdentityService"}[service]
    request = urllib.request.Request(
        f"https://{service}.{region or REGION}.amazonaws.com/", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/x-amz-json-1.1", "X-Amz-Target": f"{prefix}.{action}"})
    try:
        with urllib.request.urlopen(request, timeout=30) as reply:
            return json.loads(reply.read())
    except urllib.error.HTTPError as e:
        error = json.loads(e.read() or b"{}")
        raise CognitoError(error.get("message") or error.get("Message") or error.get("__type", "sign-in failed"))


def refresh_config():
    """Fetch the latest public sign-in IDs, so a move to another AWS account needs only a new sign-in."""
    global CONFIG, REGION
    try:
        with urllib.request.urlopen(f"{BASE}/config.json", timeout=15) as reply:
            new = json.loads(reply.read())
    except (OSError, ValueError):
        return
    needed = {"region", "user_pool_client_id", "user_pool_provider", "identity_pool_id", "usage_table"}
    if isinstance(new, dict) and needed <= new.keys() and new != CONFIG:
        (HOME / "config.json").write_text(json.dumps(new, indent=2) + "\n")
        CONFIG, REGION = new, new["region"]


def save_tokens(user, result, refresh_token=None, pool=None, client=None):
    write_private(TOKENS, {
        "pool": pool or CONFIG["user_pool_provider"],  # a sign-in from another account's pool doesn't count
        "client": client or CONFIG["user_pool_client_id"],
        "user": user,
        "id_token": result["IdToken"],
        "refresh_token": refresh_token or result["RefreshToken"],
        "expires_at": time.time() + result["ExpiresIn"],
    })


PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Lab GPU sign-in</title>
<style>
 body {{ font: 16px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f5f5f4;
        color: #1c1917; margin: 0; padding: 48px 16px; }}
 main {{ max-width: 420px; margin: auto; background: #fff; border-radius: 12px; padding: 28px 28px 20px;
        box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
 h1 {{ font-size: 20px; margin: 0 0 4px; }} p {{ margin: 8px 0 16px; color: #44403c; }}
 label {{ display: block; font-weight: 600; margin: 14px 0 4px; }}
 input {{ width: 100%; box-sizing: border-box; font-size: 17px; padding: 10px; border: 1px solid #d6d3d1;
         border-radius: 8px; }}
 button {{ margin-top: 20px; width: 100%; font-size: 17px; padding: 11px; border: 0; border-radius: 8px;
          background: #00274c; color: #fff; cursor: pointer; }}
 .err {{ background: #fef2f2; color: #991b1b; padding: 10px 12px; border-radius: 8px; }}
 a.umich {{ display: block; margin: 20px 0 6px; text-align: center; font-size: 17px; padding: 12px;
           border-radius: 8px; background: #00274c; color: #ffcb05; font-weight: 700; text-decoration: none; }}
 a.umich:hover {{ background: #00396e; }}
 details {{ margin-top: 22px; border-top: 1px solid #e7e5e4; padding-top: 14px; }}
 summary {{ cursor: pointer; color: #57534e; font-size: 15px; text-align: center; }}
 details button {{ background: #fff; color: #00274c; border: 1px solid #00274c; }}
 .hint {{ font-size: 14px; color: #78716c; }}
 .qr {{ text-align: center; margin: 12px 0; }} code {{ word-break: break-all; font-size: 13px; }}
 small {{ color: #78716c; }}
</style></head><body><main><h1>{title}</h1>{body}</main></body></html>"""


def login_page(title, body, error=None):
    if error:
        body = f'<p class="err">{html.escape(error)}</p>' + body
    return PAGE.format(title=html.escape(title), body=body)


def form(nonce, step, fields, button):
    return (f'<form method="post"><input type="hidden" name="nonce" value="{nonce}">'
            f'<input type="hidden" name="step" value="{step}">{fields}<button>{button}</button></form>')


def field(label, name, kind="text", value="", extra=""):
    return (f'<label for="{name}">{label}</label><input id="{name}" name="{name}" type="{kind}" '
            f'value="{html.escape(value)}" required {extra}>')


def claims_of(token):
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def region_of(pool):
    """The region in a pool name like cognito-idp.us-east-2.amazonaws.com/us-east-2_abc."""
    return pool.split(".")[1]


class LoginFlow:
    """The sign-in steps, driven by a small page served on 127.0.0.1 only.

    Two ways in: "Sign in with U-M" (U-M Okta through the lab's shared pool at
    login.cap-study.com; the browser comes back to localhost:<port>/callback), or
    the lab GPU password plus an authenticator code (for people without U-M login).
    """

    def __init__(self):
        self.nonce = secrets.token_urlsafe(16)
        self.client = CONFIG["user_pool_client_id"]
        self.user = json.loads(TOKENS.read_text()).get("user", "") if TOKENS.exists() else ""
        self.session = None
        self.done = threading.Event()
        self.umich = CONFIG.get("umich")
        self.ports = self.umich["callback_ports"] if self.umich else []
        self.port = None  # set by serve_page
        self.verifier = secrets.token_urlsafe(48)

    def redirect_uri(self):
        return f"http://localhost:{self.port}/callback"

    def umich_url(self):
        challenge = base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest()).rstrip(b"=").decode()
        return self.umich["domain"] + "/oauth2/authorize?" + urllib.parse.urlencode({
            "response_type": "code", "client_id": self.umich["client_id"], "redirect_uri": self.redirect_uri(),
            "scope": "openid email profile", "state": self.nonce, "identity_provider": "UMich",
            "code_challenge": challenge, "code_challenge_method": "S256"})

    def start_page(self, error=None, password_open=False):
        password = form(self.nonce, "password",
                        field("Uniqname", "user", value=self.user, extra="autocapitalize=off") +
                        field("Lab GPU password", "password", "password") +
                        ("" if self.user else '<p class="hint">First time? Use the temporary password from the '
                         'email “Your Sripada Lab GPU account”.</p>'), "Continue")
        if not (self.umich and self.port in self.ports):
            return login_page("Sign in to your lab GPU", password, error)
        was_password = TOKENS.exists() and json.loads(TOKENS.read_text()).get("pool") == CONFIG["user_pool_provider"]
        opened = " open" if password_open or was_password else ""
        return login_page("Sign in to your lab GPU",
                          f'<a class="umich" href="{html.escape(self.umich_url())}">Sign in with U-M</a>'
                          f"<details{opened}><summary>No U-M login? Use a lab GPU password</summary>{password}"
                          "</details>", error)

    def code_page(self, error=None):
        return login_page("Enter your code", "<p>Open your authenticator app (for example Okta Verify or Duo Mobile) and type "
                          "the 6-digit code for <b>SripadaLab-GPU</b>.</p>" + form(
                              self.nonce, "code", field("6-digit code", "code", extra=
                                                        'inputmode="numeric" autocomplete="one-time-code" autofocus'),
                              "Sign in"), error)

    def new_password_page(self, error=None):
        return login_page("Choose your own password", "<p>This is your first sign-in. Pick a password with at "
                          "least 12 characters, including an upper-case letter, a lower-case letter and a "
                          "number.</p>" + form(
                              self.nonce, "newpassword",
                              field("New password", "new", "password", extra="autofocus") +
                              field("New password again", "again", "password"), "Save password"), error)

    def mfa_setup_page(self, error=None):
        import segno
        setup = cognito("cognito-idp", "AssociateSoftwareToken", {"Session": self.session})
        self.session = setup["Session"]
        secret = setup["SecretCode"]
        link = f"otpauth://totp/SripadaLab-GPU:{self.user}?secret={secret}&issuer=SripadaLab-GPU"
        qr = segno.make(link, error="m").svg_inline(scale=6)
        return login_page("Set up your authenticator", "<p>Open your authenticator app (Okta Verify, Duo "
                          "Mobile, Google Authenticator, ...), choose <b>add account</b>, and scan this code:</p>"
                          f'<div class="qr">{qr}</div><p><small>Can\'t scan? Enter this key instead: '
                          f"<code>{secret}</code></small></p>" + form(
                              self.nonce, "mfasetup", field("6-digit code shown in the app", "code", extra=
                                                            'inputmode="numeric" autocomplete="one-time-code"'),
                              "Finish setup"), error)

    def callback(self, query):
        """The browser is back from U-M login with a one-time code: trade it for tokens."""
        if query.get("state", [""])[0] != self.nonce:
            return login_page("Sign-in problem", "<p>This sign-in link is out of date. Run <code>labgpu login</code> "
                              "again.</p>")
        if "code" not in query:
            reason = query.get("error_description", query.get("error", ["unknown"]))[0]
            return self.start_page(f"U-M sign-in didn't finish ({reason}). Try again, or ask the lab admin.")
        body = urllib.parse.urlencode({
            "grant_type": "authorization_code", "client_id": self.umich["client_id"], "code": query["code"][0],
            "redirect_uri": self.redirect_uri(), "code_verifier": self.verifier}).encode()
        try:
            with urllib.request.urlopen(urllib.request.Request(
                    self.umich["domain"] + "/oauth2/token", body,
                    {"Content-Type": "application/x-www-form-urlencoded"}), timeout=30) as reply:
                tokens = json.loads(reply.read())
        except (OSError, ValueError) as e:
            return self.start_page(f"U-M sign-in didn't finish ({e}). Try again, or ask the lab admin.")
        self.user = claims_of(tokens["id_token"]).get("custom:uniqname", "")
        save_tokens(self.user, {"IdToken": tokens["id_token"], "RefreshToken": tokens["refresh_token"],
                                "ExpiresIn": tokens["expires_in"]},
                    pool=self.umich["provider"], client=self.umich["client_id"])
        return self.finish()

    def finish(self):
        CREDENTIALS.unlink(missing_ok=True)
        try:
            aws_credentials()
        except SystemExit:
            TOKENS.unlink(missing_ok=True)
            return login_page("Sign-in problem", "<p>You signed in, but the lab GPU account didn't accept it. "
                              "Please tell the lab admin (Aman Taxali, ataxali@umich.edu).</p>")
        self.done.set()
        return login_page("You're signed in", "<p>All set for the next 12 hours. You can close this tab "
                          "and go back to your agent.</p>")

    def next_page(self, reply):
        """Show whatever Cognito asks for next, or finish."""
        if "AuthenticationResult" in reply:
            result = reply["AuthenticationResult"]
            # Make the authenticator this account's MFA method. Without this,
            # Cognito can offer a fresh authenticator setup to anyone with the password.
            cognito("cognito-idp", "SetUserMFAPreference", {
                "AccessToken": result["AccessToken"],
                "SoftwareTokenMfaSettings": {"Enabled": True, "PreferredMfa": True}})
            save_tokens(self.user, result)
            return self.finish()
        self.session = reply["Session"]
        return {"SOFTWARE_TOKEN_MFA": self.code_page, "NEW_PASSWORD_REQUIRED": self.new_password_page,
                "MFA_SETUP": self.mfa_setup_page}.get(reply["ChallengeName"], lambda: login_page(
                    "Sign-in problem", f"<p>Unexpected step {html.escape(reply['ChallengeName'])}. "
                    "Please tell the lab admin.</p>"))()

    def respond(self, challenge, answers):
        return cognito("cognito-idp", "RespondToAuthChallenge", {
            "ClientId": self.client, "ChallengeName": challenge, "Session": self.session,
            "ChallengeResponses": {"USERNAME": self.user, **answers}})

    def handle(self, form_data):
        step = form_data.get("step")
        try:
            if step == "password":
                self.user = form_data.get("user", "").strip().lower()
                return self.next_page(cognito("cognito-idp", "InitiateAuth", {
                    "AuthFlow": "USER_PASSWORD_AUTH", "ClientId": self.client,
                    "AuthParameters": {"USERNAME": self.user, "PASSWORD": form_data.get("password", "")}}))
            if step == "newpassword":
                if form_data.get("new") != form_data.get("again"):
                    return self.new_password_page("The two passwords are different. Please try again.")
                return self.next_page(self.respond("NEW_PASSWORD_REQUIRED", {"NEW_PASSWORD": form_data["new"]}))
            if step == "mfasetup":
                verified = cognito("cognito-idp", "VerifySoftwareToken", {
                    "Session": self.session, "UserCode": form_data.get("code", "").strip(),
                    "FriendlyDeviceName": "labgpu"})
                self.session = verified["Session"]
                return self.next_page(self.respond("MFA_SETUP", {}))
            if step == "code":
                return self.next_page(self.respond("SOFTWARE_TOKEN_MFA",
                                                   {"SOFTWARE_TOKEN_MFA_CODE": form_data.get("code", "").strip()}))
        except CognitoError as e:
            message = str(e)
            if step == "code" and "session" not in message.lower():
                return self.code_page(f"That code didn't work ({message}). Wait for a new code and try again.")
            if step == "newpassword":
                return self.new_password_page(message)
            lowered = message.lower()
            if "temporary password has expired" in lowered:
                message = "Your temporary password has expired (it lasts 7 days). Ask the lab admin for a new invite."
            elif "attempts exceeded" in lowered:
                message = "Too many tries. Please wait 15 minutes, then try again."
            elif "incorrect username or password" in lowered:
                message = ("That uniqname and password don't match. First sign-in? Paste the temporary password "
                           "from the email exactly, with no spaces before or after.")
            return self.start_page(message, password_open=True)
        return self.start_page()


class HfTokenFlow:
    """One page: paste a Hugging Face token; it goes straight to the GPU machine."""

    def __init__(self):
        self.nonce = secrets.token_urlsafe(16)
        self.done = threading.Event()
        self.user = ""

    def start_page(self, error=None):
        return login_page("Add your Hugging Face token", "<p>Some models (like Gemma or Llama) need your own "
                          "Hugging Face token. On huggingface.co, open <b>Settings → Access Tokens</b>, create a "
                          "token with <b>Read</b> access, and paste it here. It is saved only on your lab GPU "
                          "machine.</p>" + form(self.nonce, "token", field(
                              "Hugging Face token", "token", "password", extra="autofocus"), "Save on my GPU machine"),
                          error)

    def handle(self, form_data):
        token = form_data.get("token", "").strip()
        if not token.startswith("hf_"):
            return self.start_page("That doesn't look like a Hugging Face token (they start with hf_).")
        result = subprocess.run(
            ["ssh", "labgpu", "mkdir -p ~/.cache/huggingface && umask 077 && cat > ~/.cache/huggingface/token"],
            input=token, text=True, capture_output=True)
        if result.returncode != 0:
            return self.start_page("Couldn't reach your GPU machine. Make sure it's awake (labgpu up) and try again.")
        self.done.set()
        return login_page("Token saved", "<p>Your token is saved on your GPU machine. You can close this tab and go "
                          "back to your agent.</p>")


def serve_page(flow, intro):
    """Serve a flow's pages on 127.0.0.1 and open the browser; return when the flow is done."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send_page(self, text):
            body = text.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            url = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(url.query)
            if url.path == "/callback" and hasattr(flow, "callback"):
                self.send_page(flow.callback(query))  # checks its own state value
                return
            if query.get("k", [""])[0] != flow.nonce:
                self.send_error(404)
                return
            self.send_page(flow.start_page())

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            data = {k: v[0] for k, v in urllib.parse.parse_qs(self.rfile.read(length).decode()).items()}
            if data.get("nonce") != flow.nonce:
                self.send_error(403)
                return
            self.send_page(flow.handle(data))

    # U-M sign-in can only come back to the registered ports; otherwise any free port
    for port in [*getattr(flow, "ports", []), 0]:
        try:
            server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            continue
    flow.port = server.server_address[1]
    url = f"http://127.0.0.1:{flow.port}/?k={flow.nonce}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(intro)
    print(f"If no page opened, open this link: {url}", flush=True)
    webbrowser.open(url)
    finished = flow.done.wait(timeout=15 * 60)
    time.sleep(1)  # let the browser receive the final page
    server.shutdown()
    return finished


def cmd_login(args):
    refresh_config()
    flow = LoginFlow()
    if not serve_page(flow, "A sign-in page is opening in the browser. "
                            "Finish signing in there (U-M login, or lab GPU password + authenticator code)."):
        fail("Sign-in wasn't finished within 15 minutes. Run `labgpu login` again.", NOT_SIGNED_IN)
    CREDENTIALS.unlink(missing_ok=True)
    print(f"Signed in as {flow.user} for 12 hours.")


def cmd_hf_token(args):
    if machine()["State"]["Name"] != "running":
        fail("The GPU machine is asleep. Run `labgpu up` first.")
    if not serve_page(HfTokenFlow(), "A page is opening in the browser for the Hugging Face token."):
        fail("The token wasn't saved within 15 minutes. Run `labgpu hf-token` again.")
    print("Hugging Face token saved on the GPU machine (~/.cache/huggingface/token).")


def id_token():
    """A valid ID token, refreshed from the 12-hour refresh token when needed."""
    if not TOKENS.exists():
        fail("Not signed in. Run `labgpu login` (the person finishes it in their browser).", NOT_SIGNED_IN)
    tokens = json.loads(TOKENS.read_text())
    if tokens.get("pool") not in (CONFIG["user_pool_provider"], (CONFIG.get("umich") or {}).get("provider")):
        fail("Not signed in to the current lab GPU account. Run `labgpu login` (the person finishes it in their "
             "browser).", NOT_SIGNED_IN)
    if tokens["expires_at"] - 300 < time.time():
        try:
            result = cognito("cognito-idp", "InitiateAuth", {
                "AuthFlow": "REFRESH_TOKEN_AUTH", "ClientId": tokens.get("client", CONFIG["user_pool_client_id"]),
                "AuthParameters": {"REFRESH_TOKEN": tokens["refresh_token"]}},
                region=region_of(tokens["pool"]))["AuthenticationResult"]
        except CognitoError:
            fail("Sign-in expired (it lasts 12 hours). Run `labgpu login` (the person finishes it in their browser).",
                 NOT_SIGNED_IN)
        save_tokens(tokens["user"], result, refresh_token=tokens["refresh_token"], pool=tokens["pool"],
                    client=tokens.get("client"))
        tokens = json.loads(TOKENS.read_text())
    return tokens["id_token"]


def owner():
    """Your uniqname: set by the lab admin on a password account, or from U-M login."""
    claims = claims_of(id_token())
    return claims.get("custom:owner") or claims["custom:uniqname"]


def aws_credentials():
    """1-hour AWS credentials that only work on your own machine and S3 folder."""
    if CREDENTIALS.exists():
        cached = json.loads(CREDENTIALS.read_text())
        if (cached.get("IdentityPool") == CONFIG["identity_pool_id"]
                and dt.datetime.fromisoformat(cached["Expiration"]).timestamp() - 300 > time.time()):
            return cached
    token = id_token()
    logins = {json.loads(TOKENS.read_text())["pool"]: token}
    try:
        identity = cognito("cognito-identity", "GetId", {"IdentityPoolId": CONFIG["identity_pool_id"],
                                                         "Logins": logins})
        raw = cognito("cognito-identity", "GetCredentialsForIdentity",
                      {"IdentityId": identity["IdentityId"], "Logins": logins})["Credentials"]
    except CognitoError as e:
        fail(f"Couldn't get access ({e}). Run `labgpu login`, or ask the lab admin.", NOT_SIGNED_IN)
    credentials = {
        "Version": 1,
        "AccessKeyId": raw["AccessKeyId"],
        "SecretAccessKey": raw["SecretKey"],
        "SessionToken": raw["SessionToken"],
        "Expiration": dt.datetime.fromtimestamp(raw["Expiration"], dt.timezone.utc).isoformat(),
        "IdentityPool": CONFIG["identity_pool_id"],
    }
    write_private(CREDENTIALS, credentials)
    return credentials


def cmd_credentials(args):
    """For the AWS CLI, if you use it ("credential_process" in profile labgpu)."""
    print(json.dumps({k: v for k, v in aws_credentials().items() if k != "IdentityPool"}))


# ---------------------------------------------------------------- the machine
def aws(service):
    import boto3
    c = aws_credentials()
    return boto3.client(service, region_name=REGION, aws_access_key_id=c["AccessKeyId"],
                        aws_secret_access_key=c["SecretAccessKey"], aws_session_token=c["SessionToken"])


def machine():
    me = owner()
    reply = aws("ec2").describe_instances(Filters=[
        {"Name": "tag:labgpu-owner", "Values": [me]},
        {"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]}])
    instances = [i for r in reply["Reservations"] for i in r["Instances"]]
    if not instances:
        fail(f"No GPU machine found for {me}. Ask the lab admin.")
    m = instances[0]
    m["tags"] = {t["Key"]: t["Value"] for t in m.get("Tags", [])}
    return m


def usage(m):
    """This month's GPU hours and cost so far (the guard updates it every 5 minutes while awake)."""
    month = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m")
    item = aws("dynamodb").get_item(TableName=CONFIG["usage_table"], Key={
        "owner": {"S": m["tags"]["labgpu-owner"]}, "month": {"S": month}}).get("Item", {})
    hours = float(item.get("seconds", {"N": "0"})["N"]) / 3600
    disk = (float(m["tags"]["labgpu-disk-gb"]) * 0.08 if "labgpu-disk-gb" in m["tags"]
            else CONFIG["disk_monthly_usd"])
    cost = float(item.get("cost", {"N": str(disk)})["N"])
    return hours, cost


def ssm_online(instance_id, since):
    info = aws("ssm").describe_instance_information(
        Filters=[{"Key": "InstanceIds", "Values": [instance_id]}])["InstanceInformationList"]
    return bool(info) and info[0]["PingStatus"] == "Online" and info[0]["LastPingDateTime"] >= since


def cmd_status(args):
    m = machine()
    hours, cost = usage(m)
    gpu = m["tags"].get("labgpu-gpu", "Tesla T4 16 GB")
    cap = float(m["tags"].get("labgpu-cap-usd", CONFIG["monthly_cap_usd"]))
    state = {"running": "awake", "stopped": "asleep", "pending": "waking up",
             "stopping": "going to sleep"}.get(m["State"]["Name"], m["State"]["Name"])
    print(f"Your GPU machine ({gpu}) is {state}.")
    if m["State"]["Name"] == "running":
        left = CONFIG["max_hours"] * 3600 - (dt.datetime.now(dt.timezone.utc) - m["LaunchTime"]).total_seconds()
        print(f"  It stops in at most {max(0, left) / 60:.0f} minutes (sooner after 30 idle minutes).")
    print(f"This month: {hours:.1f} GPU hours, about ${cost:.2f} of the ${cap:.0f} limit.")
    if "labgpu-blocked" in m["tags"]:
        print("  The monthly limit is used up, so the machine can't start until next month. Ask the lab admin.")


def cmd_up(args):
    m = machine()
    if "labgpu-blocked" in m["tags"]:
        fail("The monthly limit is used up, so the machine can't start until next month. Ask the lab admin.")
    ec2 = aws("ec2")
    instance_id, state = m["InstanceId"], m["State"]["Name"]
    if state == "stopping":
        print("It's going to sleep; waiting before waking it again...", flush=True)
        ec2.get_waiter("instance_stopped").wait(InstanceIds=[instance_id])
        state = "stopped"
    if state == "stopped":
        ec2.start_instances(InstanceIds=[instance_id])
        print("Waking your GPU machine (about 2 minutes)...", flush=True)
        started = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1)
    else:
        started = m["LaunchTime"]
    deadline = time.time() + 10 * 60
    while time.time() < deadline:
        if machine()["State"]["Name"] == "running" and ssm_online(instance_id, started):
            print(f"Ready. Use it with:  ssh labgpu '<command>'   (it stops after 30 idle minutes, "
                  f"and {CONFIG['max_hours']:g} hours after it was woken)")
            return
        time.sleep(10)
    fail("The machine didn't come up within 10 minutes. Try `labgpu status`, then ask the lab admin.")


def cmd_down(args):
    m = machine()
    if m["State"]["Name"] in ("stopped", "stopping"):
        print("Already asleep.")
        return
    aws("ec2").stop_instances(InstanceIds=[m["InstanceId"]])
    print("Going to sleep. Files on the machine are kept; `labgpu up` wakes it again.")


def cmd_ssh_proxy(args):
    """Used by SSH (ProxyCommand in Host labgpu): one-minute key, then SSH over Session Manager."""
    m = machine()
    if m["State"]["Name"] != "running":
        fail("The GPU machine is asleep. Run: labgpu up")
    # A new machine (recreated, or moved to another account) has a new host key: forget the old one
    known = HOME / "known_instance"
    if not known.exists() or known.read_text() != m["InstanceId"]:
        (HOME / "known_hosts").unlink(missing_ok=True)
        known.write_text(m["InstanceId"])
    aws("ec2-instance-connect").send_ssh_public_key(
        InstanceId=m["InstanceId"], InstanceOSUser="ubuntu", SSHPublicKey=(HOME / "id_ed25519.pub").read_text())
    ssm = aws("ssm")
    request = {"Target": m["InstanceId"], "DocumentName": "AWS-StartSSHSession", "Parameters": {"portNumber": ["22"]}}
    session = ssm.start_session(**request)
    # Same hand-off the AWS CLI does: the plugin carries the SSH bytes over the session
    os.execv(str(PLUGIN), [str(PLUGIN),
                           json.dumps({k: session[k] for k in ("SessionId", "TokenValue", "StreamUrl")}),
                           REGION, "StartSession", "", json.dumps(request), ssm.meta.endpoint_url])


# ---------------------------------------------------------------- one-time setup
def replace_block(path, start, block):
    """Put `block` in the file, replacing an earlier block that begins with `start`."""
    lines = path.read_text().splitlines(keepends=True) if path.exists() else []
    kept, skipping = [], False
    for line in lines:
        if line.rstrip("\n") == start:
            skipping = True
            continue
        if skipping and (line.startswith("[") or line.startswith("Host ") or line.startswith("Match ")):
            skipping = False
        if not skipping:
            kept.append(line)
    text = "".join(kept).rstrip("\n")
    path.write_text((text + "\n\n" if text else "") + block)


def cmd_setup(args):
    """Called by install.sh: SSH key, SSH host "labgpu", AWS profile "labgpu"."""
    if not KEY.exists():
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "labgpu", "-f", str(KEY)], check=True)
    launcher = HOME / "bin" / "labgpu"
    ssh_dir = pathlib.Path.home() / ".ssh"
    ssh_dir.mkdir(mode=0o700, exist_ok=True)
    replace_block(ssh_dir / "config", "Host labgpu",
                  f"Host labgpu\n  User ubuntu\n  IdentityFile {KEY}\n  IdentitiesOnly yes\n"
                  f'  ProxyCommand "{launcher}" ssh-proxy\n  StrictHostKeyChecking accept-new\n'
                  f"  UserKnownHostsFile {HOME / 'known_hosts'}\n  ServerAliveInterval 60\n")
    aws_dir = pathlib.Path.home() / ".aws"
    aws_dir.mkdir(exist_ok=True)
    replace_block(aws_dir / "config", "[profile labgpu]",
                  f'[profile labgpu]\nregion = {REGION}\ncredential_process = "{launcher}" credentials\n')


def main():
    parser = argparse.ArgumentParser(prog="labgpu", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("login", help="sign in (opens your browser); lasts 12 hours")
    sub.add_parser("up", help="wake your machine")
    sub.add_parser("status", help="state, time left and this month's cost")
    sub.add_parser("down", help="put it to sleep now")
    sub.add_parser("hf-token", help="save your Hugging Face token on the machine (opens your browser)")
    for hidden in ("setup", "credentials", "ssh-proxy"):
        sub.add_parser(hidden, help=argparse.SUPPRESS)
    args = parser.parse_args()
    {"setup": cmd_setup, "login": cmd_login, "up": cmd_up, "status": cmd_status, "down": cmd_down,
     "hf-token": cmd_hf_token, "credentials": cmd_credentials, "ssh-proxy": cmd_ssh_proxy}[args.command](args)


if __name__ == "__main__":
    main()
