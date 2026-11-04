"""Sample datasets the design shows until a live source is connected. Every block is tagged demo."""

META = {
    "source": "demo",
    "range": "25 Sep – 24 Oct 2026",
    "spend": 1281071.85,
    "reach": 880496,
    "leads": 1009,
    "days": ["09-25", "09-26", "09-27", "09-28", "09-29", "09-30", "10-01", "10-02", "10-03", "10-04", "10-05", "10-06", "10-07", "10-08", "10-09", "10-10", "10-11", "10-12", "10-13", "10-14", "10-15", "10-16", "10-17", "10-18", "10-19", "10-20", "10-21", "10-22", "10-23", "10-24"],
    "cost": [33279, 33274, 30128, 62638, 64050, 69750, 53166, 54286, 44997, 51371, 50607, 50910, 47234, 44093, 49539, 41274, 44175, 48322, 42979, 44263, 43014, 35326, 28055, 31491, 30186, 30918, 26474, 30795, 25331, 39145],
    "daily_leads": [31, 32, 25, 76, 80, 68, 44, 34, 47, 54, 22, 17, 38, 39, 31, 40, 34, 43, 41, 51, 24, 35, 14, 15, 14, 16, 14, 13, 8, 9],
    "clicks": [845, 638, 678, 1316, 1296, 1031, 1085, 1292, 1022, 1045, 573, 802, 816, 785, 970, 1015, 1125, 1169, 1002, 1030, 1120, 1097, 1042, 1183, 937, 849, 867, 1027, 927, 1506],
    "campaigns": [
        {"name": "New CPD Campaign", "spend": 570342.58, "impressions": 1260709, "reach": 332835, "clicks": 10340, "landing_views": 10, "leads": 415},
        {"name": "ATHE Overall Campaign", "spend": 391378.58, "impressions": 1105199, "reach": 406404, "clicks": 10076, "landing_views": 0, "leads": 200},
        {"name": "Free Foundation Leads Campaign", "spend": 126516.61, "impressions": 261698, "reach": 137143, "clicks": 1469, "landing_views": 12, "leads": 236},
        {"name": "ATHE PostGrad", "spend": 82436.72, "impressions": 160977, "reach": 58969, "clicks": 1993, "landing_views": 3, "leads": 91},
        {"name": "Chatbot - Campaign", "spend": 46308.72, "impressions": 33004, "reach": 18951, "clicks": 825, "landing_views": 0, "leads": 3},
        {"name": "Jain PG Lead Campaign - Copy", "spend": 38084.47, "impressions": 64404, "reach": 25537, "clicks": 517, "landing_views": 2, "leads": 64},
        {"name": "New Web Traffic Campaign 2026", "spend": 22661.6, "impressions": 88528, "reach": 62570, "clicks": 4861, "landing_views": 2001, "leads": 0},
        {"name": "WTC Event", "spend": 3342.57, "impressions": 81598, "reach": 71349, "clicks": 9, "landing_views": 0, "leads": 0},
    ],
    "ages": ["18-24", "25-34", "35-44", "45-54", "55-64", "65+"],
    "men_leads": [242, 245, 111, 49, 17, 6],
    "women_leads": [138, 92, 53, 34, 11, 5],
    "men_spend": [319592, 245410, 108184, 67955, 30740, 25436],
    "women_spend": [200126, 137716, 64795, 40460, 18756, 12228],
    "regions": {
        "Western": {"spend": 1017728, "clicks": 21535, "leads": 843},
        "Central": {"spend": 104792, "clicks": 3145, "leads": 66},
        "Southern": {"spend": 57159, "clicks": 2120, "leads": 31},
        "North Western": {"spend": 44078, "clicks": 1380, "leads": 39},
        "Sabaragamuwa": {"spend": 42684, "clicks": 1501, "leads": 26},
        "Northern": {"spend": 10609, "clicks": 272, "leads": 2},
        "Uva": {"spend": 3926, "clicks": 132, "leads": 2},
        "North Central": {"spend": 0, "clicks": 0, "leads": 0},
        "Eastern": {"spend": 0, "clicks": 0, "leads": 0},
    },
}

CHANNELS = [
    {"name": "Meta Ads", "color": "#0A84FF", "spend": META["spend"], "leads": META["leads"], "enrolled": 22, "source": "demo", "trend": [52, 58, 61, 55, 49, 44, 31, 24]},
    {"name": "Google Ads", "color": "#30D158", "spend": 310000, "leads": 268, "enrolled": 9, "source": "demo", "trend": [22, 24, 25, 27, 26, 29, 31, 30]},
    {"name": "TikTok Ads", "color": "#FF375F", "spend": 140000, "leads": 192, "enrolled": 3, "source": "demo", "trend": [8, 12, 15, 19, 22, 24, 27, 29]},
    {"name": "Instagram organic", "color": "#BF5AF2", "spend": 0, "leads": 118, "enrolled": 3, "source": "demo", "trend": [11, 12, 13, 12, 14, 15, 15, 16]},
    {"name": "WhatsApp Business", "color": "#63E6E2", "spend": 18000, "leads": 236, "enrolled": 8, "source": "demo", "trend": [24, 26, 27, 29, 30, 31, 33, 34]},
    {"name": "Website & SEO", "color": "#5E5CE6", "spend": 45000, "leads": 154, "enrolled": 6, "source": "demo", "trend": [15, 16, 17, 18, 19, 20, 21, 22]},
    {"name": "LinkedIn Ads", "color": "#AC8E68", "spend": 85000, "leads": 41, "enrolled": 2, "source": "demo", "trend": [4, 5, 5, 6, 5, 6, 6, 6]},
    {"name": "Email", "color": "#FF9F0A", "spend": 22000, "leads": 63, "enrolled": 2, "source": "demo", "trend": [6, 7, 8, 8, 9, 8, 9, 9]},
    {"name": "Referral & Champions", "color": "#FFD60A", "spend": 30000, "leads": 48, "enrolled": 6, "source": "demo", "trend": [3, 4, 5, 5, 6, 7, 8, 9]},
    {"name": "Events & walk-in", "color": "#8E8E93", "spend": 65000, "leads": 72, "enrolled": 4, "source": "demo", "trend": [3, 3, 4, 4, 4, 5, 5, 5]},
]

PAGES = [
    {"path": "/hnd-computing-ai", "views": 4820, "time": "2:41", "student": 58, "parent": 14, "apply": 6.2},
    {"path": "/fees-and-payment-plans", "views": 3110, "time": "3:58", "student": 22, "parent": 61, "apply": 9.8},
    {"path": "/finance-literacy", "views": 2960, "time": "1:52", "student": 18, "parent": 9, "apply": 4.1},
    {"path": "/ai-for-hr", "views": 2140, "time": "2:05", "student": 8, "parent": 6, "apply": 5.3},
    {"path": "/scholarships", "views": 1730, "time": "3:12", "student": 41, "parent": 47, "apply": 7.6},
    {"path": "/athe-accreditation", "views": 1210, "time": "2:47", "student": 20, "parent": 58, "apply": 3.2},
    {"path": "/scu-masters", "views": 980, "time": "2:22", "student": 10, "parent": 12, "apply": 4.8},
    {"path": "/student-life", "views": 870, "time": "1:31", "student": 79, "parent": 15, "apply": 1.9},
]

PLATFORMS = [
    {"id": "meta", "name": "Meta Ads", "kind": "Facebook & Instagram ads", "color": "#0A84FF", "glyph": "M", "detail": "Spend, reach, leads, age, gender and region by campaign", "status": "Connected", "tone": "good"},
    {"id": "capi", "name": "Meta Conversions API", "kind": "Server-side events to Meta", "color": "#0A84FF", "glyph": "C", "detail": "Sends leads and enrolments back to Meta", "status": "Not connected", "tone": ""},
    {"id": "ga4", "name": "Google Analytics 4", "kind": "Website behaviour", "color": "#FF9F0A", "glyph": "G", "detail": "Pages, sessions, countries, sources and engagement", "status": "Login needed", "tone": "warn"},
    {"id": "gtm", "name": "Google Tag Manager", "kind": "Tags, triggers and events", "color": "#0A84FF", "glyph": "T", "detail": "One place to fire every pixel and event", "status": "Partly set up", "tone": "warn"},
    {"id": "gads", "name": "Google Ads", "kind": "Search and YouTube ads", "color": "#30D158", "glyph": "A", "detail": "Spend, keywords, clicks and conversions", "status": "Not connected", "tone": ""},
    {"id": "gsc", "name": "Search Console", "kind": "Organic search", "color": "#30D158", "glyph": "S", "detail": "Search terms people use to find Inspire College", "status": "Not connected", "tone": ""},
    {"id": "tiktok", "name": "TikTok Ads & Analytics", "kind": "Paid and organic TikTok", "color": "#FF375F", "glyph": "K", "detail": "Ad spend, video views and lead forms", "status": "Not connected", "tone": ""},
    {"id": "wa", "name": "WhatsApp Business Platform", "kind": "Multiple numbers, one per counsellor", "color": "#30D158", "glyph": "W", "detail": "Bot handovers, chat timelines and logged reply times", "status": "Not connected", "tone": ""},
    {"id": "ig", "name": "Instagram Insights", "kind": "Organic reach and DMs", "color": "#BF5AF2", "glyph": "I", "detail": "Reels, story and DM performance", "status": "Not connected", "tone": ""},
    {"id": "li", "name": "LinkedIn Ads", "kind": "Professional audiences", "color": "#AC8E68", "glyph": "L", "detail": "CPD and postgraduate lead forms", "status": "Not connected", "tone": ""},
    {"id": "yt", "name": "YouTube", "kind": "Video channel", "color": "#FF453A", "glyph": "Y", "detail": "Views and watch time for programme videos", "status": "Not connected", "tone": ""},
    {"id": "email", "name": "Email", "kind": "Mailjet delivery", "color": "#FF9F0A", "glyph": "E", "detail": "Sent, blocked, bounced, opened and clicked", "status": "Not connected", "tone": ""},
    {"id": "payhere", "name": "PayHere", "kind": "Payments and instalments", "color": "#5E5CE6", "glyph": "P", "detail": "Registration fees and monthly instalments", "status": "Not connected", "tone": ""},
    {"id": "brain", "name": "Inspire Brain", "kind": "LMS and ERP", "color": "#BF5AF2", "glyph": "B", "detail": "Active students, attendance and dropout", "status": "Not connected", "tone": ""},
    {"id": "forms", "name": "Website forms", "kind": "Apply and enquiry forms", "color": "#8E8E93", "glyph": "F", "detail": "Form submissions into the CRM with UTM tags", "status": "Connected", "tone": "good"},
    {"id": "calls", "name": "Call tracking", "kind": "Counsellor calls", "color": "#64D2FF", "glyph": "☎", "detail": "Call attempts, connects and durations", "status": "Not connected", "tone": ""},
]

GTM_EVENTS = [
    {"event": "page_view", "where": "All pages", "destinations": "GA4 · Meta · TikTok", "status": "Live"},
    {"event": "view_programme", "where": "Programme pages", "destinations": "GA4", "status": "Live"},
    {"event": "fee_calculator_used", "where": "No fee calculator on the website yet", "destinations": "GA4", "status": "Planned"},
    {"event": "brochure_download", "where": "Download brochure link", "destinations": "GA4", "status": "Live"},
    {"event": "whatsapp_click", "where": "Any WhatsApp link or button", "destinations": "GA4", "status": "Live"},
    {"event": "apply_start", "where": "Admissions page opened", "destinations": "GA4", "status": "Live"},
    {"event": "generate_lead", "where": "Contact, application or event form submitted", "destinations": "GA4", "status": "Live"},
    {"event": "webinar_register", "where": "Event registration form", "destinations": "GA4", "status": "Live"},
    {"event": "payment_complete", "where": "Not built. Must come from the verified PayHere notification", "destinations": "GA4", "status": "Planned"},
    {"event": "scroll_75", "where": "75% scroll on programme pages", "destinations": "GA4", "status": "Live"},
]

CHAMPIONS = [
    {"name": "Nethmi Perera", "campus": "HND Computing", "referrals": 6, "enrolled": 2, "tier": "Pathfinder"},
    {"name": "Kasun Herath", "campus": "Finance Literacy", "referrals": 4, "enrolled": 1, "tier": "Mentor"},
    {"name": "Fathima Rahman", "campus": "AI for HR", "referrals": 3, "enrolled": 1, "tier": "Advisor"},
    {"name": "Arjun Sivakumar", "campus": "HND Business", "referrals": 2, "enrolled": 0, "tier": "Rookie"},
]

VISITORS = {
    "source": "demo",
    "users_7d": 12960,
    "sessions_7d": 18420,
    "visitor_to_lead": 2.9,
    "overseas": 18,
    "mobile": 78,
    "peak": "7–10 pm",
    "avg_engagement": "1:48",
    "devices": [{"name": "Mobile", "value": 78}, {"name": "Desktop", "value": 19}, {"name": "Tablet", "value": 3}],
    "visitor_types": [{"name": "New visitors", "value": 71}, {"name": "Returning", "value": 29}],
    "intent": [{"name": "Browsing", "value": 58}, {"name": "Comparing programmes", "value": 24}, {"name": "Checking fees", "value": 12}, {"name": "Ready to apply", "value": 6}],
    "hours": [52, 44, 41, 40, 41, 43, 47, 52, 61, 72, 92, 128, 153, 162, 134, 96, 71, 62, 77, 128, 178, 214, 186, 118],
    "cities": [
        {"city": "Colombo", "country": "Sri Lanka", "users": 4120, "lat": 6.93, "lon": 79.85, "s": 1566, "p": 906, "w": 1648},
        {"city": "Gampaha", "country": "Sri Lanka", "users": 1460, "lat": 7.09, "lon": 79.99, "s": 701, "p": 350, "w": 409},
        {"city": "Kandy", "country": "Sri Lanka", "users": 980, "lat": 7.29, "lon": 80.63, "s": 539, "p": 245, "w": 196},
        {"city": "Kurunegala", "country": "Sri Lanka", "users": 640, "lat": 7.49, "lon": 80.36, "s": 333, "p": 179, "w": 128},
        {"city": "Negombo", "country": "Sri Lanka", "users": 610, "lat": 7.21, "lon": 79.84, "s": 281, "p": 146, "w": 183},
        {"city": "Dubai", "country": "United Arab Emirates", "users": 540, "lat": None, "lon": None, "s": 65, "p": 367, "w": 108},
        {"city": "Galle", "country": "Sri Lanka", "users": 420, "lat": 6.05, "lon": 80.22, "s": 210, "p": 118, "w": 92},
        {"city": "Doha", "country": "Qatar", "users": 290, "lat": None, "lon": None, "s": 35, "p": 197, "w": 58},
        {"city": "Matara", "country": "Sri Lanka", "users": 260, "lat": 5.95, "lon": 80.55, "s": 135, "p": 78, "w": 47},
        {"city": "London", "country": "United Kingdom", "users": 260, "lat": None, "lon": None, "s": 31, "p": 177, "w": 52},
        {"city": "Ratnapura", "country": "Sri Lanka", "users": 210, "lat": 6.69, "lon": 80.40, "s": 109, "p": 63, "w": 38},
        {"city": "Jaffna", "country": "Sri Lanka", "users": 180, "lat": 9.66, "lon": 80.02, "s": 90, "p": 63, "w": 27},
    ],
    "personas": [{"name": "Students", "value": 46}, {"name": "Parents & guardians", "value": 27}, {"name": "Working professionals", "value": 27}],
    "sources": [
        {"name": "Meta (paid social)", "value": 44}, {"name": "Google organic", "value": 18}, {"name": "Direct", "value": 14},
        {"name": "WhatsApp", "value": 9}, {"name": "Instagram", "value": 6}, {"name": "TikTok", "value": 5}, {"name": "Referral", "value": 4},
    ],
    "countries": [{"name": "Sri Lanka", "value": 82}, {"name": "UAE", "value": 6}, {"name": "Qatar", "value": 3}, {"name": "United Kingdom", "value": 3}, {"name": "Australia", "value": 2}, {"name": "Other", "value": 4}],
    "country_rows": [{"name": "Sri Lanka", "value": 82}, {"name": "United Arab Emirates", "value": 6}, {"name": "Qatar", "value": 3}, {"name": "United Kingdom", "value": 3}, {"name": "Australia", "value": 2}],
}

PARTNER_TYPES = ["Influencer", "Tutor", "Partner agent", "School", "Alumni", "Student champion", "Corporate partner"]
PARTNER_PLATFORMS = [
    {"id": "all", "name": "One Link · every platform"},
    {"id": "instagram", "name": "Instagram"},
    {"id": "tiktok", "name": "TikTok"},
    {"id": "youtube", "name": "YouTube"},
    {"id": "facebook", "name": "Facebook"},
    {"id": "linkedin", "name": "LinkedIn"},
    {"id": "whatsapp", "name": "WhatsApp"},
    {"id": "x", "name": "X"},
    {"id": "snapchat", "name": "Snapchat"},
    {"id": "website", "name": "Blog / website"},
    {"id": "offline", "name": "QR / print"},
]




def meta_summary() -> str:
    spend = META["spend"]
    leads = META["leads"]
    camps = "; ".join(f"{item['name']} {round(item['spend'])}/{item['leads']} leads" for item in META["campaigns"])
    ages = ", ".join(f"{age} {META['men_leads'][i]}/{META['women_leads'][i]}" for i, age in enumerate(META["ages"]))
    return f"Meta Ads {META['range']} (demo snapshot): spend LKR {spend:,.0f}, {leads} leads, CPL {spend / leads:,.0f}. Campaigns: {camps}. Leads by age (men/women): {ages}."


UNIT_ECONOMICS = [
    {"name": "HND Computing & AI", "cpl": 1957, "cac": 108717, "ltv": 262500},
    {"name": "HND Data Analytics", "cpl": 1957, "cac": 108717, "ltv": 262500},
    {"name": "HND Business Mgmt", "cpl": 1957, "cac": 108717, "ltv": 262500},
    {"name": "HND Accounting", "cpl": 1957, "cac": 108717, "ltv": 262500},
    {"name": "Finance Literacy", "cpl": 1374, "cac": 39267, "ltv": 42500},
    {"name": "AI for HR", "cpl": 1374, "cac": 39267, "ltv": 42500},
    {"name": "Digital Marketing", "cpl": 1374, "cac": 39267, "ltv": 42500},
]

ATTRIBUTION = {
    "models": ["First touch", "Last touch", "Linear", "Time decay"],
    "channels": [
        {"name": "Meta Ads", "values": [46, 31, 38, 36]},
        {"name": "Website / Google", "values": [14, 27, 22, 23]},
        {"name": "WhatsApp", "values": [12, 18, 16, 17]},
        {"name": "Referral", "values": [18, 12, 13, 14]},
        {"name": "Walk-in & events", "values": [10, 12, 11, 10]},
    ],
}
