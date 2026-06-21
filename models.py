from datetime import datetime

from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import relationship

from database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)

    # --- Настройки (из вкладки "Настройки" на фронте) ---
    units = Column(String, default="metric")        # "metric" (°C) | "imperial" (°F)
    time_format = Column(String, default="24h")      # "24h" | "12h"
    notify_daily = Column(Boolean, default=False)     # ежечасное уведомление о погоде
    notify_rain = Column(Boolean, default=False)      # уведомление о дожде за 30 минут

    # --- Домашний город, определённый по IP при регистрации ---
    home_city = Column(String, nullable=True)
    home_lat = Column(Float, nullable=True)
    home_lon = Column(Float, nullable=True)

    # --- Служебные поля для троттлинга уведомлений ---
    last_daily_notification = Column(DateTime, nullable=True)
    last_rain_notification = Column(DateTime, nullable=True)

    cities = relationship(
        "UserCity", back_populates="owner", cascade="all, delete-orphan"
    )


class UserCity(Base):
    """Города, добавленные пользователем (включая домашний)."""

    __tablename__ = "user_cities"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    name = Column(String, nullable=False)
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)
    is_home = Column(Boolean, default=False)

    owner = relationship("User", back_populates="cities")


class WeatherCache(Base):
    """Простой кэш ответов OpenWeatherMap, чтобы не дёргать API лишний раз."""

    __tablename__ = "weather_cache"

    id = Column(Integer, primary_key=True, index=True)
    cache_key = Column(String, unique=True, index=True, nullable=False)
    data = Column(String, nullable=False)  # JSON-строка с ответом OWM
    fetched_at = Column(DateTime, default=datetime.utcnow)
