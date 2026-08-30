"""Tests for the HTTP layer: status codes, auth, and response shapes."""
import pytest


def payload(**overrides) -> dict:
    data = {
        "full_name": "Ada Lovelace",
        "email": "ada@example.com",
        "role": "Backend Engineer",
        "years_experience": 5,
        "stage": "applied",
        "notes": "Strong Python background",
    }
    data.update(overrides)
    return data


class TestHealth:
    def test_health_is_public(self, client):
        response = client.get("/health")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_health_reports_candidate_count(self, client, auth_headers):
        client.post("/candidates", json=payload(), headers=auth_headers)

        assert client.get("/health").json()["candidates"] == 1


class TestAuthentication:
    @pytest.mark.parametrize(
        "method,path",
        [
            ("get", "/candidates"),
            ("post", "/candidates"),
            ("get", "/candidates/1"),
            ("patch", "/candidates/1"),
            ("delete", "/candidates/1"),
        ],
    )
    def test_rejects_missing_token(self, client, method, path):
        response = getattr(client, method)(path)

        assert response.status_code == 401

    def test_rejects_malformed_token(self, client):
        response = client.get(
            "/candidates", headers={"Authorization": "Bearer not-a-real-token"}
        )

        assert response.status_code == 401
        assert response.json()["detail"] == "Could not validate credentials."

    def test_rejects_api_key(self, client):
        """The old API key auth was removed; it must no longer grant access."""
        response = client.get("/candidates", headers={"X-API-Key": "dev-secret-key"})

        assert response.status_code == 401


class TestCreateEndpoint:
    def test_returns_201_with_generated_fields(self, client, auth_headers):
        response = client.post("/candidates", json=payload(), headers=auth_headers)

        assert response.status_code == 201
        body = response.json()
        assert body["id"] is not None
        assert body["created_at"] is not None
        assert body["updated_at"] is not None

    def test_echoes_submitted_fields(self, client, auth_headers):
        response = client.post("/candidates", json=payload(), headers=auth_headers)

        body = response.json()
        assert body["full_name"] == "Ada Lovelace"
        assert body["email"] == "ada@example.com"
        assert body["stage"] == "applied"

    def test_duplicate_email_returns_409(self, client, auth_headers):
        client.post("/candidates", json=payload(), headers=auth_headers)

        response = client.post(
            "/candidates", json=payload(full_name="Someone Else"), headers=auth_headers
        )

        assert response.status_code == 409
        assert "already exists" in response.json()["detail"]

    def test_invalid_email_returns_422(self, client, auth_headers):
        response = client.post(
            "/candidates", json=payload(email="not-an-email"), headers=auth_headers
        )

        assert response.status_code == 422

    def test_invalid_stage_returns_422(self, client, auth_headers):
        response = client.post(
            "/candidates", json=payload(stage="banana"), headers=auth_headers
        )

        assert response.status_code == 422

    def test_unknown_field_returns_422(self, client, auth_headers):
        response = client.post(
            "/candidates", json=payload(salary=100000), headers=auth_headers
        )

        assert response.status_code == 422

    def test_negative_experience_returns_422(self, client, auth_headers):
        response = client.post(
            "/candidates", json=payload(years_experience=-1), headers=auth_headers
        )

        assert response.status_code == 422


class TestListEndpoint:
    def test_returns_empty_list_initially(self, client, auth_headers):
        response = client.get("/candidates", headers=auth_headers)

        assert response.status_code == 200
        assert response.json() == []

    def test_filters_by_stage(self, client, auth_headers):
        client.post("/candidates", json=payload(email="a@example.com"), headers=auth_headers)
        client.post(
            "/candidates",
            json=payload(email="b@example.com", stage="offer"),
            headers=auth_headers,
        )

        response = client.get("/candidates?stage=offer", headers=auth_headers)

        assert len(response.json()) == 1

    def test_search_query(self, client, auth_headers):
        client.post(
            "/candidates",
            json=payload(full_name="Ada Lovelace", email="a@example.com"),
            headers=auth_headers,
        )
        client.post(
            "/candidates",
            json=payload(full_name="Alan Turing", email="b@example.com"),
            headers=auth_headers,
        )

        response = client.get("/candidates?q=turing", headers=auth_headers)

        assert len(response.json()) == 1

    def test_limit_above_maximum_returns_422(self, client, auth_headers):
        response = client.get("/candidates?limit=500", headers=auth_headers)

        assert response.status_code == 422


class TestGetEndpoint:
    def test_returns_candidate(self, client, auth_headers):
        created = client.post("/candidates", json=payload(), headers=auth_headers).json()

        response = client.get(f"/candidates/{created['id']}", headers=auth_headers)

        assert response.status_code == 200
        assert response.json()["email"] == "ada@example.com"

    def test_missing_candidate_returns_404(self, client, auth_headers):
        response = client.get("/candidates/999999", headers=auth_headers)

        assert response.status_code == 404


class TestUpdateEndpoint:
    def test_partial_update_leaves_other_fields(self, client, auth_headers):
        created = client.post("/candidates", json=payload(), headers=auth_headers).json()

        response = client.patch(
            f"/candidates/{created['id']}", json={"stage": "offer"}, headers=auth_headers
        )

        assert response.status_code == 200
        assert response.json()["stage"] == "offer"
        assert response.json()["full_name"] == "Ada Lovelace"

    def test_missing_candidate_returns_404(self, client, auth_headers):
        response = client.patch(
            "/candidates/999999", json={"stage": "offer"}, headers=auth_headers
        )

        assert response.status_code == 404

    def test_duplicate_email_returns_409(self, client, auth_headers):
        client.post("/candidates", json=payload(email="a@example.com"), headers=auth_headers)
        second = client.post(
            "/candidates", json=payload(email="b@example.com"), headers=auth_headers
        ).json()

        response = client.patch(
            f"/candidates/{second['id']}",
            json={"email": "a@example.com"},
            headers=auth_headers,
        )

        assert response.status_code == 409


class TestDeleteEndpoint:
    def test_returns_204_and_removes(self, client, auth_headers):
        created = client.post("/candidates", json=payload(), headers=auth_headers).json()

        response = client.delete(f"/candidates/{created['id']}", headers=auth_headers)

        assert response.status_code == 204
        assert client.get(f"/candidates/{created['id']}", headers=auth_headers).status_code == 404

    def test_missing_candidate_returns_404(self, client, auth_headers):
        response = client.delete("/candidates/999999", headers=auth_headers)

        assert response.status_code == 404