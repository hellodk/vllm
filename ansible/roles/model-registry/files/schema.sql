-- Hydra Model Registry — PostgreSQL schema
-- Run once (idempotent): psql "$DATABASE_URL" -f schema.sql

CREATE TABLE IF NOT EXISTS hydra_models (
    name             VARCHAR(255)             NOT NULL,
    version          VARCHAR(64)              NOT NULL,
    format           VARCHAR(32)              NOT NULL,   -- GGUF | MLX
    quantization     VARCHAR(32)              NOT NULL,   -- Q4_K_M | F16 | MXFP4 ...
    size_bytes       BIGINT                   NOT NULL,
    sha256           VARCHAR(64)              NOT NULL,   -- lowercase hex digest
    minio_path       VARCHAR(1024)            NOT NULL,   -- path within bucket
    pool_assignment  VARCHAR(64)              NOT NULL,   -- fast | reason | largepool | vision | embed | speech
    license          VARCHAR(255),                        -- nullable
    notes            TEXT,                                -- nullable
    created_at       TIMESTAMPTZ              NOT NULL DEFAULT now(),
    approved_by      VARCHAR(255),                        -- nullable until approved
    approved_at      TIMESTAMPTZ,                         -- nullable until approved

    PRIMARY KEY (name, version)
);

-- Explicit unique constraint referenced by ON CONFLICT (name, version) in registry_api.py
-- (the PRIMARY KEY already enforces uniqueness; this is a no-op but makes intent clear)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'hydra_models_name_version_key'
          AND conrelid = 'hydra_models'::regclass
    ) THEN
        ALTER TABLE hydra_models
            ADD CONSTRAINT hydra_models_name_version_key UNIQUE (name, version);
    END IF;
END;
$$;
