-- Additive academic hierarchy, counselling enrolments, reusable templates, and class snapshots.
CREATE TABLE IF NOT EXISTS academic_programmes (
    programme_id SERIAL PRIMARY KEY,
    code VARCHAR(100) NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    status VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
    position INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_academic_programmes_code_ci ON academic_programmes (lower(code));
CREATE UNIQUE INDEX IF NOT EXISTS uq_academic_programmes_name_ci ON academic_programmes (lower(name));

CREATE TABLE IF NOT EXISTS academic_levels (
    level_id SERIAL PRIMARY KEY,
    code VARCHAR(100) NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    rank INT,
    status VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
    position INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_academic_levels_code_ci ON academic_levels (lower(code));
CREATE UNIQUE INDEX IF NOT EXISTS uq_academic_levels_name_ci ON academic_levels (lower(name));

CREATE TABLE IF NOT EXISTS academic_programme_levels (
    programme_id INT NOT NULL REFERENCES academic_programmes(programme_id) ON DELETE RESTRICT,
    level_id INT NOT NULL REFERENCES academic_levels(level_id) ON DELETE RESTRICT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (programme_id, level_id)
);

CREATE TABLE IF NOT EXISTS academic_schools (
    school_id SERIAL PRIMARY KEY,
    code VARCHAR(100) NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    status VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
    position INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_academic_schools_code_ci ON academic_schools (lower(code));
CREATE UNIQUE INDEX IF NOT EXISTS uq_academic_schools_name_ci ON academic_schools (lower(name));

CREATE TABLE IF NOT EXISTS academic_courses (
    course_id SERIAL PRIMARY KEY,
    legacy_program_id INT UNIQUE REFERENCES programs(program_id) ON DELETE RESTRICT,
    programme_id INT NOT NULL REFERENCES academic_programmes(programme_id) ON DELETE RESTRICT,
    level_id INT REFERENCES academic_levels(level_id) ON DELETE RESTRICT,
    school_id INT NOT NULL REFERENCES academic_schools(school_id) ON DELETE RESTRICT,
    slug VARCHAR(255) NOT NULL UNIQUE,
    code VARCHAR(100) NOT NULL,
    title VARCHAR(255) NOT NULL,
    awarding_body VARCHAR(100) NOT NULL,
    blurb TEXT NOT NULL,
    image_url TEXT,
    status VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_academic_courses_hierarchy ON academic_courses(programme_id, level_id, school_id);
CREATE INDEX IF NOT EXISTS idx_academic_courses_code ON academic_courses(code);

CREATE TABLE IF NOT EXISTS academic_course_study_options (
    study_option_id SERIAL PRIMARY KEY,
    course_id INT NOT NULL REFERENCES academic_courses(course_id) ON DELETE CASCADE,
    study_mode VARCHAR(20) NOT NULL CHECK (study_mode IN ('full_time','part_time')),
    price INT NOT NULL CHECK (price >= 0),
    duration VARCHAR(100) NOT NULL,
    is_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (course_id, study_mode)
);

CREATE TABLE IF NOT EXISTS academic_programme_enrolments (
    enrolment_id SERIAL PRIMARY KEY,
    student_user_id INT NOT NULL REFERENCES lms_student_profiles(user_id) ON DELETE CASCADE,
    programme_id INT NOT NULL REFERENCES academic_programmes(programme_id) ON DELETE RESTRICT,
    preferred_level_id INT REFERENCES academic_levels(level_id) ON DELETE SET NULL,
    preferred_school_id INT REFERENCES academic_schools(school_id) ON DELETE SET NULL,
    preferred_course_id INT REFERENCES academic_courses(course_id) ON DELETE SET NULL,
    preferred_study_mode VARCHAR(20) CHECK (preferred_study_mode IN ('full_time','part_time')),
    status VARCHAR(30) NOT NULL DEFAULT 'awaiting_counselling'
      CHECK (status IN ('awaiting_counselling','counselling','pathway_selected','cancelled')),
    source VARCHAR(30) NOT NULL DEFAULT 'main_ui',
    counsellor_user_id INT REFERENCES users(user_id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_programme_enrolments_student ON academic_programme_enrolments(student_user_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_active_programme_enrolment
  ON academic_programme_enrolments(student_user_id, programme_id)
  WHERE status IN ('awaiting_counselling','counselling','pathway_selected');

CREATE TABLE IF NOT EXISTS academic_course_enrolments (
    course_enrolment_id SERIAL PRIMARY KEY,
    programme_enrolment_id INT REFERENCES academic_programme_enrolments(enrolment_id) ON DELETE SET NULL,
    student_user_id INT NOT NULL REFERENCES lms_student_profiles(user_id) ON DELETE CASCADE,
    course_id INT NOT NULL REFERENCES academic_courses(course_id) ON DELETE RESTRICT,
    study_mode VARCHAR(20) NOT NULL CHECK (study_mode IN ('full_time','part_time')),
    class_id INT REFERENCES lms_classes(class_id) ON DELETE SET NULL,
    agreed_price INT NOT NULL CHECK (agreed_price >= 0),
    agreed_duration VARCHAR(100) NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'awaiting_class'
      CHECK (status IN ('awaiting_class','active','completed','withdrawn','suspended')),
    confirmed_by INT REFERENCES users(user_id) ON DELETE SET NULL,
    enrolled_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_course_enrolments_student ON academic_course_enrolments(student_user_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_class_course_enrolment
  ON academic_course_enrolments(student_user_id, class_id)
  WHERE class_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS academic_course_templates (
    template_id SERIAL PRIMARY KEY,
    course_id INT NOT NULL REFERENCES academic_courses(course_id) ON DELETE RESTRICT,
    study_mode VARCHAR(20) NOT NULL CHECK (study_mode IN ('full_time','part_time')),
    title VARCHAR(255) NOT NULL,
    source_lms_course_id INT NOT NULL REFERENCES lms_courses(course_id) ON DELETE RESTRICT,
    active_version_id INT,
    created_by INT REFERENCES users(user_id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (course_id, study_mode)
);

CREATE TABLE IF NOT EXISTS academic_course_template_versions (
    template_version_id SERIAL PRIMARY KEY,
    template_id INT NOT NULL REFERENCES academic_course_templates(template_id) ON DELETE CASCADE,
    version_number INT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','published','archived')),
    snapshot JSONB NOT NULL DEFAULT '{"sections":[]}'::jsonb,
    created_by INT REFERENCES users(user_id) ON DELETE SET NULL,
    published_by INT REFERENCES users(user_id) ON DELETE SET NULL,
    published_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (template_id, version_number)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_template_draft ON academic_course_template_versions(template_id) WHERE status='draft';

DO $$ BEGIN
  ALTER TABLE academic_course_templates
    ADD CONSTRAINT fk_active_template_version FOREIGN KEY (active_version_id)
    REFERENCES academic_course_template_versions(template_version_id) ON DELETE SET NULL;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

ALTER TABLE lms_classes ADD COLUMN IF NOT EXISTS academic_course_id INT REFERENCES academic_courses(course_id) ON DELETE RESTRICT;
ALTER TABLE lms_classes ADD COLUMN IF NOT EXISTS study_mode VARCHAR(20) CHECK (study_mode IN ('full_time','part_time'));
ALTER TABLE lms_classes ADD COLUMN IF NOT EXISTS source_template_version_id INT REFERENCES academic_course_template_versions(template_version_id) ON DELETE SET NULL;
ALTER TABLE lms_classes ADD COLUMN IF NOT EXISTS last_synced_version_id INT REFERENCES academic_course_template_versions(template_version_id) ON DELETE SET NULL;
ALTER TABLE lms_classes ADD COLUMN IF NOT EXISTS content_snapshot JSONB NOT NULL DEFAULT '{"sections":[]}'::jsonb;

-- Convert the current CMS catalogue into authoritative courses.
INSERT INTO academic_programmes (code, name, position)
SELECT DISTINCT upper(regexp_replace(level, '[^A-Za-z0-9]+', '_', 'g')), trim(level),
       dense_rank() OVER (ORDER BY trim(level))
FROM programs
ON CONFLICT DO NOTHING;

INSERT INTO academic_schools (code, name, position)
SELECT DISTINCT upper(regexp_replace(school, '[^A-Za-z0-9]+', '_', 'g')),
       CASE WHEN lower(trim(school)) LIKE 'school of %' THEN trim(school) ELSE 'School of ' || trim(school) END,
       dense_rank() OVER (ORDER BY trim(school))
FROM programs
ON CONFLICT DO NOTHING;

INSERT INTO academic_levels (code, name, rank, position)
SELECT DISTINCT 'L' || (regexp_match(code, '(?i)L(?:evel[[:space:]]*)?([0-9]+)'))[1],
       'Level ' || (regexp_match(code, '(?i)L(?:evel[[:space:]]*)?([0-9]+)'))[1],
       ((regexp_match(code, '(?i)L(?:evel[[:space:]]*)?([0-9]+)'))[1])::int,
       ((regexp_match(code, '(?i)L(?:evel[[:space:]]*)?([0-9]+)'))[1])::int
FROM programs WHERE code ~* 'L(?:evel[[:space:]]*)?[0-9]+'
ON CONFLICT DO NOTHING;

INSERT INTO academic_programme_levels (programme_id, level_id)
SELECT DISTINCT ap.programme_id, al.level_id
FROM programs p
JOIN academic_programmes ap ON lower(ap.name)=lower(trim(p.level))
JOIN academic_levels al ON al.code='L' || (regexp_match(p.code, '(?i)L(?:evel[[:space:]]*)?([0-9]+)'))[1]
ON CONFLICT DO NOTHING;

INSERT INTO academic_courses
  (course_id, legacy_program_id, programme_id, level_id, school_id, slug, code, title, awarding_body, blurb, image_url, status)
SELECT p.program_id, p.program_id, ap.programme_id, al.level_id, s.school_id, p.slug, p.code, p.title,
       p.awarding_body, p.blurb, p.image_url, 'active'
FROM programs p
JOIN academic_programmes ap ON lower(ap.name)=lower(trim(p.level))
JOIN academic_schools s ON lower(s.name)=lower(CASE WHEN lower(trim(p.school)) LIKE 'school of %' THEN trim(p.school) ELSE 'School of ' || trim(p.school) END)
LEFT JOIN academic_levels al ON al.code='L' || (regexp_match(p.code, '(?i)L(?:evel[[:space:]]*)?([0-9]+)'))[1]
ON CONFLICT (legacy_program_id) DO UPDATE SET
  programme_id=EXCLUDED.programme_id, level_id=EXCLUDED.level_id, school_id=EXCLUDED.school_id,
  slug=EXCLUDED.slug, code=EXCLUDED.code, title=EXCLUDED.title, awarding_body=EXCLUDED.awarding_body,
  blurb=EXCLUDED.blurb, image_url=EXCLUDED.image_url, updated_at=now();

INSERT INTO academic_course_study_options (course_id, study_mode, price, duration)
SELECT ac.course_id, mode.study_mode, p.price_from, p.duration
FROM academic_courses ac JOIN programs p ON p.program_id=ac.legacy_program_id
CROSS JOIN (VALUES ('full_time'),('part_time')) AS mode(study_mode)
ON CONFLICT (course_id, study_mode) DO NOTHING;

-- Link existing classes to the authoritative course and infer study mode without hiding uncertain records.
UPDATE lms_classes cl SET academic_course_id=ac.course_id
FROM lms_courses lc JOIN academic_courses ac ON ac.legacy_program_id=lc.program_id
WHERE cl.course_id=lc.course_id AND cl.academic_course_id IS NULL;

UPDATE lms_classes SET study_mode=CASE
  WHEN code ~* '(^|[^A-Z])PT([^A-Z]|$)' OR name ~* 'part[ -]?time' THEN 'part_time'
  WHEN code ~* '(^|[^A-Z])FT([^A-Z]|$)' OR name ~* 'full[ -]?time' THEN 'full_time'
  ELSE study_mode END
WHERE study_mode IS NULL;

CREATE TABLE IF NOT EXISTS academic_migration_review (
    review_id SERIAL PRIMARY KEY,
    record_type VARCHAR(40) NOT NULL,
    record_id INT NOT NULL,
    reason TEXT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'open',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (record_type, record_id, reason)
);

INSERT INTO academic_migration_review (record_type, record_id, reason)
SELECT 'class', class_id, 'Study mode could not be inferred from the existing class code or name.'
FROM lms_classes WHERE academic_course_id IS NOT NULL AND study_mode IS NULL
ON CONFLICT DO NOTHING;

-- Capture legacy course content as stable JSON before classes become independently editable.
WITH course_snapshots AS (
  SELECT lc.course_id,
    jsonb_build_object('sections', COALESCE((
      SELECT jsonb_agg(jsonb_build_object(
        'template_key', 'legacy-section-' || m.module_id,
        'legacy_module_id', m.module_id,
        'title', m.title, 'description', m.description, 'position', m.position,
        'status', m.status, 'vimeo_folder_uri', m.vimeo_folder_uri,
        'items', COALESCE((
          SELECT jsonb_agg(jsonb_build_object(
            'template_key', 'legacy-item-' || i.learning_item_id,
            'legacy_learning_item_id', i.learning_item_id,
            'type', i.item_type, 'title', i.title, 'description', i.description,
            'resource_url', i.resource_url, 'thumbnail_url', i.thumbnail_url,
            'text_content', i.text_content, 'duration_minutes', i.duration_minutes,
            'position', i.position, 'status', i.status, 'is_required', i.is_required
          ) ORDER BY i.position)
          FROM lms_learning_items i WHERE i.module_id=m.module_id
        ), '[]'::jsonb)
      ) ORDER BY m.position)
      FROM lms_modules m WHERE m.course_id=lc.course_id
    ), '[]'::jsonb)) AS snapshot
  FROM lms_courses lc
)
UPDATE lms_classes cl SET content_snapshot=cs.snapshot
FROM course_snapshots cs
WHERE cl.course_id=cs.course_id AND cl.academic_course_id IS NOT NULL;

-- One reusable legacy template per course/mode represented by an existing class.
INSERT INTO academic_course_templates (course_id, study_mode, title, source_lms_course_id)
SELECT cl.academic_course_id, cl.study_mode,
       ac.title || ' · ' || initcap(replace(cl.study_mode, '_', ' ')), min(cl.course_id)
FROM lms_classes cl JOIN academic_courses ac ON ac.course_id=cl.academic_course_id
WHERE cl.study_mode IS NOT NULL
GROUP BY cl.academic_course_id, cl.study_mode, ac.title
ON CONFLICT (course_id, study_mode) DO NOTHING;

INSERT INTO academic_course_template_versions (template_id, version_number, status, snapshot, published_at)
SELECT t.template_id, 1, 'published',
       COALESCE((SELECT cl.content_snapshot FROM lms_classes cl
                 WHERE cl.academic_course_id=t.course_id AND cl.study_mode=t.study_mode
                 ORDER BY cl.class_id LIMIT 1), '{"sections":[]}'::jsonb), now()
FROM academic_course_templates t
WHERE NOT EXISTS (SELECT 1 FROM academic_course_template_versions v WHERE v.template_id=t.template_id)
ON CONFLICT DO NOTHING;

UPDATE academic_course_templates t SET active_version_id=v.template_version_id
FROM academic_course_template_versions v
WHERE v.template_id=t.template_id AND v.status='published' AND t.active_version_id IS NULL;

UPDATE lms_classes cl SET source_template_version_id=t.active_version_id,
  last_synced_version_id=t.active_version_id
FROM academic_course_templates t
WHERE t.course_id=cl.academic_course_id AND t.study_mode=cl.study_mode
  AND cl.source_template_version_id IS NULL;

-- Preserve current class assignments as historical course enrolments.
INSERT INTO academic_course_enrolments
  (student_user_id, course_id, study_mode, class_id, agreed_price, agreed_duration,
   status, enrolled_at)
SELECT cs.student_user_id, cl.academic_course_id, cl.study_mode, cl.class_id,
       opt.price, opt.duration,
       CASE WHEN cl.status='completed' THEN 'completed' ELSE 'active' END,
       cs.assigned_at
FROM lms_class_students cs
JOIN lms_classes cl ON cl.class_id=cs.class_id
JOIN academic_course_study_options opt ON opt.course_id=cl.academic_course_id
  AND opt.study_mode=cl.study_mode
WHERE cl.academic_course_id IS NOT NULL AND cl.study_mode IS NOT NULL
  AND NOT EXISTS (
    SELECT 1 FROM academic_course_enrolments ace
    WHERE ace.student_user_id=cs.student_user_id AND ace.class_id=cl.class_id
  );

INSERT INTO academic_migration_review (record_type, record_id, reason)
SELECT 'course_enrolment', ce.student_user_id,
       'Existing course enrolment has no class with an inferable Full-time or Part-time mode.'
FROM lms_course_enrollments ce
WHERE NOT EXISTS (
  SELECT 1 FROM lms_class_students cs JOIN lms_classes cl ON cl.class_id=cs.class_id
  WHERE cs.student_user_id=ce.student_user_id AND cl.course_id=ce.course_id
    AND cl.study_mode IS NOT NULL
)
ON CONFLICT DO NOTHING;

SELECT setval(pg_get_serial_sequence('academic_courses','course_id'), COALESCE((SELECT max(course_id) FROM academic_courses),1), true);
