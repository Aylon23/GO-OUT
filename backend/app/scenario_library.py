"""What the scenario page knows about the world: market factors (as liquid ETFs), past events
to replay, and themes that connect words like "war with Iran" to those events.

Event windows are approximate: from the last close before the news to the close when the
first move had played out. They are meant for replays and calibration, not as a precise
history of each episode.
"""
from __future__ import annotations

# symbol, English label, Hebrew label, group
FACTORS: list[tuple[str, str, str, str]] = [
    ("SPY", "US stocks (S&P 500)", "מניות ארה״ב (S&P 500)", "Stocks"),
    ("QQQ", "Nasdaq 100", "נאסד״ק 100", "Stocks"),
    ("SOXX", "Chip makers", "מניות שבבים", "Stocks"),
    ("IWM", "US small caps", "מניות קטנות בארה״ב", "Stocks"),
    ("EIS", "Israeli stocks", "מניות ישראל", "Stocks"),
    ("XLK", "Tech sector", "סקטור הטכנולוגיה", "Sectors"),
    ("XLF", "Banks and finance", "בנקים ופיננסים", "Sectors"),
    ("XLE", "Energy stocks", "מניות אנרגיה", "Sectors"),
    ("XLV", "Healthcare", "בריאות", "Sectors"),
    ("XLY", "Consumer discretionary", "צריכה מחזורית", "Sectors"),
    ("XLP", "Consumer staples", "צריכה בסיסית", "Sectors"),
    ("XLI", "Industrials", "תעשייה", "Sectors"),
    ("XLU", "Utilities", "תשתיות", "Sectors"),
    ("XLB", "Materials", "חומרי גלם", "Sectors"),
    ("XLRE", "Real estate", "נדל״ן", "Sectors"),
    ("XLC", "Communication services", "תקשורת", "Sectors"),
    ("ITA", "Defense", "ביטחוניות", "Sectors"),
    ("TLT", "Long US bonds (20y+)", "אג״ח ארה״ב ארוכות", "Bonds"),
    ("IEF", "Mid US bonds (7-10y)", "אג״ח ארה״ב בינוניות", "Bonds"),
    ("HYG", "High-yield credit", "אג״ח קונצרני בתשואה גבוהה", "Bonds"),
    ("USO", "Oil", "נפט", "Commodities"),
    ("GLD", "Gold", "זהב", "Commodities"),
    ("BTC-USD", "Bitcoin", "ביטקוין", "Crypto"),
    ("^VIX", "Fear index (VIX)", "מדד הפחד (VIX)", "Volatility"),
]
FACTOR_SYMBOLS = [f[0] for f in FACTORS]
LABEL = {f[0]: f[1] for f in FACTORS}
LABEL_HE = {f[0]: f[2] for f in FACTORS}

# Sector name (Yahoo) -> factor that best stands in for a stock with too little history.
SECTOR_PROXY = {
    "Technology": "XLK", "Communication Services": "XLC", "Consumer Cyclical": "XLY", "Consumer Defensive": "XLP",
    "Healthcare": "XLV", "Financial Services": "XLF", "Energy": "XLE", "Industrials": "XLI", "Utilities": "XLU",
    "Basic Materials": "XLB", "Real Estate": "XLRE",
}

# id, name, Hebrew name, start (last close before), end, tags
EVENTS: list[dict] = [
    {"id": "iran_jun25", "name": "Israel–Iran war", "he": "מלחמת ישראל–איראן", "start": "2025-06-12", "end": "2025-06-24", "tags": ["iran", "war", "israel", "oil"]},
    {"id": "iran_oct24", "name": "Iran missile attack on Israel", "he": "מתקפת הטילים של איראן", "start": "2024-09-30", "end": "2024-10-04", "tags": ["iran", "war", "israel", "oil"]},
    {"id": "iran_apr24", "name": "Iran drone and missile attack", "he": "מתקפת הכטב״מים של איראן", "start": "2024-04-12", "end": "2024-04-19", "tags": ["iran", "war", "israel"]},
    {"id": "oct7_23", "name": "October 7 attack and war", "he": "7 באוקטובר והמלחמה", "start": "2023-10-06", "end": "2023-10-13", "tags": ["israel", "war"]},
    {"id": "ukraine22", "name": "Russia invades Ukraine", "he": "רוסיה פולשת לאוקראינה", "start": "2022-02-23", "end": "2022-03-08", "tags": ["war", "oil", "inflation"]},
    {"id": "covid20", "name": "COVID crash", "he": "קריסת הקורונה", "start": "2020-02-19", "end": "2020-03-23", "tags": ["pandemic", "crash", "recession", "oil_down"]},
    {"id": "oilwar20", "name": "Saudi–Russia oil price war", "he": "מלחמת מחירי הנפט", "start": "2020-03-06", "end": "2020-03-09", "tags": ["oil_down", "crash"]},
    {"id": "oilneg20", "name": "Oil futures go negative", "he": "הנפט במחיר שלילי", "start": "2020-04-17", "end": "2020-04-21", "tags": ["oil_down"]},
    {"id": "cpi_jun22", "name": "Hot inflation print, Fed hikes 0.75%", "he": "אינפלציה גבוהה והעלאת ריבית חדה", "start": "2022-06-09", "end": "2022-06-16", "tags": ["rates_up", "inflation"]},
    {"id": "jackson22", "name": "Powell at Jackson Hole", "he": "נאום פאוול בג׳קסון הול", "start": "2022-08-25", "end": "2022-09-06", "tags": ["rates_up"]},
    {"id": "cpi_sep22", "name": "Inflation surprise (Sep 2022)", "he": "הפתעת אינפלציה (ספט׳ 2022)", "start": "2022-09-12", "end": "2022-09-13", "tags": ["rates_up", "inflation"]},
    {"id": "bear22", "name": "2022 bear market", "he": "שוק הדובים של 2022", "start": "2022-01-03", "end": "2022-10-12", "tags": ["rates_up", "inflation", "recession", "tech_down"]},
    {"id": "cpi_nov23", "name": "Inflation cools, rate-cut hopes", "he": "האינפלציה מתקררת, ציפייה להורדת ריבית", "start": "2023-11-13", "end": "2023-11-14", "tags": ["rates_down"]},
    {"id": "fedcut_sep24", "name": "Fed starts cutting (0.5%)", "he": "הפד מתחיל להוריד ריבית", "start": "2024-09-17", "end": "2024-09-19", "tags": ["rates_down"]},
    {"id": "svb23", "name": "SVB and regional bank failures", "he": "קריסת SVB והבנקים האזוריים", "start": "2023-03-08", "end": "2023-03-17", "tags": ["banks", "rates_down"]},
    {"id": "libday25", "name": "\"Liberation Day\" tariffs", "he": "המכסים של \"יום השחרור\"", "start": "2025-04-02", "end": "2025-04-08", "tags": ["tariffs", "crash"]},
    {"id": "tariffpause25", "name": "Tariff pause rally", "he": "הראלי אחרי השעיית המכסים", "start": "2025-04-08", "end": "2025-04-09", "tags": ["tariffs_relief", "rally"]},
    {"id": "deepseek25", "name": "DeepSeek AI shock", "he": "הלם DeepSeek", "start": "2025-01-24", "end": "2025-01-27", "tags": ["ai", "tech_down"]},
    {"id": "yen24", "name": "Yen carry-trade unwind", "he": "פירוק עסקאות הין", "start": "2024-07-31", "end": "2024-08-05", "tags": ["crash", "tech_down"]},
    {"id": "election24", "name": "US election 2024", "he": "הבחירות בארה״ב 2024", "start": "2024-11-05", "end": "2024-11-11", "tags": ["election", "rally"]},
    {"id": "trade19", "name": "Trade war escalation (May 2019)", "he": "הסלמה במלחמת הסחר (מאי 2019)", "start": "2019-05-03", "end": "2019-05-13", "tags": ["tariffs", "china"]},
    {"id": "trade19b", "name": "Trade war and yuan drop (Aug 2019)", "he": "מלחמת סחר ונפילת היואן (אוג׳ 2019)", "start": "2019-07-31", "end": "2019-08-05", "tags": ["tariffs", "china"]},
    {"id": "q4_18", "name": "Q4 2018 sell-off", "he": "המפולת של סוף 2018", "start": "2018-10-03", "end": "2018-12-24", "tags": ["rates_up", "crash", "recession"]},
    {"id": "volm18", "name": "\"Volmageddon\" (Feb 2018)", "he": "קריסת התנודתיות (פבר׳ 2018)", "start": "2018-02-01", "end": "2018-02-08", "tags": ["crash", "rates_up"]},
    {"id": "brexit16", "name": "Brexit vote", "he": "משאל הברקזיט", "start": "2016-06-23", "end": "2016-06-27", "tags": ["crash"]},
    {"id": "china15", "name": "China devaluation (Aug 2015)", "he": "פיחות היואן (אוג׳ 2015)", "start": "2015-08-18", "end": "2015-08-25", "tags": ["china", "crash"]},
    {"id": "usdown11", "name": "US credit downgrade", "he": "הורדת דירוג ארה״ב", "start": "2011-07-29", "end": "2011-08-08", "tags": ["crash", "recession"]},
    {"id": "lehman08", "name": "Lehman collapse", "he": "קריסת ליהמן", "start": "2008-09-12", "end": "2008-10-10", "tags": ["banks", "crash", "recession"]},
]
EVENT_BY_ID = {e["id"]: e for e in EVENTS}

# Themes: words -> past events and the few factors that carry the story.
# `any` is a list of word groups; every group must match. `fallback` gives directions (+1/-1)
# used only when none of the events has data.
THEMES: list[dict] = [
    {"id": "iran_war", "label": "War with Iran", "any": [["איראן", "אירן", "iran", "iranian"]],
     "events": ["iran_jun25", "iran_oct24", "iran_apr24", "oct7_23"], "factors": ["USO", "SPY", "EIS", "ITA", "GLD"],
     "fallback": {"USO": 1, "SPY": -1, "EIS": -1, "ITA": 1, "GLD": 1}},
    {"id": "israel_war", "label": "Escalation in Israel", "any": [["ישראל", "israel", "חיזבאללה", "hezbollah", "עזה", "gaza", "לבנון", "lebanon"],
     ["מלחמה", "war", "הסלמה", "escalation", "טרור", "terror", "מתקפה", "attack", "תקיפה", "ביטחוני"]],
     "events": ["oct7_23", "iran_apr24", "iran_oct24"], "factors": ["EIS", "^TA125.TA", "SPY"], "fallback": {"EIS": -1, "^TA125.TA": -1, "SPY": -1}},
    {"id": "war", "label": "A major war", "any": [["מלחמה", "מלחמת עולם", "war", "invasion", "פלישה", "invade"]],
     "events": ["ukraine22", "oct7_23", "iran_jun25"], "factors": ["SPY", "USO", "GLD", "ITA"], "fallback": {"SPY": -1, "USO": 1, "GLD": 1, "ITA": 1}},
    {"id": "pandemic", "label": "A pandemic", "any": [["מגפה", "קורונה", "covid", "pandemic", "וירוס", "virus", "סגר", "lockdown"]],
     "events": ["covid20"], "factors": ["SPY", "USO", "TLT"], "fallback": {"SPY": -1, "USO": -1, "TLT": 1}},
    {"id": "recession", "label": "A recession", "any": [["מיתון", "recession", "האטה", "slowdown"]],
     "events": ["covid20", "q4_18", "lehman08", "bear22"], "factors": ["SPY", "TLT", "HYG", "USO"], "fallback": {"SPY": -1, "TLT": 1, "HYG": -1, "USO": -1}},
    {"id": "bank_crisis", "label": "A banking crisis", "any": [["בנק", "בנקים", "bank", "banks", "svb"], ["משבר", "קריס", "crisis", "collapse", "fail", "ריצה", "run"]],
     "events": ["svb23", "lehman08"], "factors": ["XLF", "SPY", "TLT"], "fallback": {"XLF": -1, "SPY": -1, "TLT": 1}},
    {"id": "crash", "label": "A market crash", "any": [["קריסה", "קריסת", "מפולת", "crash", "sell-off", "selloff", "התרסקות", "בהלה", "panic", "התמוטטות"]],
     "events": ["covid20", "q4_18", "yen24", "libday25", "volm18"], "factors": ["SPY", "^VIX", "TLT"], "fallback": {"SPY": -1, "^VIX": 1, "TLT": 1}},
    {"id": "tariffs", "label": "Tariffs and a trade war", "any": [["מכס", "מכסים", "tariff", "tariffs", "trade war", "מלחמת סחר"]],
     "events": ["libday25", "trade19", "trade19b"], "factors": ["SPY", "SOXX", "IWM"], "fallback": {"SPY": -1, "SOXX": -1, "IWM": -1}},
    {"id": "ai_bust", "label": "AI stocks bust", "any": [["בועה", "bubble", "deepseek", "דיפסיק", "בינה מלאכותית", "ai"]],
     "events": ["deepseek25", "yen24"], "factors": ["SOXX", "QQQ"], "fallback": {"SOXX": -1, "QQQ": -1}},
    {"id": "china", "label": "China / Taiwan tension", "any": [["סין", "טייוואן", "china", "taiwan", "chinese"]],
     "events": ["trade19", "trade19b", "china15"], "factors": ["SOXX", "QQQ", "SPY"], "fallback": {"SOXX": -1, "QQQ": -1, "SPY": -1}},
    {"id": "rate_hike", "label": "The Fed raises rates", "any": [["העלאת ריבית", "מעלה ריבית", "מעלה את הריבית", "יעלה ריבית", "יעלה את הריבית", "ריבית עולה",
     "הריבית עולה", "הריבית תעלה", "rate hike", "hikes rates", "raises rates", "rates rise", "rates up", "אינפלציה", "inflation"]],
     "events": ["cpi_jun22", "jackson22", "cpi_sep22"], "factors": ["TLT", "QQQ"], "fallback": {"TLT": -1, "QQQ": -1}},
    {"id": "rate_cut", "label": "The Fed cuts rates", "any": [["הורדת ריבית", "מוריד ריבית", "מוריד את הריבית", "יוריד ריבית", "יוריד את הריבית", "ריבית יורדת",
     "הריבית יורדת", "הריבית תרד", "rate cut", "cuts rates", "rates fall", "rates down", "lower rates"]],
     "events": ["cpi_nov23", "fedcut_sep24"], "factors": ["TLT", "IWM"], "fallback": {"TLT": 1, "IWM": 1}},
]
THEME_BY_ID = {t["id"]: t for t in THEMES}
