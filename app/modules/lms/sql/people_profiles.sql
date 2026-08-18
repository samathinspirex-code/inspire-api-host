-- Step 4: academic profiles attached one-to-one to existing user accounts.
CREATE TABLE IF NOT EXISTS lms_student_profiles (
    user_id        INT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    student_number VARCHAR(100) NOT NULL UNIQUE,
    phone           VARCHAR(50),
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS lms_lecturer_profiles (
    user_id      INT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    staff_number VARCHAR(100) NOT NULL UNIQUE,
    job_title    VARCHAR(150),
    phone        VARCHAR(50),
    expertise    TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
