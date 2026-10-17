from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr


class UserBase(BaseModel):
    email: EmailStr
    username: str


class UserCreate(UserBase):
    password: str


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserUpdate(BaseModel):
    username: Optional[str] = None


class UserRead(UserBase):
    id: str
    has_password: bool
    google_linked: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class GoogleLogin(BaseModel):
    # the ID token Google Identity Services hands the frontend
    credential: str


class GoogleToken(Token):
    # True when this sign-in created the account, so the frontend can show the onboarding
    is_new_user: bool = False


class TokenData(BaseModel):
    user_id: Optional[str] = None

