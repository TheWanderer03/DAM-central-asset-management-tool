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

## AWS S3 deployment
The app supports AWS S3 for uploads and CloudFront (or another CDN) for public asset delivery. Keep the S3 bucket private and grant the CloudFront distribution read access with Origin Access Control (OAC). Run the app with an IAM role, such as an ECS task role or EC2 instance profile, rather than long-lived AWS keys.

Set these variables in the deployment environment:

```env
CLOUD_STORAGE_PROVIDER=s3
CLOUD_BUCKET_NAME=your-private-bucket
AWS_REGION=us-east-1
PUBLIC_ASSET_BASE_URL=https://your-cloudfront-domain
```

Leave `AWS_S3_ENDPOINT_URL` empty for AWS S3. Boto3 uses its standard credential chain, so omit static `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` when using an IAM role. For local development, those variables may be set in `.env` and must never be committed. Configure `DATABASE_URL` for a managed PostgreSQL database (such as RDS), set a strong `SECRET_KEY`, and run `flask --app run db upgrade` during deployment. Restrict database access to the application security group.

The IAM role needs `s3:PutObject` and `s3:GetObject` on the bucket's application asset prefix. Configure bucket CORS to allow your browser application's origin to issue `PUT` requests with the `Content-Type` header. The browser must send the same content type used when requesting the presigned URL.

For an existing asset, call `POST /api/assets/presign-upload` with its `assetId`, `filename`, and `contentType`. Upload the file with `PUT` to the returned `uploadUrl`, then call `POST /api/assets/<assetId>/confirm-upload` with the returned `storageKey`. To make it the current asset version, call `POST /api/assets/<assetId>/replace-version` with that key. Confirmation and replacement read the object's MIME type and size from S3 rather than trusting client metadata. Public API URLs and `/a/<publicId>` resolve through `PUBLIC_ASSET_BASE_URL`.

For S3-compatible providers, set `AWS_S3_ENDPOINT_URL` and provide credentials as required by that service. The local filesystem remains the default when `CLOUD_STORAGE_PROVIDER=local`.