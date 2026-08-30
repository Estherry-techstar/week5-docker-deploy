"""Tests for the CRUD layer, run against a real PostgreSQL database."""
import pytest
from sqlalchemy.exc import IntegrityError

from app import crud
from app.models import Candidate, Interview, Stage
from app.schemas import CandidateCreate, CandidateUpdate


def make_payload(**overrides) -> CandidateCreate:
    """Build a valid candidate payload, overriding fields as needed."""
    data = {
        "full_name": "Ada Lovelace",
        "email": "ada@example.com",
        "role": "Backend Engineer",
        "years_experience": 5,
        "stage": Stage.APPLIED,
        "notes": "Strong Python background",
    }
    data.update(overrides)
    return CandidateCreate(**data)


class TestCreate:
    def test_create_returns_candidate_with_generated_id(self, db):
        candidate = crud.create(db, make_payload())

        assert candidate.id is not None
        assert candidate.full_name == "Ada Lovelace"

    def test_create_sets_timestamps_from_database(self, db):
        candidate = crud.create(db, make_payload())

        assert candidate.created_at is not None
        assert candidate.updated_at is not None

    def test_create_defaults_stage_to_applied(self, db):
        payload = CandidateCreate(
            full_name="Alan Turing",
            email="alan@example.com",
            role="Data Engineer",
        )
        candidate = crud.create(db, payload)

        assert candidate.stage == Stage.APPLIED

    def test_duplicate_email_violates_unique_constraint(self, db):
        crud.create(db, make_payload())

        with pytest.raises(IntegrityError):
            crud.create(db, make_payload(full_name="Someone Else"))


class TestEmailExists:
    def test_returns_false_when_absent(self, db):
        assert crud.email_exists(db, "nobody@example.com") is False

    def test_returns_true_when_present(self, db):
        crud.create(db, make_payload())

        assert crud.email_exists(db, "ada@example.com") is True

    def test_is_case_insensitive(self, db):
        crud.create(db, make_payload())

        assert crud.email_exists(db, "ADA@EXAMPLE.COM") is True

    def test_exclude_id_ignores_the_named_candidate(self, db):
        candidate = crud.create(db, make_payload())

        assert crud.email_exists(db, "ada@example.com", exclude_id=candidate.id) is False


class TestGet:
    def test_returns_candidate_by_id(self, db):
        created = crud.create(db, make_payload())

        found = crud.get(db, created.id)

        assert found is not None
        assert found.email == "ada@example.com"

    def test_returns_none_for_missing_id(self, db):
        assert crud.get(db, 999999) is None


class TestList:
    def test_returns_empty_list_when_no_candidates(self, db):
        assert crud.list_candidates(db) == []

    def test_returns_candidates_ordered_by_id(self, db):
        crud.create(db, make_payload(email="a@example.com"))
        crud.create(db, make_payload(email="b@example.com"))

        rows = crud.list_candidates(db)

        assert len(rows) == 2
        assert rows[0].id < rows[1].id

    def test_filters_by_stage(self, db):
        crud.create(db, make_payload(email="a@example.com", stage=Stage.APPLIED))
        crud.create(db, make_payload(email="b@example.com", stage=Stage.OFFER))

        rows = crud.list_candidates(db, stage="offer")

        assert len(rows) == 1
        assert rows[0].email == "b@example.com"

    def test_search_matches_name_case_insensitively(self, db):
        crud.create(db, make_payload(full_name="Ada Lovelace", email="a@example.com"))
        crud.create(db, make_payload(full_name="Alan Turing", email="b@example.com"))

        rows = crud.list_candidates(db, q="lovelace")

        assert len(rows) == 1
        assert rows[0].full_name == "Ada Lovelace"

    def test_search_matches_role(self, db):
        crud.create(db, make_payload(email="a@example.com", role="Backend Engineer"))
        crud.create(db, make_payload(email="b@example.com", role="Data Analyst"))

        rows = crud.list_candidates(db, q="analyst")

        assert len(rows) == 1

    def test_limit_and_offset_paginate(self, db):
        for i in range(5):
            crud.create(db, make_payload(email=f"c{i}@example.com"))

        page = crud.list_candidates(db, limit=2, offset=2)

        assert len(page) == 2


class TestCount:
    def test_counts_rows(self, db):
        assert crud.count(db) == 0

        crud.create(db, make_payload())

        assert crud.count(db) == 1


class TestUpdate:
    def test_updates_only_provided_fields(self, db):
        candidate = crud.create(db, make_payload())

        updated = crud.update(db, candidate.id, CandidateUpdate(stage=Stage.OFFER))

        assert updated.stage == Stage.OFFER
        assert updated.full_name == "Ada Lovelace"

    def test_returns_none_for_missing_candidate(self, db):
        assert crud.update(db, 999999, CandidateUpdate(stage=Stage.HIRED)) is None


class TestDelete:
    def test_removes_candidate(self, db):
        candidate = crud.create(db, make_payload())

        assert crud.delete(db, candidate.id) is True
        assert crud.get(db, candidate.id) is None

    def test_returns_false_for_missing_candidate(self, db):
        assert crud.delete(db, 999999) is False


class TestCascade:
    def test_deleting_candidate_removes_interviews(self, db):
        from datetime import datetime, timedelta, timezone

        candidate = crud.create(db, make_payload())
        db.add(
            Interview(
                candidate_id=candidate.id,
                scheduled_at=datetime.now(timezone.utc) + timedelta(days=1),
                interviewer="Chidi Okafor",
            )
        )
        db.commit()

        assert db.query(Interview).count() == 1

        crud.delete(db, candidate.id)

        assert db.query(Interview).count() == 0