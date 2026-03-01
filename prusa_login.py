#!/usr/bin/env python3
"""
prusa_login.py — Authenticate with Prusa account for PrusaConnect uploads.

Runs an OAuth2 PKCE login in the browser, lets you pick a target printer,
and saves credentials to ~/.config/prusa_calibration/tokens.json.

pa_calibration.py and temperature_tower.py read that file when
--prusaconnect or --prusaconnect-print is passed.

Usage:
    python3 prusa_login.py              # log in and pick a printer
    python3 prusa_login.py --status     # show saved token / printer info
    python3 prusa_login.py --logout     # remove saved tokens
    python3 prusa_login.py --refresh    # force-refresh the access token now

Works with 2FA (TOTP / Google Authenticator / SMS) — authentication happens
entirely inside the browser; this script never sees your password or OTP codes.
"""

import argparse
import base64
import hashlib
import json
import pathlib
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

# ── OAuth2 / API constants (from PrusaSlicer ServiceConfig.cpp) ────────────
_CLIENT_ID    = "oamhmhZez7opFosnwzElIgE2oGgI2iJORSkw587O"
_AUTH_URL     = "https://account.prusa3d.com/o/authorize/"
_TOKEN_URL    = "https://account.prusa3d.com/o/token/"
_PRINTERS_URL = "https://connect.prusa3d.com/slicer/v1/printers"
# PrusaSlicer's whitelisted redirect URI — we use it and ask the user to
# paste the resulting URL back, since http://localhost is not whitelisted.
_REDIRECT_URI = "prusaslicer://login"

# ── Token storage ──────────────────────────────────────────────────────────
TOKEN_DIR  = pathlib.Path.home() / ".config" / "prusa_calibration"
TOKEN_FILE = TOKEN_DIR / "tokens.json"


# ── PKCE helpers ───────────────────────────────────────────────────────────

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) using SHA-256 / S256."""
    verifier  = _b64url(secrets.token_bytes(40))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


# ── Token file I/O ─────────────────────────────────────────────────────────

def _save_tokens(data: dict) -> None:
    TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(data, indent=2))
    TOKEN_FILE.chmod(0o600)


def load_tokens() -> dict | None:
    """Return saved token dict, or None if not logged in."""
    if not TOKEN_FILE.exists():
        return None
    try:
        return json.loads(TOKEN_FILE.read_text())
    except Exception:
        return None


# ── HTTP helpers ───────────────────────────────────────────────────────────

def _post_form(url: str, fields: dict) -> dict:
    body = urllib.parse.urlencode(fields).encode()
    req  = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _get_json(url: str, access_token: str) -> object:
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {access_token}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


# ── OAuth2 flow ────────────────────────────────────────────────────────────

def _exchange_code(code: str, verifier: str) -> dict:
    return _post_form(_TOKEN_URL, {
        "grant_type":    "authorization_code",
        "client_id":     _CLIENT_ID,
        "code":          code,
        "redirect_uri":  _REDIRECT_URI,
        "code_verifier": verifier,
    })


def _do_refresh(refresh_tok: str) -> dict:
    return _post_form(_TOKEN_URL, {
        "grant_type":    "refresh_token",
        "client_id":     _CLIENT_ID,
        "refresh_token": refresh_tok,
    })


def _browser_login() -> dict:
    """Open browser for PKCE login; user pastes back the redirect URL."""
    verifier, challenge = _pkce_pair()

    params = urllib.parse.urlencode({
        "client_id":             _CLIENT_ID,
        "response_type":         "code",
        "scope":                 "basic_info",
        "redirect_uri":          _REDIRECT_URI,
        "code_challenge":        challenge,
        "code_challenge_method": "S256",
    })
    auth_url = f"{_AUTH_URL}?{params}"

    print("Opening browser for Prusa login…")
    print("  (If it doesn't open automatically, visit the URL below.)\n")
    webbrowser.open(auth_url)

    print("After logging in, the browser will try to open a 'prusaslicer://'")
    print("URL and show an error — that's expected.")
    print()
    print("Copy the full URL from the browser address bar and paste it here.")
    print("  It looks like:  prusaslicer://login?code=XXXXXXXXXX")
    print()

    while True:
        try:
            raw = input("Paste URL (or just the code): ").strip()
        except EOFError:
            print("\nAborted.", file=sys.stderr)
            sys.exit(1)

        if raw.startswith("prusaslicer://"):
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(raw).query)
            code = qs.get("code", [""])[0]
        else:
            code = raw  # user pasted just the bare code value

        if code:
            break
        print("Couldn't find a 'code' parameter — please try again.")

    print("Exchanging code for tokens…")
    return _exchange_code(code, verifier)


# ── Printer selection ──────────────────────────────────────────────────────

def _fetch_printers(access_token: str) -> list[dict]:
    """Return list of printer dicts from /slicer/v1/printers."""
    try:
        data = _get_json(_PRINTERS_URL, access_token)
    except urllib.error.HTTPError as e:
        print(f"ERROR: failed to fetch printer list: HTTP {e.code}", file=sys.stderr)
        sys.exit(1)

    # Normalise response: may be a list or {"printers":[...]} / {"items":[...]}
    if isinstance(data, list):
        return data
    for key in ("printers", "items", "data"):
        if key in data and isinstance(data[key], list):
            return data[key]
    return []


def _pick_printer(access_token: str) -> tuple[str, str, str]:
    """List printers interactively; return (team_id, printer_uuid, name)."""
    print("\nFetching your printers from PrusaConnect…")
    printers = _fetch_printers(access_token)

    if not printers:
        print("ERROR: no printers found in your PrusaConnect account.", file=sys.stderr)
        print("       Make sure at least one printer is registered at connect.prusa3d.com",
              file=sys.stderr)
        sys.exit(1)

    print(f"\nFound {len(printers)} printer(s):")
    for i, p in enumerate(printers):
        name     = p.get("name") or p.get("printer_model") or "Unknown"
        uuid     = p.get("uuid") or p.get("printer_uuid") or "?"
        team_id  = str(p.get("team_id") or "?")
        state    = p.get("connect_state") or p.get("state") or ""
        print(f"  [{i + 1}] {name}  (uuid={uuid}  team={team_id}  {state})")

    while True:
        try:
            raw = input(f"\nSelect printer [1–{len(printers)}]: ").strip()
            idx = int(raw) - 1
            if 0 <= idx < len(printers):
                p = printers[idx]
                team_id = str(
                    p.get("team_id")
                    or p.get("team", {}).get("id", "")
                )
                uuid = p.get("uuid") or p.get("printer_uuid") or ""
                name = p.get("name") or p.get("printer_model") or "?"
                if not team_id or not uuid:
                    print("Could not extract team_id / uuid from that entry. "
                          "Raw data:", json.dumps(p, indent=2))
                    print("Please report this so the field names can be fixed.")
                    sys.exit(1)
                return team_id, uuid, name
        except (ValueError, EOFError):
            pass
        print(f"Please enter a number between 1 and {len(printers)}.")


# ── Commands ───────────────────────────────────────────────────────────────

def cmd_login() -> None:
    raw = _browser_login()
    access_token  = raw["access_token"]
    refresh_token = raw.get("refresh_token", "")
    expires_in    = int(raw.get("expires_in", 3600))
    expires_at    = time.time() + expires_in - 60   # 1-min early expiry buffer

    team_id, printer_uuid, printer_name = _pick_printer(access_token)

    _save_tokens({
        "access_token":  access_token,
        "refresh_token": refresh_token,
        "expires_at":    expires_at,
        "team_id":       team_id,
        "printer_uuid":  printer_uuid,
        "printer_name":  printer_name,
    })
    print(f"\nLogged in. Selected printer: {printer_name}")
    print("You can now run pa_calibration.py or temperature_tower.py with --prusaconnect.")


def cmd_status() -> None:
    t = load_tokens()
    if not t:
        print(f"Not logged in.  Run: python3 prusa_login.py")
        return
    remaining = t.get("expires_at", 0) - time.time()
    if remaining > 0:
        tok_status = f"valid, expires in {int(remaining // 60)}m {int(remaining % 60)}s"
    else:
        tok_status = "EXPIRED — will auto-refresh on next upload"
    print(f"Logged in")
    print(f"  Printer  : {t.get('printer_name', '?')}  "
          f"(uuid={t.get('printer_uuid', '?')})")
    print(f"  Team ID  : {t.get('team_id', '?')}")
    print(f"  Token    : {tok_status}")
    print(f"  File     : {TOKEN_FILE}")


def cmd_logout() -> None:
    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()
        print(f"Removed {TOKEN_FILE}. You are now logged out.")
    else:
        print("Not logged in.")


def cmd_refresh() -> None:
    t = load_tokens()
    if not t or not t.get("refresh_token"):
        print("Not logged in (or no refresh token).  Run: python3 prusa_login.py",
              file=sys.stderr)
        sys.exit(1)
    print("Refreshing access token…")
    try:
        raw = _do_refresh(t["refresh_token"])
    except urllib.error.HTTPError as e:
        print(f"ERROR: token refresh failed: HTTP {e.code}", file=sys.stderr)
        sys.exit(1)
    t["access_token"] = raw["access_token"]
    if "refresh_token" in raw:
        t["refresh_token"] = raw["refresh_token"]
    t["expires_at"] = time.time() + int(raw.get("expires_in", 3600)) - 60
    _save_tokens(t)
    print("Token refreshed successfully.")


# ── Main ───────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    g = p.add_mutually_exclusive_group()
    g.add_argument("--status",  action="store_true",
                   help="Show saved token and printer info")
    g.add_argument("--logout",  action="store_true",
                   help="Remove saved tokens")
    g.add_argument("--refresh", action="store_true",
                   help="Force-refresh the access token now")
    args = p.parse_args()

    if args.status:
        cmd_status()
    elif args.logout:
        cmd_logout()
    elif args.refresh:
        cmd_refresh()
    else:
        cmd_login()


if __name__ == "__main__":
    main()
