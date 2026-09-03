"""Tests for signup, login, and JWT-protected access."""
import pytest

from app import crud
from app.auth import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.schemas import UserCreate


class TestPasswordHashing:
    def test_hash_is_not_the_password(self):
        hashed = hash_password("supersecret123")

        assert hashed != "supersecret123"
        assert hashed.startswith("$2b$")

    def test_same_password_hashes_differently(self):
        """Each hash uses a fresh salt, so identical passwords look unrelated."""
        assert hash_password("samepassword") != hash_password("samepassword")

    def test_verify_accepts_correct_password(self):
        hashed = hash_password("supersecret123")

        assert verify_password("supersecret123", hashed) is True

    def test_verify_rejects_wrong_password(self):
        hashed = hash_password("supersecret123")

        assert verify_password("wrongpassword", hashed) is False

    def test_verify_returns_false_on_malformed_hash(self):
        """A corrupt hash must not raise, or a bad login becomes a 500."""
        assert verify_password("anything", "not-a-hash") is False


class TestTokens:
    def test_round_trip(self):
        token = create_access_token(42)

        assert decode_access_token(token) == 42

    def test_rejects_garbage(self):
        assert decode_access_token("not-a-token") is None

    def test_rejects_tampered_token(self):
        token = create_access_token(42)
        tampered = token[:-4] + "aaaa"

        assert decode_access_token(tampered) is None


class TestSignup:
    def test_creates_user_and_returns_202(self, client):
        """202 rather than 201: the duplicate branch has no resource to report."""
        response = client.post(
            "/auth/signup",
            json={"email": "new@example.com", "password": "supersecret123"},
        )

        assert response.status_code == 202

    def test_response_never_includes_the_hash(self, client):
        response = client.post(
            "/auth/signup",
            json={"email": "new@example.com", "password": "supersecret123"},
        )

        assert "hashed_password" not in response.json()
        assert "password" not in response.json()

    def test_password_is_stored_hashed(self, client, db):
        client.post(
            "/auth/signup",
            json={"email": "new@example.com", "password": "supersecret123"},
        )

        user = crud.get_user_by_email(db, "new@example.com")
        assert user.hashed_password != "supersecret123"
        assert verify_password("supersecret123", user.hashed_password)

    def test_duplicate_email_is_indistinguishable_from_a_new_one(
        self, client, test_user
    ):
        """Signup must not reveal whether an email is already registered.

        The two responses are compared to each other rather than to literal
        values, so this keeps holding if the status code or wording changes.
        """
        existing = client.post(
            "/auth/signup",
            json={"email": "tester@example.com", "password": "supersecret123"},
        )
        fresh = client.post(
            "/auth/signup",
            json={"email": "brand-new@example.com", "password": "supersecret123"},
        )

        assert existing.status_code == fresh.status_code
        assert existing.json() == fresh.json()

    def test_duplicate_signup_does_not_change_the_existing_password(
        self, client, test_user
    ):
        """Returning a generic response must not mean quietly overwriting the row.

        If a duplicate signup replaced the stored hash, this endpoint would be
        account takeover: anyone could reset any account by re-registering it.
        """
        client.post(
            "/auth/signup",
            json={"email": "tester@example.com", "password": "attacker-chosen-pw"},
        )

        response = client.post(
            "/auth/login",
            data={"username": "tester@example.com", "password": "testpassword123"},
        )

        assert response.status_code == 200

    def test_short_password_returns_422(self, client):
        response = client.post(
            "/auth/signup", json={"email": "new@example.com", "password": "short"}
        )

        assert response.status_code == 422

    def test_password_over_72_bytes_returns_422(self, client):
        """bcrypt silently ignores bytes past 72, so we reject them at the edge."""
        response = client.post(
            "/auth/signup", json={"email": "new@example.com", "password": "x" * 100}
        )

        assert response.status_code == 422

    def test_invalid_email_returns_422(self, client):
        response = client.post(
            "/auth/signup", json={"email": "not-an-email", "password": "supersecret123"}
        )

        assert response.status_code == 422


class TestLogin:
    def test_returns_a_token(self, client, test_user):
        response = client.post(
            "/auth/login",
            data={"username": "tester@example.com", "password": "testpassword123"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["token_type"] == "bearer"
        assert decode_access_token(body["access_token"]) == test_user.id

    def test_wrong_password_returns_401(self, client, test_user):
        response = client.post(
            "/auth/login",
            data={"username": "tester@example.com", "password": "wrongpassword"},
        )

        assert response.status_code == 401

    def test_unknown_email_returns_401(self, client):
        response = client.post(
            "/auth/login",
            data={"username": "nobody@example.com", "password": "testpassword123"},
        )

        assert response.status_code == 401

    def test_same_message_for_unknown_email_and_wrong_password(self, client, test_user):
        """Error text must not reveal whether an account exists."""
        unknown = client.post(
            "/auth/login",
            data={"username": "nobody@example.com", "password": "testpassword123"},
        )
        wrong = client.post(
            "/auth/login",
            data={"username": "tester@example.com", "password": "wrongpassword"},
        )

        assert unknown.json()["detail"] == wrong.json()["detail"]

    def test_email_is_case_insensitive(self, client, test_user):
        response = client.post(
            "/auth/login",
            data={"username": "TESTER@EXAMPLE.COM", "password": "testpassword123"},
        )

        assert response.status_code == 200

    def test_inactive_user_cannot_log_in(self, client, db, test_user):
        test_user.is_active = False
        db.commit()

        response = client.post(
            "/auth/login",
            data={"username": "tester@example.com", "password": "testpassword123"},
        )

        assert response.status_code == 401


class TestProtectedAccess:
    def test_me_requires_a_token(self, client):
        assert client.get("/auth/me").status_code == 401

    def test_me_returns_the_current_user(self, client, auth_headers, test_user):
        response = client.get("/auth/me", headers=auth_headers)

        assert response.status_code == 200
        assert response.json()["email"] == test_user.email

    def test_token_for_deleted_user_is_rejected(self, client, db, test_user):
        token = create_access_token(test_user.id)
        db.delete(test_user)
        db.commit()

        response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 401

    def test_token_for_deactivated_user_is_rejected(self, client, db, test_user):
        """Revocation works because we check is_active on every request."""
        token = create_access_token(test_user.id)
        test_user.is_active = False
        db.commit()

        response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 401


class TestRateLimiting:
    def test_login_is_rate_limited(self, client, test_user):
        """The sixth login attempt within a minute is rejected.

        Note the sixth attempt uses the *correct* password and is still
        blocked: the limiter runs before authentication, so an attacker who
        guesses right on attempt six still gets nothing.
        """
        for _ in range(5):
            client.post(
                "/auth/login",
                data={"username": "tester@example.com", "password": "wrongpassword"},
            )

        response = client.post(
            "/auth/login",
            data={"username": "tester@example.com", "password": "testpassword123"},
        )

        assert response.status_code == 429

    def test_signup_is_rate_limited(self, client):
        """Signup allows 5 per hour to limit junk account creation."""
        for i in range(5):
            client.post(
                "/auth/signup",
                json={"email": f"user{i}@example.com", "password": "supersecret123"},
            )

        response = client.post(
            "/auth/signup",
            json={"email": "sixth@example.com", "password": "supersecret123"},
        )

        assert response.status_code == 429