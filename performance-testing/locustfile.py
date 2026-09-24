"""
Load test for the CrossFX API (the brief's "Performance Testing Results"
deliverable).

Two endpoints matter and they are deliberately different in kind:

  POST /remittances/quote            the pure-compute path — fee arithmetic,
                                     a mock FX rate, one INSERT. No network
                                     calls leave the process.
  POST /remittances/{id}/confirm-cash-in
                                     the asynchronous path — it commits, then
                                     publishes to Redis and returns without
                                     waiting for XRPL.

Neither touches the XRP Ledger, which is the whole point of the queue: the
request path is decoupled from settlement, so this measures the API, and
measure_settlement.py measures the ledger leg separately.

Two things about the numbers, both worth stating in the report:

  * A 403 from /quote is not an error. Every live quote holds limit headroom
    (spec §6), so a synthetic sender eventually exhausts their R3,000 daily
    ceiling. Those are counted separately as `limit_exhausted` and excluded
    from the failure rate, because a business rule refusing a request is not
    the API failing.
  * FastHttpUser is used rather than HttpUser. Measuring server throughput
    with a client that saturates first measures the client, and the
    requests-based user runs out of CPU before the API does.

Run (from performance-testing/, API running, users seeded):

    ./.venv/bin/locust -f locustfile.py --host http://127.0.0.1:8000
    ./.venv/bin/locust -f locustfile.py --host http://127.0.0.1:8000 \
        --headless -u 50 -r 10 -t 60s --csv results/quote
"""
import itertools
import json
import os
import sys
import threading
from collections import Counter

from locust import FastHttpUser, between, events, task

SEND_AMOUNT = os.environ.get("SEND_AMOUNT", "50.00")
USERS_FILE = os.environ.get("USERS_FILE", "users.json")
# Statuses a remittance will not move on from, so polling can stop.
TERMINAL_STATUSES = frozenset({"settled", "failed", "refunded"})

CASH_IN_METHOD = "agent_cash"

# Counters for outcomes Locust's own statistics would misreport.
_counters = Counter()
_lock = threading.Lock()


def _count(name):
    with _lock:
        _counters[name] += 1


@events.init.add_listener
def _load_credentials(environment, **_kwargs):
    """
    Loads the seeded users once per process and hands them out round-robin.

    Failing loudly here rather than per-user keeps a missing users.json from
    showing up as thousands of identical task errors.
    """
    try:
        with open(USERS_FILE) as handle:
            users = json.load(handle)
    except FileNotFoundError:
        sys.exit(
            f"{USERS_FILE} not found — seed the synthetic users first:\n"
            f"    ./.venv/bin/python seed_users.py --count 40"
        )
    if not users:
        sys.exit(f"{USERS_FILE} is empty — re-run seed_users.py")

    environment.crossfx_users = itertools.cycle(users)
    environment.crossfx_user_count = len(users)
    print(f"Loaded {len(users)} synthetic senders from {USERS_FILE}")


@events.test_stop.add_listener
def _report(environment, **_kwargs):
    """Prints the outcomes Locust's failure count deliberately excludes."""
    stats = environment.stats.total
    print("\n" + "=" * 62)
    print("CrossFX load test summary")
    print("=" * 62)
    print(f"  Requests              {stats.num_requests}")
    print(f"  Failures              {stats.num_failures}")
    print(f"  Requests/sec          {stats.total_rps:.1f}")
    print(f"  Median response       {stats.median_response_time} ms")
    print(f"  95th percentile       {stats.get_response_time_percentile(0.95)} ms")
    print(f"  Max response          {stats.max_response_time:.0f} ms")
    for name, value in sorted(_counters.items()):
        print(f"  {name:<21} {value}")
    print("=" * 62)
    print(
        "  Note: limit_exhausted is a 403 from the daily/monthly remittance\n"
        "  ceiling (spec §6), not an API failure."
    )


class Sender(FastHttpUser):
    """
    One synthetic sender: quotes repeatedly, funds some of those quotes, and
    occasionally reads their wallet.

    The task weights approximate a real corridor's shape — senders price a
    transfer more often than they commit to one, and check a balance less
    often than either.
    """

    wait_time = between(0.1, 0.5)

    def on_start(self):
        credentials = next(self.environment.crossfx_users)
        self.beneficiary_id = credentials["beneficiary_id"]
        self.quoted = []
        # Remittances that have been paid in and are now settling, for
        # poll_status to watch.
        self.tracked = []
        self.limit_reached = False

        with self.client.post(
            "/auth/login",
            json={
                "email": credentials["email"],
                "password": credentials["password"],
            },
            name="POST /auth/login",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"login failed: {response.status_code}")
                self.headers = None
                return
            response.success()
            self.headers = {
                "Authorization": f"Bearer {response.json()['access_token']}"
            }

    @task(6)
    def quote(self):
        """The pure-compute path: fee arithmetic, a mock rate, one INSERT."""
        if not self.headers or self.limit_reached:
            return
        with self.client.post(
            "/remittances/quote",
            json={
                "beneficiary_id": self.beneficiary_id,
                "zar_send_amount": SEND_AMOUNT,
            },
            headers=self.headers,
            name="POST /remittances/quote",
            catch_response=True,
        ) as response:
            if response.status_code == 201:
                response.success()
                _count("quotes_created")
                self.quoted.append(response.json()["remittance_id"])
            elif response.status_code == 403:
                # The sender's daily ceiling. Expected, and not a failure —
                # but stop quoting from this user so the remaining run
                # measures the API rather than the limit check.
                response.success()
                _count("limit_exhausted")
                self.limit_reached = True
            elif response.status_code == 503:
                # The FX source could not produce a rate.
                response.failure("FX rate unavailable")
                _count("fx_unavailable")
            else:
                response.failure(f"{response.status_code}: {response.text[:120]}")

    @task(2)
    def confirm_cash_in(self):
        """
        The asynchronous path: commit, publish to the queue, return.

        A 200 with queued=false means the queue was unreachable — the cash-in
        still succeeded, so it is recorded rather than failed. That branch is
        exactly what the load test should exercise if Redis is not running.
        """
        if not self.headers or not self.quoted:
            return
        remittance_id = self.quoted.pop()
        with self.client.post(
            f"/remittances/{remittance_id}/confirm-cash-in",
            json={"cash_in_method": CASH_IN_METHOD},
            headers=self.headers,
            name="POST /remittances/{id}/confirm-cash-in",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
                _count(
                    "cash_in_queued"
                    if response.json().get("queued")
                    else "cash_in_not_queued"
                )
                self.tracked.append(remittance_id)
            elif response.status_code == 409:
                # An expired quote, or one already confirmed.
                response.success()
                _count("cash_in_conflict")
            else:
                response.failure(f"{response.status_code}: {response.text[:120]}")

    @task(4)
    def poll_status(self):
        """
        The endpoint the UI polls every three seconds per in-flight
        transfer (frontend/src/screens/Send.jsx).

        It had no coverage here at all, which left the highest-RPS
        endpoint in the real system unmeasured: N senders watching a
        settlement generate far more requests than the sends themselves.
        The weight reflects that -- a sender polls a transfer many times
        for each one they create.
        """
        if not self.headers or not self.tracked:
            return
        remittance_id = self.tracked[-1]
        with self.client.get(
            f"/remittances/{remittance_id}",
            headers=self.headers,
            name="GET /remittances/{id}",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
                _count(f"poll_{response.json()['status']}")
                if response.json()["status"] in TERMINAL_STATUSES:
                    self.tracked.pop()
            else:
                response.failure(
                    f"{response.status_code}: {response.text[:120]}"
                )

    @task(1)
    def wallet(self):
        """A read, for a baseline the write paths can be compared against."""
        if not self.headers:
            return
        self.client.get(
            "/wallet/balance", headers=self.headers, name="GET /wallet/balance"
        )
