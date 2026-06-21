from typing import Optional, List
from pydantic import BaseModel, EmailStr


# ===================== Авторизация =====================

class UserCreate(BaseModel):
    email: EmailStr
    password: str


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ===================== Настройки =====================

class UserSettings(BaseModel):
    units: str
    time_format: str
    notify_daily: bool
    notify_rain: bool

    class Config:
        from_attributes = True


class UserSettingsUpdate(BaseModel):
    units: Optional[str] = None
    time_format: Optional[str] = None
    notify_daily: Optional[bool] = None
    notify_rain: Optional[bool] = None


# ===================== Города =====================

class CityCreate(BaseModel):
    name: str
    lat: float
    lon: float


class CityOut(BaseModel):
    id: int
    name: str
    lat: float
    lon: float
    is_home: bool

    class Config:
        from_attributes = True


# ===================== Пользователь =====================

class UserOut(BaseModel):
    id: int
    email: str
    home_city: Optional[str] = None
    home_lat: Optional[float] = None
    home_lon: Optional[float] = None
    settings: UserSettings
    cities: List[CityOut]

    class Config:
        from_attributes = True
