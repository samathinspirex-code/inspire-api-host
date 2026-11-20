-- Top announcement strip (shown above the menu on every page until a visitor closes it).
INSERT INTO site_settings (key, value) VALUES
  ('announcement_enabled', 'true'::jsonb),
  ('announcement_text', '"Your Future, Made Affordable! 🎓 UK Qualifications from just LKR 5,000/month."'::jsonb),
  ('announcement_link', '"/programs"'::jsonb)
ON CONFLICT (key) DO NOTHING;
