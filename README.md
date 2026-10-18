# DAM: Central Asset Manager

## Setup
1. `docker compose up -d`
2. `python -m venv venv && source venv/bin/activate`
3. `pip install -r requirements.txt`
4. `cp .env.example .env`
5. `flask --app run db upgrade`
6. `python seed.py`   (prints a demo API key, save it)
7. `python run.py`    (http://localhost:5000)

## Demo logins (password: password123)
admin@acme.test, editor@acme.test, viewer@acme.test

## Public API
GET /api/public/assets/<publicId>
-> { publicId, name, type, version, url, mimeType, sizeBytes, updatedAt }