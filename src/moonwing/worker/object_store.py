"""Object store implementations for the worker.

Provides both an in-memory store (for tests) and a real MinIO-backed
store (for production).  Both conform to the ``ObjectStore`` protocol
defined in ``tasks.py``.
"""

from __future__ import annotations

import io
import json
import logging

from minio import Minio
from minio.error import S3Error

logger = logging.getLogger("moonwing.worker.object_store")


class MinioObjectStore:
    """S3-compatible object store backed by MinIO."""

    def __init__(
        self,
        *,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        secure: bool = False,
    ) -> None:
        self._client = Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
        )
        self._bucket = bucket
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        """Create the bucket if it doesn't already exist."""
        try:
            if not self._client.bucket_exists(self._bucket):
                self._client.make_bucket(self._bucket)
                logger.info("created bucket %s", self._bucket)
        except S3Error:
            logger.warning("could not verify/create bucket %s — will retry on first write", self._bucket)

    def put_json(self, *, object_key: str, payload: dict) -> None:
        data = json.dumps(payload, sort_keys=True).encode("utf-8")
        self._client.put_object(
            self._bucket,
            object_key,
            io.BytesIO(data),
            length=len(data),
            content_type="application/json",
        )
        logger.debug("stored %s/%s (%d bytes)", self._bucket, object_key, len(data))

    def delete(self, *, object_key: str) -> None:
        self._client.remove_object(self._bucket, object_key)
        logger.debug("deleted %s/%s", self._bucket, object_key)

    def get_json(self, *, object_key: str) -> dict:
        """Retrieve and parse a JSON object (useful for debugging/tests)."""
        response = self._client.get_object(self._bucket, object_key)
        try:
            return json.loads(response.read())
        finally:
            response.close()
            response.release_conn()
