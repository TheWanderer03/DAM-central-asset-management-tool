# DAM: Central Asset Manager

A multi-tenant digital asset manager. Assets are stored in an S3-compatible
object store (MinIO locally), managed through a web dashboard, and served to
customer websites through a public API.

## Setup
1. Copy `.env.example` to `.env`
2. `docker compose up -d`
3. `python -m venv venv` and activate it (`venv\Scripts\Activate.ps1` on Windows)
4. `pip install -r requirements.txt`
5. `flask --app run db upgrade`
6. `python seed.py`   (prints a demo API key, save it)
7. `python run.py`    (http://localhost:5000)

Run `flask --app run db upgrade` again after pulling changes that add
migrations (for example the user active-status migration).

### MinIO
MinIO's S3 API is available at `http://localhost:9000`; its console is at
`http://localhost:9001`. The default local credentials are `dam-minio` and
`dam-minio-local-password`. Docker creates the `dam-assets` bucket and enables
anonymous downloads so local asset URLs work in a browser. Keep these ports
bound to localhost and change the credentials before sharing this setup. MinIO
handles browser CORS preflight requests for local presigned uploads. Docker
builds MinIO's community server and client from source because upstream no
longer publishes community container images; the initial build requires Go
module downloads and may take a few minutes.

Note that the bucket is public-read in local development, so anyone who knows
an object's URL can download it. Do not store private files in it.

## Demo logins (password: password123)
admin@acme.test, editor@acme.test, viewer@acme.test

## Dashboard
After `python run.py`, open http://localhost:5000 and log in with one of the
demo accounts. Pages:

| Page | Path | Purpose |
|------|------|---------|
| Dashboard | `/` | Overview |
| Assets | `/assets` | Browse, search and filter assets; open an asset for its versions and usage |
| Upload | `/upload` | Upload a new asset |
| Websites | `/websites` | Register websites and create or revoke their API keys |
| Users | `/users` | Manage users and account status |
| Settings | `/settings` | Organisation settings |

Actions are role-based: viewers can browse, editors can upload and replace
assets, and admins can manage websites, API keys, users and the audit log
(`GET /api/audit`).

## Demo website
`dummy-website/index.html` is a sample customer site that loads assets through
the public API. Serve it from a different origin than the dashboard, for
example:

```
cd dummy-website
python -m http.server 4000
```

then open http://localhost:4000. Use the demo API key printed by `seed.py`, or
create a key on the Websites page. The public API allows cross-origin requests,
so the demo site can call it from another port.

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

## Tests
```
python -m pytest
```
`tests/test_cloud_storage.py` covers the MinIO routes. `tests/test_isolation.py`
(tenant isolation) is still a stub and errors on a missing fixture.

## Known limitations
- The dashboard Upload page (`POST /api/assets/upload`) still saves files to the
  local `uploads/` folder. Only the presign, confirm and replace API routes use
  MinIO.
- Tenant isolation is not yet covered by a working test.

## Production
TODO: restore the AWS S3 and CloudFront deployment notes (IAM role guidance and
S3 CORS requirements) that were removed from this file. They are still in the
`main` branch history: `git show origin/main:README.md`.