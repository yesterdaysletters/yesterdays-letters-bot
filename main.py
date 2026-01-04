import os
import random
import base64
import json
import requests
from io import BytesIO
from datetime import datetime, date
import pytz

from openai import OpenAI
from PIL import Image, ImageDraw, ImageFont, ImageStat

# =========================================================
# ENV / CONFIG
# =========================================================
OPENAI_KEY = os.environ.get("OPENAI_API_KEY")
FB_TOKEN = os.environ.get("FB_PAGE_ACCESS_TOKEN")
FB_PAGE_ID = os.environ.get("FB_PAGE_ID")
TIMEZONE = os.getenv("TIMEZONE", "Asia/Manila")

if not OPENAI_KEY:
    raise Exception("OPENAI_API_KEY missing")
if not FB_TOKEN or not FB_PAGE_ID:
    raise Exception("Facebook secrets missing")

client = OpenAI(api_key=OPENAI_KEY)

# =========================================================
# COST CONTROL
# =========================================================
POST_WINDOWS = [(19, 21)]  # 7–9 PM ONLY
LAST_POST_FILE = "last_post.txt"
HOLIDAY_HISTORY_FILE = "holiday_history.json"

def is_good_posting_time():
    tz = pytz.timezone(TIMEZONE)
    hour = datetime.now(tz).hour
    return any(start <= hour < end for start, end in POST_WINDOWS)

def already_posted_today():
    today = datetime.now(pytz.timezone(TIMEZONE)).strftime("%Y-%m-%d")
    if os.path.exists(LAST_POST_FILE):
        with open(LAST_POST_FILE) as f:
            return f.read().strip() == today
    return False

def mark_posted_today():
    today = datetime.now(pytz.timezone(TIMEZONE)).strftime("%Y-%m-%d")
    with open(LAST_POST_FILE, "w") as f:
        f.write(today)

# =========================================================
# CURATED HUMAN THOUGHT BANK (NO LLM)
# =========================================================
THOUGHT_BANK = {
    "rain": [
        "Some nights, faith is the only shelter.",
        "I whispered prayers I didn’t know how to say out loud.",
        "God hears you, even in the rain."
    ],
    "forest": [
        "Growth is quiet when no one is watching.",
        "Not everything that’s slow is lost.",
        "I stayed long enough to hear myself think."
    ],
    "road": [
        "I didn’t know where I was going, only that I had to keep walking.",
        "The road teaches patience.",
        "Faith sometimes looks like the next step."
    ],
    "water": [
        "Still waters teach louder lessons.",
        "Some answers arrive gently.",
        "I let go of what I could no longer carry."
    ],
    "night": [
        "God has a plan. Trust, wait, and believe.",
        "Even here, I was not forgotten.",
        "The stars stayed with me."
    ]
}

# =========================================================
# SCENE → PROMPT
# =========================================================
SCENE_PROMPTS = {
    "rain": "night rain, umbrella, wet pavement, soft streetlights",
    "forest": "quiet forest clearing, moonlight through trees",
    "road": "empty road at dusk, long shadows, distant horizon",
    "water": "calm lake at night, stars reflected on water",
    "night": "open night sky, gentle starlight, peaceful stillness"
}

STATIC_STYLE = (
    "Studio Ghibli inspired illustration with realistic cinematic lighting. "
    "Soft bloom, natural shadows, gentle atmospheric depth. "
    "Painterly textures, restrained line work, nostalgic mood."
)

# =========================================================
# MONTHLY VISUAL THEMES (NO EXTRA COST)
# =========================================================
MONTHLY_THEMES = {
    "01": "Cool blue tones, quiet beginnings, minimal contrast.",
    "02": "Warm highlights, soft shadows, longing and memory.",
    "03": "Balanced neutral light, sense of becoming.",
    "04": "Bright diffused light, hopeful softness.",
    "05": "Clear light, grounded stillness.",
    "06": "Golden hour warmth, nostalgic glow.",
    "07": "Cool night tones, silence and depth.",
    "08": "Muted warmth, waiting and pause.",
    "09": "Soft desaturation, letting go.",
    "10": "Higher contrast, cinematic depth.",
    "11": "Warm interior glow, gratitude.",
    "12": "Cold nights with small warm lights, quiet hope."
}

def get_monthly_theme():
    month = datetime.now(pytz.timezone(TIMEZONE)).strftime("%m")
    return MONTHLY_THEMES.get(month, "")

def choose_scene_and_text():
    scene = random.choice(list(THOUGHT_BANK.keys()))
    text = random.choice(THOUGHT_BANK[scene])
    return scene, text

# =========================================================
# HOLIDAY POSTS — EXACT DATE ONLY
# =========================================================
HOLIDAY_POSTS = {
    (1, 1):  {"name": "new_year", "text": "This year, I’m learning to walk slower and trust God more.", "scene": "quiet lakeside at dawn"},
    (2, 14): {"name": "valentines", "text": "Love is choosing patience when it would be easier to leave.", "scene": "evening street lights, two figures walking"},
    (3, 8):  {"name": "womens_day", "text": "Strong women don’t always speak loudly. Sometimes they endure quietly.", "scene": "woman by window, morning light"},
    (4, 1):  {"name": "april_fools", "text": "Not everything that looks like failure is the end of the story.", "scene": "winding road, light through clouds"},
    (5, 1):  {"name": "labor_may", "text": "The work you do in silence still matters.", "scene": "worker resting at sunset"},
    (6, 1):  {"name": "pride", "text": "You are allowed to exist without explaining yourself.", "scene": "person standing in open field at sunrise"},
    (7, 4):  {"name": "independence", "text": "Freedom begins when fear no longer decides for you.", "scene": "open road under wide sky"},
    (8, 4):  {"name": "friendship", "text": "Some friendships are answers to prayers we never said out loud.", "scene": "two silhouettes at golden hour"},
    (9, 1):  {"name": "labor_sep", "text": "Rest is not quitting. It’s preparation.", "scene": "empty park bench, late afternoon"},
    (10, 31):{"name": "halloween", "text": "Not everything hidden is dangerous. Some things are healing.", "scene": "foggy forest path, lantern glow"},
    (11, 28):{"name": "thanksgiving", "text": "Gratitude doesn’t erase pain, but it softens the weight.", "scene": "table by window, autumn light"},
    (12, 25):{"name": "christmas", "text": "Hope often arrives quietly, not loudly.", "scene": "snowy street at night, warm windows"},
}

def load_holiday_history():
    if not os.path.exists(HOLIDAY_HISTORY_FILE):
        return {}
    with open(HOLIDAY_HISTORY_FILE) as f:
        return json.load(f)

def save_holiday_history(history):
    with open(HOLIDAY_HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)

def get_today_holiday():
    today = date.today()
    key = (today.month, today.day)
    if key not in HOLIDAY_POSTS:
        return None

    history = load_holiday_history()
    year = str(today.year)
    used = history.get(year, [])

    holiday = HOLIDAY_POSTS[key]
    if holiday["name"] in used:
        return None

    return holiday

def mark_holiday_u
