-- Optional video on a news or event post (portrait 9:16 or landscape 16:9).
ALTER TABLE news_events ADD COLUMN IF NOT EXISTS video_url TEXT;
ALTER TABLE news_events ADD COLUMN IF NOT EXISTS video_orientation VARCHAR(20);
ALTER TABLE news_events ADD COLUMN IF NOT EXISTS video_poster_url TEXT;
ALTER TABLE news_events DROP CONSTRAINT IF EXISTS news_events_video_orientation_check;
ALTER TABLE news_events ADD CONSTRAINT news_events_video_orientation_check
    CHECK (video_orientation IS NULL OR video_orientation IN ('portrait', 'landscape'));
