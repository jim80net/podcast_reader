from __future__ import annotations

import argparse
import getpass
import time
from urllib.parse import urlsplit

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Complete one real Stripe test Checkout through the running dev service"
    )
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    args = parser.parse_args()
    origin = args.base_url.rstrip("/")
    parsed = urlsplit(origin)
    if parsed.scheme != "https" or not parsed.netloc or parsed.path:
        raise SystemExit("--base-url must be a canonical HTTPS origin")
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
        checkout_host = urlsplit(checkout_url).hostname or ""
        if not checkout_url.startswith("https://") or not checkout_host.endswith("stripe.com"):
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
