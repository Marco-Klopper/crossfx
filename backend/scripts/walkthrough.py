"""
Runs the full Track 1 golden path against a LIVE server and prints each
request/response as it goes, so you can watch the whole identity journey
execute in order and confirm you understand what each step does.

Usage (from backend/, with the server already running):
    uvicorn app.main:app --reload &
    python -m scripts.create_admin --email admin@example.com --password adminpass123
    python -m scripts.walkthrough

Steps: register -> login -> apply KYC -> admin approves -> add a beneficiary
-> GET /me showing the upgraded (verified) limits.
"""
import argparse
import json
import sys

import httpx

STEP_WIDTH = 78


def _print_step(title: str) -> None:
    print(f"\n{'=' * STEP_WIDTH}\n{title}\n{'=' * STEP_WIDTH}")


def _call(client: httpx.Client, method: str, path: str, **kwargs) -> httpx.Response:
    resp = client.request(method, path, **kwargs)
    print(f"{method} {path}")
    if kwargs.get("json") is not None:
        print("  body:", json.dumps(kwargs["json"]))
    print(f"  -> {resp.status_code} {json.dumps(resp.json(), indent=2)}")
    return resp


def run(base_url: str, admin_email: str, admin_password: str) -> None:
    with httpx.Client(base_url=base_url, timeout=10.0) as client:
        _print_step("1. Register a new sender")
        register_payload = {
            "email": "walkthrough.alice@example.com",
            "password": "password123",
            "full_name": "Alice Walkthrough",
        }
        resp = _call(client, "POST", "/auth/register", json=register_payload)
        if resp.status_code == 409:
            print("  (already registered from a previous run - continuing)")
        elif resp.status_code != 201:
            sys.exit("Registration failed unexpectedly - see response above.")

        _print_step("2. Log in as the sender")
        resp = _call(
            client,
            "POST",
            "/auth/login",
            json={"email": register_payload["email"], "password": register_payload["password"]},
        )
        resp.raise_for_status()
        sender_token = resp.json()["access_token"]
        sender_headers = {"Authorization": f"Bearer {sender_token}"}

        _print_step("3. Check /me before KYC - unverified limits")
        _call(client, "GET", "/auth/me", headers=sender_headers)

        _print_step("4. Submit a KYC application")
        resp = _call(
            client,
            "POST",
            "/kyc/apply",
            json={
                "full_name": "Alice Walkthrough",
                "date_of_birth": "1990-05-15",
                "nationality": "South African",
                "identification_number": "9005155800086",
                "residential_address": "1 Long St, Cape Town",
                "mobile_number": "+27821234567",
                "email": register_payload["email"],
                "source_of_funds": "Salary",
            },
            headers=sender_headers,
        )
        if resp.status_code == 409:
            print("  (already applied from a previous run - fetching existing application)")
            application_id = _call(client, "GET", "/kyc/status", headers=sender_headers).json()[
                "latest_application"
            ]["id"]
        else:
            resp.raise_for_status()
            application_id = resp.json()["id"]

        _print_step("5. Log in as admin")
        resp = _call(client, "POST", "/auth/login", json={"email": admin_email, "password": admin_password})
        resp.raise_for_status()
        admin_headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}

        _print_step("6. Admin approves the KYC application")
        resp = _call(client, "POST", f"/admin/kyc/{application_id}/approve", headers=admin_headers)
        if resp.status_code == 409:
            print("  (already approved from a previous run - continuing)")

        _print_step("7. Add a beneficiary")
        resp = _call(
            client,
            "POST",
            "/beneficiaries/",
            json={
                "full_name": "Bob Recipient",
                "contact": "bob.walkthrough@example.com",
                "country": "United States",
                "preferred_payout_currency": "usd",
                "relationship_to_sender": "Brother",
            },
            headers=sender_headers,
        )
        if resp.status_code == 409:
            print("  (already added from a previous run - continuing)")

        _print_step("8. Check /me again - now approved, with verified limits")
        _call(client, "GET", "/auth/me", headers=sender_headers)

        print("\nDone. That's the full Track 1 identity journey.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--admin-email", default="admin@example.com")
    parser.add_argument("--admin-password", default="adminpass123")
    args = parser.parse_args()
    run(args.base_url, args.admin_email, args.admin_password)


if __name__ == "__main__":
    main()
