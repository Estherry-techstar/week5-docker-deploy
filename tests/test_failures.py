"""Tests for expired tokens and database failures.

These cover the two failure modes that are hardest to trigger by accident:
a token that was legitimately issued but has aged out, and a database that
stops answering mid-request.
"""
from datetime import datetime, timedelta, timezone

import pytest
from jose import jwt
from sqlalchemy.exc import OperationalError

from app.auth import decode_access_token
from app.config import settings
from app.database import get_db
from app.main import app


def make_expired_token(user_id: int, minutes_ago: int = 5) -> str:
    """Build a correctly signed token whose expiry has already passed."""
    expired_at = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    claims = {"sub": str(user_id), "exp": expired_at}
    return jwt.encode(claims, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


class TestExpiredTokens:
    def test_decode_rejects_an_expired_token(self):
        """The signature is valid; only the expiry has passed."""
        assert decode_access_token(make_expired_token(42)) is None

    def test_expired_token_is_rejected_by_protected_endpoint(self, client, test_user):
        token = make_expired_token(test_user.id)

        response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 401

    def test_expired_token_looks_like_any_other_bad_token(self, client, test_user):
        """Expiry must not be distinguishable from a forged token.

        Saying "your token expired" tells an attacker the token was
        otherwise genuine, so both cases return identical responses.
        """
        expired = client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {make_expired_token(test_user.id)}"},
        )
        forged = client.get("/auth/me", headers={"Authorization": "Bearer nonsense"})

        assert expired.status_code == forged.status_code
        assert expired.json() == forged.json()

    def test_a_token_expiring_shortly_is_still_accepted(self, client, test_user):
        """Guards against an off-by-one that would reject valid tokens."""
        expiry = datetime.now(timezone.utc) + timedelta(seconds=30)
        token = jwt.encode(
            {"sub": str(test_user.id), "exp": expiry},
            settings.jwt_secret_key,
            algorithm=settings.jwt_algorithm,
        )

        response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 200


class TestDatabaseFailures:
    """The database is replaced with one that fails, without stopping Postgres."""

    @pytest.fixture
    def failing_client(self, client):
        """A client whose database session raises on connection."""

        def failing_get_db():
            raise OperationalError("SELECT 1", {}, Exception("connection refused"))

        app.dependency_overrides[get_db] = failing_get_db
        yield client
        app.dependency_overrides.clear()

    def test_health_check_returns_503_when_the_database_is_down(self, failing_client):
        response = failing_client.get("/health")

        assert response.status_code == 503

    def test_listing_candidates_returns_503(self, failing_client, auth_headers):
        response = failing_client.get("/candidates", headers=auth_headers)

        assert response.status_code == 503

    def test_the_error_body_leaks_nothing_about_the_database(self, failing_client):
        """SQLAlchemy's message contains the host and user; the response must not."""
        body = failing_client.get("/health").text.lower()

        assert "connection refused" not in body
        assert "postgresql" not in body
        assert settings.postgres_user.lower() not in body