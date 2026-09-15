CREATE TABLE IF NOT EXISTS cms_testimonials (
    testimonial_id BIGSERIAL PRIMARY KEY,
    seed_key TEXT UNIQUE,
    name VARCHAR(160) NOT NULL,
    programme VARCHAR(200) NOT NULL,
    caption VARCHAR(600) NOT NULL,
    video_url TEXT NOT NULL,
    thumbnail_url TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0 CHECK(position >= 0),
    status VARCHAR(20) NOT NULL DEFAULT 'Draft' CHECK(status IN ('Draft','Published')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Seed once; rerunning the migration preserves edits and deletions.
CREATE TABLE IF NOT EXISTS cms_testimonial_migrations (name TEXT PRIMARY KEY);
INSERT INTO cms_testimonials(seed_key,name,programme,caption,video_url,thumbnail_url,position,status)
SELECT slug,name,programme,caption,'/testimonials/'||slug||'.mp4','/testimonials/'||slug||'.jpg',position,'Published'
FROM (VALUES
 ('gimhani-edirisinghe','Gimhani Edirisinghe','HND','An HND journey, in Gimhani’s own words.',1),
 ('akram-razik','Akram Razik','Foundation + HND','From Foundation to HND — hear Akram’s experience.',2),
 ('nidarshana-premkumar','Nidarshana Premkumar','HND','Get to know Nidarshana’s HND experience at Inspire.',3),
 ('yara-benjamin','Yara Benjamin','Foundation','Starting with Foundation — Yara shares her story.',4)
) AS initial(slug,name,programme,caption,position)
WHERE NOT EXISTS (SELECT 1 FROM cms_testimonial_migrations WHERE name='initial-four')
ON CONFLICT(seed_key) DO NOTHING;
INSERT INTO cms_testimonial_migrations(name) VALUES('initial-four') ON CONFLICT DO NOTHING;
