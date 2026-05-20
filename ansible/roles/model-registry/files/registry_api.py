#!/usr/bin/env python3
"""
Hydra Model Registry API
Minimal FastAPI service for air-gapped LLM model management.

Artifacts stored in MinIO (:9000), metadata in Fleet Platform PostgreSQL.
sha256 is verified at registration time against the MinIO object.

Endpoints:
  GET    /health
  GET    /api/v1/models
  GET    /api/v1/models/{name}
  POST   /api/v1/models/register     (admin token required)
  POST   /api/v1/models/{name}/approve
  DELETE /api/v1/models/{name}
"""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from typing import Optional

import psycopg2
from fastapi import FastAPI, HTTPException, Depends, Header
from minio import Minio
from minio.error import S3Error
from pydantic import BaseModel

app = FastAPI(
    title="Hydra Model Registry",
    version="1.0",
    description="Air-gapped LLM model catalog — approval workflow + sha256 verification",
)

DATABASE_URL  = os.environ["DATABASE_URL"]
MINIO_URL     = os.environ["MINIO_URL"].removeprefix("http://").removeprefix("https://")
MINIO_BUCKET  = os.environ.get("MINIO_BUCKET", "hydra-models")
ADMIN_TOKEN   = os.environ.get("ADMIN_TOKEN", "changeme-admin")

minio = Minio(
    MINIO_URL,
    access_key=os.environ["MINIO_ACCESS_KEY"],
    secret_key=os.environ["MINIO_SECRET_KEY"],
    secure=False,
)


def _db():
    """Open a new psycopg2 connection.

    Usage pattern::

        conn = _db()
        try:
            with conn.cursor() as cur:
                ...
            conn.commit()
        finally:
            conn.close()

    NOTE: psycopg2 uses the connection as a context manager for *transactions*
    only — it does NOT close the connection on __exit__.  Always call
    conn.close() explicitly (or use the helper below).
    """
    return psycopg2.connect(DATABASE_URL)


def _require_admin(authorization: str = Header(...)):
    token = authorization.removeprefix("Bearer ").strip()
    if token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Admin token required")


# ── Schema ────────────────────────────────────────────────────────────────────

class ModelRegisterRequest(BaseModel):
    name: str
    version: str = "1.0"
    format: str               # GGUF | MLX
    quantization: str         # Q4_K_M | F16 | MXFP4 | Q3_K_M ...
    size_bytes: int
    sha256: str               # hex digest of the model file
    minio_path: str           # path within bucket, e.g. gguf/Llama-3-8B-Q4_K_M.gguf
    pool_assignment: str      # fast | reason | largepool | vision | embed | speech
    license: Optional[str] = None
    notes: Optional[str] = None


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "model-registry"}


# ── List models ───────────────────────────────────────────────────────────────

@app.get("/api/v1/models")
def list_models(pool: Optional[str] = None, approved_only: bool = True):
    conn = _db()
    try:
        with conn.cursor() as cur:
            query = """
                SELECT name, version, format, quantization, size_bytes,
                       pool_assignment, approved_by, approved_at,
                       sha256, minio_path, license, created_at
                FROM hydra_models
            """
            filters = []
            params: list = []
            if pool:
                filters.append("pool_assignment = %s")
                params.append(pool)
            if approved_only:
                filters.append("approved_by IS NOT NULL")
            if filters:
                query += " WHERE " + " AND ".join(filters)
            query += " ORDER BY pool_assignment, name"
            cur.execute(query, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


# ── Get single model ──────────────────────────────────────────────────────────

@app.get("/api/v1/models/{name}")
def get_model(name: str):
    conn = _db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM hydra_models WHERE name = %s", (name,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail=f"Model {name!r} not found")
            cols = [d[0] for d in cur.description]
            result = dict(zip(cols, row))
    finally:
        conn.close()
    # Add a pre-signed download URL (valid 1 hour)
    try:
        from datetime import timedelta
        url = minio.presigned_get_object(
            MINIO_BUCKET, result["minio_path"],
            expires=timedelta(hours=1),
        )
        result["download_url"] = url
    except Exception:
        result["download_url"] = None
    return result


# ── Register model ────────────────────────────────────────────────────────────

@app.post("/api/v1/models/register", dependencies=[Depends(_require_admin)])
def register_model(req: ModelRegisterRequest):
    # 1. Verify artifact exists in MinIO
    try:
        stat = minio.stat_object(MINIO_BUCKET, req.minio_path)
    except S3Error as e:
        raise HTTPException(
            status_code=400,
            detail=f"MinIO object not found: {req.minio_path} — {e}. Upload with: mc cp <file> local/{MINIO_BUCKET}/{req.minio_path}",
        )

    # 2. Verify size matches
    if stat.size != req.size_bytes:
        raise HTTPException(
            status_code=400,
            detail=f"Size mismatch: MinIO reports {stat.size} bytes, request says {req.size_bytes}",
        )

    # 3. Compute sha256 from the actual MinIO object and verify against caller-supplied value
    response = None
    try:
        response = minio.get_object(MINIO_BUCKET, req.minio_path)
        h = hashlib.sha256()
        while True:
            chunk = response.read(8192)
            if not chunk:
                break
            h.update(chunk)
        computed_hex = h.hexdigest()
    finally:
        if response is not None:
            response.close()
            response.release_conn()

    if computed_hex != req.sha256.lower():
        raise HTTPException(
            status_code=400,
            detail=f"SHA256 mismatch: computed {computed_hex}, request supplied {req.sha256.lower()}",
        )

    # 4. Insert / upsert into registry
    conn = _db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO hydra_models
                  (name, version, format, quantization, size_bytes, sha256,
                   minio_path, pool_assignment, license, notes, created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (name, version) DO UPDATE SET
                  sha256          = EXCLUDED.sha256,
                  minio_path      = EXCLUDED.minio_path,
                  pool_assignment = EXCLUDED.pool_assignment,
                  notes           = EXCLUDED.notes
                RETURNING name, version
            """, (
                req.name, req.version, req.format, req.quantization,
                req.size_bytes, req.sha256.lower(), req.minio_path,
                req.pool_assignment, req.license, req.notes,
                datetime.now(timezone.utc),
            ))
            result = cur.fetchone()
        conn.commit()
    finally:
        conn.close()
    return {
        "registered": result[0],
        "version": result[1],
        "status": "pending_approval",
        "next_step": f"POST /api/v1/models/{result[0]}/approve",
    }


# ── Approve model ─────────────────────────────────────────────────────────────

@app.post("/api/v1/models/{name}/approve", dependencies=[Depends(_require_admin)])
def approve_model(name: str, approved_by: str = "admin"):
    conn = _db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE hydra_models SET approved_by=%s, approved_at=%s WHERE name=%s RETURNING name, pool_assignment",
                (approved_by, datetime.now(timezone.utc), name),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail=f"Model {name!r} not found — register it first")
            row = cur.fetchone()
        conn.commit()
    finally:
        conn.close()
    return {
        "approved": row[0],
        "pool_assignment": row[1],
        "approved_by": approved_by,
        "status": "approved — LiteLLM will pick up on next config sync",
    }


# ── Retire model ──────────────────────────────────────────────────────────────

@app.delete("/api/v1/models/{name}", dependencies=[Depends(_require_admin)])
def retire_model(name: str, delete_artifact: bool = False):
    conn = _db()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM hydra_models WHERE name=%s RETURNING name, minio_path", (name,))
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail=f"Model {name!r} not found")
            row = cur.fetchone()
        conn.commit()
    finally:
        conn.close()
    result = {"retired": row[0]}
    if delete_artifact:
        try:
            minio.remove_object(MINIO_BUCKET, row[1])
            result["artifact_deleted"] = row[1]
        except Exception as e:
            result["artifact_warning"] = f"Could not delete MinIO object: {e}"
    return result
