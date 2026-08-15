"""Rule-based title optimizer for Shorts.

Turns a raw clip hook into a curiosity-gap headline.

No LLM, no network, no external deps: pure stdlib rules.

Design constraints:
  * Never fabricate a claim.
  * No em dashes in final titles.
  * Titles stay under max_len chars.
  * Stable: same hook + niche + keywords + clip index always yields same title.
"""

import hashlib
import html
import re
import unicodedata

# Latin letter blocks (basic Latin, Latin-1 Supplement, Latin Extended-A/B,
# Latin Extended-Additional, Latin Extended-C). Any letter outside these is a
# non-Latin script (Cyrillic, Greek, CJK, Arabic, Hebrew, ...) and marks a
# hook as non-English "gibberish" from a foreign source or a Whisper
# hallucination -- never fit for an English Short title.
_LATIN_BLOCKS = (
    (0x0041, 0x007A),  # A-Z a-z
    (0x00C0, 0x024F),  # Latin-1 Supplement + Latin Extended-A/B
    (0x1E00, 0x1EFF),  # Latin Extended-Additional
    (0x2C60, 0x2C7F),  # Latin Extended-C
)


def _is_latin_letter(cp):
    return any(lo <= cp <= hi for lo, hi in _LATIN_BLOCKS)


# High-frequency English tokens used to sanity-check whether a Latin-script
# hook is actually English. Kept small and possessive-free; enough for a
# coverage heuristic, not a dictionary.
_COMMON_ENGLISH = frozenset("""
a about after again all also an and any are as at be because been before being
between both but by can could did do does doing down each even every few find
first for from get go going good got had has have he her here him his how i if
in into is it its just know like long look made make man many me more most much
my never new no not now of off on one only or other our out over own people
right said same say see she should so some something still such take tell than
that the their them then there these they thing this those through time to too
two under up us very want was way we well were what when where which while who
why will with would year you your
""".split())


def _tokenize(text):
    return [t.lower() for t in re.findall(r"[A-Za-z][A-Za-z']*", text or "")]


def _leading_clause(text):
    """Return the first sentence of a hook (what a Shorts title shows first)."""
    text = (text or "").strip()
    match = re.search(r"[.!?]", text)
    if match:
        return text[: match.end()]
    return text


def looks_non_english(text):
    """True when a hook looks like non-English / hallucinated garbage.

    Rules (stdlib only):
      1. Any non-Latin letter (Cyrillic, Greek, CJK, Arabic, ...) -> garbage.
      2. The leading clause (first sentence) must be English-dominant too:
         a hook that *starts* in a foreign language is broken even when the
         tail is English, because Shorts titles are truncated from the front.
      3. Otherwise, over a minimum length, low coverage of common English
         tokens -> probably a foreign (e.g. Welsh) transcript.
    """
    text = _fix_spacing(text)
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False

    if any(not _is_latin_letter(ord(c)) for c in letters):
        return True

    lead = _leading_clause(text)
    lead_tokens = _tokenize(lead)
    if len(lead_tokens) >= 6:
        lead_hits = sum(1 for t in lead_tokens if t in _COMMON_ENGLISH)
        if lead_hits / len(lead_tokens) < 0.10:
            return True

    tokens = _tokenize(text)
    if len(tokens) < 6:
        return False

    hits = sum(1 for t in tokens if t in _COMMON_ENGLISH)
    return hits / len(tokens) < 0.30


def _looks_non_english(text):
    return looks_non_english(text)


FILLER_LEADS = [
    "and then",
    "you know",
    "i mean",
    "basically",
    "honestly",
    "literally",
    "actually",
    "to be honest",
    "to be fair",
    "at the end of the day",
    "what i'm saying is",
    "what i am saying is",
    "let me tell you",
    "let me explain",
    "here's the thing",
    "here is the thing",
    "the thing is",
    "so",
    "and",
    "but",
    "well",
    "um",
    "uh",
    "okay",
    "ok",
]

# These are stripped only when followed by punctuation.
# This avoids breaking phrases like "Look at this" or "Right before".
FILLER_LEADS_PUNCT_ONLY = [
    "look",
    "listen",
    "right",
    "now",
    "like",
    "as a result",
]

STRONG_HOOK_MARKERS = [
    "secret",
    "real reason",
    "nobody tells you",
    "nobody talks about",
    "the truth",
    "this is why",
    "here's why",
    "here is why",
    "that's why",
    "the reason",
    "mistake",
    "worst",
    "best",
    "most people",
    "nobody",
    "no one",
    "never",
    "don't",
    "do not",
    "stop",
    "before you",
    "what happens",
    "how to",
    "one thing",
    "biggest",
    "smallest",
    "first time",
    "last time",
    "turns out",
    "i was wrong",
    "myth",
    "lie",
    "hidden",
    "untold",
    "red flag",
    "warning",
    "trap",
    "scam",
    "costly",
    "dangerous",
    "changed everything",
    "exposed",
]

QUESTION_WORDS = {
    "who", "what", "when", "where", "why", "how", "which"
}

AUX_QUESTION_STARTERS = {
    "can", "could", "should", "would",
    "do", "does", "did",
    "is", "are", "was", "were",
}

TRAILING_JUNK_WORDS = {
    "and", "or", "but", "because", "if", "when", "while", "with",
    "without", "to", "for", "of", "in", "on", "at", "from", "as",
    "the", "a", "an", "this", "that", "these", "those",
}

GENERIC_HASHTAGS = {
    "short", "shorts", "ytshorts", "youtube", "viral", "fyp", "foryou"
}

GENERIC_FRAMES = [
    "The part people miss",
    "The key detail",
    "The moment that matters",
    "The lesson",
    "The pattern",
    "The takeaway",
]

# Keep default niche frames conservative.
# Put aggressive labels like mistake, trap, warning, secret in FRAME_HINTS below.
NICHE_FRAMES = {
    "capital_mindset": [
        "The money lesson",
        "The wealth mindset",
        "The mindset shift",
        "The money pattern",
        "The rich habit",
    ],
    "flick_shorts": [
        "The movie detail",
        "The scene detail",
        "The plot clue",
        "The film lesson",
        "The key moment",
    ],
    "future_tech_daily": [
        "The tech shift",
        "The future signal",
        "The AI lesson",
        "The tech detail",
        "The next upgrade",
    ],
    "peak_human_lab": [
        "The body signal",
        "The performance lesson",
        "The health detail",
        "The recovery lesson",
        "The human edge",
    ],
    "untold_frontlines": [
        "The real story",
        "The frontline detail",
        "The key context",
        "The turning point",
        "The untold detail",
    ],
    "psychology_behavior": [
        "The psychology",
        "The behavior pattern",
        "The mental shift",
        "The human pattern",
        "The motive",
    ],
    "self_improvement": [
        "The lesson",
        "The mindset shift",
        "The habit that matters",
        "The simple rule",
        "The hard truth",
    ],
    "relationships_dating": [
        "The dating pattern",
        "The relationship signal",
        "The communication lesson",
        "The hidden dynamic",
        "The moment that matters",
    ],
    "personal_finance": [
        "The money lesson",
        "The finance detail",
        "The cash flow lesson",
        "The wealth habit",
        "The budget lesson",
    ],
    "real_estate_wealth": [
        "The property play",
        "The real estate lesson",
        "The buyer detail",
        "The equity move",
        "The deal detail",
    ],
    "crypto_web3": [
        "The crypto move",
        "The Web3 lesson",
        "The market signal",
        "The token lesson",
        "The blockchain shift",
    ],
    "history_mysteries": [
        "The history detail",
        "The history lesson",
        "The old pattern",
        "The key context",
        "The mystery",
    ],
    "true_crime_cases": [
        "The case file",
        "The case detail",
        "The timeline clue",
        "The clue that matters",
        "The key detail",
    ],
    "productivity_career": [
        "The career move",
        "The productivity lesson",
        "The work pattern",
        "The promotion signal",
        "The simple system",
    ],
    "fitness_strength": [
        "The training lesson",
        "The strength detail",
        "The form cue",
        "The muscle signal",
        "The workout rule",
    ],
    "nutrition_metabolism": [
        "The nutrition lesson",
        "The metabolism detail",
        "The food pattern",
        "The simple swap",
        "The hunger signal",
    ],
    "stoicism_philosophy": [
        "The stoic lesson",
        "The ancient lesson",
        "The mental shift",
        "The discipline rule",
        "The hard truth",
    ],
    "science_space": [
        "The discovery",
        "The science clue",
        "The space mystery",
        "The hidden pattern",
        "The breakthrough",
    ],
    "books_big_ideas": [
        "The big idea",
        "The book lesson",
        "The idea that sticks",
        "The mental model",
        "The key insight",
    ],
    "documentaries_society": [
        "The hidden world",
        "The society pattern",
        "The human story",
        "The system detail",
        "The part unseen",
    ],
    "creator_economy_marketing": [
        "The creator play",
        "The marketing lesson",
        "The attention shift",
        "The audience signal",
        "The hook that works",
    ],
    "geopolitics_power": [
        "The power move",
        "The geopolitical signal",
        "The leverage point",
        "The strategy shift",
        "The pressure point",
    ],
    "ai_tools": [
        "The AI shift",
        "The automation lesson",
        "The workflow detail",
        "The tool that matters",
        "The future signal",
    ],
    "entrepreneurship_business": [
        "The business lesson",
        "The founder move",
        "The startup pattern",
        "The customer signal",
        "The growth lesson",
    ],
    "sales_persuasion": [
        "The sales lesson",
        "The persuasion cue",
        "The buyer signal",
        "The closing detail",
        "The trust shift",
    ],
    "luxury_lifestyle": [
        "The luxury detail",
        "The status signal",
        "The lifestyle lesson",
        "The taste cue",
        "The hidden cost",
    ],
    "travel_adventure": [
        "The travel lesson",
        "The place detail",
        "The adventure moment",
        "The local signal",
        "The trip detail",
    ],
    "parenting_family": [
        "The parenting lesson",
        "The family pattern",
        "The child signal",
        "The simple moment",
        "The home lesson",
    ],
    "education_learning": [
        "The learning lesson",
        "The study detail",
        "The memory cue",
        "The classroom pattern",
        "The simple explanation",
    ],
    "news_breakdown": [
        "The key detail",
        "The context",
        "The timeline",
        "The real update",
        "The part that matters",
    ],
    "pop_culture": [
        "The pop culture detail",
        "The celebrity moment",
        "The fan theory",
        "The key clip",
        "The moment that matters",
    ],
    "gaming_esports": [
        "The gaming lesson",
        "The clutch moment",
        "The strategy detail",
        "The esports read",
        "The play that matters",
    ],
    "animals_nature": [
        "The nature detail",
        "The animal signal",
        "The wild moment",
        "The survival cue",
        "The instinct",
    ],
    "medical_health": [
        "The health lesson",
        "The body signal",
        "The doctor detail",
        "The symptom clue",
        "The recovery point",
    ],
    "law_justice": [
        "The legal detail",
        "The courtroom moment",
        "The justice question",
        "The case detail",
        "The key argument",
    ],
    "investing_markets": [
        "The market signal",
        "The investing lesson",
        "The portfolio detail",
        "The risk point",
        "The money move",
    ],
    "motivation": [
        "The mindset shift",
        "The hard lesson",
        "The discipline rule",
        "The wake up call",
        "The reason to keep going",
    ],
    "food_cooking": [
        "The cooking detail",
        "The flavor trick",
        "The kitchen lesson",
        "The simple swap",
        "The chef move",
    ],
}

# Backwards-compatible single-frame map.
NICHE_FRAME = {key: frames[0] for key, frames in NICHE_FRAMES.items()}
GENERIC_FRAME = GENERIC_FRAMES[0]

NICHE_ALIASES = {
    "money": "personal_finance",
    "finance": "personal_finance",
    "wealth": "capital_mindset",
    "mindset": "self_improvement",
    "self_help": "self_improvement",
    "dating": "relationships_dating",
    "relationships": "relationships_dating",
    "relationship": "relationships_dating",
    "real_estate": "real_estate_wealth",
    "property": "real_estate_wealth",
    "crypto": "crypto_web3",
    "bitcoin": "crypto_web3",
    "web3": "crypto_web3",
    "tech": "future_tech_daily",
    "ai": "ai_tools",
    "artificial_intelligence": "ai_tools",
    "history": "history_mysteries",
    "crime": "true_crime_cases",
    "true_crime": "true_crime_cases",
    "career": "productivity_career",
    "productivity": "productivity_career",
    "fitness": "fitness_strength",
    "gym": "fitness_strength",
    "nutrition": "nutrition_metabolism",
    "health": "medical_health",
    "science": "science_space",
    "space": "science_space",
    "books": "books_big_ideas",
    "marketing": "creator_economy_marketing",
    "creator": "creator_economy_marketing",
    "geopolitics": "geopolitics_power",
    "business": "entrepreneurship_business",
    "startup": "entrepreneurship_business",
    "sales": "sales_persuasion",
    "news": "news_breakdown",
    "gaming": "gaming_esports",
    "law": "law_justice",
    "investing": "investing_markets",
    "markets": "investing_markets",
    "food": "food_cooking",
    "cooking": "food_cooking",
}

# Aggressive frames are only used when the hook or keywords contain support.
FRAME_HINTS = [
    (("mistake", "wrong", "error", "fail", "fails", "failure", "avoid", "regret"), "The mistake"),
    (("trap", "scam", "risk", "danger", "warning", "red flag", "costly", "debt"), "The warning sign"),
    (("because", "reason", "why"), "The reason"),
    (("secret", "hidden", "nobody", "no one", "untold", "overlooked"), "The part people miss"),
    (("myth", "lie", "fake", "false"), "The myth"),
    (("habit", "routine", "discipline", "daily"), "The habit"),
    (("rule", "framework", "system", "formula"), "The simple rule"),
    (("shift", "changed", "change", "turning point", "pivot"), "The turning point"),
    (("proof", "data", "evidence", "study", "research"), "The proof"),
    (("money", "income", "cash", "wealth", "rich", "poor", "debt", "invest"), "The money lesson"),
    (("ai", "tech", "software", "robot", "automation"), "The tech shift"),
]

COMPRESSION_RULES = [
    (r"\bdue to the fact that\b", "because"),
    (r"\bin order to\b", "to"),
    (r"\bthe reason why\b", "why"),
    (r"\bat this point in time\b", "now"),
    (r"\bfor the purpose of\b", "for"),
    (r"\ba little bit\b", "a bit"),
    (r"\bdo not\b", "don't"),
    (r"\bdoes not\b", "doesn't"),
    (r"\bdid not\b", "didn't"),
    (r"\bis not\b", "isn't"),
    (r"\bare not\b", "aren't"),
    (r"\bwas not\b", "wasn't"),
    (r"\bwere not\b", "weren't"),
    (r"\bcannot\b", "can't"),
    (r"\bcan not\b", "can't"),
    (r"\bwill not\b", "won't"),
    (r"\bwould not\b", "wouldn't"),
    (r"\bshould not\b", "shouldn't"),
    (r"\bcould not\b", "couldn't"),
    (r"\bi am\b", "I'm"),
    (r"\byou are\b", "you're"),
    (r"\bwe are\b", "we're"),
    (r"\bthey are\b", "they're"),
    (r"\bit is\b", "it's"),
    (r"\bthat is\b", "that's"),
]

CANONICAL_CASE = {
    "youtube": "YouTube",
    "tiktok": "TikTok",
    "instagram": "Instagram",
    "facebook": "Facebook",
    "google": "Google",
    "openai": "OpenAI",
    "chatgpt": "ChatGPT",
    "ai": "AI",
    "api": "API",
    "ceo": "CEO",
    "cfo": "CFO",
    "cto": "CTO",
    "ipo": "IPO",
    "etf": "ETF",
    "roi": "ROI",
    "kpi": "KPI",
    "vr": "VR",
    "ar": "AR",
    "ev": "EV",
    "dna": "DNA",
    "nasa": "NASA",
    "fbi": "FBI",
    "cia": "CIA",
    "usa": "USA",
    "uk": "UK",
    "u.s.": "U.S.",
    "bitcoin": "Bitcoin",
    "ethereum": "Ethereum",
    "web3": "Web3",
    "nft": "NFT",
    "nfts": "NFTs",
    "iphone": "iPhone",
    "ios": "iOS",
    "ebay": "eBay",
}


_TRANSLATION_TABLE = str.maketrans({
    "\u2018": "'",
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u2026": "...",
    "\u2014": ", ",
    "\u2013": "-",
    "\u2212": "-",
})


def _safe_max_len(max_len):
    try:
        value = int(max_len)
    except (TypeError, ValueError):
        value = 72
    return max(0, value)


def _stable_int(*parts):
    payload = "\x1f".join(str(p) for p in parts).encode("utf-8", "ignore")
    return int(hashlib.sha1(payload).hexdigest()[:12], 16)


def _word_count(text):
    return len(re.findall(r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?", text or ""))


def _phrase_in(text, phrase):
    pattern = r"(?<![A-Za-z0-9_])" + re.escape(phrase) + r"(?![A-Za-z0-9_])"
    return re.search(pattern, text or "", flags=re.I) is not None


def _has_any_phrase(text, phrases):
    return any(_phrase_in(text, phrase) for phrase in phrases)


def _normalize_niche(niche):
    key = str(niche or "").strip().lower()
    key = key.replace("-", "_").replace(" ", "_").replace("/", "_")
    key = re.sub(r"[^a-z0-9_]+", "", key)
    key = re.sub(r"_+", "_", key).strip("_")
    return NICHE_ALIASES.get(key, key)


def _nice_niche_name(niche):
    key = _normalize_niche(niche)
    if not key:
        return "Short"
    return key.replace("_", " ").title()


def _keyword_text(keywords):
    if keywords is None:
        return ""

    if isinstance(keywords, str):
        return keywords.lower()

    pieces = []

    if isinstance(keywords, dict):
        for key in sorted(keywords.keys(), key=lambda x: str(x)):
            pieces.append(str(key))
            value = keywords[key]
            if isinstance(value, (set, frozenset)):
                pieces.extend(str(v) for v in sorted(value, key=lambda x: str(x)) if v is not None)
            elif isinstance(value, (list, tuple)):
                pieces.extend(str(v) for v in value if v is not None)
            elif value is not None and not isinstance(value, (int, float, bool)):
                pieces.append(str(value))
    elif isinstance(keywords, (set, frozenset)):
        pieces.extend(str(k) for k in sorted(keywords, key=lambda x: str(x)) if k is not None)
    else:
        try:
            for item in keywords:
                if item is not None:
                    pieces.append(str(item))
        except TypeError:
            pieces.append(str(keywords))

    return " ".join(pieces).lower()


def _replace_hashtag(match):
    tag = match.group(1)
    compact = tag.replace("_", "").replace("-", "").lower()
    if compact in GENERIC_HASHTAGS:
        return " "
    return tag.replace("_", " ").replace("-", " ")


def _fix_spacing(text):
    text = (text or "").replace("\u2014", ", ").replace("\u2013", "-").replace("\u2212", "-")
    text = text.replace("\u2026", "...")
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"([,:;!?])(?=\S)", r"\1 ", text)
    text = re.sub(r"\.{4,}", "...", text)
    text = re.sub(r"\s*\.\.\.\s*", "... ", text)
    text = re.sub(r"([!?]){2,}", r"\1", text)
    text = re.sub(r"([,;:]){2,}", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


def _fix_case_terms(text):
    for raw, repl in sorted(CANONICAL_CASE.items(), key=lambda item: len(item[0]), reverse=True):
        pattern = r"(?<![A-Za-z0-9_])" + re.escape(raw) + r"(?![A-Za-z0-9_])"
        text = re.sub(pattern, repl, text, flags=re.I)
    return text


def _capitalize_start(text):
    text = re.sub(r"(?<![A-Za-z])i(?![A-Za-z])", "I", text or "")
    match = re.match(r"^([\"'(\[]*)([A-Za-z][A-Za-z0-9']*)", text)
    if not match:
        return text

    prefix, word = match.group(1), match.group(2)

    if word == "I" or word.isupper() or re.match(r"[a-z][A-Z]", word):
        return text

    if word[0].islower():
        start = len(prefix)
        return text[:start] + word[0].upper() + text[start + 1:]

    return text


def _strip_leads(text):
    always = sorted(FILLER_LEADS, key=len, reverse=True)
    soft = sorted(FILLER_LEADS_PUNCT_ONLY, key=len, reverse=True)

    for _ in range(12):
        original = text
        text = re.sub(r"^[,\s.:;!?]+", "", text).strip()
        removed = False

        for lead in always:
            pattern = r"^\s*" + re.escape(lead) + r"(?:\s+|[,.:;!?]+\s*)"
            match = re.match(pattern, text, flags=re.I)
            if match:
                text = text[match.end():].strip()
                removed = True
                break

        if removed:
            continue

        for lead in soft:
            pattern = r"^\s*" + re.escape(lead) + r"\s*[,.:;!?]+\s*"
            match = re.match(pattern, text, flags=re.I)
            if match:
                text = text[match.end():].strip()
                removed = True
                break

        if not removed and text == original:
            break

    return text


def _remove_inline_fillers(text):
    for phrase in ("you know", "i mean"):
        pattern = r"(?i)(?:\s*,\s*|\s+)\b" + re.escape(phrase) + r"\b(?:\s*,\s*|\s+)"
        text = re.sub(pattern, " ", text)
    return _fix_spacing(text)


def _apply_compression(text):
    for pattern, repl in COMPRESSION_RULES:
        text = re.sub(pattern, repl, text, flags=re.I)

    # Only dedupe common filler repeats, not all repeated words.
    text = re.sub(r"(?i)\b(very|really|just|like|so)\s+\1\b", r"\1", text)
    return text


def _clean(text):
    text = html.unescape(str(text or ""))
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(_TRANSLATION_TABLE)

    text = re.sub(r"[\r\n\t]+", " ", text)

    # Transcript artifacts.
    text = re.sub(r"\b\d{1,2}:\d{2}(?::\d{2})?\b", " ", text)
    text = re.sub(
        r"^\s*(?:speaker\s*\d+|host|guest|interviewer|interviewee|narrator|male|female)\s*[-:]\s*",
        " ",
        text,
        flags=re.I,
    )
    text = re.sub(r"\[\s*(?:music|applause|laughs?|laughter|inaudible|silence|crosstalk|noise)\s*\]", " ", text, flags=re.I)
    text = re.sub(r"\(\s*(?:music|applause|laughs?|laughter|inaudible|silence|crosstalk|noise)\s*\)", " ", text, flags=re.I)

    # URLs, hashtags, handles.
    text = re.sub(r"\b(?:https?://|www\.)\S+", " ", text, flags=re.I)
    text = re.sub(r"(?<!\w)#([A-Za-z][\w-]*)", _replace_hashtag, text)
    text = re.sub(r"(?<!\w)@([A-Za-z][\w.-]*)", r"\1", text)

    text = re.sub(r"\s+", " ", text).strip().strip("\"' ")
    text = _strip_leads(text)
    text = _remove_inline_fillers(text)
    text = _apply_compression(text)
    text = _fix_spacing(text)
    text = _fix_case_terms(text)
    text = _capitalize_start(text)
    text = re.sub(r"^[,\s.:;!?]+", "", text).strip()

    return text


def _is_question_like(text):
    stripped = (text or "").strip()
    if stripped.endswith("?"):
        return True

    match = re.match(r"^[\"'(\[]*([A-Za-z]+)\b", stripped)
    if not match:
        return False

    first = match.group(1).lower()
    if first in QUESTION_WORDS:
        return True

    if first in AUX_QUESTION_STARTERS and _word_count(stripped) >= 3:
        return True

    return False


def _has_strong_hook(text):
    return _has_any_phrase(text, STRONG_HOOK_MARKERS)


def _remove_trailing_junk(text):
    words = (text or "").rstrip(" ,.;:").split()

    while words:
        last = re.sub(r"[^A-Za-z']", "", words[-1]).lower()
        if last not in TRAILING_JUNK_WORDS:
            break
        words.pop()

    return " ".join(words).rstrip(" ,.;:")


LOWERABLE_STARTS = {
    "this", "that", "these", "those", "it", "they", "them", "he", "she",
    "we", "you", "your", "my", "our", "their", "people", "most",
    "everyone", "everybody", "nobody", "someone", "something",
    "a", "an", "the",
}


def _lower_lead_clause(clause):
    match = re.match(r"^([\"'(\[]*)([A-Za-z][A-Za-z0-9']*)", clause or "")
    if not match:
        return clause

    prefix, word = match.group(1), match.group(2)

    if word == "I" or word.isupper() or re.match(r"[a-z][A-Z]", word):
        return clause

    if word.lower() in LOWERABLE_STARTS:
        start = len(prefix)
        return clause[:start] + word[0].lower() + clause[start + 1:]

    return clause


def _reason_candidate(text):
    match = re.search(r"\bbecause\b", text, flags=re.I)
    if not match:
        return None

    before = text[:match.start()].strip(" ,.;:")
    before = _remove_trailing_junk(before)

    if _word_count(before) < 2 or _word_count(before) > 12:
        return None

    if re.search(r"\b(?:but|until|unless|however|although)\b", before, flags=re.I):
        return None

    if before.lower().startswith(("i think", "i guess", "maybe", "probably")):
        return None

    if _is_question_like(before):
        return before

    before = _lower_lead_clause(before)
    return _capitalize_start(_fix_spacing("Why " + before.rstrip(".!?")))


def _contrast_candidate(text):
    contrast_rules = [
        ("but", ", but..."),
        ("until", " until..."),
        ("unless", " unless..."),
        ("except", ", except..."),
        ("then", ", then..."),
    ]

    for word, suffix in contrast_rules:
        match = re.search(r"\b" + re.escape(word) + r"\b", text, flags=re.I)
        if not match:
            continue

        left = text[:match.start()].strip(" ,.;:")
        right = text[match.end():].strip(" ,.;:")
        left = _remove_trailing_junk(left)

        if _word_count(left) < 4 or _word_count(left) > 12:
            continue

        if _word_count(right) < 2:
            continue

        return _capitalize_start(_fix_spacing(left + suffix))

    return None


def _pick_frame(text, niche, keywords, clip_index):
    niche_key = _normalize_niche(niche)
    keyword_text = _keyword_text(keywords)
    haystack = (text + " " + keyword_text).lower()

    for markers, frame in FRAME_HINTS:
        if any(_phrase_in(haystack, marker) for marker in markers):
            return frame

    frames = NICHE_FRAMES.get(niche_key) or GENERIC_FRAMES
    index = _stable_int(text.lower(), niche_key, keyword_text, clip_index) % len(frames)
    return frames[index]


def _truncate(text, max_len):
    max_len = _safe_max_len(max_len)
    if max_len <= 0:
        return ""

    text = _fix_spacing(text)

    if len(text) <= max_len:
        return text

    if max_len <= 3:
        return text[:max_len]

    cut = text[:max_len]

    # Prefer a full sentence if one exists reasonably far into the title.
    sentence_end = None
    for match in re.finditer(r"[.!?]\s+", cut):
        end = match.start() + 1
        if end >= max_len * 0.45:
            sentence_end = end

    if sentence_end:
        return cut[:sentence_end].rstrip()

    cut_at = None

    for pattern, ratio in (
        (r"[,;:]\s+", 0.58),
        (r"\s+(?:because|but|when|if|while|after|before|unless|until)\s+", 0.58),
        (r"\s+", 0.55),
    ):
        best = None
        for match in re.finditer(pattern, cut):
            pos = match.start()
            if pos >= max_len * ratio:
                best = pos
        if best is not None:
            cut_at = best
            break

    if cut_at is None:
        cut_at = max_len - 3

    result = cut[:cut_at].rstrip(" ,.;:")
    result = _remove_trailing_junk(result)

    if not result:
        result = cut[:max_len - 3].rstrip(" ,.;:")

    if result and result[-1] in ".!?":
        return result[:max_len]

    if len(result) + 3 > max_len:
        result = result[:max_len - 3].rstrip(" ,.;:")
        result = _remove_trailing_junk(result)

    return (result + "...")[:max_len]


def _finalize_title(text, max_len):
    max_len = _safe_max_len(max_len)
    if max_len <= 0:
        return ""

    text = _fix_spacing(text)
    text = re.sub(r"(?<!\w)#([A-Za-z][\w-]*)", _replace_hashtag, text)

    # Enforce no em dashes or en dashes in final title.
    text = text.replace("\u2014", ", ").replace("\u2013", "-").replace("\u2212", "-")

    text = text.strip().strip("\"'")
    text = re.sub(r"^[,.:;]+", "", text).strip()
    text = re.sub(r"[,;:]+$", "", text).strip()
    text = _fix_case_terms(text)
    text = _capitalize_start(text)

    if len(text) > max_len:
        text = _truncate(text, max_len)

    return text[:max_len]


def _candidate_score(title, original, max_len):
    if not title or len(title) > max_len:
        return -100000

    score = 0
    length = len(title)
    words = _word_count(title)
    lower = title.lower()

    upper_target = min(max_len, 64)

    if 32 <= length <= upper_target:
        score += 6
    elif 22 <= length <= max_len:
        score += 4
    elif 14 <= length <= max_len:
        score += 1
    else:
        score -= 2

    if 4 <= words <= 11:
        score += 3
    elif words > 14:
        score -= 2

    if title.rstrip().endswith("?"):
        score += 2

    if "..." in title:
        score += 3

    if ":" in title:
        score += 2

    if re.search(r"\b\d+(?:[.,]\d+)?%?\b|\$\s?\d", title):
        score += 2

    if re.search(
        r"\b(?:why|but|until|unless|mistake|truth|nobody|never|stop|hidden|cost|wrong|before|after|secret|reason|myth|trap|scam|warning)\b",
        lower,
        flags=re.I,
    ):
        score += 3

    # For plain long statements, prefer a curiosity frame over the untouched base.
    if lower == (original or "").lower():
        score -= 2

    if title.endswith("...") and length < 18:
        score -= 2

    return score


def _best_candidate(candidates, original, max_len):
    seen = set()
    cleaned = []

    for candidate in candidates:
        candidate = _finalize_title(_truncate(candidate, max_len), max_len)
        if not candidate:
            continue

        key = candidate.lower()
        if key in seen:
            continue

        seen.add(key)
        cleaned.append(candidate)

    if not cleaned:
        return _finalize_title(_truncate(original, max_len), max_len)

    best_index = 0
    best_score = None

    for index, title in enumerate(cleaned):
        score = _candidate_score(title, original, max_len)
        key = (score, -index)

        if best_score is None or key > best_score:
            best_score = key
            best_index = index

    return cleaned[best_index]


def optimize_title(hook, niche="", keywords=None, clip_index=0, max_len=72):
    """Return an optimized, hashtag-free title for a clip hook."""
    max_len = _safe_max_len(max_len)
    if max_len <= 0:
        return ""

    text = _clean(hook)

    if not text:
        fallback = f"{_nice_niche_name(niche) or 'Short'} clip #{clip_index}"
        return _finalize_title(_truncate(fallback, max_len), max_len)

    # Non-English / Whisper-hallucinated hooks must never surface as titles.
    # Fall back to a clean niche label instead of shipping gibberish.
    if _looks_non_english(text):
        fallback = f"{_nice_niche_name(niche) or 'Short'} insight #{clip_index}"
        return _finalize_title(_truncate(fallback, max_len), max_len)

    ends_question = text.rstrip().endswith("?")
    ends_bang = text.rstrip().endswith("!")

    # Already punchy: keep it mostly intact.
    if ends_question or ends_bang or _is_question_like(text):
        return _finalize_title(_truncate(text, max_len), max_len)

    # Short or already curiosity-loaded: leave mostly intact.
    if _word_count(text) <= 8 or _has_strong_hook(text):
        return _finalize_title(_truncate(text, max_len), max_len)

    candidates = []

    # Safe curiosity: only created when "because" exists in the hook.
    reason = _reason_candidate(text)
    if reason:
        candidates.append(reason)

    # Safe cliffhanger: only created when a contrast word exists in the hook.
    contrast = _contrast_candidate(text)
    if contrast:
        candidates.append(contrast)

    # Conservative niche frame.
    frame = _pick_frame(text, niche, keywords, clip_index)
    candidates.append(f"{frame}: {text}")

    # Generic fallback frame.
    if frame != GENERIC_FRAME:
        candidates.append(f"{GENERIC_FRAME}: {text}")

    # Last fallback: the cleaned hook.
    candidates.append(text)

    return _best_candidate(candidates, text, max_len)
