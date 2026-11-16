"""Vercel-compatible FastAPI dashboard + protected monitoring endpoint."""
from __future__ import annotations

import hmac
import os
from pathlib import Path
from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Response, status
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from bbmonitor.security import COOKIE, TTL_SECONDS, password_valid, session_valid, sign_session
from bbmonitor.service import (
    MAX_SYMBOLS, normalize_symbol, scan_all, test_telegram, validate_spot_symbol,
)
from bbmonitor.store import Store, StorageError

app = FastAPI(title='BB Squeeze Monitor', docs_url=None, redoc_url=None)


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class InstrumentRequest(BaseModel):
    symbol: str = Field(min_length=3, max_length=30)


class PauseRequest(BaseModel):
    paused: bool


def admin(session: str | None = Cookie(default=None, alias=COOKIE)):
    if not session_valid(session):
        raise HTTPException(401, 'Masuk ke dashboard terlebih dahulu.')
    return True


def store():
    try:
        return Store()
    except StorageError as exc:
        raise HTTPException(503, str(exc)) from None


def is_cron_authorized(header):
    expected = os.environ.get('CRON_SECRET', '')
    return bool(len(expected) >= 24 and header and
                hmac.compare_digest(header, 'Bearer ' + expected))


@app.get('/api/health')
def health():
    return {'ok': True, 'service': 'bb-squeeze-monitor', 'timeframe': '1h'}


@app.post('/api/auth/login')
def login(body: LoginRequest, response: Response):
    if not password_valid(body.password):
        raise HTTPException(401, 'Password salah atau ADMIN_PASSWORD belum dikonfigurasi.')
    try:
        cookie = sign_session()
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from None
    response.set_cookie(
        COOKIE, cookie, max_age=TTL_SECONDS, httponly=True,
        secure=os.environ.get('APP_ENV') != 'local', samesite='strict', path='/',
    )
    return {'ok': True}


@app.post('/api/auth/logout')
def logout(response: Response):
    response.delete_cookie(COOKIE, path='/')
    return {'ok': True}


@app.get('/api/auth/me')
def whoami(session: str | None = Cookie(default=None, alias=COOKIE)):
    return {'authenticated': session_valid(session)}


@app.get('/api/instruments')
def list_instruments(_: bool = Depends(admin), db: Store = Depends(store)):
    symbols = db.symbols()
    paused = db.paused_symbols()
    return {
        'instruments': [
            {'symbol': symbol, 'paused': symbol in paused, 'status': db.get_status(symbol)}
            for symbol in symbols
        ],
        'last_scan': db.last_scan(), 'max_symbols': MAX_SYMBOLS, 'interval': '1h',
    }


@app.post('/api/instruments', status_code=201)
def add_instrument(body: InstrumentRequest, _: bool = Depends(admin), db: Store = Depends(store)):
    try:
        symbol = normalize_symbol(body.symbol)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    current = db.symbols()
    if symbol in current:
        raise HTTPException(409, 'Instrumen sudah ada di daftar.')
    if len(current) >= MAX_SYMBOLS:
        raise HTTPException(400, f'Maksimal {MAX_SYMBOLS} instrumen untuk versi ini.')
    try:
        info = validate_spot_symbol(symbol)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from None
    db.add(symbol)
    return {'ok': True, 'instrument': info}


@app.delete('/api/instruments/{symbol}')
def delete_instrument(symbol: str, _: bool = Depends(admin), db: Store = Depends(store)):
    try:
        symbol = normalize_symbol(symbol)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    if symbol not in db.symbols():
        raise HTTPException(404, 'Instrumen tidak ada.')
    db.remove(symbol)
    return {'ok': True}


@app.patch('/api/instruments/{symbol}/pause')
def pause_instrument(symbol: str, body: PauseRequest, _: bool = Depends(admin), db: Store = Depends(store)):
    try:
        symbol = normalize_symbol(symbol)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    if symbol not in db.symbols():
        raise HTTPException(404, 'Instrumen tidak ada.')
    db.set_paused(symbol, body.paused)
    return {'ok': True, 'paused': body.paused}


@app.post('/api/telegram/test')
def telegram_test(_: bool = Depends(admin)):
    try:
        test_telegram()
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from None
    return {'ok': True}


def perform_scan():
    try:
        return scan_all()
    except StorageError as exc:
        raise HTTPException(503, str(exc)) from None


@app.post('/api/scan')
def dashboard_scan(_: bool = Depends(admin)):
    return perform_scan()


@app.get('/api/monitor')
def scheduled_scan(authorization: str | None = Header(default=None)):
    if not is_cron_authorized(authorization):
        raise HTTPException(401, 'Unauthorized')
    return perform_scan()


# Root dashboard: static content, no secrets exposed in the browser.
app.mount('/', StaticFiles(directory=str(Path(__file__).parent / 'static'), html=True), name='dashboard')
