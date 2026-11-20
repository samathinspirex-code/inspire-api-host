-- Small key/value store for website-wide settings managed in the CMS (e.g. the Christmas theme switch).
CREATE TABLE IF NOT EXISTS site_settings (
  key TEXT PRIMARY KEY,
  value JSONB NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by INTEGER
);

INSERT INTO site_settings (key, value) VALUES ('christmas_theme', 'false'::jsonb)
ON CONFLICT (key) DO NOTHING;
