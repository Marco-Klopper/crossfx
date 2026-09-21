"""
Runs Track 3's remittance journey against a LIVE server and prints each
request/response, picking up exactly where scripts/walkthrough.py stops.

Usage (from backend/, with the server already running):
    uvicorn app.main:app --reload &
    python -m scripts.create_admin --email admin@example.com --password adminpass123
    python -m scripts.walkthrough_remittance

Steps: register sender and recipient -> KYC-approve the sender -> add the
recipient as a beneficiary -> quote -> confirm ZAR cash-in (which queues
settlement) -> poll status -> cash out to fiat and have an admin release it.

Settlement itself is Track 2's worker, and it needs Redis and XRPL
Testnet. Without them, confirming cash-in still succeeds — it reports
`queued: false` and leaves the remittance re-publishable — and the
cash-out steps are skipped, because there is nothing in the recipient's
wallet yet. Run these to see the whole thing settle:

    docker run -p 6379:6379 redis:7-alpine
    python -m scripts.init_platform_wallets
    python -m worker.settlement_worker
"""
import argparse
import json
import sys
import time
import uuid

import httpx

STEP_WIDTH = 78


def _print_step(title: str) -> None:
    print(f"\n{'=' * STEP_WIDTH}\n{title}\n{'=' * STEP_WIDTH}")


def _call(client: httpx.Client, method: str, path: str, **kwargs) -> httpx.Response:
    resp = client.request(method, path, **kwargs)
    print(f"{method} {path}")
    if kwargs.get("json") is not None:
        print("  body:", json.dumps(kwargs["json"]))
    try:
        rendered = json.dumps(resp.json(), indent=2)
    except ValueError:
        rendered = resp.text or "<empty>"
    print(f"  -> {resp.status_code} {rendered}")
    return resp


def _login(client: httpx.Client, email: str, password: str) -> dict:
    resp = client.post("/auth/login", json={"email": email, "password": password})
    resp.raise_for_status()
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _register(client: httpx.Client, email: str, password: str, full_name: str) -> None:
    resp = _call(
        client,
        "POST",
        "/auth/register",
        json={"email": email, "password": password, "full_name": full_name},
    )
    if resp.status_code not in (201, 409):
        sys.exit("Registration failed unexpectedly - see response above.")


def run(base_url: str, admin_email: str, admin_password: str) -> None:
    # Fresh addresses per run: a beneficiary is unique per (sender, contact),
    # and a sender's daily limit is real money that does not reset on demand.
    tag = uuid.uuid4().hex[:8]
    sender_email = f"sender.{tag}@example.com"
    recipient_email = f"recipient.{tag}@example.com"
    password = "password123"

    with httpx.Client(base_url=base_url, timeout=15.0) as client:
        _print_step("1. Register the sender and the recipient")
        # The recipient needs an account of their own: under pooled custody
        # the credit lands in their internal wallet (spec 9.1).
        _register(client, sender_email, password, "Sipho Sender")
        _register(client, recipient_email, password, "Rita Recipient")
        sender_headers = _login(client, sender_email, password)
        recipient_headers = _login(client, recipient_email, password)

        _print_step("2. Get the sender through KYC (Track 1)")
        resp = _call(
            client,
            "POST",
            "/kyc/apply",
            json={
                "full_name": "Sipho Sender",
                "date_of_birth": "1990-05-15",
                "nationality": "South African",
                "identification_number": "9005155800086",
                "residential_address": "1 Long St, Cape Town",
                "mobile_number": "+27821234567",
                "email": sender_email,
                "source_of_funds": "Salary",
            },
            headers=sender_headers,
        )
        resp.raise_for_status()
        application_id = resp.json()["id"]

        admin_headers = _login(client, admin_email, admin_password)
        _call(
            client,
            "POST",
            f"/admin/kyc/{application_id}/approve",
            headers=admin_headers,
        )

        _print_step("3. Register the recipient as a beneficiary")
        resp = _call(
            client,
            "POST",
            "/beneficiaries/",
            json={
                "full_name": "Rita Recipient",
                # The contact IS the recipient's login email - that is how
                # the settlement worker resolves who to credit.
                "contact": recipient_email,
                "country": "United States",
                "preferred_payout_currency": "USD",
                "relationship_to_sender": "Sister",
            },
            headers=sender_headers,
        )
        resp.raise_for_status()
        beneficiary_id = resp.json()["id"]

        _print_step("4. Quote R1 000 (spec 4, 5, 6)")
        resp = _call(
            client,
            "POST",
            "/remittances/quote",
            json={"beneficiary_id": beneficiary_id, "zar_send_amount": "1000.00"},
            headers=sender_headers,
        )
        resp.raise_for_status()
        quote = resp.json()
        remittance_id = quote["remittance_id"]
        print(
            f"\n  R{quote['zar_send_amount']} - R{quote['transaction_fee_zar']} fee "
            f"- R{quote['fx_margin_zar']} margin = R{quote['net_converted_zar']}"
            f"\n  at {quote['fx_rate']} -> {quote['uctusd_amount']} UCTUSD"
            f"\n  all-in rate {quote['effective_rate']} ZAR per UCTUSD"
            f"\n  daily headroom left: R{quote['limits']['daily_remaining_zar']}"
        )

        _print_step("5. Confirm the ZAR cash-in, which queues settlement (spec 8)")
        resp = _call(
            client,
            "POST",
            f"/remittances/{remittance_id}/confirm-cash-in",
            json={"cash_in_method": "agent_cash"},
            headers=sender_headers,
        )
        resp.raise_for_status()
        queued = resp.json()["queued"]
        if not queued:
            print(
                "\n  The queue is unreachable, so this remittance is confirmed "
                "but not yet published.\n  Start Redis and re-run "
                f"POST /admin/remittances/{remittance_id}/confirm-payment."
            )

        _print_step("6. Poll the remittance until the worker settles it")
        for attempt in range(6 if queued else 1):
            resp = _call(
                client, "GET", f"/remittances/{remittance_id}", headers=sender_headers
            )
            if resp.json()["status"] in ("settled", "failed"):
                break
            if attempt < 5:
                time.sleep(2)

        _print_step("7. The recipient's wallet")
        resp = _call(client, "GET", "/wallet/balance", headers=recipient_headers)
        balance = resp.json()["uctusd_balance"]

        if float(balance) <= 0:
            print(
                "\n  Nothing has settled yet, so there is nothing to cash out.\n"
                "  Start Redis, the pooled wallets and the settlement worker\n"
                "  (see this script's docstring), then run it again."
            )
            print("\nDone - the sender's half of the journey ran end to end.")
            return

        _print_step("8. The recipient cashes out to USD (spec 10)")
        resp = _call(
            client,
            "POST",
            "/wallet/cash-out",
            json={"uctusd_amount": balance, "payout_currency": "USD"},
            headers=recipient_headers,
        )
        resp.raise_for_status()
        cash_out_id = resp.json()["id"]

        _print_step("9. An admin releases the payout")
        _call(
            client,
            "POST",
            f"/admin/cash-outs/{cash_out_id}/approve",
            headers=admin_headers,
        )

        _print_step("10. The wallet, now holding fiat")
        _call(client, "GET", "/wallet/balance", headers=recipient_headers)
        _call(client, "GET", "/wallet/transactions", headers=recipient_headers)

        print("\nDone. Quote -> cash-in -> settle -> cash-out, end to end.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--admin-email", default="admin@example.com")
    parser.add_argument("--admin-password", default="adminpass123")
    args = parser.parse_args()
    run(args.base_url, args.admin_email, args.admin_password)


if __name__ == "__main__":
    main()
