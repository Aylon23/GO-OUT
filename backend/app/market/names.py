"""Readable security names and portfolio nicknames.

clean_name("AMAZON.COM, INC.", "AMZN")            -> "Amazon"
clean_name("Invesco QQQ Trust 1", "QQQ")          -> "Invesco QQQ (Nasdaq 100)"   (override map)
auto_aliases("Keren")                             -> ["קרן"]
auto_aliases("אבא")                               -> ["Dad", "Abba"]
"""
from __future__ import annotations

import re

# Names Yahoo spells badly or too long. Extend freely.
NAME_OVERRIDES = {
    "QQQ": "Invesco QQQ (Nasdaq 100)",
    "SPY": "SPDR S&P 500",
    "VOO": "Vanguard S&P 500",
    "IVV": "iShares Core S&P 500",
    "XLK": "Technology Select Sector SPDR",
    "XLI": "Industrial Select Sector SPDR",
    "XLF": "Financial Select Sector SPDR",
    "XLE": "Energy Select Sector SPDR",
    "XLV": "Health Care Select Sector SPDR",
    "XLY": "Consumer Discretionary Select Sector SPDR",
    "XLP": "Consumer Staples Select Sector SPDR",
    "SPMO": "Invesco S&P 500 Momentum",
    "IBIT": "iShares Bitcoin Trust",
    "ETHA": "iShares Ethereum Trust",
    "SOXX": "iShares Semiconductor",
    "IWM": "iShares Russell 2000",
    "FXI": "iShares China Large-Cap",
    "BRK-B": "Berkshire Hathaway B",
    "BRK-A": "Berkshire Hathaway A",
    "GOOGL": "Alphabet A",
    "GOOG": "Alphabet C",
    "META": "Meta Platforms",
    "NVO": "Novo Nordisk",
    "UNH": "UnitedHealth",
    "CRWV": "CoreWeave",
    "XPEV": "XPeng",
    "TTWO": "Take-Two Interactive",
    "AMZN": "Amazon",
    "NFLX": "Netflix",
    "DUOL": "Duolingo",
    "TEVA": "Teva",
    "TEVA.TA": "Teva",
}

_SUFFIX = re.compile(
    r"(?:,?\s+(?:Incorporated|Inc\.?|Corporation|Corp\.?|Company|Co\.?|Limited|Ltd\.?|PLC|plc|S\.A\.?|N\.V\.?|A/S|AG|SE|"
    r"Holdings?|Group|Trust\s+\d+|ETF|Fund|Common Stock|Class [A-C]|Ordinary Shares|ADR|\(The\)))+\.?\s*$",
    re.I,
)
_KEEP_UPPER = {"AI", "ETF", "S&P", "SPDR", "US", "USA", "QQQ", "AMD", "IBM", "HP", "NV", "SA", "II", "III", "MSCI", "REIT", "USD"}


def _recase(s: str) -> str:
    """Title-case names that arrive in ALL CAPS or with odd casing ("Amazon.Com")."""
    words = []
    for w in s.split():
        bare = re.sub(r"[^A-Za-z&]", "", w)
        if bare.upper() in _KEEP_UPPER:
            words.append(w.upper() if bare.isalpha() else w)
        elif w.isupper() or w.islower():
            words.append("-".join(p[:1].upper() + p[1:].lower() for p in w.split("-")))
        else:
            words.append(w)
    return " ".join(words)


def clean_name(name: str | None, ticker: str = "") -> str:
    t = (ticker or "").upper()
    if t in NAME_OVERRIDES:
        return NAME_OVERRIDES[t]
    n = (name or "").strip()
    if not n or n.upper() == t:
        return t  # no real name: show the ticker as is
    n = re.sub(r"\s*\((?:[A-Z]+:)?[A-Z.\- ]+\)\s*$", "", n)  # "(XNAS:QQQ)"
    n = re.sub(r"\.com\b", "", n, flags=re.I)  # "Amazon.com"
    if n.isupper() or re.search(r"[a-z]\.[A-Z][a-z]", n):
        n = _recase(n)
    for _ in range(3):
        n2 = _SUFFIX.sub("", n).strip(" ,.")
        if n2 == n or not n2:
            break
        n = n2
    return n or t


# ---- nicknames ---------------------------------------------------------------------------------

FAMILY = {  # English <-> Hebrew words people use for family portfolios
    "dad": ["אבא"], "father": ["אבא"], "mom": ["אמא"], "mother": ["אמא"], "mum": ["אמא"],
    "grandma": ["סבתא"], "grandpa": ["סבא"], "brother": ["אח"], "sister": ["אחות"], "me": ["אני"], "my portfolio": ["שלי"],
    "אבא": ["Dad"], "אמא": ["Mom"], "סבתא": ["Grandma"], "סבא": ["Grandpa"],
}
KNOWN = {  # common names whose spelling a letter rule would get wrong
    "keren": "קרן", "shir": "שיר", "ido": "עידו", "noa": "נועה", "yael": "יעל", "tamar": "תמר", "maya": "מאיה", "omer": "עומר",
    "itai": "איתי", "itay": "איתי", "eyal": "אייל", "avi": "אבי", "dana": "דנה", "michal": "מיכל", "ron": "רון", "guy": "גיא",
    "yoni": "יוני", "lior": "ליאור", "shira": "שירה", "noam": "נועם", "ori": "אורי", "tal": "טל", "gal": "גל", "dan": "דן",
    "kabir": "כביר", "aylon": "אילון", "eilon": "אילון", "moshe": "משה", "david": "דוד", "yossi": "יוסי",
}
_DIGRAPHS = [("sh", "ש"), ("ch", "ח"), ("kh", "ח"), ("tz", "צ"), ("ts", "צ"), ("th", "ת"), ("ph", "פ"), ("ou", "ו"), ("oo", "ו"),
             ("ee", "י"), ("ei", "י"), ("ai", "יי"), ("ay", "יי")]
_LETTERS = {"b": "ב", "c": "ק", "d": "ד", "f": "פ", "g": "ג", "h": "ה", "j": "ג'", "k": "ק", "l": "ל", "m": "מ", "n": "נ", "p": "פ",
            "q": "ק", "r": "ר", "s": "ס", "t": "ט", "v": "ו", "w": "ו", "x": "קס", "y": "י", "z": "ז", "i": "י", "o": "ו", "u": "ו",
            "a": "", "e": ""}
_FINAL = {"כ": "ך", "מ": "ם", "נ": "ן", "פ": "ף", "צ": "ץ"}
_HE2EN = {"א": "a", "ב": "b", "ג": "g", "ד": "d", "ה": "h", "ו": "o", "ז": "z", "ח": "ch", "ט": "t", "י": "i", "כ": "k", "ך": "ch",
          "ל": "l", "מ": "m", "ם": "m", "נ": "n", "ן": "n", "ס": "s", "ע": "a", "פ": "p", "ף": "f", "צ": "tz", "ץ": "tz", "ק": "k",
          "ר": "r", "ש": "sh", "ת": "t"}


def _en_to_he(word: str) -> str:
    w = word.lower()
    if w in KNOWN:
        return KNOWN[w]
    out, i = "", 0
    if w and w[0] in "aeiou":
        out += "א" if w[0] in "ae" else ("אי" if w[0] == "i" else "או")
        i = 1
    while i < len(w):
        pair = w[i : i + 2]
        hit = next((he for en, he in _DIGRAPHS if pair == en), None)
        if hit:
            out += hit
            i += 2
            continue
        ch = w[i]
        if ch == "a" and i == len(w) - 1:
            out += "ה"
        else:
            out += _LETTERS.get(ch, "")
        i += 1
    if out and out[-1] in _FINAL:
        out = out[:-1] + _FINAL[out[-1]]
    return out


def _he_to_en(word: str) -> str:
    known = {he: en.title() for en, he in KNOWN.items()}
    if word in known:
        return known[word]
    s = "".join(_HE2EN.get(c, c) for c in word)
    return s[:1].upper() + s[1:]


def auto_aliases(name: str) -> list[str]:
    """Hebrew and English nicknames for a portfolio name. Editable afterwards in Settings."""
    n = name.strip()
    low = n.lower()
    out: list[str] = []
    if low in FAMILY:
        out += FAMILY[low]
    elif re.search(r"[֐-׿]", n):
        out.append(" ".join(_he_to_en(w) for w in n.split()))
    else:
        parts = [p for p in re.split(r"\s*(?:&|and|ו)\s*|\s+", n) if p]
        he = [FAMILY[p.lower()][0] if p.lower() in FAMILY else _en_to_he(p) for p in parts]
        if he:
            out.append(he[0] if len(he) == 1 else " ".join(he[:-1]) + " ו" + he[-1])
    return [a for a in dict.fromkeys(out) if a and a.lower() != low]
