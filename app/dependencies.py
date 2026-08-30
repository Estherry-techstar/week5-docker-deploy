"""Request-scoped dependencies for authentication."""
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app import crud
from app.auth import decode_access_token
from app.database import get_db
from app.models import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")

CREDENTIALS_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials.",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Resolve the caller's token into a User, or reject the request."""
    user_id = decode_access_token(token)
    if user_id is None:
        raise CREDENTIALS_ERROR

    user = crud.get_user(db, user_id)
    if user is None:
        raise CREDENTIALS_ERROR

    if not user.is_active:
        raise CREDENTIALS_ERROR

    return user