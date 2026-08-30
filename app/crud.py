from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import Candidate
from app.schemas import CandidateCreate, CandidateUpdate


def email_exists(db: Session, email: str, exclude_id: int | None = None) -> bool:
    stmt = select(Candidate.id).where(func.lower(Candidate.email) == email.lower())
    if exclude_id is not None:
        stmt = stmt.where(Candidate.id != exclude_id)
    return db.execute(stmt).first() is not None


def create(db: Session, payload: CandidateCreate) -> Candidate:
    candidate = Candidate(**payload.model_dump())
    db.add(candidate)
    db.commit()
    db.refresh(candidate)
    return candidate


def get(db: Session, candidate_id: int) -> Candidate | None:
    return db.get(Candidate, candidate_id)


def count(db: Session) -> int:
    return db.execute(select(func.count()).select_from(Candidate)).scalar_one()


def list_candidates(
    db: Session,
    stage: str | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Candidate]:
    stmt = select(Candidate).order_by(Candidate.id)

    if stage:
        stmt = stmt.where(Candidate.stage == stage)

    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(
            or_(Candidate.full_name.ilike(pattern), Candidate.role.ilike(pattern))
        )

    stmt = stmt.offset(offset).limit(limit)
    return list(db.execute(stmt).scalars().all())


def update(db: Session, candidate_id: int, payload: CandidateUpdate) -> Candidate | None:
    candidate = db.get(Candidate, candidate_id)
    if candidate is None:
        return None

    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(candidate, field, value)

    db.commit()
    db.refresh(candidate)
    return candidate


def delete(db: Session, candidate_id: int) -> bool:
    candidate = db.get(Candidate, candidate_id)
    if candidate is None:
        return False
    db.delete(candidate)
    db.commit()
    return True