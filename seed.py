"""Seed the database with sample candidates and interviews.

Idempotent: running it twice will not create duplicates.
Usage: python seed.py
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Candidate, Interview, Stage

CANDIDATES = [
    {
        "full_name": "Ada Lovelace",
        "email": "ada@example.com",
        "role": "Backend Engineer",
        "years_experience": 5,
        "stage": Stage.INTERVIEW,
        "notes": "Strong Python background",
    },
    {
        "full_name": "Grace Hopper",
        "email": "grace@example.com",
        "role": "Platform Engineer",
        "years_experience": 12,
        "stage": Stage.OFFER,
        "notes": "Excellent systems knowledge",
    },
    {
        "full_name": "Alan Turing",
        "email": "alan@example.com",
        "role": "Data Engineer",
        "years_experience": 3,
        "stage": Stage.SCREENING,
        "notes": None,
    },
]

INTERVIEWS = {
    "ada@example.com": [
        {"interviewer": "Chidi Okafor", "days": 2, "feedback": "Clear communicator"},
        {"interviewer": "Ngozi Eze", "days": 5, "feedback": None},
    ],
    "grace@example.com": [
        {"interviewer": "Chidi Okafor", "days": 1, "feedback": "Recommend hire"},
    ],
}


def seed() -> None:
    db = SessionLocal()
    try:
        for data in CANDIDATES:
            exists = db.execute(
                select(Candidate).where(Candidate.email == data["email"])
            ).scalar_one_or_none()

            if exists:
                print(f"skip   {data['email']} (already present)")
                continue

            candidate = Candidate(**data)
            db.add(candidate)
            db.flush()

            for iv in INTERVIEWS.get(data["email"], []):
                db.add(
                    Interview(
                        candidate_id=candidate.id,
                        scheduled_at=datetime.now(timezone.utc)
                        + timedelta(days=iv["days"]),
                        interviewer=iv["interviewer"],
                        feedback=iv["feedback"],
                    )
                )

            print(f"create {data['email']}")

        db.commit()
        print("\nSeed complete.")
    finally:
        db.close()


if __name__ == "__main__":
    seed()