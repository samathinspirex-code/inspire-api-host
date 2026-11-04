-- Arena seasons: an admin can restart XP and collecting. Past claims stay as history under their season.
CREATE TABLE IF NOT EXISTS crm_arena_seasons (
    season_number INTEGER PRIMARY KEY,
    started_at TIMESTAMP NOT NULL DEFAULT NOW(),
    started_by INTEGER,
    previous_standings JSONB NOT NULL DEFAULT '[]'::jsonb
);

ALTER TABLE crm_arena_claims ADD COLUMN IF NOT EXISTS season_number INTEGER NOT NULL DEFAULT 1;
DROP INDEX IF EXISTS ux_crm_arena_claims_level;
CREATE UNIQUE INDEX IF NOT EXISTS ux_crm_arena_claims_season_level ON crm_arena_claims (season_number, user_id, quest_id, level, claim_date);

ALTER TABLE crm_arena_reward_collections ADD COLUMN IF NOT EXISTS season_number INTEGER NOT NULL DEFAULT 1;
DROP INDEX IF EXISTS ux_crm_arena_tier_collection;
CREATE UNIQUE INDEX IF NOT EXISTS ux_crm_arena_tier_collection_season ON crm_arena_reward_collections (season_number, reward_id, user_id) WHERE source = 'tier';
