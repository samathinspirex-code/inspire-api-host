-- Optional monthly instalment (LKR) shown in the course page "At a glance" card; empty hides the row.
ALTER TABLE academic_courses
  ADD COLUMN IF NOT EXISTS monthly_instalment INTEGER CHECK (monthly_instalment >= 0);

ALTER TABLE programs
  ADD COLUMN IF NOT EXISTS monthly_instalment INTEGER CHECK (monthly_instalment >= 0);
