from __future__ import annotations

import math
import os
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as api_app
from bbmonitor import indicator, security, service


class FakeStore:
    def __init__(self):
        self.tracked = []
        self.paused = set()
        self.statuses = {}
        self.alerts = set()
        self.last = None
        self.locked = False
    def symbols(self): return sorted(self.tracked)
    def paused_symbols(self): return self.paused.copy()
    def add(self, s):
        if s not in self.tracked: self.tracked.append(s)
    def remove(self, s):
        self.tracked.remove(s)
        self.paused.discard(s)
        self.statuses.pop(s, None)
    def set_paused(self, s, paused):
        if paused: self.paused.add(s)
        else: self.paused.discard(s)
    def get_status(self, s): return self.statuses.get(s)
    def save_status(self, s, status): self.statuses[s] = status
    def claim_event(self, s, t):
        if (s, t) in self.alerts: return False
        self.alerts.add((s, t))
        return True
    def release_event(self, s, t): self.alerts.remove((s, t))
    def scan_lock(self, t):
        if self.locked: return False
        self.locked = True
        return True
    def unlock(self, t): self.locked = False
    def last_scan(self): return self.last
    def save_last_scan(self, data): self.last = data


def synthetic_bars(length=200):
    now = 1_750_000_000_000
    return [indicator.Candle(
        open_ms=now + i * 3600_000, close_ms=now + (i + 1) * 3600_000 - 1,
        close=100 + math.sin(i / 3) * 1.7)
        for i in range(length)]


def test_indicator_math():
    candles = synthetic_bars()
    result = indicator.snapshots(candles, period=20, lookback=120)
    assert len(result) == 200
    assert result[-1].bandwidth_pct is not None
    assert result[-1].cutoff_pct is not None
    assert isinstance(result[-1].is_squeeze, bool)
    assert result[-1].upper > result[-1].middle > result[-1].lower


def test_normalize():
    assert service.normalize_symbol(' btc/usdt ') == 'BTCUSDT'
    assert service.normalize_symbol('ETH-USDT') == 'ETHUSDT'
    import pytest
    with pytest.raises(ValueError): service.normalize_symbol('bad!')


def test_session(monkeypatch):
    monkeypatch.setenv('ADMIN_PASSWORD', 'very-long-example-password-1234')
    monkeypatch.setenv('SESSION_SECRET', 'secure-test-key-at-least-32-characters')
    assert security.password_valid('very-long-example-password-1234')
    assert not security.password_valid('other')
    token = security.sign_session(now=100)
    assert security.session_valid(token, now=101)
    assert not security.session_valid(token, now=100 + security.TTL_SECONDS + 5)
    assert not security.session_valid(token[:-4] + 'zzzz', now=101)


def test_api_access_and_crud(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'local')
    monkeypatch.setenv('ADMIN_PASSWORD', 'secure-example-test-password')
    monkeypatch.setenv('SESSION_SECRET', 'test-session-secret-longer-than-32-chars')
    monkeypatch.setenv('CRON_SECRET', 'test-cron-secret-more-than-24-chars')
    db = FakeStore()
    api_app.app.dependency_overrides[api_app.store] = lambda: db
    client = TestClient(api_app.app)
    try:
        assert client.get('/api/health').status_code == 200
        assert client.get('/api/instruments').status_code == 401
        assert client.get('/api/monitor').status_code == 401
        assert client.post('/api/auth/login', json={'password': 'wrong'}).status_code == 401
        assert client.post('/api/auth/login', json={'password': 'secure-example-test-password'}).status_code == 200
        assert client.get('/api/instruments').status_code == 200
        with patch.object(api_app, 'validate_spot_symbol', return_value={'symbol':'BTCUSDT','base':'BTC','quote':'USDT'}):
            resp = client.post('/api/instruments', json={'symbol':'btc/usdt'})
        assert resp.status_code == 201
        assert db.tracked == ['BTCUSDT']
        assert client.post('/api/instruments', json={'symbol':'BTCUSDT'}).status_code == 409
        assert client.patch('/api/instruments/BTCUSDT/pause', json={'paused':True}).status_code == 200
        assert db.paused == {'BTCUSDT'}
        assert client.patch('/api/instruments/BTCUSDT/pause', json={'paused':False}).status_code == 200
        assert db.paused == set()
        assert client.delete('/api/instruments/BTCUSDT').status_code == 200
        assert not db.tracked
        with patch.object(api_app, 'scan_all', return_value={'state': 'completed', 'total': 0}):
            assert client.get('/api/monitor', headers={'Authorization':'Bearer test-cron-secret-more-than-24-chars'}).status_code == 200
        assert client.post('/api/auth/logout').status_code == 200
        assert client.get('/api/instruments').status_code == 401
    finally:
        api_app.app.dependency_overrides.clear()


def test_scanner_disabled_and_lock():
    db = FakeStore()
    db.add('BTCUSDT')
    db.set_paused('BTCUSDT', True)
    with patch.object(service, 'get_candles', side_effect=AssertionError('should not fetch')):
        scanned = service.scan_all(db)
    assert scanned['total'] == 0
    assert db.last['alerts'] == 0
    db.locked = True
    assert service.scan_all(db)['state'] == 'already_running'


def test_scanner_normal_and_no_alert(monkeypatch):
    db = FakeStore()
    db.add('BTCUSDT')
    bars = synthetic_bars()
    # Mock market data with recent candle times so age check passes.
    now_ms = 1_750_000_000_000 + 200*3600_000
    with patch.object(service.time, 'time', return_value=now_ms/1000), \
         patch.object(service, 'get_candles', return_value=bars), \
         patch.object(service, 'send_telegram', side_effect=AssertionError('unexpected Telegram')):
        result = service.scan_all(db)
    assert result['total'] == 1
    assert result['errors'] == 0
    assert db.get_status('BTCUSDT')['state'] in ('normal','squeeze')
    assert not result['alerts']


def test_squeeze_transition_sends_once(monkeypatch):
    db = FakeStore()
    db.add('BTCUSDT')
    # Simulate the exact moment a fresh, confirmed 1H candle changes state.
    base = 1_850_000_000_000
    old = indicator.Snapshot(open_ms=base - 7200_000, close_ms=base - 3600_001,
        price=100.0, middle=100.0, upper=103.0, lower=97.0,
        bandwidth_pct=6.0, cutoff_pct=3.0, is_squeeze=False)
    new = indicator.Snapshot(open_ms=base - 3600_000, close_ms=base - 1,
        price=100.0, middle=100.0, upper=100.4, lower=99.6,
        bandwidth_pct=0.8, cutoff_pct=2.0, is_squeeze=True)
    with patch.object(service.time, 'time', return_value=(base + 90_000) / 1000), \
         patch.object(service, 'get_candles', return_value=synthetic_bars()), \
         patch.object(service, 'snapshots', return_value=[old, new]), \
         patch.object(service, 'send_telegram') as send:
        first = service.scan_one('BTCUSDT', db)
        second = service.scan_one('BTCUSDT', db)
        assert first['alert_sent'] is True
        assert second['alert_sent'] is False
        send.assert_called_once()
        assert len(db.alerts) == 1


def test_failed_telegram_allows_retry():
    db = FakeStore()
    base = 1_850_000_000_000
    old = indicator.Snapshot(open_ms=base - 7200_000, close_ms=base - 3600_001,
        price=100.0, middle=100.0, upper=103.0, lower=97.0,
        bandwidth_pct=6.0, cutoff_pct=3.0, is_squeeze=False)
    new = indicator.Snapshot(open_ms=base - 3600_000, close_ms=base - 1,
        price=100.0, middle=100.0, upper=100.4, lower=99.6,
        bandwidth_pct=0.8, cutoff_pct=2.0, is_squeeze=True)
    with patch.object(service.time, 'time', return_value=(base + 90_000) / 1000), \
         patch.object(service, 'get_candles', return_value=synthetic_bars()), \
         patch.object(service, 'snapshots', return_value=[old, new]), \
         patch.object(service, 'send_telegram', side_effect=RuntimeError('Network error')):
        result = service.scan_one('BTCUSDT', db)
    assert result['state'] == 'error'
    assert not db.alerts
