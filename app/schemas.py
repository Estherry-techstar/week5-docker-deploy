from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models import Stage

Name = Annotated[str, Field(min_length=2, max_length=80)]
Role = Annotated[str, Field(min_length=2, max_length=80)]
Years = Annotated[int, Field(ge=0, le=60)]


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