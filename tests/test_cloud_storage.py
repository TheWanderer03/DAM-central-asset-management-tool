from flask import Flask

from app.storage import (
    asset_url_for,
    build_storage_key,
    generate_presigned_upload_url,
    inspect_s3_object_info,
)


def make_app():
    app = Flask(__name__)
    app.config.update(
        CLOUD_STORAGE_PROVIDER="s3",
        CLOUD_BUCKET_NAME="dam-assets",
        PUBLIC_ASSET_BASE_URL="https://cdn.example.com/files/",
    )
    return app


def test_build_storage_key_uses_asset_prefix_and_sanitizes_filename():
    key = build_storage_key(42, r"..\private\logo.PNG")
    assert key.startswith("assets/42/")
    assert key.endswith("_logo.png")
    assert ".." not in key


def test_asset_url_joins_cdn_base_and_storage_key():
    with make_app().app_context():
        assert asset_url_for("/assets/42/logo.png") == (
            "https://cdn.example.com/files/assets/42/logo.png"
        )


def test_generate_presigned_upload_url_passes_content_type(monkeypatch):
    class S3Client:
        def generate_presigned_url(self, operation, *, Params, ExpiresIn):
            assert operation == "put_object"
            assert Params == {
                "Bucket": "dam-assets",
                "Key": "assets/42/logo.png",
                "ContentType": "image/png",
            }
            assert ExpiresIn == 300
            return "https://s3.example.com/upload"

    monkeypatch.setattr("app.storage.get_s3_client", lambda: S3Client())
    with make_app().app_context():
        assert generate_presigned_upload_url(
            "assets/42/logo.png",
            content_type="image/png",
        ) == "https://s3.example.com/upload"


def test_inspect_s3_object_info_reads_actual_s3_metadata(monkeypatch):
    class S3Client:
        def head_object(self, **kwargs):
            assert kwargs == {"Bucket": "dam-assets", "Key": "assets/42/logo.png"}
            return {"ContentType": "image/png", "ContentLength": 2048}

    monkeypatch.setattr("app.storage.get_s3_client", lambda: S3Client())
    with make_app().app_context():
        assert inspect_s3_object_info("/assets/42/logo.png") == {
            "mimeType": "image/png",
            "sizeBytes": 2048,
        }


def test_s3_client_uses_minio_settings_and_path_style(monkeypatch):
    captured = {}

    def fake_client(service_name, **kwargs):
        captured["service_name"] = service_name
        captured.update(kwargs)
        return object()

    monkeypatch.setattr("app.storage.boto3.client", fake_client)
    app = make_app()
    app.config.update(
        S3_ENDPOINT_URL="http://localhost:9000",
        S3_ACCESS_KEY_ID="dam-minio",
        S3_SECRET_ACCESS_KEY="local-secret",
        S3_REGION="us-east-1",
    )
    with app.app_context():
        from app.storage import get_s3_client

        get_s3_client()

    assert captured["service_name"] == "s3"
    assert captured["endpoint_url"] == "http://localhost:9000"
    assert captured["aws_access_key_id"] == "dam-minio"
    assert captured["aws_secret_access_key"] == "local-secret"
    assert captured["region_name"] == "us-east-1"
    assert captured["config"].s3["addressing_style"] == "path"
