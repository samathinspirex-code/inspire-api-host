-- Counsellor Arena: call outcomes, quest levels, and admin-managed rewards.
ALTER TABLE crm_activities ADD COLUMN IF NOT EXISTS call_outcome VARCHAR(30);

ALTER TABLE crm_arena_claims ADD COLUMN IF NOT EXISTS level INTEGER NOT NULL DEFAULT 1;
ALTER TABLE crm_arena_claims ADD COLUMN IF NOT EXISTS base_xp INTEGER;
ALTER TABLE crm_arena_claims ADD COLUMN IF NOT EXISTS streak_bonus_xp INTEGER NOT NULL DEFAULT 0;
ALTER TABLE crm_arena_claims DROP CONSTRAINT IF EXISTS crm_arena_claims_user_id_quest_id_claim_date_key;
CREATE UNIQUE INDEX IF NOT EXISTS ux_crm_arena_claims_level ON crm_arena_claims (user_id, quest_id, level, claim_date);

CREATE TABLE IF NOT EXISTS crm_arena_rewards (
    reward_id SERIAL PRIMARY KEY,
    reward_type VARCHAR(20) NOT NULL,
    brand VARCHAR(120),
    title VARCHAR(160) NOT NULL,
    value_label VARCHAR(80),
    description TEXT,
    tier_index INTEGER,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_by INTEGER,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS crm_arena_reward_codes (
    code_id SERIAL PRIMARY KEY,
    reward_id INTEGER NOT NULL REFERENCES crm_arena_rewards (reward_id) ON DELETE CASCADE,
    code VARCHAR(160) NOT NULL,
    expires_at DATE,
    status VARCHAR(20) NOT NULL DEFAULT 'available',
    created_by INTEGER,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (reward_id, code)
);
CREATE INDEX IF NOT EXISTS ix_crm_arena_reward_codes_stock ON crm_arena_reward_codes (reward_id, status);

CREATE TABLE IF NOT EXISTS crm_arena_reward_collections (
    collection_id SERIAL PRIMARY KEY,
    reward_id INTEGER NOT NULL REFERENCES crm_arena_rewards (reward_id),
    user_id INTEGER NOT NULL,
    source VARCHAR(20) NOT NULL,
    status VARCHAR(20) NOT NULL,
    code_id INTEGER REFERENCES crm_arena_reward_codes (code_id),
    issued_code VARCHAR(160),
    note TEXT,
    requested_at TIMESTAMP NOT NULL DEFAULT NOW(),
    decided_by INTEGER,
    decided_at TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_crm_arena_tier_collection ON crm_arena_reward_collections (reward_id, user_id) WHERE source = 'tier';
CREATE INDEX IF NOT EXISTS ix_crm_arena_collections_status ON crm_arena_reward_collections (status, requested_at);
