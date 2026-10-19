import os
import uuid

import boto3
from flask import current_app


def is_cloud_storage_enabled():
    provider = (current_app.config.get("CLOUD_STORAGE_PROVIDER") or "local").lower()
    return provider not in {"", "local", "none"}


def cloud_bucket_name():
    return (current_app.config.get("CLOUD_BUCKET_NAME") or "").strip()


def get_s3_client():
    if not is_cloud_storage_enabled() or not cloud_bucket_name():
        return None

    client_kwargs = {
        "region_name": current_app.config.get("AWS_REGION") or None,
    }
    endpoint_url = (current_app.config.get("AWS_S3_ENDPOINT_URL") or "").strip()
    if endpoint_url:
        client_kwargs["endpoint_url"] = endpoint_url

    access_key = (current_app.config.get("AWS_ACCESS_KEY_ID") or "").strip()
    secret_key = (current_app.config.get("AWS_SECRET_ACCESS_KEY") or "").strip()
    if access_key:
        client_kwargs["aws_access_key_id"] = access_key
    if secret_key:
        client_kwargs["aws_secret_access_key"] = secret_key

    return boto3.client("s3", **client_kwargs)


def build_storage_key(asset_id, filename):
    safe_name = os.path.basename((filename or "upload.bin").replace("\\", "/"))
    stem, extension = os.path.splitext(safe_name)
    return f"assets/{asset_id}/{uuid.uuid4().hex}_{stem[:40]}{extension.lower()}"


def asset_url_for(storage_key):
    base_url = (current_app.config.get("PUBLIC_ASSET_BASE_URL") or "").rstrip("/")
    if not base_url:
        return ""
    return f"{base_url}/{storage_key.lstrip('/')}"


def generate_presigned_upload_url(storage_key, content_type=None, expires_in=300):
    client = get_s3_client()
    if client is None:
        return None

    params = {"Bucket": cloud_bucket_name(), "Key": storage_key.lstrip("/")}
    if content_type:
        params["ContentType"] = content_type
    return client.generate_presigned_url(
        "put_object",
        Params=params,
        ExpiresIn=expires_in,
    )


def inspect_s3_object_info(storage_key):
    client = get_s3_client()
    if client is None:
        return None

    response = client.head_object(
        Bucket=cloud_bucket_name(),
        Key=storage_key.lstrip("/"),
    )
    mime_type = response.get("ContentType") or "application/octet-stream"
    return {
        "mimeType": mime_type,
        "sizeBytes": response.get("ContentLength", 0),
    }
