CREATE TABLE IF NOT EXISTS news_events (
    news_event_id BIGSERIAL PRIMARY KEY,
    slug VARCHAR(255) NOT NULL UNIQUE,
    title VARCHAR(255) NOT NULL,
    kind VARCHAR(20) NOT NULL DEFAULT 'News' CHECK (kind IN ('News', 'Event')),
    category VARCHAR(100) NOT NULL,
    image_url TEXT NOT NULL,
    excerpt TEXT NOT NULL,
    content JSONB NOT NULL DEFAULT '[]'::jsonb,
    author VARCHAR(150) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'Draft' CHECK (status IN ('Draft', 'Review', 'Published')),
    published_on DATE,
    event_date DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_news_events_status_published
    ON news_events(status, published_on DESC);
CREATE INDEX IF NOT EXISTS idx_news_events_kind
    ON news_events(kind);

INSERT INTO news_events
    (slug, title, kind, category, image_url, excerpt, content, author, status, published_on)
VALUES
    (
        'winc-partnership',
        'Inspire College partners with WINC, UK',
        'News',
        'Partnership',
        'https://inspirecollege.lk/wp-content/uploads/2025/12/build-your-future-with-inspire-collage-and-WINC-1024x532.png',
        'A new partnership with WINC opens a Top-Up route to a UK Bachelor''s degree for every HND graduate.',
        '["Inspire College has entered a new partnership with WINC (UK), giving HND graduates a direct Top-Up route to a fully accredited UK Bachelor''s (Hons) degree — without leaving Colombo.", "The partnership covers both the School of Computing and School of Business, and the first Top-Up cohorts are expected to begin within the year."]'::jsonb,
        'Inspire College', 'Published', '2025-12-04'
    ),
    (
        'steven-enderby-chairman',
        'Steven Enderby — our new Chairman',
        'News',
        'Leadership',
        'https://inspirecollege.lk/wp-content/uploads/2024/04/BLOG-POST-STEVE-AS-A-CHIRMAN-1024x555.jpg',
        '25+ years in private equity, strategy and governance — Steven Enderby joins Inspire College as Chairman.',
        '["Inspire College is pleased to welcome Steven Enderby as Chairman of the Board. Steven brings 25+ years of leadership experience across private equity, strategy and governance, including as former CEO of Hemas Holdings PLC.", "Inspire was built around a simple idea: affordable, flexible, globally recognised education should be available to every Sri Lankan — wherever they are."]'::jsonb,
        'Inspire College', 'Published', '2026-04-11'
    ),
    (
        'athe-partnership',
        'Partnership with ATHE, UK awarding body',
        'News',
        'Partnership',
        'https://inspirecollege.lk/wp-content/uploads/2024/04/London-School-of-Business-and-Finance-is-in-Sri-Lanka-Now-scaled-1-1024x555.png',
        'ATHE-validated HND programs are now live across the School of Computing and School of Business.',
        '["Inspire College''s HND programs are now validated by ATHE (UK), an Ofqual-regulated awarding body — giving students a globally recognised qualification at Level 5.", "The first ATHE-validated intake covers five Computing pathways and two Business pathways, all delivered 100% online."]'::jsonb,
        'Inspire College', 'Published', '2026-04-11'
    ),
    (
        'open-day-2026',
        'Inspire College Open Day — book your spot',
        'Event',
        'Events',
        'https://inspirecollege.lk/wp-content/uploads/2025/12/build-your-future-with-inspire-collage-and-WINC-1024x532.png',
        'Meet faculty, tour the online learning platform, and get your questions answered live.',
        '["Join our next Open Day for a live walkthrough of the online learning platform, a Q&A with faculty from the School of Computing and School of Business, and a look at the first-50 HND enrollment offer.", "Sessions run online — register through the Admissions page and an advisor will send you the link."]'::jsonb,
        'Inspire College', 'Published', '2026-05-20'
    ),
    (
        'first-50-hnd-cohort',
        'First 50 HND students enroll at LKR 295,000',
        'News',
        'Admissions',
        'https://inspirecollege.lk/wp-content/uploads/2025/12/build-your-future-with-inspire-collage-and-WINC-1024x532.png',
        'A limited-time offer for the first 50 HND students, before the fee returns to LKR 400,000.',
        '["The first 50 students to enroll in any ATHE-validated HND program will lock in a fee of LKR 295,000 — a LKR 105,000 saving on the regular LKR 400,000 fee.", "Seats are allocated on a first-come, first-served basis once an application is confirmed by an advisor."]'::jsonb,
        'Inspire College', 'Published', '2026-06-02'
    )
ON CONFLICT (slug) DO NOTHING;
