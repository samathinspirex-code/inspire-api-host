-- Step 3: class cohorts delivered under academic courses.
CREATE TABLE IF NOT EXISTS lms_classes (
    class_id      SERIAL PRIMARY KEY,
    course_id     INT NOT NULL REFERENCES lms_courses(course_id) ON DELETE RESTRICT,
    code          VARCHAR(100) NOT NULL UNIQUE,
    name          VARCHAR(255) NOT NULL,
    description   TEXT,
    start_date    DATE NOT NULL,
    end_date      DATE NOT NULL,
    delivery_mode VARCHAR(20) NOT NULL DEFAULT 'online'
                  CHECK (delivery_mode IN ('online', 'hybrid', 'on_site')),
    timezone      VARCHAR(100) NOT NULL DEFAULT 'Asia/Colombo',
    capacity      INT NOT NULL DEFAULT 50 CHECK (capacity BETWEEN 1 AND 1000),
    status        VARCHAR(20) NOT NULL DEFAULT 'planned'
                  CHECK (status IN ('planned', 'active', 'completed', 'cancelled')),
    created_by    INT REFERENCES users(user_id) ON DELETE SET NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_lms_classes_dates CHECK (end_date >= start_date)
);

CREATE INDEX IF NOT EXISTS idx_lms_classes_course_id ON lms_classes(course_id);
CREATE INDEX IF NOT EXISTS idx_lms_classes_status ON lms_classes(status);
