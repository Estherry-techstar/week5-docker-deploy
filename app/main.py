"""FastAPI application: the front desk that receives HTTP requests."""
from fastapi import Depends, FastAPI, HTTPException, Query, Response, status
from fastapi.security import OAuth2PasswordRequestForm
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
    Token,
    UserCreate,
    UserRead,
)
from app.security import require_api_key

app = FastAPI(
    title="Candidate Tracker API",
    description="PostgreSQL-backed CRUD service with JWT authentication.",
    version="3.0.0",
)


@app.get("/health", tags=["meta"])
def health(db: Session = Depends(get_db)) -> dict:
    """Cheap endpoint so CI / uptime checks can confirm the app booted."""
    return {"status": "ok", "candidates": crud.count(db)}


# --- auth ------------------------------------------------------------------


@app.post(
    "/auth/signup",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    tags=["auth"],
)
def signup(payload: UserCreate, db: Session = Depends(get_db)) -> UserRead:
    if crud.get_user_by_email(db, payload.email):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with that email already exists.",
        )
    return crud.create_user(db, payload)


@app.post("/auth/login", response_model=Token, tags=["auth"])
def login(
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
    dependencies=[Depends(require_api_key)],
)
def create_candidate(
    payload: CandidateCreate, db: Session = Depends(get_db)
) -> CandidateRead:
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
    dependencies=[Depends(require_api_key)],
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
    dependencies=[Depends(require_api_key)],
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
    dependencies=[Depends(require_api_key)],
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
    dependencies=[Depends(require_api_key)],
)
def delete_candidate(candidate_id: int, db: Session = Depends(get_db)) -> Response:
    if not crud.delete(db, candidate_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Candidate {candidate_id} not found.",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)