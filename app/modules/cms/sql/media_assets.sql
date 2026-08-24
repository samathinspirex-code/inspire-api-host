CREATE TABLE IF NOT EXISTS media_assets (
    media_asset_id BIGSERIAL PRIMARY KEY,
    object_key VARCHAR(700) UNIQUE NOT NULL,
    bucket VARCHAR(255) NOT NULL,
    name VARCHAR(120),
    original_filename VARCHAR(255) NOT NULL,
    content_type VARCHAR(120) NOT NULL,
    size_bytes BIGINT,
    kind VARCHAR(40) NOT NULL DEFAULT 'image',
    folder VARCHAR(80) NOT NULL DEFAULT 'media-library',
    alt_text VARCHAR(255),
    public_url TEXT,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    created_by BIGINT REFERENCES users(user_id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE media_assets ADD COLUMN IF NOT EXISTS name VARCHAR(120);
UPDATE media_assets
SET name = LEFT(
    COALESCE(NULLIF(TRIM(BOTH '-' FROM REGEXP_REPLACE(LOWER(SPLIT_PART(original_filename, '.', 1)), '[^a-z0-9._-]+', '-', 'g')), ''), 'asset')
    || '-' || media_asset_id,
    120
)
WHERE name IS NULL OR name = '';
ALTER TABLE media_assets ALTER COLUMN name SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_media_assets_name ON media_assets(name);
CREATE INDEX IF NOT EXISTS idx_media_assets_status_created ON media_assets(status, created_at DESC);
