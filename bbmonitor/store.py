"""Minimal Redis HTTP client for Upstash; no credentials go to browsers."""
from __future__ import annotations

import json
import os
import requests

PREFIX = 'bbs:v1:'


class StorageError(RuntimeError):
    pass


class Store:
    def __init__(self):
        self.url = os.environ.get('UPSTASH_REDIS_REST_URL', '').rstrip('/')
        self.token = os.environ.get('UPSTASH_REDIS_REST_TOKEN', '')
        if not self.url.startswith('https://') or not self.token:
            raise StorageError('Upstash REST URL/token belum dikonfigurasi.')

    def cmd(self, *command):
        try:
            response = requests.post(
                self.url,
                headers={'Authorization': f'Bearer {self.token}', 'Content-Type': 'application/json'},
                data=json.dumps(list(command)),
                timeout=(3, 8),
            )
            response.raise_for_status()
            result = response.json()
            if 'error' in result:
                raise StorageError('Redis command gagal.')
            return result.get('result')
        except (requests.RequestException, ValueError) as exc:
            raise StorageError('Koneksi ke penyimpanan gagal.') from exc

    def symbols(self):
        return sorted(self.cmd('SMEMBERS', PREFIX + 'symbols') or [])

    def paused_symbols(self):
        return set(self.cmd('SMEMBERS', PREFIX + 'paused') or [])

    def add(self, symbol):
        return self.cmd('SADD', PREFIX + 'symbols', symbol)

    def remove(self, symbol):
        self.cmd('SREM', PREFIX + 'symbols', symbol)
        self.cmd('SREM', PREFIX + 'paused', symbol)
        self.cmd('DEL', PREFIX + 'status:' + symbol)

    def set_paused(self, symbol, paused):
        if paused:
            self.cmd('SADD', PREFIX + 'paused', symbol)
        else:
            self.cmd('SREM', PREFIX + 'paused', symbol)

    def get_status(self, symbol):
        raw = self.cmd('GET', PREFIX + 'status:' + symbol)
        return json.loads(raw) if raw else None

    def save_status(self, symbol, data):
        self.cmd('SET', PREFIX + 'status:' + symbol, json.dumps(data, separators=(',', ':')))

    def claim_event(self, symbol, candle_open_ms):
        # NX = only first worker wins, EX = recover after 10 days.
        key = f'{PREFIX}alert:1h:{symbol}:{candle_open_ms}'
        return self.cmd('SET', key, 'sending', 'NX', 'EX', 864000) == 'OK'

    def release_event(self, symbol, candle_open_ms):
        self.cmd('DEL', f'{PREFIX}alert:1h:{symbol}:{candle_open_ms}')

    def scan_lock(self, job_id):
        return self.cmd('SET', PREFIX + 'scan_lock', job_id, 'NX', 'EX', 180) == 'OK'

    def unlock(self, job_id):
        # Atomic compare-and-delete via Redis Lua script avoids removing another worker's lock.
        script = 'if redis.call("GET",KEYS[1])==ARGV[1] then return redis.call("DEL",KEYS[1]) else return 0 end'
        self.cmd('EVAL', script, 1, PREFIX + 'scan_lock', job_id)

    def last_scan(self):
        raw = self.cmd('GET', PREFIX + 'last_scan')
        return json.loads(raw) if raw else None

    def save_last_scan(self, payload):
        self.cmd('SET', PREFIX + 'last_scan', json.dumps(payload, separators=(',', ':')))
