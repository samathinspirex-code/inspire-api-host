-- AI-written search engine title and description for a news or event post.
-- Empty means the website falls back to the post title and short summary.
ALTER TABLE news_events ADD COLUMN IF NOT EXISTS meta_title VARCHAR(70);
ALTER TABLE news_events ADD COLUMN IF NOT EXISTS meta_description VARCHAR(200);
