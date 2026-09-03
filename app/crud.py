from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.auth import hash_password, verify_password
from app.models import Candidate, User
from app.schemas import CandidateCreate, CandidateUpdate, UserCreate

# A pre-computed hash of a throwaway password. Used to keep failed logins
# taking the same time whether or not the email exists.
DUMMY_HASH = hash_password("dummy-password-for-timing-consistency")


def dummy_verify(password: str = "dummy-password-for-timing-consistency") -> None:
    """Spend one password verification's worth of time. Result discarded.

    Called on branches where there's no real hash to check, so response
    latency doesn't reveal whether an account exists. Both login (unknown
    email) and signup (email already registered) rely on this.
    """
    verify_password(password, DUMMY_HASH)


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


# --- users -----------------------------------------------------------------


def get_user_by_email(db: Session, email: str) -> User | None:
    """Look up a user by email, case-insensitively."""
    stmt = select(User).where(func.lower(User.email) == email.lower())
    return db.execute(stmt).scalar_one_or_none()


def get_user(db: Session, user_id: int) -> User | None:
    return db.get(User, user_id)


def create_user(db: Session, payload: UserCreate) -> User:
    """Create a user, storing only the bcrypt hash of the password."""
    user = User(
        email=payload.email.lower(),
        hashed_password=hash_password(payload.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate_user(db: Session, email: str, password: str) -> User | None:
    """Return the user if the credentials are valid, otherwise None.

    Always runs a password verification, even when the email is unknown, so
    that the response time does not reveal whether an account exists.
    """
    user = get_user_by_email(db, email)

    if user is None:
        dummy_verify(password)
        return None

    if not verify_password(password, user.hashed_password):
        return None

    if not user.is_active:
        return None

    return user