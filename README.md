# DAM: Central Asset Manager

## Setup
1. Copy `.env.example` to `.env`
2. `docker compose up -d`
3. `python -m venv venv` and activate it (`venv\Scripts\Activate.ps1` on Windows)
4. `pip install -r requirements.txt`
5. `flask --app run db upgrade`
6. `python seed.py`   (prints a demo API key, save it)
7. `python run.py`    (http://localhost:5000)

MinIO's S3 API is available at `http://localhost:9000`; its console is at
`http://localhost:9001`. The default local credentials are `dam-minio` and
`dam-minio-local-password`. Docker creates the `dam-assets` bucket and enables
anonymous downloads so local asset URLs work in a browser. Keep these ports
bound to localhost and change the credentials before sharing this setup. MinIO
handles browser CORS preflight requests for local presigned uploads. Docker
builds MinIO's community server and client from source because upstream no
longer publishes community container images; the initial build requires Go
module downloads and may take a few minutes.

## Demo logins (password: password123)
admin@acme.test, editor@acme.test, viewer@acme.test

## Public API
GET /api/public/assets/<publicId>
-> { publicId, name, type, version, url, mimeType, sizeBytes, updatedAt }

## Local MinIO storage
The local setup uses MinIO as an S3-compatible object store and requires no
AWS account or credentials. `.env.example` configures the MinIO endpoint,
credentials, bucket, and public asset URL.

For an existing asset, call `POST /api/assets/presign-upload` with its `assetId`, `filename`, and `contentType`. Upload the file with `PUT` to the returned `uploadUrl`, then call `POST /api/assets/<assetId>/confirm-upload` with the returned `storageKey`. To make it the current asset version, call `POST /api/assets/<assetId>/replace-version` with that key. Confirmation and replacement read the object's MIME type and size from S3 rather than trusting client metadata. Public API URLs and `/a/<publicId>` resolve through `PUBLIC_ASSET_BASE_URL`.

The app accepts legacy `AWS_*` environment variables for existing S3
configurations, but they are not needed for MinIO.