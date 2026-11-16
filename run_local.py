"""Optional local dev entrypoint; loads .env only in local development."""
from pathlib import Path
import os


def load_env():
    path = Path(__file__).parent / '.env'
    if not path.exists():
        raise SystemExit('Buat file .env dari .env.example terlebih dahulu.')
    for raw in path.read_text(encoding='utf-8').splitlines():
        if not raw or raw.lstrip().startswith('#') or '=' not in raw:
            continue
        key, value = raw.split('=', 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    os.environ['APP_ENV'] = 'local'


if __name__ == '__main__':
    load_env()
    import uvicorn
    uvicorn.run('app:app', host='127.0.0.1', port=8000, reload=False)
