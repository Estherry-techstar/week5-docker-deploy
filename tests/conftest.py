"""Shared pytest fixtures: an isolated database that resets between tests."""
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import crud
from app.auth import create_access_token
from app.database import get_db
from app.main import app, limiter
from app.models import Base
from app.schemas import UserCreate

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://appuser:devpassword@localhost:5433/appdb_week4_test",
)


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """Give every test a fresh rate-limit allowance.

    Rate-limit state is global and keyed on client IP. Without this, tests
    would inherit one another's request counts and fail unpredictably.
    """
    limiter.reset()
    yield


@pytest.fixture(scope="session")
def engine():
    """One engine for the whole test run. Creates the schema once."""
    eng = create_engine(TEST_DATABASE_URL)
    Base.metadata.drop_all(bind=eng)
    Base.metadata.create_all(bind=eng)
    yield eng
    Base.metadata.drop_all(bind=eng)
    eng.dispose()


@pytest.fixture
def db(engine):
    """A session wrapped in a transaction that is always rolled back."""
    connection = engine.connect()
    transaction = connection.begin()
    Session = sessionmaker(bind=connection)
    session = Session()

    yield session

    session.close()
    if transaction.is_active:
        transaction.rollback()
    connection.close()


@pytest.fixture
def client(db):
    """A TestClient whose endpoints use the rolled-back test session."""

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def test_user(db):
    """A registered, active user."""
    return crud.create_user(
        db, UserCreate(email="tester@example.com", password="testpassword123")
    )


@pytest.fixture
def auth_headers(client, test_user):
    """Bearer token header for the test user."""
    token = create_access_token(test_user.id)
    return {"Authorization": f"Bearer {token}"}