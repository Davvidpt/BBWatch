"""Binance public spot candles and Telegram-only alerts (no trading endpoints)."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import logging
import os
import re
import time
import uuid

import requests

from .indicator import Candle, snapshots, squeeze_started
from .store import Store

LOG = logging.getLogger(__name__)
BASE = 'https://data-api.binance.vision'
SYMBOL_PATTERN = re.compile(r'^[A-Z0-9]{5,20}$')
MAX_SYMBOLS = 20


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def normalize_symbol(value: str) -> str:
    symbol = value.strip().upper().replace('/', '').replace('-', '').replace(' ', '')
    if not SYMBOL_PATTERN.fullmatch(symbol):
        raise ValueError('Simbol tidak valid. Contoh: BTCUSDT atau ETHUSDT.')
    return symbol


def validate_spot_symbol(symbol: str) -> dict:
    """Check that user input corresponds to a real Binance spot symbol."""
    try:
        response = requests.get(BASE + '/api/v3/exchangeInfo', params={'symbol': symbol}, timeout=(4, 8))
        if response.status_code in (400, 404):
            raise ValueError(f'{symbol} tidak ditemukan di Binance Spot.')
        response.raise_for_status()
        symbols = response.json().get('symbols', [])
        if len(symbols) != 1 or symbols[0].get('symbol') != symbol:
            raise ValueError('Simbol tidak terdaftar di Binance Spot.')
        s = symbols[0]
        if s.get('status') != 'TRADING' or not (s.get('isSpotTradingAllowed', 'SPOT' in s.get('permissions', []))):
            raise ValueError(f'{symbol} saat ini tidak aktif di pasar spot Binance.')
        return {'symbol': symbol, 'base': s.get('baseAsset', ''), 'quote': s.get('quoteAsset', '')}
    except requests.RequestException:
        raise RuntimeError('Tidak dapat memvalidasi simbol ke Binance. Coba lagi nanti.') from None


def get_candles(symbol: str):
    try:
        response = requests.get(
            BASE + '/api/v3/klines',
            params={'symbol': symbol, 'interval': '1h', 'limit': 200},
            timeout=(4, 10),
        )
        response.raise_for_status()
        rows = response.json()
        if not isinstance(rows, list):
            raise ValueError('Format data candle tidak sesuai.')
        grace_time = int(time.time() * 1000) - 30_000
        return [Candle(open_ms=int(row[0]), close_ms=int(row[6]), close=float(row[4]))
                for row in rows if int(row[6]) < grace_time]
    except requests.RequestException:
        raise RuntimeError('Gagal mengambil candle dari Binance.') from None


def send_telegram(symbol: str, snap):
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    chat = os.environ.get('TELEGRAM_CHAT_ID', '')
    if not token or not chat:
        raise RuntimeError('Telegram token / chat ID belum diatur.')
    candle_utc = datetime.fromtimestamp(snap.close_ms / 1000, timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    # Quote asset can vary; do not wrongly label non-USDT pairs.
    text = (
        '🔔 BOLLINGER BAND SQUEEZE\n\n'
        f'Instrumen: {symbol} (Binance Spot)\n'
        'Timeframe: 1H\n'
        f'Candle close: {candle_utc}\n'
        f'Harga close: {snap.price:,.8g}\n'
        f'BandWidth: {snap.bandwidth_pct:.3f}%\n'
        f'Historical P20: {snap.cutoff_pct:.3f}%\n\n'
        'Volatilitas relatif rendah; arah breakout belum diketahui.\n'
        'Notifikasi edukatif, bukan sinyal transaksi.'
    )
    try:
        resp = requests.post(
            f'https://api.telegram.org/bot{token}/sendMessage',
            json={'chat_id': chat, 'text': text, 'disable_web_page_preview': True},
            timeout=(4, 10),
        )
        success = resp.status_code == 200 and resp.json().get('ok') is True
    except (requests.RequestException, ValueError):
        raise RuntimeError('Koneksi Telegram gagal (detail disembunyikan).') from None
    if not success:
        raise RuntimeError(f'Telegram menolak notifikasi (HTTP {resp.status_code}).')


def test_telegram():
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    chat = os.environ.get('TELEGRAM_CHAT_ID', '')
    if not token or not chat:
        raise RuntimeError('Telegram token / chat ID belum diatur.')
    try:
        resp = requests.post(
            f'https://api.telegram.org/bot{token}/sendMessage',
            json={'chat_id': chat, 'text': '✅ BB Squeeze Monitor: tes notifikasi berhasil.'},
            timeout=(4, 10),
        )
        if resp.status_code != 200 or not resp.json().get('ok'):
            raise RuntimeError(f'Telegram menolak pesan tes (HTTP {resp.status_code}).')
    except requests.RequestException:
        raise RuntimeError('Koneksi Telegram gagal (detail disembunyikan).') from None


def scan_one(symbol: str, store: Store):
    """One latest *closed* candle per symbol; no repeated alert on same transition."""
    snap = None
    try:
        bars = get_candles(symbol)
        if len(bars) < 142:
            raise RuntimeError('Riwayat candle belum cukup untuk BB(20) + 120 lookback.')
        series = snapshots(bars, period=20, deviation=2.0, lookback=120, squeeze_percentile=20)
        snap, previous = series[-1], series[-2]
        if snap.is_squeeze is None:
            raise RuntimeError('Belum ada cukup data untuk menentukan status squeeze.')
        age_minutes = (int(time.time() * 1000) - snap.close_ms) / 60000
        if age_minutes > 150:
            raise RuntimeError('Data candle terlalu lama; alert dibatalkan untuk mencegah sinyal kedaluwarsa.')
        status = {
            'symbol': symbol,
            'state': 'squeeze' if snap.is_squeeze else 'normal',
            'price': snap.price,
            'bandwidth': round(snap.bandwidth_pct, 4),
            'threshold': round(snap.cutoff_pct, 4),
            'middle': round(snap.middle, 8),
            'upper': round(snap.upper, 8),
            'lower': round(snap.lower, 8),
            'candle_at': datetime.fromtimestamp(snap.close_ms / 1000, timezone.utc).isoformat(),
            'checked_at': timestamp(),
            'alert_sent': False,
        }
        if squeeze_started(previous, snap) and age_minutes <= 150 and store.claim_event(symbol, snap.open_ms):
            try:
                send_telegram(symbol, snap)
                status['alert_sent'] = True
            except Exception:
                # Allow a later retry after a failed send. Extreme network timeouts
                # after Telegram accepted can still create a duplicate on retry.
                store.release_event(symbol, snap.open_ms)
                raise
        store.save_status(symbol, status)
        return {'symbol': symbol, 'state': status['state'], 'alert_sent': status['alert_sent']}
    except Exception as exc:
        LOG.warning('Monitor %s gagal: %s', symbol, type(exc).__name__)
        safe = str(exc) if isinstance(exc, (RuntimeError, ValueError)) else 'Terjadi kesalahan saat monitoring.'
        store.save_status(symbol, {'symbol': symbol, 'state': 'error', 'message': safe, 'checked_at': timestamp()})
        return {'symbol': symbol, 'state': 'error', 'message': safe}


def scan_all(store=None):
    store = store or Store()
    job_id = uuid.uuid4().hex
    if not store.scan_lock(job_id):
        return {'state': 'already_running', 'message': 'Pemindaian lain masih berlangsung.'}
    try:
        symbols = store.symbols()
        paused = store.paused_symbols()
        active = [s for s in symbols if s not in paused]
        results = []
        if active:
            with ThreadPoolExecutor(max_workers=min(5, len(active))) as pool:
                futures = [pool.submit(scan_one, symbol, store) for symbol in active]
                for future in as_completed(futures):
                    results.append(future.result())
        payload = {
            'state': 'completed', 'checked_at': timestamp(), 'total': len(active),
            'alerts': sum(bool(r.get('alert_sent')) for r in results),
            'errors': sum(r['state'] == 'error' for r in results),
            'results': sorted(results, key=lambda r: r['symbol']),
        }
        store.save_last_scan(payload)
        return payload
    finally:
        store.unlock(job_id)
