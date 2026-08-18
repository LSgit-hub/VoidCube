"""CLI adapters for interactive provider login and logout."""

from __future__ import annotations

import os
from pathlib import Path

from ...infrastructure.providers import auth as _provider_auth

# Preserve the established import surface while the CLI command handlers move
# independently from the shared credential and provider contracts.
globals().update(
    {
        name: value
        for name, value in vars(_provider_auth).items()
        if not name.startswith("__")
    }
)
# ── CLI command handlers ────────────────────────────────────────────────


def login_command(args) -> None:
    """Interactive provider login.

    Handles ``VoidCube login`` and ``VoidCube login --provider <name>``.
    Walks the user through the Nous OAuth device flow or API key entry.

    Args:
        args: argparse namespace with optional ``provider``, ``portal_url``,
              ``inference_url``, ``client_id``, ``scope``, ``no_browser``,
              ``timeout``, ``ca_bundle``, ``insecure`` attributes.
    """
    provider = getattr(args, "provider", None)

    # Determine target provider
    if not provider:
        try:
            from ...infrastructure.config.configuration import get_active_provider_key, load_config
            config = load_config()
            provider = get_active_provider_key(config)
        except Exception:
            provider = None

    if not provider:
        print()
        print("No provider specified and no active provider configured.")
        print()
        print("Usage:")
        print("  VoidCube login --provider nous        Login with Nous Research")
        print()
        print("First configure a provider:  VoidCube api")
        print("Or set an API key directly:  VoidCube config set providers.<name>.api_key <key>")
        return

    provider = provider.lower().strip()

    if provider in ("nous",):
        _login_nous(args)
    else:
        # Generic API-key provider
        _login_api_key(provider, args)


def _login_nous(args) -> None:
    """OAuth device flow login for Nous Research."""
    import time
    import urllib.request
    import urllib.error
    import json
    import webbrowser

    portal_url = getattr(args, "portal_url", None) or os.getenv(
        "NOUS_PORTAL_URL", DEFAULT_NOUS_PORTAL_URL
    )
    client_id = getattr(args, "client_id", None) or "VoidCube-cli"
    scope = getattr(args, "scope", None) or "openid profile email"
    no_browser = getattr(args, "no_browser", False)
    timeout = getattr(args, "timeout", 15.0)

    print()
    print("> Login with Nous Research")
    print()

    # Step 1: Device authorization request
    device_url = f"{portal_url}/oauth2/device/code"
    data = json.dumps({
        "client_id": client_id,
        "scope": scope,
    }).encode("utf-8")

    try:
        req = urllib.request.Request(
            device_url,
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            device_resp = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode() if hasattr(exc, "read") else ""
        print(f"  ✗ Device authorization failed: HTTP {exc.code}")
        if body:
            print(f"    {body[:500]}")
        return
    except Exception as exc:
        print(f"  ✗ Cannot reach Nous portal: {exc}")
        print(f"    Check your network and portal URL: {portal_url}")
        return

    verification_uri = device_resp.get("verification_uri_complete") or device_resp.get("verification_uri", "")
    user_code = device_resp.get("user_code", "")
    device_code = device_resp.get("device_code", "")
    interval = device_resp.get("interval", 5)
    expires_in = device_resp.get("expires_in", 600)

    if not user_code:
        print("  ✗ No user_code in device authorization response")
        return

    print(f"  Verification code: {user_code}")
    if verification_uri:
        print(f"  Open: {verification_uri}")

    if not no_browser and verification_uri:
        print()
        print("  Opening browser...")
        try:
            webbrowser.open(verification_uri)
        except Exception:
            print("  (could not open browser — open the URL above manually)")

    print()
    print(f"  Waiting for authorization (expires in {expires_in}s)...")

    # Step 2: Poll for token
    token_url = f"{portal_url}/oauth2/token"
    deadline = time.time() + expires_in

    while time.time() < deadline:
        time.sleep(interval)
        try:
            token_data = json.dumps({
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code,
                "client_id": client_id,
            }).encode("utf-8")
            req = urllib.request.Request(
                token_url,
                data=token_data,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                token_resp = json.loads(resp.read().decode())

            if "access_token" in token_resp:
                # Store the credential
                _store_provider_credential("nous", token_resp)
                print()
                print("  ✓ Login successful!")
                print()
                print("  Run 'VoidCube model' to select a Nous model.")
                return
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode()
                err = json.loads(body).get("error", "")
            except Exception:
                err = ""
            if err == "authorization_pending":
                continue  # User hasn't approved yet — keep polling
            elif err == "slow_down":
                interval += 2
                continue
            elif err == "expired_token":
                print()
                print("  ✗ Verification code expired. Run 'VoidCube login --provider nous' to retry.")
                return
            else:
                print(f"  ✗ Token request failed: HTTP {exc.code} — {body[:300]}")
                return
        except Exception as exc:
            print(f"  ✗ Token request error: {exc}")
            continue

    print()
    print("  ✗ Timed out waiting for authorization.")


def _login_api_key(provider: str, args) -> None:
    """Interactive API key entry for generic providers."""
    import getpass

    print()
    print(f"> Login with {provider.title()}")
    print()

    # Show what env var to set
    if provider in PROVIDER_REGISTRY:
        env_vars = PROVIDER_REGISTRY[provider].get("api_key_env_vars", [])
        if env_vars:
            print(f"  Set environment variable: {env_vars[0]}")
            print(f"  Or add to ~/.VoidCube/.env")
    else:
        env_var = f"{provider.upper().replace('-', '_')}_API_KEY"
        print(f"  Set environment variable: {env_var}")

    print()
    try:
        api_key = getpass.getpass("  API Key (input hidden): ").strip()
    except EOFError:
        print("\n  Cancelled.")
        return

    if not api_key:
        print("  No key entered. Cancelled.")
        return

    # Save to .env
    env_key = PROVIDER_REGISTRY.get(provider, {}).get("api_key_env_vars", [None])[0]
    if not env_key:
        env_key = f"{provider.upper().replace('-', '_')}_API_KEY"
    try:
        from ...infrastructure.config.configuration import save_env_value
        from ...infrastructure.config.runtime_paths import get_env_path
        env_file = get_env_path()
        save_env_value(env_key, api_key)
        print(f"  ✓ API key saved to {env_file}")
        _store_provider_credential(provider, {"api_key": api_key})
    except Exception as exc:
        print(f"  ✗ Failed to save: {exc}")
        print("  Manually add the key to ~/.VoidCube/.env without printing it here:")
        print(f"  {env_key}=<redacted>")


def _store_provider_credential(provider: str, credential: dict) -> None:
    """Store a provider credential in the auth store (thread-safe)."""
    try:
        with _auth_store_lock:
            store = _load_auth_store()
            store[provider] = credential
            # Inline the write inside the lock so load+save is atomic
            import json as _json
            store_path = _get_auth_store_path()
            tmp_path = store_path.with_suffix(".tmp")
            try:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    _json.dump(store, f, ensure_ascii=False, indent=2)
                tmp_path.replace(store_path)
            finally:
                try:
                    if tmp_path.exists():
                        tmp_path.unlink()
                except OSError:
                    pass
    except Exception:
        pass  # Best-effort


def logout_command(args) -> None:
    """Clear provider authentication.

    Handles ``VoidCube logout`` and ``VoidCube logout --provider <name>``.
    Removes stored credentials, clears env vars from .env, and resets
    provider config.

    Args:
        args: argparse namespace with optional ``provider`` attribute.
    """
    provider = getattr(args, "provider", None)

    if not provider:
        try:
            from ...infrastructure.config.configuration import get_active_provider_key, load_config
            config = load_config()
            provider = get_active_provider_key(config)
        except Exception:
            provider = None

    if not provider:
        print()
        print("No provider specified and no active provider configured.")
        print()
        print("Usage:")
        print("  VoidCube logout --provider nous")
        return

    provider = provider.lower().strip()
    provider_name = PROVIDER_REGISTRY.get(provider, {}).get("name", provider)

    print()
    print(f"> Logout from {provider_name} ({provider})")
    print()

    # Remove from auth store
    try:
        store = _load_auth_store()
        if provider in store:
            del store[provider]
            _save_auth_store(store)
            print("  ✓ Cleared stored credentials")
        else:
            print("  (no stored credentials found)")
    except Exception:
        pass

    # Remove API key env var(s) from .env
    env_keys = PROVIDER_REGISTRY.get(provider, {}).get("api_key_env_vars", [])
    if not env_keys:
        env_keys = [f"{provider.upper().replace('-', '_')}_API_KEY"]

    try:
        from ...infrastructure.config.configuration import save_env_value
        for key in env_keys:
            save_env_value(key, "")
        if env_keys:
            print(f"  ✓ Cleared {', '.join(env_keys)} from .env")
    except Exception:
        pass

    # Clear provider from config if it's the active provider
    try:
        from ...infrastructure.config.configuration import load_config, save_config, get_active_provider_key
        config = load_config()
        if get_active_provider_key(config) == provider:
            config["active_provider"] = ""
            save_config(config)
            print("  ✓ Reset active provider")
    except Exception:
        pass

    print()
    print("  Logged out. Run 'VoidCube api' to reconfigure.")
