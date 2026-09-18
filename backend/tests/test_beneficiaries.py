"""
Integration tests for routers/beneficiaries.py, including the cross-user
isolation behaviour (404, not 403, for another sender's beneficiary).
"""


def _valid_beneficiary(**overrides):
    payload = {
        "full_name": "Bob Recipient",
        "contact": "bob@example.com",
        "country": "United States",
        "preferred_payout_currency": "usd",
        "relationship_to_sender": "Brother",
    }
    payload.update(overrides)
    return payload


def test_create_and_list(client, auth_headers):
    headers, _ = auth_headers
    resp = client.post("/beneficiaries/", json=_valid_beneficiary(), headers=headers)
    assert resp.status_code == 201
    assert resp.json()["preferred_payout_currency"] == "USD"  # normalized upper-case

    listed = client.get("/beneficiaries/", headers=headers).json()
    assert len(listed) == 1
    assert listed[0]["full_name"] == "Bob Recipient"


def test_duplicate_contact_conflicts(client, auth_headers):
    headers, _ = auth_headers
    client.post("/beneficiaries/", json=_valid_beneficiary(), headers=headers)
    resp = client.post("/beneficiaries/", json=_valid_beneficiary(), headers=headers)
    assert resp.status_code == 409


def test_invalid_currency_rejected(client, auth_headers):
    headers, _ = auth_headers
    resp = client.post(
        "/beneficiaries/", json=_valid_beneficiary(preferred_payout_currency="XYZ"), headers=headers
    )
    assert resp.status_code == 422


def test_list_is_scoped_to_owner(client, auth_headers, user_factory, auth_header_for):
    headers, _ = auth_headers
    client.post("/beneficiaries/", json=_valid_beneficiary(), headers=headers)

    other_user = user_factory(email="other@example.com")
    other_headers = auth_header_for(other_user)

    assert client.get("/beneficiaries/", headers=other_headers).json() == []
    assert len(client.get("/beneficiaries/", headers=headers).json()) == 1


def test_reading_another_users_beneficiary_returns_404_not_403(
    client, auth_headers, user_factory, auth_header_for
):
    headers, _ = auth_headers
    created = client.post("/beneficiaries/", json=_valid_beneficiary(), headers=headers).json()

    other_user = user_factory(email="mallory@example.com")
    other_headers = auth_header_for(other_user)

    resp = client.get(f"/beneficiaries/{created['id']}", headers=other_headers)
    assert resp.status_code == 404


def test_delete_own_beneficiary(client, auth_headers):
    headers, _ = auth_headers
    created = client.post("/beneficiaries/", json=_valid_beneficiary(), headers=headers).json()

    resp = client.delete(f"/beneficiaries/{created['id']}", headers=headers)
    assert resp.status_code == 204
    assert client.get("/beneficiaries/", headers=headers).json() == []


def test_delete_another_users_beneficiary_returns_404(
    client, auth_headers, user_factory, auth_header_for
):
    headers, _ = auth_headers
    created = client.post("/beneficiaries/", json=_valid_beneficiary(), headers=headers).json()

    other_user = user_factory(email="mallory2@example.com")
    other_headers = auth_header_for(other_user)

    resp = client.delete(f"/beneficiaries/{created['id']}", headers=other_headers)
    assert resp.status_code == 404


def test_cannot_delete_a_beneficiary_with_remittances(
    client, quote_factory, fake_queue
):
    """
    Track 3 made beneficiaries load-bearing: the settlement worker
    resolves the recipient through this row, and the sender's history
    would lose the name the money was sent to.
    """
    _, headers, _, beneficiary = quote_factory()

    response = client.delete(f"/beneficiaries/{beneficiary.id}", headers=headers)

    assert response.status_code == 409
    assert "remittances" in response.json()["detail"]
    assert client.get(f"/beneficiaries/{beneficiary.id}", headers=headers).status_code == 200
