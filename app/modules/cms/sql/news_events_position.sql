-- Website order for news and events, set in the CMS (lower first; 0 = not arranged).
ALTER TABLE news_events ADD COLUMN IF NOT EXISTS position INTEGER NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS idx_news_events_position ON news_events (position);
