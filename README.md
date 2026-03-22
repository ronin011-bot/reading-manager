# Reading Manager (Local Web App)

Local app to manage large reading libraries (focused on `~/Documents/READ`).

## Features (MVP)
- Scan local reading folder and index files
- Search by filename/path
- Filter by topic, status, age bucket
- Track status (`to_read`, `in_progress`, `done`)
- Add priority, tags, notes per file
- Rescan anytime from UI

## Quick start
```bash
cd reading-manager
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --reload --port 8787
```

Open: http://127.0.0.1:8787

## Config
Environment variable:
- `READINGS_ROOT` (optional)
  - default: `/Users/ronin/Documents/READ`

## Data storage
- SQLite DB at `reading-manager/data/readings.db`
- Only metadata is indexed (path/name/mtime/size)
- File contents are not modified

## Deploy
This app is now deploy-ready for platforms like Render/Railway/Fly:
- `requirements.txt` includes runtime deps
- `Procfile` defines start command
- `render.yaml` includes a one-click Render service config

Generic start command:
```bash
uvicorn app:app --host 0.0.0.0 --port $PORT
```

Note: on cloud deployments, `READINGS_ROOT` should point to an accessible folder in that environment.
