#!/usr/bin/env python3
"""
YouTube Creator Finder — InnerTube discovery + headless browser for contacts

Discovery and stats come from YouTube's internal JSON API (no rendering, real
pagination). The headless browser is used only for the channel About page,
because the "View email address" reveal is DOM- and login-gated and cannot be
reached over plain HTTP.

SETUP (one time):
  pip install playwright
  playwright install chromium

FIRST RUN:
  python3 youtube_creator_finder.py --login
  A browser window opens. Log into YouTube, then come back and press Enter.
  The session is saved to ./browser_profile/ and reused after that.

USAGE:
  python3 youtube_creator_finder.py              # headless (normal runs)
  python3 youtube_creator_finder.py --headed     # watch the browser work
  python3 youtube_creator_finder.py --login      # re-authenticate
"""

import argparse
import asyncio
import csv
import json
import os
import random
import re
import sys
from datetime import datetime
from urllib.parse import quote_plus, unquote

from playwright.async_api import async_playwright
from playwright.async_api import TimeoutError as PWTimeout

# ─── Config ───────────────────────────────────────────────────────────────────
# Resolve local state from the repository root so CLI and web-server launches
# behave the same regardless of the current working directory. Never commit the
# browser profile: it contains an authenticated session and is ignored by Git.
ROOT_DIR     = os.path.dirname(os.path.abspath(__file__))
PROFILE_DIR  = os.getenv("CREATORSCOPE_PROFILE_DIR", os.path.join(ROOT_DIR, "browser_profile"))
CACHE_FILE   = os.getenv(
    "CREATORSCOPE_LEGACY_CACHE",
    os.path.join(ROOT_DIR, ".runtime", "legacy_creators.json"),
)
EXPORT_FILE  = os.getenv(
    "CREATORSCOPE_EXPORT_FILE",
    os.path.join(ROOT_DIR, "exports", "creators_export.csv"),
)
BASE_URL     = "https://www.youtube.com"

# Skip channels created on/after this year. DISABLED (None) by default: it was
# inherited from the original script and a test run showed it rejecting every
# vlog channel a search returned (2021-2024), producing an empty CSV. Set to a
# year (e.g. 2021) only if you specifically want to exclude newer creators.
MIN_ACCOUNT_YEAR = None

# Concurrency. Kept modest on purpose: this drives an authenticated Google
# account, and request rate is what gets accounts flagged.
API_CONCURRENCY  = 8   # InnerTube JSON calls
PAGE_CONCURRENCY = 8   # rendered About pages

PAGE_TIMEOUT = 45_000
WAIT_TIMEOUT = 15_000

# Never needed for text extraction; the About page is ~1.5MB largely because of
# images. Blocking these is the cheapest speedup available.
BLOCKED_RESOURCE_TYPES = {"image", "media", "font"}

AUTH_COOKIES = {"SAPISID", "LOGIN_INFO", "__Secure-3PSID", "SID"}

# InnerTube wants the base64 "channels only" filter verbatim in the JSON body —
# NOT percent-encoded. (The URL form is the %3D%3D variant.)
CHANNEL_FILTER = "EgIQAg=="

INNERTUBE_CLIENT = {
    "clientName": "WEB",
    "clientVersion": "2.20240101.00.00",
    "hl": "en",
}

REGIONS = {
    "1":  ("US", "United States"),
    "2":  ("GB", "United Kingdom"),
    "3":  ("CA", "Canada"),
    "4":  ("AU", "Australia"),
    "5":  ("DE", "Germany"),
    "6":  ("FR", "France"),
    "7":  ("JP", "Japan"),
    "8":  ("KR", "South Korea"),
    "9":  ("IN", "India"),
    "10": ("BR", "Brazil"),
    "11": ("MX", "Mexico"),
    "12": ("NG", "Nigeria"),
    "13": ("ZA", "South Africa"),
    "14": ("KE", "Kenya"),
    "15": ("EG", "Egypt"),
    "16": ("ID", "Indonesia"),
    "17": ("PH", "Philippines"),
    "18": ("TH", "Thailand"),
    "19": ("VN", "Vietnam"),
    "20": ("SG", "Singapore"),
}

NICHES = {
    "1": ("vlog",         "Vlogs"),
    "2": ("talking head", "Talking Head"),
    "3": ("lifestyle",    "Lifestyle"),
    "4": ("editorial",    "Editorial/Commentary"),
    "5": ("commentary",   "Commentary"),
    "6": ("storytime",    "Storytime"),
    "7": ("daily vlog",   "Daily Vlogs"),
}

EMAIL_REGEX = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b')

EMAIL_DENYLIST = {
    "example@example.com",
    "support@youtube.com",
    "press@youtube.com",
}

SOCIAL_PATTERNS = {
    "instagram": re.compile(r'instagram\.com/([A-Za-z0-9_.]+)', re.I),
    "twitter":   re.compile(r'(?:twitter\.com|x\.com)/([A-Za-z0-9_]+)', re.I),
    "tiktok":    re.compile(r'tiktok\.com/@([A-Za-z0-9_.]+)', re.I),
    "linkedin":  re.compile(r'linkedin\.com/in/([A-Za-z0-9_-]+)', re.I),
}

SOCIAL_DENYLIST = {"youtube", "share", "intent", "home", "explore", "shorts"}

ABOUT_PATTERNS = {
    "subscribers": re.compile(r'([\d.,]+[KMB]?)\s+subscribers', re.I),
    "videos":      re.compile(r'([\d.,]+[KMB]?)\s+videos', re.I),
    "views":       re.compile(r'([\d.,]+[KMB]?)\s+views', re.I),
}

# Lines that sit near "Joined" but are not a country.
ABOUT_NOISE_PREFIXES = ("sign in", "more info", "share", "description", "subscribe")

# Country is the line directly above "Joined" — except when it isn't. A channel
# with no country set leaves the line above "Joined" holding whatever the About
# panel rendered last (the "View email address" reveal button, a description
# blurb, a shouted socials list). Guarding with prefix/character rules kept
# letting those through, so the candidate is now matched against the actual set
# of country names YouTube emits, and anything else is dropped rather than
# guessed at.
COUNTRIES = {
    "afghanistan": "Afghanistan", "albania": "Albania", "algeria": "Algeria",
    "american samoa": "American Samoa", "andorra": "Andorra",
    "angola": "Angola", "anguilla": "Anguilla",
    "antarctica": "Antarctica",
    "antigua and barbuda": "Antigua and Barbuda",
    "argentina": "Argentina", "armenia": "Armenia", "aruba": "Aruba",
    "australia": "Australia", "austria": "Austria",
    "azerbaijan": "Azerbaijan", "bahamas": "Bahamas", "bahrain": "Bahrain",
    "bangladesh": "Bangladesh", "barbados": "Barbados",
    "belarus": "Belarus", "belgium": "Belgium", "belize": "Belize",
    "benin": "Benin", "bermuda": "Bermuda", "bhutan": "Bhutan",
    "bolivia": "Bolivia",
    "bosnia and herzegovina": "Bosnia and Herzegovina",
    "botswana": "Botswana", "bouvet island": "Bouvet Island",
    "brazil": "Brazil",
    "british indian ocean territory": "British Indian Ocean Territory",
    "british virgin islands": "British Virgin Islands",
    "brunei": "Brunei", "bulgaria": "Bulgaria",
    "burkina faso": "Burkina Faso", "burundi": "Burundi",
    "cambodia": "Cambodia", "cameroon": "Cameroon", "canada": "Canada",
    "cape verde": "Cape Verde", "cayman islands": "Cayman Islands",
    "central african republic": "Central African Republic",
    "chad": "Chad", "chile": "Chile", "china": "China",
    "christmas island": "Christmas Island",
    "cocos (keeling) islands": "Cocos (Keeling) Islands",
    "colombia": "Colombia", "comoros": "Comoros", "congo": "Congo",
    "congo - kinshasa": "Congo - Kinshasa",
    "cook islands": "Cook Islands", "costa rica": "Costa Rica",
    "croatia": "Croatia", "cuba": "Cuba", "curaçao": "Curaçao",
    "cyprus": "Cyprus", "czechia": "Czechia",
    "côte d'ivoire": "Côte d'Ivoire", "denmark": "Denmark",
    "djibouti": "Djibouti", "dominica": "Dominica",
    "dominican republic": "Dominican Republic", "ecuador": "Ecuador",
    "el salvador": "El Salvador",
    "equatorial guinea": "Equatorial Guinea", "eritrea": "Eritrea",
    "estonia": "Estonia", "eswatini": "Eswatini", "ethiopia": "Ethiopia",
    "falkland islands": "Falkland Islands", "faroe islands": "Faroe Islands",
    "fiji": "Fiji", "finland": "Finland", "france": "France",
    "french guiana": "French Guiana",
    "french polynesia": "French Polynesia",
    "french southern territories": "French Southern Territories",
    "gabon": "Gabon", "gambia": "Gambia", "georgia": "Georgia",
    "germany": "Germany", "ghana": "Ghana", "gibraltar": "Gibraltar",
    "greece": "Greece", "greenland": "Greenland", "grenada": "Grenada",
    "guadeloupe": "Guadeloupe", "guam": "Guam", "guatemala": "Guatemala",
    "guernsey": "Guernsey", "guinea": "Guinea",
    "guinea-bissau": "Guinea-Bissau", "guyana": "Guyana", "haiti": "Haiti",
    "honduras": "Honduras", "hong kong": "Hong Kong", "hungary": "Hungary",
    "iceland": "Iceland", "india": "India", "indonesia": "Indonesia",
    "iran": "Iran", "iraq": "Iraq", "ireland": "Ireland",
    "isle of man": "Isle of Man", "israel": "Israel", "italy": "Italy",
    "jamaica": "Jamaica", "japan": "Japan", "jersey": "Jersey",
    "jordan": "Jordan", "kazakhstan": "Kazakhstan", "kenya": "Kenya",
    "kiribati": "Kiribati", "kuwait": "Kuwait", "kyrgyzstan": "Kyrgyzstan",
    "laos": "Laos", "latvia": "Latvia", "lebanon": "Lebanon",
    "lesotho": "Lesotho", "liberia": "Liberia", "libya": "Libya",
    "liechtenstein": "Liechtenstein", "lithuania": "Lithuania",
    "luxembourg": "Luxembourg", "macao": "Macao", "madagascar": "Madagascar",
    "malawi": "Malawi", "malaysia": "Malaysia", "maldives": "Maldives",
    "mali": "Mali", "malta": "Malta", "marshall islands": "Marshall Islands",
    "martinique": "Martinique", "mauritania": "Mauritania",
    "mauritius": "Mauritius", "mayotte": "Mayotte", "mexico": "Mexico",
    "micronesia": "Micronesia", "moldova": "Moldova", "monaco": "Monaco",
    "mongolia": "Mongolia", "montenegro": "Montenegro",
    "montserrat": "Montserrat", "morocco": "Morocco",
    "mozambique": "Mozambique", "myanmar (burma)": "Myanmar (Burma)",
    "namibia": "Namibia", "nauru": "Nauru", "nepal": "Nepal",
    "netherlands": "Netherlands", "new caledonia": "New Caledonia",
    "new zealand": "New Zealand", "nicaragua": "Nicaragua",
    "niger": "Niger", "nigeria": "Nigeria", "niue": "Niue",
    "norfolk island": "Norfolk Island", "north korea": "North Korea",
    "north macedonia": "North Macedonia",
    "northern mariana islands": "Northern Mariana Islands",
    "norway": "Norway", "oman": "Oman", "pakistan": "Pakistan",
    "palau": "Palau", "palestinian territories": "Palestinian Territories",
    "panama": "Panama", "papua new guinea": "Papua New Guinea",
    "paraguay": "Paraguay", "peru": "Peru", "philippines": "Philippines",
    "pitcairn islands": "Pitcairn Islands", "poland": "Poland",
    "portugal": "Portugal", "puerto rico": "Puerto Rico", "qatar": "Qatar",
    "romania": "Romania", "russia": "Russia", "rwanda": "Rwanda",
    "réunion": "Réunion", "samoa": "Samoa", "san marino": "San Marino",
    "saudi arabia": "Saudi Arabia", "senegal": "Senegal", "serbia": "Serbia",
    "seychelles": "Seychelles", "sierra leone": "Sierra Leone",
    "singapore": "Singapore", "sint maarten": "Sint Maarten",
    "slovakia": "Slovakia", "slovenia": "Slovenia",
    "solomon islands": "Solomon Islands", "somalia": "Somalia",
    "south africa": "South Africa", "south georgia": "South Georgia",
    "south korea": "South Korea", "south sudan": "South Sudan",
    "spain": "Spain", "sri lanka": "Sri Lanka",
    "st. barthélemy": "St. Barthélemy", "st. helena": "St. Helena",
    "st. kitts and nevis": "St. Kitts and Nevis", "st. lucia": "St. Lucia",
    "st. martin": "St. Martin",
    "st. pierre and miquelon": "St. Pierre and Miquelon",
    "st. vincent and grenadines": "St. Vincent and Grenadines",
    "sudan": "Sudan", "suriname": "Suriname",
    "svalbard and jan mayen": "Svalbard and Jan Mayen", "sweden": "Sweden",
    "switzerland": "Switzerland", "syria": "Syria",
    "são tomé and príncipe": "São Tomé and Príncipe", "taiwan": "Taiwan",
    "tajikistan": "Tajikistan", "tanzania": "Tanzania",
    "thailand": "Thailand", "timor-leste": "Timor-Leste", "togo": "Togo",
    "tokelau": "Tokelau", "tonga": "Tonga",
    "trinidad and tobago": "Trinidad and Tobago", "tunisia": "Tunisia",
    "turkey": "Turkey", "turkmenistan": "Turkmenistan",
    "turks and caicos islands": "Turks and Caicos Islands",
    "tuvalu": "Tuvalu", "u.s. virgin islands": "U.S. Virgin Islands",
    "uganda": "Uganda", "ukraine": "Ukraine",
    "united arab emirates": "United Arab Emirates",
    "united kingdom": "United Kingdom", "united states": "United States",
    "uruguay": "Uruguay", "uzbekistan": "Uzbekistan", "vanuatu": "Vanuatu",
    "vatican city": "Vatican City", "venezuela": "Venezuela",
    "vietnam": "Vietnam", "wallis and futuna": "Wallis and Futuna",
    "western sahara": "Western Sahara", "yemen": "Yemen",
    "zambia": "Zambia", "zimbabwe": "Zimbabwe", "åland islands": "Åland Islands",
}

ABOUT_RENDERER_SELECTORS = [
    "ytd-about-channel-renderer",
    "ytd-channel-about-renderer",
    "#about-container",
]

REVEAL_BUTTON_SELECTORS = [
    "button:has-text('View email address')",
    "yt-button-shape:has-text('View email address') button",
    "a:has-text('View email address')",
]

DIALOG_SELECTORS = [
    "tp-yt-paper-dialog",
    "ytd-popup-container tp-yt-paper-dialog",
]

CAPTCHA_SELECTORS = [
    "iframe[src*='recaptcha']",
    "yt-captcha",
    "#captcha-form",
]

CONSENT_BUTTON_SELECTORS = [
    "button:has-text('Accept all')",
    "button:has-text('I agree')",
    "button[aria-label*='Accept']",
    "form[action*='consent'] button",
]


# ─── Pure helpers ─────────────────────────────────────────────────────────────

def extract_emails(text):
    """Pull email addresses out of free text, dropping known page-chrome noise."""
    if not text:
        return []
    found = {e for e in EMAIL_REGEX.findall(text) if e.lower() not in EMAIL_DENYLIST}
    return sorted({e.rstrip('.').lower() for e in found})


def extract_socials(text):
    """Pull social handles out of free text, dropping site plumbing."""
    if not text:
        return {}
    socials = {}
    for platform, pattern in SOCIAL_PATTERNS.items():
        hits = {h for h in pattern.findall(text) if h.lower() not in SOCIAL_DENYLIST}
        if hits:
            socials[platform] = sorted(hits)
    return socials


def build_channel_url(ref):
    """Map a channel id or @handle onto its About page URL."""
    ref = ref.strip()
    if ref.startswith("@"):
        return f"{BASE_URL}/{ref}/about"
    return f"{BASE_URL}/channel/{ref}/about"


def parse_joined_year(joined_text, now_year=None):
    """Extract a join year from 'Joined Mar 12, 2014' or 'Joined 3 years ago'."""
    if not joined_text:
        return None
    now_year = now_year or datetime.now().year
    m = re.search(r'(\d+)\s+years?\s+ago', joined_text, re.I)
    if m:
        return now_year - int(m.group(1))
    m = re.search(r'(\d{4})', joined_text)
    return int(m.group(1)) if m else None


def normalise_handle(raw):
    """
    A vanity URL path into a display handle: "L%C3%A2mVlog" -> "@LâmVlog".

    YouTube hands the vanity path back percent-encoded whenever the handle has
    non-ASCII characters, so a Vietnamese channel lands in the CSV as a run of
    escape codes instead of its name.
    """
    if not raw:
        return ""
    handle = unquote(str(raw).strip()).strip("/")
    if not handle:
        return ""
    return handle if handle.startswith("@") else f"@{handle}"


def clean_country(value):
    """Canonical country name, or '' if the value isn't a country at all.

    Applied when parsing and again on export, because caches written before
    this validation existed already hold things like "View email address" in
    their country field, and processed channels are never re-scraped.
    """
    return COUNTRIES.get(str(value or '').strip().lower().rstrip(':'), '')


def parse_about_stats(text):
    """
    Parse the About panel's visible text into stats.

    The panel reads roughly:

        www.youtube.com/@mkbhd
        United States
        Joined Mar 21, 2008
        21.3M subscribers
        1,852 videos
        5,687,525,589 views

    Country is the nearest preceding line that names an actual country. That
    last part matters: the line above "Joined" is only sometimes a country. On
    a signed-in profile the reveal button reads "View email address" (not
    "Sign in to see email address"), and a channel with no country set leaves
    a description tail there instead. Anything that isn't a recognised country
    name is dropped rather than written into the column.
    """
    if not text:
        return {}

    out = {}
    for key, pattern in ABOUT_PATTERNS.items():
        m = pattern.search(text)
        if m:
            out[key] = m.group(1)

    m = re.search(r'Joined\s+([^\n\t]{3,40})', text)
    if m:
        out["joined"] = f"Joined {m.group(1).strip()}"
        out["account_year"] = parse_joined_year(out["joined"])

    m = re.search(r'(?:www\.)?youtube\.com/(@[\w.-]+)', text)
    if m:
        out["handle"] = m.group(1)

    lines = [l.strip() for l in text.splitlines() if l.strip()]
    for i, line in enumerate(lines):
        if not line.lower().startswith("joined"):
            continue
        for cand in reversed(lines[:i]):
            low = cand.lower()
            if "youtube.com" in low or low.startswith(ABOUT_NOISE_PREFIXES):
                continue
            country = clean_country(cand)
            if country:
                out["country"] = country
            break
        break

    return out


def find_key(obj, key, depth=0):
    """Depth-first search for the first value under `key`."""
    if depth > 40:
        return None
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            r = find_key(v, key, depth + 1)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = find_key(v, key, depth + 1)
            if r is not None:
                return r
    return None


def channel_ids_from(data):
    """
    Channel ids present in a search response, in order, deduped.

    Serializes with compact separators on purpose: the default json.dumps
    spacing ('"browseId": "UC..."') does not match the pattern below, which
    silently yields zero results.
    """
    if not isinstance(data, (dict, list)):
        return []
    blob = json.dumps(data, separators=(',', ':'))
    return list(dict.fromkeys(re.findall(r'"browseId":"(UC[\w-]{22})"', blob)))


# ─── Cache ────────────────────────────────────────────────────────────────────

def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE) as f:
            return json.load(f)
    return {"processed_ids": [], "creators": []}


def save_cache(cache):
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, 'w') as f:
        json.dump(cache, f, indent=2)


def is_processed(cache, cid):
    return cid in cache["processed_ids"]


def add_to_cache(cache, creator):
    cache["processed_ids"].append(creator["channel_id"])
    cache["creators"].append(creator)
    save_cache(cache)


# ─── InnerTube (JSON API, no rendering) ───────────────────────────────────────

async def innertube(request, endpoint, payload):
    """POST to YouTube's internal API using the browser session's cookies."""
    try:
        r = await request.post(
            f"{BASE_URL}/youtubei/v1/{endpoint}?prettyPrint=false",
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            timeout=30_000,
        )
        if r.status != 200:
            return None
        return await r.json()
    except Exception as e:
        print(f"  ⚠️  InnerTube {endpoint}: {e}")
        return None


async def search_channels(
    request,
    query,
    region_code,
    limit,
    semaphore,
    exclude_ids=None,
    on_progress=None,
):
    """Search channels deeply enough to return ``limit`` *eligible* IDs.

    Previously, the limit was applied before already-processed channels were
    removed. A search could report 100 channels, leave only 40 new ones to
    review, and stop far short of the requested contact target. Exclusions are
    now skipped while pagination continues, so deeper results replace them.
    """
    ids, token = [], None
    seen_ids: set[str] = set()
    seen_tokens: set[str] = set()
    excluded = set(exclude_ids or ())
    client = dict(INNERTUBE_CLIENT, gl=region_code)

    while len(ids) < limit:
        payload = {"context": {"client": client}}
        if token:
            payload["continuation"] = token
        else:
            payload["query"] = query
            payload["params"] = CHANNEL_FILTER

        async with semaphore:
            await asyncio.sleep(random.uniform(0.25, 0.65))
            data = await innertube(request, "search", payload)
        if not data:
            break

        page_ids = channel_ids_from(data)
        for channel_id in page_ids:
            if channel_id in seen_ids:
                continue
            seen_ids.add(channel_id)
            if channel_id not in excluded:
                ids.append(channel_id)
                if len(ids) >= limit:
                    break

        if on_progress:
            on_progress(ids[:])

        cont = find_key(data, "continuationCommand")
        next_token = cont.get("token") if isinstance(cont, dict) else None
        if not next_token or next_token in seen_tokens:
            break
        seen_tokens.add(next_token)
        token = next_token

    return ids[:limit]


async def channel_metadata(request, channel_id):
    """Channel title/handle/description — channel-level, unlike search shelves."""
    data = await innertube(request, "browse", {
        "context": {"client": INNERTUBE_CLIENT},
        "browseId": channel_id,
    })
    if not data:
        return {}
    meta = find_key(data, "channelMetadataRenderer") or {}
    return {
        "title": (meta.get("title") or "").strip() or None,
        "description": meta.get("description"),
        "handle": normalise_handle(
            (meta.get("vanityChannelUrl") or "").rsplit("/", 1)[-1]) or None,
    }


# ─── Browser ──────────────────────────────────────────────────────────────────

async def block_assets(route):
    if route.request.resource_type in BLOCKED_RESOURCE_TYPES:
        await route.abort()
    else:
        await route.continue_()


async def launch(pw, headless):
    """
    channel="chromium" is deliberate: plain headless=True would silently use
    the chromium_headless_shell binary (old headless mode), which is both more
    detectable and missing features. This selects full Chromium.
    """
    ctx = await pw.chromium.launch_persistent_context(
        user_data_dir=PROFILE_DIR,
        channel="chromium",
        headless=headless,
        locale="en-US",
        viewport={"width": 1440, "height": 900},
        args=["--disable-blink-features=AutomationControlled"],
    )
    await ctx.route("**/*", block_assets)
    return ctx


async def is_logged_in(ctx):
    names = {c["name"] for c in await ctx.cookies(BASE_URL)}
    return bool(names & AUTH_COOKIES)


async def dismiss_consent(page):
    """Clear Google's cookie interstitial, which intercepts fresh profiles."""
    on_consent = "consent." in page.url
    if not on_consent:
        try:
            on_consent = "before you continue" in (await page.title() or "").lower()
        except Exception:
            on_consent = False
    if not on_consent:
        return False

    for sel in CONSENT_BUTTON_SELECTORS:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible():
                await loc.click(timeout=5000)
                await page.wait_for_timeout(2500)
                return True
        except Exception:
            continue
    return False


async def do_login(pw):
    print("\n🔐 Opening a browser window — log into YouTube, then come back here.")
    ctx = await launch(pw, headless=False)
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    await dismiss_consent(page)
    await page.wait_for_timeout(1500)

    await asyncio.get_event_loop().run_in_executor(
        None, input, "\n   Press Enter once you're logged in... ")

    if await is_logged_in(ctx):
        print(f"✅ Session saved to ./{PROFILE_DIR}/")
    else:
        print("⚠️  No authenticated cookies found — login may not have completed.")
    await ctx.close()


async def ensure_session(pw, args):
    if args.login:
        await do_login(pw)

    ctx = await launch(pw, headless=not args.headed)
    if not await is_logged_in(ctx):
        await ctx.close()
        print("❌ Not logged in. Run:  python3 youtube_creator_finder.py --login")
        raise SystemExit(1)
    return ctx


async def first_text(page, selectors):
    for sel in selectors:
        try:
            el = await page.query_selector(sel)
            if el:
                txt = (await el.inner_text() or '').strip()
                if not txt:
                    txt = (await el.text_content() or '').strip()
                if txt:
                    return txt
        except Exception:
            continue
    return ''


async def reveal_business_email(page):
    """
    Click YouTube's 'View email address' reveal.

    Returns (emails, status):
      'revealed'          clicked, found an address
      'captcha'           a challenge appeared that we won't solve
      'not_signed_in'     page says 'Sign in to see email address'
      'no_reveal_button'  channel publishes no gated business email
      'no_email_shown'    clicked but nothing usable appeared
    """
    try:
        body = await page.inner_text("body")
        if "sign in to see email address" in body.lower():
            return [], "not_signed_in"
    except Exception:
        pass

    clicked = False
    for sel in REVEAL_BUTTON_SELECTORS:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible():
                await loc.click(timeout=5000)
                clicked = True
                break
        except Exception:
            continue

    if not clicked:
        return [], "no_reveal_button"

    await page.wait_for_timeout(1400)

    for sel in CAPTCHA_SELECTORS:
        try:
            if await page.query_selector(sel):
                return [], "captcha"
        except Exception:
            continue

    for sel in DIALOG_SELECTORS + ABOUT_RENDERER_SELECTORS:
        try:
            el = await page.query_selector(sel)
            if el:
                emails = extract_emails(await el.inner_text())
                if emails:
                    return emails, "revealed"
        except Exception:
            continue

    return [], "no_email_shown"


async def scrape_channel(ctx, ref, semaphore):
    """
    Load a channel's About page once and pull everything from it.

    The About panel carries subscribers, video count, total views, join date,
    country and the contact links — more than the JSON API exposes for a
    channel, and it's the only place the email reveal can be clicked.
    """
    async with semaphore:
        page = await ctx.new_page()
        try:
            url = build_channel_url(ref)
            await asyncio.sleep(random.uniform(0.3, 0.8))
            try:
                await page.goto(url, wait_until="domcontentloaded",
                                timeout=PAGE_TIMEOUT)
            except PWTimeout:
                pass  # a slow, partially-rendered page is still worth parsing
            except Exception as e:
                print(f"     ⚠️  {e}")
                return None

            # Only re-request if consent actually intercepted us — doing this
            # unconditionally doubles every page load.
            if await dismiss_consent(page):
                try:
                    await page.goto(url, wait_until="domcontentloaded",
                                    timeout=PAGE_TIMEOUT)
                except Exception:
                    pass
            try:
                await page.wait_for_selector(
                    ", ".join(ABOUT_RENDERER_SELECTORS), timeout=WAIT_TIMEOUT)
            except PWTimeout:
                pass
            await page.wait_for_timeout(850)

            meta = await page.evaluate("""() => {
                const m = window.ytInitialData?.metadata?.channelMetadataRenderer;
                if (!m) return null;
                return {id: m.externalId || null, title: m.title || null,
                        description: m.description || null,
                        vanity: m.vanityChannelUrl || null};
            }""") or {}

            about_text = await first_text(page, ABOUT_RENDERER_SELECTORS)

            if not meta.get("id") and not about_text:
                return None  # consent wall / error page / unrendered shell

            stats = parse_about_stats(about_text)

            links = []
            for sel in ("#links-container", "ytd-about-channel-renderer"):
                try:
                    el = await page.query_selector(sel)
                    if el:
                        links = await el.eval_on_selector_all(
                            "a[href]", "els => els.map(e => e.href)")
                        if links:
                            break
                except Exception:
                    continue
            links_text = ' '.join(links)

            revealed, reveal_status = await reveal_business_email(page)

            channel_id = meta.get("id")
            if not channel_id:
                canonical = await page.evaluate(
                    "() => document.querySelector('link[rel=canonical]')?.href || ''")
                m = re.search(r'/channel/(UC[\w-]{22})', canonical or '')
                channel_id = m.group(1) if m else ref.lstrip('@')

            handle = stats.get("handle") or normalise_handle(
                (meta.get("vanity") or "").rsplit("/", 1)[-1])

            return {
                "channel_id":      channel_id,
                "title":           (meta.get("title") or "Unknown").strip(),
                "handle":          handle,
                "description":     (meta.get("description") or "").strip(),
                "stats":           stats,
                "revealed_emails": revealed,
                "reveal_status":   reveal_status,
                "page_text":       f"{meta.get('description') or ''}\n{links_text}",
            }
        except Exception as e:
            print(f"     ⚠️  {e}")
            return None
        finally:
            await page.close()


# ─── Menu ─────────────────────────────────────────────────────────────────────

def menu(options, prompt, multi=False):
    print(f"\n{prompt}")
    for k, v in options.items():
        print(f"  {k}. {v[1] if isinstance(v, tuple) else v}")
    while True:
        raw = input("\nEnter choice(s): ").strip()
        if not raw:
            continue
        keys  = [k.strip() for k in raw.split(',')]
        valid = [k for k in keys if k in options]
        if valid:
            return [options[k] for k in valid] if multi else options[valid[0]]
        print("Invalid. Try again.")


def get_max():
    while True:
        try:
            v = input("\nMax creators to find (default 50): ").strip()
            return int(v) if v else 50
        except ValueError:
            print("Enter a number.")


# ─── Export ───────────────────────────────────────────────────────────────────

def flat(text, limit=None):
    """
    Collapse a free-text field onto one line.

    CSV quoting handles embedded newlines correctly, but plenty of viewers —
    terminal pagers, `cut`, `column`, naive splitters — do not, and a
    description spanning a dozen lines makes every row below it look like it
    belongs to the wrong channel. Flattening keeps the file readable everywhere
    and costs nothing that matters for outreach.
    """
    text = re.sub(r'\s*\n\s*', ' | ', str(text or ''))
    text = re.sub(r'[ \t]{2,}', ' ', text).strip(' |')
    if limit and len(text) > limit:
        cut = text[:limit].rsplit(' ', 1)[0].rstrip(' |')
        text = f"{cut or text[:limit]}…"
    return text


def export_csv(cache):
    os.makedirs(os.path.dirname(EXPORT_FILE), exist_ok=True)
    fields = [
        'channel_id', 'handle', 'title', 'account_year', 'joined', 'country',
        'subscriber_count', 'video_count', 'total_views',
        'business_email', 'emails', 'contact_source', 'contact_status',
        'instagram', 'twitter', 'tiktok', 'linkedin',
        'region_searched', 'niches_matched', 'found_at', 'description',
    ]
    with open(EXPORT_FILE, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for c in cache['creators']:
            if c.get('skipped'):
                continue
            # Records written before the stats dict existed hold these flat.
            # Fall back to them so re-exporting an older cache doesn't blank
            # out columns that were already captured.
            s = c.get('stats') or {}
            w.writerow({
                'channel_id':       c.get('channel_id', ''),
                'handle':           normalise_handle(c.get('handle')),
                'title':            flat(c.get('title'), 200),
                'account_year':     s.get('account_year', c.get('account_year', '')),
                'joined':           s.get('joined', ''),
                'country':          clean_country(s.get('country', c.get('country'))),
                'subscriber_count': s.get('subscribers', c.get('subscriber_count', '')),
                'video_count':      s.get('videos', ''),
                'total_views':      s.get('views', ''),
                'business_email':   '; '.join(c.get('business_email', [])),
                'emails':           '; '.join(c.get('emails', [])),
                'contact_source':   c.get('contact_source', ''),
                'contact_status':   c.get('contact_status', ''),
                'instagram':        '; '.join(c.get('socials', {}).get('instagram', [])),
                'twitter':          '; '.join(c.get('socials', {}).get('twitter', [])),
                'tiktok':           '; '.join(c.get('socials', {}).get('tiktok', [])),
                'linkedin':         '; '.join(c.get('socials', {}).get('linkedin', [])),
                'region_searched':  c.get('region_searched', ''),
                'niches_matched':   '; '.join(c.get('niches_matched', [])),
                'found_at':         c.get('found_at', ''),
                'description':      flat(c.get('description'), 500),
            })


# ─── Pipeline ─────────────────────────────────────────────────────────────────

async def pipeline(args, region_code, region_name, niches, max_results):
    async with async_playwright() as pw:
        ctx = await ensure_session(pw, args)
        try:
            cache = load_cache()
            api_sem  = asyncio.Semaphore(API_CONCURRENCY)
            page_sem = asyncio.Semaphore(PAGE_CONCURRENCY)

            print(f"\n🔍 Searching {region_name}...")
            all_ids = []
            for query, name in niches:
                ids = await search_channels(ctx.request, query, region_code,
                                            max_results, api_sem)
                print(f"  📌 {name}: {len(ids)} channels")
                all_ids.extend(i for i in ids if i not in all_ids)

            new_ids = [c for c in all_ids if not is_processed(cache, c)]
            print(f"\n📊 {len(new_ids)} new channels to check")

            found = 0
            idx = 0
            while idx < len(new_ids) and found < max_results:
                # Load a batch concurrently, then process it serially so cache
                # writes and counting stay ordered.
                batch = new_ids[idx: idx + PAGE_CONCURRENCY]
                idx += len(batch)
                results = await asyncio.gather(
                    *(scrape_channel(ctx, ref, page_sem) for ref in batch))

                for ref, details in zip(batch, results):
                    if found >= max_results:
                        break

                    print(f"\n  🔎 {ref}...")
                    if not details:
                        print("     ❌ Failed to load")
                        continue

                    real_id = details["channel_id"]
                    if is_processed(cache, real_id):
                        print("     ⏭️  Already processed")
                        continue

                    stats = details["stats"]
                    year = stats.get("account_year")
                    if MIN_ACCOUNT_YEAR and year and year >= MIN_ACCOUNT_YEAR:
                        print(f"     ⏭️  Too new ({year})")
                        add_to_cache(cache, {"channel_id": real_id, "skipped": True,
                                             "reason": "too_new"})
                        continue

                    business_emails = details["revealed_emails"]
                    link_emails     = extract_emails(details["page_text"])
                    socials         = extract_socials(details["page_text"])

                    all_emails = sorted(set(business_emails) | set(link_emails))
                    if business_emails:
                        source = "revealed"
                    elif link_emails:
                        source = "description"
                    else:
                        source = "none"

                    if not all_emails and not socials:
                        print(f"     ⏭️  No contact info ({details['reveal_status']})")
                        add_to_cache(cache, {"channel_id": real_id, "skipped": True,
                                             "reason": "no_contact_info"})
                        continue

                    creator = {
                        "channel_id":       real_id,
                        "handle":           details["handle"],
                        "title":            details["title"],
                        "description":      details["description"],
                        "stats":            stats,
                        "business_email":   business_emails,
                        "emails":           all_emails,
                        "contact_source":   source,
                        "contact_status":   details["reveal_status"],
                        "socials":          socials,
                        "region_searched":  region_name,
                        "niches_matched":   [n[1] for n in niches],
                        "found_at":         datetime.now().isoformat(),
                    }
                    add_to_cache(cache, creator)
                    found += 1
                    print(f"  ✅ {creator['title']} — {len(all_emails)} email(s), "
                          f"{len(socials)} social(s) [{source}]")
        finally:
            await ctx.close()

    export_csv(load_cache())
    print(f"\n✅ Done!")
    print(f"📁 {CACHE_FILE}")
    print(f"📄 {EXPORT_FILE}")


def main():
    parser = argparse.ArgumentParser(description="YouTube creator contact finder.")
    parser.add_argument("--login", action="store_true",
                        help="open a headed browser to log in, then save the session")
    parser.add_argument("--headed", action="store_true",
                        help="run with a visible browser window")
    args = parser.parse_args()

    print("=" * 58)
    print("  YouTube Creator Finder — InnerTube + headless browser")
    print("=" * 58)

    region_code, region_name = menu(REGIONS, "Select region:")
    niches = menu(NICHES, "Select niche(s) (comma-separated):", multi=True)
    max_results = get_max()

    try:
        asyncio.run(pipeline(args, region_code, region_name, niches, max_results))
    except KeyboardInterrupt:
        print("\n\n👋 Interrupted. Progress saved.")


if __name__ == '__main__':
    main()
