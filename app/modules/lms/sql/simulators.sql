CREATE TABLE IF NOT EXISTS lms_sim_templates (
    template_id SERIAL PRIMARY KEY,
    starter_key VARCHAR(40) UNIQUE,
    source_template_id INTEGER REFERENCES lms_sim_templates(template_id) ON DELETE SET NULL,
    title VARCHAR(160) NOT NULL,
    tagline VARCHAR(400) NOT NULL DEFAULT '',
    programme VARCHAR(120) NOT NULL DEFAULT '',
    level VARCHAR(20) NOT NULL DEFAULT 'ug',
    tone VARCHAR(20) NOT NULL DEFAULT 'student' CHECK (tone IN ('student', 'professional')),
    content JSONB NOT NULL,
    roles JSONB NOT NULL DEFAULT '[]'::jsonb,
    is_archived BOOLEAN NOT NULL DEFAULT FALSE,
    created_by INTEGER REFERENCES users(user_id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
;
CREATE INDEX IF NOT EXISTS idx_lms_sim_templates_owner ON lms_sim_templates (created_by, is_archived)
;
CREATE TABLE IF NOT EXISTS lms_sim_runs (
    run_id SERIAL PRIMARY KEY,
    class_id INTEGER NOT NULL REFERENCES lms_classes(class_id) ON DELETE CASCADE,
    template_id INTEGER REFERENCES lms_sim_templates(template_id) ON DELETE SET NULL,
    title VARCHAR(160) NOT NULL,
    tagline VARCHAR(400) NOT NULL DEFAULT '',
    tone VARCHAR(20) NOT NULL DEFAULT 'student' CHECK (tone IN ('student', 'professional')),
    content JSONB NOT NULL,
    roles JSONB NOT NULL DEFAULT '[]'::jsonb,
    min_team_size INTEGER NOT NULL DEFAULT 2 CHECK (min_team_size BETWEEN 1 AND 8),
    max_team_size INTEGER NOT NULL DEFAULT 4 CHECK (max_team_size BETWEEN 1 AND 8),
    allow_solo BOOLEAN NOT NULL DEFAULT FALSE,
    opens_at TIMESTAMPTZ NOT NULL,
    closes_at TIMESTAMPTZ NOT NULL,
    xp_per_round INTEGER NOT NULL DEFAULT 100 CHECK (xp_per_round BETWEEN 0 AND 1000),
    points_per_round INTEGER NOT NULL DEFAULT 30 CHECK (points_per_round BETWEEN 0 AND 1000),
    status VARCHAR(20) NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    created_by INTEGER REFERENCES users(user_id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (closes_at > opens_at),
    CHECK (min_team_size <= max_team_size)
)
;
CREATE INDEX IF NOT EXISTS idx_lms_sim_runs_class ON lms_sim_runs (class_id, opens_at DESC)
;
CREATE TABLE IF NOT EXISTS lms_sim_teams (
    team_id SERIAL PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES lms_sim_runs(run_id) ON DELETE CASCADE,
    name VARCHAR(80) NOT NULL,
    is_solo BOOLEAN NOT NULL DEFAULT FALSE,
    status VARCHAR(20) NOT NULL DEFAULT 'forming' CHECK (status IN ('forming', 'playing', 'completed')),
    current_round INTEGER NOT NULL DEFAULT 0,
    kpi_state JSONB NOT NULL DEFAULT '{}'::jsonb,
    final_score INTEGER,
    final_band VARCHAR(20),
    created_by INTEGER REFERENCES users(user_id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ
)
;
CREATE INDEX IF NOT EXISTS idx_lms_sim_teams_run ON lms_sim_teams (run_id, status)
;
CREATE TABLE IF NOT EXISTS lms_sim_team_members (
    team_id INTEGER NOT NULL REFERENCES lms_sim_teams(team_id) ON DELETE CASCADE,
    student_user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    run_id INTEGER NOT NULL REFERENCES lms_sim_runs(run_id) ON DELETE CASCADE,
    role_key VARCHAR(40),
    joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (team_id, student_user_id),
    CONSTRAINT uq_lms_sim_member_run UNIQUE (run_id, student_user_id),
    CONSTRAINT uq_lms_sim_member_role UNIQUE (team_id, role_key)
)
;
CREATE TABLE IF NOT EXISTS lms_sim_invites (
    invite_id SERIAL PRIMARY KEY,
    team_id INTEGER NOT NULL REFERENCES lms_sim_teams(team_id) ON DELETE CASCADE,
    run_id INTEGER NOT NULL REFERENCES lms_sim_runs(run_id) ON DELETE CASCADE,
    invited_by INTEGER REFERENCES users(user_id) ON DELETE SET NULL,
    student_user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    status VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'declined', 'cancelled')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    responded_at TIMESTAMPTZ
)
;
CREATE UNIQUE INDEX IF NOT EXISTS uq_lms_sim_invites_pending ON lms_sim_invites (team_id, student_user_id) WHERE status = 'pending'
;
CREATE INDEX IF NOT EXISTS idx_lms_sim_invites_student ON lms_sim_invites (student_user_id, run_id, status)
;
CREATE TABLE IF NOT EXISTS lms_sim_votes (
    team_id INTEGER NOT NULL REFERENCES lms_sim_teams(team_id) ON DELETE CASCADE,
    round_index INTEGER NOT NULL,
    decision_index INTEGER NOT NULL,
    student_user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    option_index INTEGER NOT NULL,
    voted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (team_id, round_index, decision_index, student_user_id)
)
;
CREATE TABLE IF NOT EXISTS lms_sim_round_results (
    team_id INTEGER NOT NULL REFERENCES lms_sim_teams(team_id) ON DELETE CASCADE,
    round_index INTEGER NOT NULL,
    winners JSONB NOT NULL,
    kpi_before JSONB NOT NULL,
    kpi_after JSONB NOT NULL,
    delta JSONB NOT NULL,
    debrief TEXT NOT NULL DEFAULT '',
    score INTEGER NOT NULL,
    closed_by INTEGER REFERENCES users(user_id) ON DELETE SET NULL,
    closed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (team_id, round_index)
)
;
CREATE TABLE IF NOT EXISTS lms_sim_messages (
    message_id SERIAL PRIMARY KEY,
    team_id INTEGER NOT NULL REFERENCES lms_sim_teams(team_id) ON DELETE CASCADE,
    sender_user_id INTEGER REFERENCES users(user_id) ON DELETE SET NULL,
    body VARCHAR(1000) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
;
CREATE INDEX IF NOT EXISTS idx_lms_sim_messages_team ON lms_sim_messages (team_id, message_id)
;
CREATE TABLE IF NOT EXISTS lms_xp_ledger (
    entry_id SERIAL PRIMARY KEY,
    student_user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    class_id INTEGER REFERENCES lms_classes(class_id) ON DELETE SET NULL,
    source_type VARCHAR(40) NOT NULL,
    run_id INTEGER REFERENCES lms_sim_runs(run_id) ON DELETE SET NULL,
    team_id INTEGER REFERENCES lms_sim_teams(team_id) ON DELETE SET NULL,
    xp INTEGER NOT NULL DEFAULT 0,
    points INTEGER NOT NULL DEFAULT 0,
    reason VARCHAR(300) NOT NULL DEFAULT '',
    awarded_by INTEGER REFERENCES users(user_id) ON DELETE SET NULL,
    event_key VARCHAR(120) NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
;
CREATE INDEX IF NOT EXISTS idx_lms_xp_ledger_student ON lms_xp_ledger (student_user_id, created_at DESC)
;
CREATE INDEX IF NOT EXISTS idx_lms_xp_ledger_class ON lms_xp_ledger (class_id, student_user_id)
;
