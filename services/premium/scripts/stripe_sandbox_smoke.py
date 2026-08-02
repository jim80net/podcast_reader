from __future__ import annotations

import argparse
import getpass
import time
from urllib.parse import urlsplit

import httpx

from podcast_reader_premium.billing import is_safe_checkout_url


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _canonical_origin(value: str) -> str:
    origin = value.rstrip("/")
    try:
        parsed = urlsplit(origin)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a canonical HTTPS origin") from exc
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
        or hostname is None
        or hostname.endswith(".")
        or any(character.isspace() for character in hostname)
    ):
        raise argparse.ArgumentTypeError("must be a canonical HTTPS origin")
    try:
        hostname.encode("ascii")
    except UnicodeEncodeError as exc:
        raise argparse.ArgumentTypeError("hostname must use ASCII or punycode") from exc
    display_host = f"[{hostname}]" if ":" in hostname else hostname
    canonical_netloc = display_host if port is None else f"{display_host}:{port}"
    if origin != f"https://{canonical_netloc}" or port == 443:
        raise argparse.ArgumentTypeError("must be a canonical HTTPS origin")
    return origin


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Complete one real Stripe test Checkout through the running dev service"
    )
    parser.add_argument("--base-url", required=True, type=_canonical_origin)
    parser.add_argument("--email", required=True)
    parser.add_argument("--timeout-seconds", type=_positive_int, default=300)
    args = parser.parse_args()
    origin = args.base_url
    password = getpass.getpass("Test account password: ")
    with httpx.Client(base_url=origin, timeout=15) as client:
        login = client.post(
            "/v1/browser-sessions",
            json={"email": args.email, "password": password},
        )
        if login.status_code != 201:
            raise SystemExit(f"test account login failed with HTTP {login.status_code}")
        csrf_token = login.json()["csrf_token"]
        checkout = client.post(
            "/v1/billing/checkout-sessions",
            headers={"Origin": origin, "X-CSRF-Token": csrf_token},
        )
        if checkout.status_code != 201:
            raise SystemExit(f"Checkout creation failed with HTTP {checkout.status_code}")
        checkout_url = checkout.json()["checkout_url"]
        if not isinstance(checkout_url, str) or not is_safe_checkout_url(checkout_url):
            raise SystemExit("service returned a non-Stripe Checkout URL")
        print(f"Complete the Stripe sandbox Checkout in a browser: {checkout_url}")
        deadline = time.monotonic() + args.timeout_seconds
        while time.monotonic() < deadline:
            account = client.get("/account")
            if account.status_code == 200 and "<strong>premium</strong>" in account.text:
                print("real Stripe sandbox smoke passed: verified webhook granted premium")
                return
            time.sleep(2)
    raise SystemExit("timed out before the forwarded webhook granted premium")


if __name__ == "__main__":
    main()
