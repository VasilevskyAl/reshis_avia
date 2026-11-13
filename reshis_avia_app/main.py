import os
import sqlite3
import hashlib
import secrets
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import FileResponse
from pydantic import BaseModel
import pandas as pd

app = FastAPI()
FORECAST_FILE = "Demidov_Artem_Andreevich_my_forecast.csv"
ROUTES_FILE = "routes.csv"
DB_FILE = "users.db"

forecast_df = pd.read_csv(FORECAST_FILE)
if os.path.exists(ROUTES_FILE):
    routes_df = pd.read_csv(ROUTES_FILE)
else:
    routes_df = pd.DataFrame([
        {"route": "MOW-AER", "base_price": 9000.0, "price_sensitivity": 0.14},
        {"route": "MOW-KGD", "base_price": 7500.0, "price_sensitivity": 0.10},
        {"route": "MOW-LED", "base_price": 5500.0, "price_sensitivity": 0.30},
        {"route": "MOW-MRV", "base_price": 8000.0, "price_sensitivity": 0.07},
        {"route": "MOW-OVB", "base_price": 12000.0, "price_sensitivity": 0.03},
    ])


def init_db():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, username TEXT NOT NULL)")
    conn.commit()
    conn.close()


init_db()


def hash_password(pwd: str) -> str:
    return hashlib.sha256(("reshis_avia_salt_" + pwd).encode("utf-8")).hexdigest()


def get_current_user(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Требуется авторизация")
    token = authorization.removeprefix("Bearer ").strip()
    conn = sqlite3.connect(DB_FILE)
    row = conn.execute("SELECT username FROM sessions WHERE token = ?", (token,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=401, detail="Сессия истекла, войдите снова")
    return next(iter(row))


class AuthRequest(BaseModel):
    username: str
    password: str


class PredictRequest(BaseModel):
    origin: str
    destination: str
    date: str
    price: float


@app.get("/")
def serve_frontend():
    return FileResponse("index.html", headers={"Cache-Control": "no-store"})


@app.post("/api/register")
def register(req: AuthRequest):
    uname = req.username.strip()
    if len(uname) < 3 or len(req.password) < 4:
        raise HTTPException(status_code=400, detail="Логин от 3 символов, пароль от 4 символов")
    conn = sqlite3.connect(DB_FILE)
    try:
        conn.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", (uname, hash_password(req.password)))
        token = secrets.token_hex(24)
        conn.execute("INSERT INTO sessions (token, username) VALUES (?, ?)", (token, uname))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail="Пользователь с таким логином уже существует")
    conn.close()
    return {"token": token, "username": uname}


@app.post("/api/login")
def login(req: AuthRequest):
    uname = req.username.strip()
    conn = sqlite3.connect(DB_FILE)
    row = conn.execute("SELECT password_hash FROM users WHERE username = ?", (uname,)).fetchone()
    if not row or next(iter(row)) != hash_password(req.password):
        conn.close()
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    token = secrets.token_hex(24)
    conn.execute("INSERT INTO sessions (token, username) VALUES (?, ?)", (token, uname))
    conn.commit()
    conn.close()
    return {"token": token, "username": uname}


@app.post("/api/logout")
def logout(authorization: str | None = Header(default=None)):
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
        conn = sqlite3.connect(DB_FILE)
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()
        conn.close()
    return {"status": "ok"}


@app.post("/api/predict")
def predict_demand(req: PredictRequest, authorization: str | None = Header(default=None)):
    get_current_user(authorization)
    rc = f"{req.origin.strip().upper()}-{req.destination.strip().upper()}"
    fl = forecast_df[(forecast_df["route"] == rc) & (forecast_df["date"] == req.date)]
    if fl.empty:
        raise HTTPException(status_code=404, detail=f"Рейс {rc} на {req.date} не найден")
    ri = routes_df[routes_df["route"] == rc]
    if ri.empty:
        raise HTTPException(status_code=404, detail=f"Маршрут {rc} не найден")
    frow = next(iter(fl.to_dict(orient="records")))
    rrow = next(iter(ri.to_dict(orient="records")))
    seats = int(frow["seats"])
    bp = float(rrow["base_price"])
    ps = float(rrow["price_sensitivity"])
    qs = []
    med = 0.0
    for q in ("0.1", "0.2", "0.3", "0.4", "0.5", "0.6", "0.7", "0.8", "0.9"):
        qd = round(float(frow[f"{q}_base_demand"]) - ps * (req.price - bp), 1)
        qs.append({"quantile": q, "demand": qd})
        if q == "0.5":
            med = qd
    return {
        "route": rc,
        "date": req.date,
        "seats": seats,
        "desired": med,
        "sold": round(min(seats, max(0.0, med))),
        "quantiles": qs,
    }
