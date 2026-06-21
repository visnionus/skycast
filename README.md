# Skycast

Погодное веб-приложение с авторизацией пользователей.

**Стек:** FastAPI + SQLite (бэкенд), HTML/CSS/JS (фронтенд), OpenWeatherMap API.

## Возможности

- Регистрация и вход (JWT-токены, пароли хранятся в виде bcrypt-хэша)
- Автоопределение домашнего города через геолокацию браузера
- Текущая погода и прогноз на 5 дней
- Добавление/удаление городов через поиск
- Настройки: единицы измерения (°C/°F), формат времени, уведомления
- Кэширование запросов к OpenWeatherMap в SQLite

## Установка

```bash
git clone <ссылка на этот репозиторий>
cd skycast-backend
pip install -r requirements.txt
```

Скопируй `.env.example` в `.env` и впиши свои ключи:

```bash
cp .env.example .env
```

```dotenv
OWM_API_KEY=твой_ключ_с_openweathermap.org
SECRET_KEY=любая_случайная_строка
```

Сгенерировать `SECRET_KEY`:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

## Запуск

```bash
python -m uvicorn main:app --reload
```

Открой **http://localhost:8000**

## Структура проекта

```
main.py        — все маршруты API (авторизация, погода, города, настройки)
models.py      — таблицы базы данных (SQLAlchemy)
schemas.py     — схемы запросов/ответов API (Pydantic)
auth.py        — хэширование паролей и JWT-токены
database.py    — подключение к SQLite
config.py      — настройки из .env
static/        — фронтенд (index.html)
```
