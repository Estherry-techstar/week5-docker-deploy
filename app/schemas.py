from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models import Stage

Name = Annotated[str, Field(min_length=2, max_length=80)]
Role = Annotated[str, Field(min_length=2, max_length=80)]
Years = Annotated[int, Field(ge=0, le=60)]

# bcrypt silently ignores anything past 72 bytes, so we reject it at the boundary
# rather than letting it reach the hashing layer.
Password = Annotated[str, Field(min_length=8, max_length=72)]


class CandidateBase(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    full_name: Name
    email: EmailStr
    role: Role
    years_experience: Years = 0
    stage: Stage = Stage.APPLIED
    notes: str | None = Field(default=None, max_length=500)

    @field_validator("full_name", "role")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be blank")
        return v


class CandidateCreate(CandidateBase):
    """Body the client sends on POST."""


class CandidateUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    full_name: Name | None = None
    email: EmailStr | None = None
    role: Role | None = None
    years_experience: Years | None = None
    stage: Stage | None = None
    notes: str | None = Field(default=None, max_length=500)


class CandidateRead(CandidateBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


class UserCreate(BaseModel):
    """Body the client sends on signup."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    email: EmailStr
    password: Password


class UserRead(BaseModel):
    """What the API sends back about a user. Deliberately excludes the hash."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    is_active: bool
    created_at: datetime


class Token(BaseModel):
    """OAuth2-shaped token response."""

    access_token: str
    token_type: str = "bearer"