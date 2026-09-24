"""
Creates the synthetic users the load test drives (the brief: "Generate
synthetic users for your performance tests and simulations").

Every load-test sender has to be set up before Locust can do anything useful,
because the remittance endpoints are gated:

  * /remittances/quote requires an APPROVED KYC application, so each sender
    must apply and an admin must approve them;
  * confirming cash-in requires the beneficiary's contact to match a
    registered user's email, or the API returns a 409 — so every sender needs
    a real recipient account, not an invented address.

Each sender gets its own recipient so that concurrent settlements touch
different ledger rows, which is what makes the concurrency measurement mean
something rather than serialising on one wallet.

Usage (from performance-testing/, with the API running):

    ./.venv/bin/python seed_users.py --count 40

Writes users.json, which locustfile.py reads.
"""
import argparse
import json
import sys
import time
import uuid

import requests

DEFAULT_PASSWORD = "loadtest-password-123"


def _post(session, api, path, payload=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = session.post(f"{api}{path}", json=payload, headers=headers, timeout=30)
    if response.status_code >= 400:
        raise RuntimeError(
            f"POST {path} -> {response.status_code}: {response.text[:300]}"
        )
    return response.json() if response.content else None


def login(session, api, email, password):
    return _post(session, api, "/auth/login", {"email": email, "password": password})[
        "access_token"
    ]


def create_sender(session, api, admin_token, email, password, run_id, index):
    """Registers a sender, applies for KYC, and has the admin approve it."""
    _post(
        session,
        api,
        "/auth/register",
        {"email": email, "password": password, "full_name": f"Load Sender {index}"},
    )
    token = login(session, api, email, password)

    application = _post(
        session,
        api,
        "/kyc/apply",
        {
            "full_name": f"Load Sender {index}",
            "date_of_birth": "1990-01-01",
            "nationality": "South African",
            # Unique per applicant: real KYC would deduplicate on this, and a
            # shared value would misrepresent the load as one person.
            "identification_number": f"{run_id}{index:04d}",
            "residential_address": "1 Long Street, Cape Town",
            "mobile_number": "+27821234567",
            "email": email,
            "source_of_funds": "Salary",
        },
        token=token,
    )
    _post(session, api, f"/admin/kyc/{application['id']}/approve", token=admin_token)
    return token


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--count", type=int, default=40)
    parser.add_argument("--admin-email", default="admin@example.com")
    parser.add_argument("--admin-password", default="adminpass123")
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    parser.add_argument("--out", default="users.json")
    args = parser.parse_args()

    session = requests.Session()

    try:
        admin_token = login(
            session, args.api, args.admin_email, args.admin_password
        )
    except Exception as exc:
        print(
            f"Could not log in as {args.admin_email}: {exc}\n"
            f"Create one first:  cd ../backend && "
            f"./.venv/bin/python -m scripts.create_admin "
            f"--email {args.admin_email} --password {args.admin_password}",
            file=sys.stderr,
        )
        return 1

    # A short run id keeps each seeding run's emails distinct, so the script
    # can be re-run without colliding with users it made last time.
    run_id = uuid.uuid4().hex[:6]
    users = []
    started = time.time()

    for index in range(args.count):
        sender_email = f"ls-{run_id}-{index}@example.com"
        recipient_email = f"lr-{run_id}-{index}@example.com"

        try:
            token = create_sender(
                session, args.api, admin_token, sender_email,
                args.password, run_id, index,
            )

            # The recipient only has to exist — settlement resolves them by
            # matching the beneficiary's contact against User.email.
            _post(
                session,
                args.api,
                "/auth/register",
                {
                    "email": recipient_email,
                    "password": args.password,
                    "full_name": f"Load Recipient {index}",
                },
            )

            beneficiary = _post(
                session,
                args.api,
                "/beneficiaries/",
                {
                    "full_name": f"Load Recipient {index}",
                    "contact": recipient_email,
                    "country": "United States",
                    "preferred_payout_currency": "USD",
                    "relationship_to_sender": "Family",
                },
                token=token,
            )
        except Exception as exc:
            print(f"  user {index} failed: {exc}", file=sys.stderr)
            continue

        users.append(
            {
                "email": sender_email,
                "password": args.password,
                "recipient_email": recipient_email,
                "beneficiary_id": beneficiary["id"],
            }
        )
        if (index + 1) % 10 == 0:
            print(f"  seeded {index + 1}/{args.count}")

    with open(args.out, "w") as handle:
        json.dump(users, handle, indent=2)

    elapsed = time.time() - started
    print(
        f"\nSeeded {len(users)}/{args.count} senders (each KYC-approved, with a "
        f"registered recipient) in {elapsed:.1f}s -> {args.out}"
    )
    if len(users) < args.count:
        print("Some users failed; see the errors above.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
