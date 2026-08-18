-- Step 5: many-to-many course and class relationships.
CREATE TABLE IF NOT EXISTS lms_course_lecturers (
    course_id        INT NOT NULL REFERENCES lms_courses(course_id) ON DELETE CASCADE,
    lecturer_user_id INT NOT NULL REFERENCES lms_lecturer_profiles(user_id) ON DELETE CASCADE,
    assigned_by      INT REFERENCES users(user_id) ON DELETE SET NULL,
    assigned_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (course_id, lecturer_user_id)
);

CREATE TABLE IF NOT EXISTS lms_class_lecturers (
    class_id         INT NOT NULL REFERENCES lms_classes(class_id) ON DELETE CASCADE,
    lecturer_user_id INT NOT NULL REFERENCES lms_lecturer_profiles(user_id) ON DELETE CASCADE,
    assigned_by      INT REFERENCES users(user_id) ON DELETE SET NULL,
    assigned_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (class_id, lecturer_user_id)
);

CREATE TABLE IF NOT EXISTS lms_course_enrollments (
    course_id      INT NOT NULL REFERENCES lms_courses(course_id) ON DELETE CASCADE,
    student_user_id INT NOT NULL REFERENCES lms_student_profiles(user_id) ON DELETE CASCADE,
    status          VARCHAR(20) NOT NULL DEFAULT 'enrolled'
                    CHECK (status IN ('enrolled', 'completed', 'withdrawn', 'suspended')),
    enrolled_by     INT REFERENCES users(user_id) ON DELETE SET NULL,
    enrolled_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (course_id, student_user_id)
);

CREATE TABLE IF NOT EXISTS lms_class_students (
    class_id       INT NOT NULL REFERENCES lms_classes(class_id) ON DELETE CASCADE,
    student_user_id INT NOT NULL REFERENCES lms_student_profiles(user_id) ON DELETE CASCADE,
    assigned_by     INT REFERENCES users(user_id) ON DELETE SET NULL,
    assigned_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (class_id, student_user_id)
);

CREATE INDEX IF NOT EXISTS idx_course_lecturers_lecturer ON lms_course_lecturers(lecturer_user_id);
CREATE INDEX IF NOT EXISTS idx_class_lecturers_lecturer ON lms_class_lecturers(lecturer_user_id);
CREATE INDEX IF NOT EXISTS idx_course_enrollments_student ON lms_course_enrollments(student_user_id);
CREATE INDEX IF NOT EXISTS idx_class_students_student ON lms_class_students(student_user_id);
