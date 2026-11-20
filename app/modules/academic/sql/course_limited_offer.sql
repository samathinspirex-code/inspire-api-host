-- Course-level limited offer stamp (website programme box + fees reveal); off for existing courses.
ALTER TABLE academic_courses
  ADD COLUMN IF NOT EXISTS limited_offer_enabled BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS limited_offer_label VARCHAR(40) NOT NULL DEFAULT 'Limited Offer';

ALTER TABLE programs
  ADD COLUMN IF NOT EXISTS limited_offer_enabled BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS limited_offer_label VARCHAR(40) NOT NULL DEFAULT 'Limited Offer';
