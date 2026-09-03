"""FastAPI application: the front desk that receives HTTP requests."""
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import crud
from app.auth import create_access_token
from app.database import get_db
from app.dependencies import get_current_user
from app.models import Stage, User
from app.schemas import (
    CandidateCreate,
    CandidateRead,
    CandidateUpdate,
    MessageResponse,
    Token,
    UserCreate,
    UserRead,
)

app = FastAPI(
    title="Candidate Tracker API",
    description="PostgreSQL-backed CRUD service with JWT authentication.",
    version="3.0.0",
)

# Rate limiting is keyed on client IP. This is a first layer only: it does not
# stop a distributed attack, and behind a proxy every request appears to come
# from the proxy unless X-Forwarded-For is handled.
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Single source of truth for the signup response. Both the "created" and the
# "already exists" paths return this exact object, so the response body cannot
# be used to test whether an address is registered.
SIGNUP_ACCEPTED = MessageResponse(
    detail="If this email can be registered, you'll receive a confirmation shortly."
)


@app.get("/health", tags=["meta"])
def health(db: Session = Depends(get_db)) -> dict:
    """Cheap endpoint so CI / uptime checks can confirm the app booted.

    Deliberately unauthenticated so monitoring can reach it.
    """
    return {"status": "ok", "candidates": crud.count(db)}


# --- auth ------------------------------------------------------------------


@app.post(
    "/auth/signup",
    response_model=MessageResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["auth"],
)
@limiter.limit("5/hour")
def signup(
    request: Request, payload: UserCreate, db: Session = Depends(get_db)
) -> MessageResponse:
    """Register an account without disclosing whether the email already exists.

    Signup is an unauthenticated endpoint, so any observable difference between
    the "new address" and "already registered" branches lets an attacker test a
    list of emails against the user table. For a KYC/financial client, mere
    membership in that table is itself sensitive: it answers "does this person
    hold an account here?".

    Every branch therefore returns 202 with an identical body and spends
    comparable time. The information the real user needs -- confirm your
    account, versus someone tried to register your address -- belongs in an
    email to that address, which only its owner can read. That notification is
    not implemented yet; see README for the deferred work.
    """
    if crud.get_user_by_email(db, payload.email):
        # Do not touch the existing row. Overwriting the stored hash here would
        # turn this endpoint into account takeover. Burn a comparable amount of
        # time instead, so response latency does not replace the status code as
        # an existence oracle.
        crud.dummy_verify()
        return SIGNUP_ACCEPTED

    try:
        crud.create_user(db, payload)
    except IntegrityError:
        # The check above is check-then-act: two concurrent signups for the same
        # address both pass it, and the loser hits the unique constraint. Let it
        # land on the same generic response rather than a 500, which would be a
        # rarer but equally usable oracle.
        db.rollback()
        crud.dummy_verify()

    return SIGNUP_ACCEPTED


@app.post("/auth/login", response_model=Token, tags=["auth"])
@limiter.limit("5/minute")
def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
) -> Token:
    user = crud.authenticate_user(db, form_data.username, form_data.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return Token(access_token=create_access_token(user.id))


@app.get("/auth/me", response_model=UserRead, tags=["auth"])
def read_current_user(current_user: User = Depends(get_current_user)) -> UserRead:
    return current_user


# --- candidates ------------------------------------------------------------


@app.post(
    "/candidates",
    response_model=CandidateRead,
    status_code=status.HTTP_201_CREATED,
    tags=["candidates"],
    dependencies=[Depends(get_current_user)],
)
def create_candidate(
    payload: CandidateCreate, db: Session = Depends(get_db)
) -> CandidateRead:
    # Unlike /auth/signup, this 409 is fine: the route is authenticated, and a
    # candidate record is tracked data rather than an account, so confirming it
    # exists does not disclose whether a person banks here.
    if crud.email_exists(db, payload.email):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A candidate with email {payload.email} already exists.",
        )
    return crud.create(db, payload)


@app.get(
    "/candidates",
    response_model=list[CandidateRead],
    tags=["candidates"],
    dependencies=[Depends(get_current_user)],
)
def list_candidates(
    stage: Stage | None = Query(default=None, description="Filter by stage"),
    q: str | None = Query(default=None, description="Search name or role"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[CandidateRead]:
    return crud.list_candidates(
        db, stage=stage.value if stage else None, q=q, limit=limit, offset=offset
    )


@app.get(
    "/candidates/{candidate_id}",
    response_model=CandidateRead,
    tags=["candidates"],
    dependencies=[Depends(get_current_user)],
)
def get_candidate(candidate_id: int, db: Session = Depends(get_db)) -> CandidateRead:
    candidate = crud.get(db, candidate_id)
    if candidate is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Candidate {candidate_id} not found.",
        )
    return candidate


@app.patch(
    "/candidates/{candidate_id}",
    response_model=CandidateRead,
    tags=["candidates"],
    dependencies=[Depends(get_current_user)],
)
def update_candidate(
    candidate_id: int, payload: CandidateUpdate, db: Session = Depends(get_db)
) -> CandidateRead:
    if payload.email and crud.email_exists(db, payload.email, exclude_id=candidate_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A candidate with email {payload.email} already exists.",
        )
    updated = crud.update(db, candidate_id, payload)
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Candidate {candidate_id} not found.",
        )
    return updated


@app.delete(
    "/candidates/{candidate_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["candidates"],
    dependencies=[Depends(get_current_user)],
)
def delete_candidate(candidate_id: int, db: Session = Depends(get_db)) -> Response:
    if not crud.delete(db, candidate_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Candidate {candidate_id} not found.",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)