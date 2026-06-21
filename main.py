"""
Skycast API — бэкенд погодного приложения.

Один файл со всеми маршрутами специально для простоты — так удобнее
объяснять на защите: всё в одном месте, читается сверху вниз.

Помимо этого файла есть:
  models.py    — таблицы базы данных (User, UserCity, WeatherCache)
  database.py  — подключение к SQLite
  auth.py      — хэширование паролей и JWT-токены
  config.py    — настройки из .env (ключ OWM, секрет JWT)
"""

import json
from datetime import datetime, timedelta

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session

import auth
import models
import schemas
from config import settings
from database import Base, engine, get_db

# Создаём таблицы в SQLite при первом запуске
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Skycast API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ════════════════════════════════════════════════════════════
# АВТОРИЗАЦИЯ
# ════════════════════════════════════════════════════════════

# Города "по умолчанию", которые получает каждый новый пользователь
DEFAULT_CITIES = [
    {"name": "Санкт-Петербург", "lat": 59.9343, "lon": 30.3351},
    {"name": "Москва", "lat": 55.7558, "lon": 37.6173},
    {"name": "Казань", "lat": 55.8304, "lon": 49.0661},
]


def user_to_schema(user: models.User) -> schemas.UserOut:
    """Собирает полный профиль пользователя для ответа фронтенду."""
    return schemas.UserOut(
        id=user.id,
        email=user.email,
        home_city=user.home_city,
        home_lat=user.home_lat,
        home_lon=user.home_lon,
        settings=schemas.UserSettings.model_validate(user),
        cities=[schemas.CityOut.model_validate(c) for c in user.cities],
    )


@app.post("/auth/register", response_model=schemas.Token, status_code=201)
async def register(user_in: schemas.UserCreate, request: Request, db: Session = Depends(get_db)):
    """Регистрация: создаёт пользователя и пытается определить город по IP."""

    if db.query(models.User).filter(models.User.email == user_in.email).first():
        raise HTTPException(400, "Пользователь с таким email уже существует")

    user = models.User(email=user_in.email, hashed_password=auth.hash_password(user_in.password))
    db.add(user)
    db.flush()  # получаем user.id до коммита, чтобы привязать к нему города

    # Пробуем определить город по IP (на localhost обычно не сработает —
    # тогда фронт сам спросит геолокацию у браузера)
    client_ip = request.client.host if request.client else ""
    location = await locate_by_ip(client_ip)

    home_name = None
    if location:
        user.home_city = location["city"]
        user.home_lat = location["lat"]
        user.home_lon = location["lon"]
        home_name = location["city"]
        db.add(models.UserCity(user_id=user.id, name=location["city"],
                                lat=location["lat"], lon=location["lon"], is_home=True))

    # Добавляем стандартный набор городов (кроме совпавшего с домашним)
    for city in DEFAULT_CITIES:
        if city["name"] != home_name:
            db.add(models.UserCity(user_id=user.id, **city, is_home=False))

    db.commit()
    token = auth.create_access_token({"sub": str(user.id)})
    return schemas.Token(access_token=token)


@app.post("/auth/login", response_model=schemas.Token)
def login(credentials: schemas.UserLogin, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == credentials.email).first()
    if not user or not auth.verify_password(credentials.password, user.hashed_password):
        raise HTTPException(401, "Неверный email или пароль")

    token = auth.create_access_token({"sub": str(user.id)})
    return schemas.Token(access_token=token)


@app.get("/auth/me", response_model=schemas.UserOut)
def get_me(current_user: models.User = Depends(auth.get_current_user)):
    return user_to_schema(current_user)


class LocationIn(BaseModel):
    lat: float
    lon: float


@app.post("/auth/set-location", response_model=schemas.UserOut)
async def set_location(
    loc: LocationIn,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """
    Сохраняет домашний город по координатам, которые browser определил
    через navigator.geolocation (используется, когда IP не дал результата,
    например на localhost при разработке).

    Сам узнаёт название города через OWM reverse geocoding — фронту
    достаточно прислать только lat/lon.
    """
    try:
        city_name = await reverse_geocode(loc.lat, loc.lon)
    except Exception:
        city_name = "Мой город"

    current_user.home_city = city_name
    current_user.home_lat = loc.lat
    current_user.home_lon = loc.lon

    home_city = next((c for c in current_user.cities if c.is_home), None)
    if home_city:
        home_city.name, home_city.lat, home_city.lon = city_name, loc.lat, loc.lon
    else:
        db.add(models.UserCity(user_id=current_user.id, name=city_name,
                                lat=loc.lat, lon=loc.lon, is_home=True))

    db.commit()
    db.refresh(current_user)
    return user_to_schema(current_user)


# ════════════════════════════════════════════════════════════
# ПОГОДА (проксирование OpenWeatherMap + кэш)
# ════════════════════════════════════════════════════════════

OWM_URL = "https://api.openweathermap.org/data/2.5"
CACHE_MINUTES = 10  # сколько держим закэшированный ответ "свежим"


async def fetch_weather(db: Session, kind: str, lat: float, lon: float, units: str) -> dict:
    """
    Берёт погоду от OWM с кэшированием в SQLite.
    kind = "weather" (текущая) или "forecast" (на 5 дней).
    Ответ всегда на русском (lang=ru) — упрощает приложение,
    не нужно тащить перевод по всему интерфейсу.
    """
    cache_key = f"{kind}:{round(lat,2)}:{round(lon,2)}:{units}"
    cached = db.query(models.WeatherCache).filter(models.WeatherCache.cache_key == cache_key).first()

    if cached and cached.fetched_at > datetime.utcnow() - timedelta(minutes=CACHE_MINUTES):
        return json.loads(cached.data)

    params = {"lat": lat, "lon": lon, "appid": settings.owm_api_key, "units": units, "lang": "ru"}
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"{OWM_URL}/{kind}", params=params)
        response.raise_for_status()
        data = response.json()

    if cached:
        cached.data, cached.fetched_at = json.dumps(data), datetime.utcnow()
    else:
        db.add(models.WeatherCache(cache_key=cache_key, data=json.dumps(data)))
    db.commit()

    return data


@app.get("/weather/current")
async def weather_current(
    lat: float = Query(...), lon: float = Query(...),
    db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_user),
):
    try:
        return await fetch_weather(db, "weather", lat, lon, current_user.units)
    except Exception:
        raise HTTPException(502, "Не удалось получить данные от сервиса погоды")


@app.get("/weather/forecast")
async def weather_forecast(
    lat: float = Query(...), lon: float = Query(...),
    db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_user),
):
    try:
        return await fetch_weather(db, "forecast", lat, lon, current_user.units)
    except Exception:
        raise HTTPException(502, "Не удалось получить данные от сервиса погоды")


# ════════════════════════════════════════════════════════════
# ГЕОКОДИНГ (поиск города по названию / по координатам)
# ════════════════════════════════════════════════════════════

async def locate_by_ip(ip: str) -> dict | None:
    """Определяет город по IP. На localhost обычно вернёт None."""
    if not ip or ip in ("127.0.0.1", "localhost", "::1", "testclient"):
        return None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"http://ip-api.com/json/{ip}?fields=status,city,lat,lon")
            data = r.json()
            if data.get("status") == "success":
                return {"city": data["city"], "lat": data["lat"], "lon": data["lon"]}
    except Exception:
        pass
    return None


async def geocode_city(query: str, limit: int = 5) -> list[dict]:
    """Поиск городов по названию (для кнопки 'добавить город')."""
    params = {"q": query, "limit": limit, "appid": settings.owm_api_key}
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get("https://api.openweathermap.org/geo/1.0/direct", params=params)
        r.raise_for_status()
        data = r.json()

    return [{
        "name": item.get("local_names", {}).get("ru") or item["name"],
        "country": item.get("country"),
        "state": item.get("state"),
        "lat": item["lat"],
        "lon": item["lon"],
    } for item in data]


async def reverse_geocode(lat: float, lon: float) -> str:
    """Название города по координатам (для браузерной геолокации)."""
    params = {"lat": lat, "lon": lon, "limit": 1, "appid": settings.owm_api_key}
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get("https://api.openweathermap.org/geo/1.0/reverse", params=params)
        r.raise_for_status()
        data = r.json()
    if data:
        return data[0].get("local_names", {}).get("ru") or data[0].get("name", "Мой город")
    return "Мой город"


# ════════════════════════════════════════════════════════════
# ГОРОДА ПОЛЬЗОВАТЕЛЯ
# ════════════════════════════════════════════════════════════

@app.get("/cities/search")
async def search_cities(q: str = Query(..., min_length=1), current_user: models.User = Depends(auth.get_current_user)):
    try:
        return await geocode_city(q)
    except Exception:
        raise HTTPException(502, "Не удалось выполнить поиск города")


@app.get("/cities", response_model=list[schemas.CityOut])
def list_cities(current_user: models.User = Depends(auth.get_current_user)):
    return current_user.cities


@app.post("/cities", response_model=schemas.CityOut, status_code=201)
def add_city(city: schemas.CityCreate, db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_user)):
    new_city = models.UserCity(user_id=current_user.id, name=city.name, lat=city.lat, lon=city.lon, is_home=False)
    db.add(new_city)
    db.commit()
    db.refresh(new_city)
    return new_city


@app.delete("/cities/{city_id}", status_code=204)
def delete_city(city_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_user)):
    city = db.query(models.UserCity).filter(models.UserCity.id == city_id, models.UserCity.user_id == current_user.id).first()
    if not city:
        raise HTTPException(404, "Город не найден")
    if city.is_home:
        raise HTTPException(400, "Невозможно удалить домашний город")
    db.delete(city)
    db.commit()


# ════════════════════════════════════════════════════════════
# НАСТРОЙКИ ПОЛЬЗОВАТЕЛЯ
# ════════════════════════════════════════════════════════════

VALID_UNITS = {"metric", "imperial"}
VALID_TIME_FORMATS = {"24h", "12h"}


@app.get("/settings", response_model=schemas.UserSettings)
def get_settings(current_user: models.User = Depends(auth.get_current_user)):
    return current_user


@app.patch("/settings", response_model=schemas.UserSettings)
def update_settings(update: schemas.UserSettingsUpdate, db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_user)):
    data = update.model_dump(exclude_unset=True)

    # Отбрасываем невалидные значения
    if "units" in data and data["units"] not in VALID_UNITS:
        data.pop("units")
    if "time_format" in data and data["time_format"] not in VALID_TIME_FORMATS:
        data.pop("time_format")

    for field, value in data.items():
        setattr(current_user, field, value)

    db.commit()
    db.refresh(current_user)
    return current_user


# ════════════════════════════════════════════════════════════
# УВЕДОМЛЕНИЯ
# ════════════════════════════════════════════════════════════

@app.get("/notifications/check")
async def check_notifications(db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_user)):
    """
    Фронт дёргает этот эндпоинт раз в несколько минут. Сервер сам решает,
    пора ли показать уведомление, и не спамит: "daily" — раз в час,
    "rain" — раз в 30 минут.
    """
    notifications = []
    if not current_user.home_lat or not current_user.home_lon:
        return {"notifications": notifications}

    lat, lon = current_user.home_lat, current_user.home_lon
    now = datetime.utcnow()

    if current_user.notify_daily:
        last = current_user.last_daily_notification
        if not last or now - last >= timedelta(hours=1):
            try:
                data = await fetch_weather(db, "weather", lat, lon, current_user.units)
                temp = round(data["main"]["temp"])
                unit = "°F" if current_user.units == "imperial" else "°C"
                notifications.append({
                    "type": "daily",
                    "title": f"Погода в {current_user.home_city}",
                    "message": f"Сейчас {temp}{unit}, {data['weather'][0]['description']}",
                })
                current_user.last_daily_notification = now
            except Exception:
                pass

    if current_user.notify_rain:
        last = current_user.last_rain_notification
        if not last or now - last >= timedelta(minutes=30):
            try:
                data = await fetch_weather(db, "forecast", lat, lon, current_user.units)
                nearest = data.get("list", [{}])[0]
                main = nearest.get("weather", [{}])[0].get("main", "").lower()
                if main in ("rain", "drizzle", "thunderstorm"):
                    notifications.append({
                        "type": "rain",
                        "title": "Скоро дождь",
                        "message": "В ближайшее время ожидаются осадки, возьмите зонт ☔",
                    })
                    current_user.last_rain_notification = now
            except Exception:
                pass

    if notifications:
        db.commit()

    return {"notifications": notifications}


# ════════════════════════════════════════════════════════════
# СТАТИКА (фронтенд)
# ════════════════════════════════════════════════════════════

@app.get("/api")
def api_root():
    return {"status": "ok", "service": "Skycast API"}


# Mount регистрируем последним, чтобы он не перехватывал маршруты выше
app.mount("/", StaticFiles(directory="static", html=True), name="static")
