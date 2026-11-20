-- Optional discounted price while the limited offer is on; the normal price stays as the struck-through original.
ALTER TABLE academic_courses
  ADD COLUMN IF NOT EXISTS limited_offer_price INTEGER CHECK (limited_offer_price >= 0);

ALTER TABLE programs
  ADD COLUMN IF NOT EXISTS limited_offer_price INTEGER CHECK (limited_offer_price >= 0);
