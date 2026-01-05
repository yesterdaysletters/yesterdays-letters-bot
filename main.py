import os
import random
import base64
import json
import requests
import csv
from io import BytesIO
from datetime import datetime, date
import pytz

from openai import OpenAI
from PIL import Image, ImageDraw, ImageFont, ImageStat, ImageFilter

# =========================================================
# ENV / CONFIG
# =========================================================
OPENAI_KEY = os.environ.get("OPENAI_API_KEY")
FB_TOKEN = os.environ.get("FB_PAGE_ACCESS_TOKEN")
FB_PAGE_ID = os.environ.get("FB_PAGE_ID")
TIMEZONE = os.getenv("TIMEZONE", "Asia/Manila")
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"

if not OPENAI_KEY and not DRY_RUN:
    raise Exception("OPENAI_API_KEY missing")
if (not FB_TOKEN or not FB_PAGE_ID) and not DRY_RUN:
    raise Exception("Facebook secrets missing")

if not DRY_RUN:
    client = OpenAI(api_key=OPENAI_KEY)
else:
    client = None

# =========================================================
# COST CONTROL
# =========================================================
POST_WINDOWS = [(19, 01)]  # 7–10 PM (expanded for testing)
LAST_POST_FILE = "last_post.txt"
HOLIDAY_HISTORY_FILE = "holiday_history.json"

# STATE FILES
MONTHLY_USAGE_FILE = "monthly_usage.json"
THOUGHT_HISTORY_FILE = "thought_history.json"
ENGAGEMENT_LOG_FILE = "engagement_log.csv"
ERROR_LOG_FILE = "error_log.txt"
KILL_SWITCH_FILE = "posting_disabled.flag"

MAX_MONTHLY_IMAGES = 30
THOUGHT_COOLDOWN_DAYS = 35  # Full month + buffer to prevent recycling

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
# FEATURE LOGIC
# =========================================================
def check_kill_switch():
    if os.path.exists(KILL_SWITCH_FILE):
        return True
    return False

def validate_fonts():
    """Check that all required fonts exist before making API calls."""
    required_fonts = [
        "fonts/LibreBaskerville-Regular.ttf",
    ]
    missing = [f for f in required_fonts if not os.path.exists(f)]
    if missing:
        raise Exception(f"Missing font files: {missing}")

def enable_kill_switch():
    with open(KILL_SWITCH_FILE, "w") as f:
        f.write("DISABLED DUE TO FB API ERROR")

def load_json_file(filepath):
    if not os.path.exists(filepath):
        return {}
    with open(filepath, "r") as f:
        return json.load(f)

def save_json_file(filepath, data):
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2)

def check_monthly_cap():
    data = load_json_file(MONTHLY_USAGE_FILE)
    month = datetime.now(pytz.timezone(TIMEZONE)).strftime("%Y-%m")
    count = data.get(month, 0)
    return count >= MAX_MONTHLY_IMAGES

def increment_monthly_cap():
    data = load_json_file(MONTHLY_USAGE_FILE)
    month = datetime.now(pytz.timezone(TIMEZONE)).strftime("%Y-%m")
    data[month] = data.get(month, 0) + 1
    save_json_file(MONTHLY_USAGE_FILE, data)

def get_thought_cooldown_history():
    return load_json_file(THOUGHT_HISTORY_FILE)

def update_thought_history(thought_text):
    history = get_thought_cooldown_history()
    today = datetime.now(pytz.timezone(TIMEZONE)).strftime("%Y-%m-%d")
    history[thought_text] = today
    save_json_file(THOUGHT_HISTORY_FILE, history)

def log_engagement(scene, thought, status="POSTED"):
    exists = os.path.exists(ENGAGEMENT_LOG_FILE)
    with open(ENGAGEMENT_LOG_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not exists:
            writer.writerow(["Date", "Time", "Scene", "Thought", "Status"])
        
        now = datetime.now(pytz.timezone(TIMEZONE))
        writer.writerow([
            now.strftime("%Y-%m-%d"),
            now.strftime("%H:%M:%S"),
            scene,
            thought,
            status
        ])

def log_error(e):
    now = datetime.now(pytz.timezone(TIMEZONE)).strftime("%Y-%m-%d %H:%M:%S")
    with open(ERROR_LOG_FILE, "a") as f:
        f.write(f"[{now}] ERROR: {e}\n")
        import traceback
        traceback.print_exc(file=f)

def check_token_health():
    if DRY_RUN:
        return True
    
    url = f"https://graph.facebook.com/me?access_token={FB_TOKEN}"
    try:
        r = requests.get(url)
        if r.status_code != 200:
            log_error(f"Token Health Check Failed: {r.text}")
            enable_kill_switch()
            return False
        return True
    except Exception as e:
        log_error(f"Token Health Check Exception: {e}")
        enable_kill_switch()
        return False


# =========================================================
# CURATED HUMAN THOUGHT BANK - 38 UNIQUE THOUGHTS
# =========================================================
THOUGHT_BANK = {
    # GROWTH & PATIENCE (7)
    "growth": [
        "Growth is quiet when no one is watching.",
        "Not everything that's slow is lost.",
        "I stayed long enough to hear myself think.",
        "Becoming who you're meant to be takes time.",
        "Some seasons are just for learning, not harvesting.",
        "You are not behind. You are exactly where you need to be.",
        "The version of you that's coming will be worth the wait.",
    ],
    # FAITH & TRUST (7)
    "faith": [
        "Some nights, faith is the only shelter.",
        "I whispered prayers I didn't know how to say out loud.",
        "God hears you, even in the rain.",
        "God has a plan. Trust, wait, and believe.",
        "Even here, I was not forgotten.",
        "Faith sometimes looks like the next step.",
        "When you can't see the path, trust the One who does.",
    ],
    # LOVE & CONNECTION (6)
    "love": [
        "Some friendships are answers to prayers we never said out loud.",
        "Love is choosing patience when it would be easier to leave.",
        "The people who stay are the ones who matter.",
        "Not every connection is meant to last, but some are meant to teach.",
        "You are someone's reason to believe in kindness.",
        "Home isn't always a place. Sometimes it's a person.",
    ],
    # HEALING & LETTING GO (6)
    "healing": [
        "I let go of what I could no longer carry.",
        "Healing doesn't mean forgetting. It means it no longer controls you.",
        "Some goodbyes are blessings in disguise.",
        "You don't have to carry yesterday into tomorrow.",
        "Rest is not quitting. It's preparation.",
        "It's okay to outgrow who you used to be.",
    ],
    # HOPE & NEW BEGINNINGS (7)
    "hope": [
        "The stars stayed with me.",
        "Still waters teach louder lessons.",
        "Some answers arrive gently.",
        "Hope often arrives quietly, not loudly.",
        "Every ending is just a new beginning wearing a disguise.",
        "The light you're looking for might already be inside you.",
        "Tomorrow is unwritten. That's the beauty of it.",
    ],
    # PEACE & SOLITUDE (5)
    "peace": [
        "I didn't know where I was going, only that I had to keep walking.",
        "The road teaches patience.",
        "Silence isn't empty. It's full of answers.",
        "Peace isn't the absence of storms. It's finding calm within them.",
        "Sometimes doing nothing is the bravest thing you can do.",
    ],
}

# =========================================================
# SCENE → DETAILED PROMPTS (12 HIGH-QUALITY SCENES)
# =========================================================
SCENE_PROMPTS = {
    # COZY INTERIOR SCENES
    "cozy_kitchen": (
        "Cozy kitchen with open French window overlooking a sparkling lake at golden hour, "
        "morning sunlight streaming in casting warm dappled shadows on terracotta tiles, "
        "potted herbs on windowsill, vintage kettle, lived-in details, birds flying in bright blue sky"
    ),
    "seaside_cafe": (
        "Black cat sitting in rustic seaside café doorway gazing at turquoise ocean, "
        "polished wooden floors reflecting warm afternoon sunlight, glass jars on shelves, "
        "stone patio with potted flowers, gentle waves, fluffy clouds on horizon"
    ),
    "window_rain": (
        "Person reading book by large window during gentle rainstorm, cozy interior lighting, "
        "warm lamp glow, rain streaking down glass, city lights blurred outside, "
        "cup of tea steaming, comfortable blanket, peaceful solitude"
    ),
    
    # NATURE & OUTDOOR SCENES
    "countryside_hill": (
        "Boy with backpack sitting under massive ancient oak tree on grassy hillside, "
        "overlooking peaceful pastoral village with red rooftops below, golden hour sunlight, "
        "wildflowers swaying, distant mountains, fluffy cumulus clouds, nostalgic summer afternoon"
    ),
    "lake_boat": (
        "Young couple in small wooden rowboat drifting under overhanging fruit tree branches, "
        "dappled sunlight filtering through bright green leaves, crystal clear water with perfect reflections, "
        "one person reading book, peaceful summer day, orange fruit hanging from branches"
    ),
    "forest_stream": (
        "Person carefully crossing moss-covered stepping stones across gentle forest stream, "
        "golden light rays piercing through dense tree canopy, shallow water reflecting trees, "
        "ferns and wildflowers on banks, misty atmosphere, magical woodland feeling"
    ),
    "flower_field": (
        "Person walking alone through vast wildflower meadow at sunset, "
        "purple and yellow flowers stretching to distant blue mountains, "
        "warm golden light, hair blowing gently in breeze, sense of freedom and peace"
    ),
    
    # ADVENTURE & TRAVEL SCENES
    "beach_cottage": (
        "Vintage turquoise VW van parked by weathered beach cottage, "
        "crystal turquoise waves lapping sandy shore, surfboards leaning against cottage, "
        "palm tree shadows on sand, bright sunflowers blooming, tropical paradise afternoon"
    ),
    "starlit_camp": (
        "Two friends sitting around warm campfire next to vintage camper van, "
        "spectacular Milky Way stretching across dark blue night sky, "
        "distant mountains silhouetted, warm firelight on faces, peaceful stargazing, fireflies"
    ),
    "rooftop_sunset": (
        "Person sitting alone on city rooftop watching dramatic sunset, "
        "warm orange and pink clouds filling sky, city skyline silhouettes below, "
        "potted plants around, string lights not yet lit, contemplative moment"
    ),
    
    # NIGHT & CONTEMPLATIVE SCENES
    "night_balcony": (
        "Person leaning on apartment balcony railing overlooking city lights at night, "
        "stars visible above light pollution, warm interior light spilling out, "
        "plants in terracotta pots, distant traffic, quiet reflection moment"
    ),
    "rainy_street": (
        "Person with clear umbrella walking on rainy evening city street, "
        "neon shop signs reflecting in wet pavement puddles, warm yellow streetlights, "
        "other pedestrians with umbrellas, cozy restaurant windows glowing, cinematic atmosphere"
    ),
}

STATIC_STYLE = (
    "Studio Ghibli anime illustration with hyper-realistic lighting and shadows. "
    "Warm dappled sunlight, natural shadow casting, detailed reflections on water and surfaces. "
    "Vibrant saturated colors, dramatic volumetric clouds, soft bloom effects. "
    "Rich environmental textures, lush vegetation, cozy lived-in atmosphere. "
    "Cinematic composition with depth, atmospheric perspective, and nostalgic mood."
)

# =========================================================
# MONTHLY VISUAL THEMES
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

# Seasonal Map: Month -> List of preferred thought categories
SEASONAL_MAP = {
    "12": ["hope", "faith"],
    "01": ["hope", "growth"],
    "02": ["love"],
    "10": ["healing", "peace"],
}

def choose_scene_and_text():
    # 1. Load history
    history = get_thought_cooldown_history()
    today_dt = datetime.now(pytz.timezone(TIMEZONE))
    
    # 2. Filter eligible thoughts (not on cooldown)
    all_eligible = []
    
    for category, thoughts in THOUGHT_BANK.items():
        for t in thoughts:
            last_used_str = history.get(t)
            if last_used_str:
                last_used_dt = datetime.strptime(last_used_str, "%Y-%m-%d").replace(tzinfo=pytz.timezone(TIMEZONE))
                days_diff = (today_dt - last_used_dt).days
                if days_diff < THOUGHT_COOLDOWN_DAYS:
                    continue  # Skip if used recently
            all_eligible.append((category, t))
    
    if not all_eligible:
        # Fallback if literally everything is on cooldown
        category = random.choice(list(THOUGHT_BANK.keys()))
        text = random.choice(THOUGHT_BANK[category])
        scene = random.choice(list(SCENE_PROMPTS.keys()))
        return scene, text
    
    # 3. Apply seasonal preference if applicable
    current_month = today_dt.strftime("%m")
    preferred_categories = SEASONAL_MAP.get(current_month, [])
    
    if preferred_categories:
        seasonal_eligible = [x for x in all_eligible if x[0] in preferred_categories]
        if seasonal_eligible:
            if DRY_RUN:
                print(f"Applying seasonal filter for month {current_month}: {preferred_categories}")
            category, text = random.choice(seasonal_eligible)
            scene = random.choice(list(SCENE_PROMPTS.keys()))
            return scene, text
    
    # 4. Pick random from full valid list
    category, text = random.choice(all_eligible)
    scene = random.choice(list(SCENE_PROMPTS.keys()))
    return scene, text

# =========================================================
# HOLIDAY POSTS — EXACT DATE ONLY
# =========================================================
HOLIDAY_POSTS = {
    (1, 1):  {"name": "new_year", "text": "This year, I'm learning to walk slower and trust God more.", "scene": "quiet lakeside at dawn, person sitting on dock watching sunrise, mist over water"},
    (2, 14): {"name": "valentines", "text": "Love is choosing patience when it would be easier to leave.", "scene": "couple walking hand in hand on evening street with warm cafe lights"},
    (3, 8):  {"name": "womens_day", "text": "Strong women don't always speak loudly. Sometimes they endure quietly.", "scene": "woman by window with morning light streaming in, cup of coffee, peaceful strength"},
    (4, 1):  {"name": "april_fools", "text": "Not everything that looks like failure is the end of the story.", "scene": "winding road through hills with light breaking through clouds, hopeful journey"},
    (5, 1):  {"name": "labor_may", "text": "The work you do in silence still matters.", "scene": "worker resting at sunset, overlooking completed work, peaceful exhaustion"},
    (6, 1):  {"name": "pride", "text": "You are allowed to exist without explaining yourself.", "scene": "person standing in open field at sunrise, arms open, freedom"},
    (7, 4):  {"name": "independence", "text": "Freedom begins when fear no longer decides for you.", "scene": "open road under wide dramatic sky, journey ahead"},
    (8, 4):  {"name": "friendship", "text": "Some friendships are answers to prayers we never said out loud.", "scene": "two friends sitting on hillside at golden hour, laughing together"},
    (9, 1):  {"name": "labor_sep", "text": "Rest is not quitting. It's preparation.", "scene": "empty park bench under shady tree, late afternoon dappled light"},
    (10, 31):{"name": "halloween", "text": "Not everything hidden is dangerous. Some things are healing.", "scene": "foggy forest path with lantern glow, mysterious but peaceful"},
    (11, 28):{"name": "thanksgiving", "text": "Gratitude doesn't erase pain, but it softens the weight.", "scene": "warm dinner table by window with autumn light, family gathering"},
    (12, 25):{"name": "christmas", "text": "Hope often arrives quietly, not loudly.", "scene": "snowy street at night with warm glowing windows, peaceful christmas eve"},
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

def mark_holiday_used(name):
    today = date.today()
    year = str(today.year)
    history = load_holiday_history()
    history.setdefault(year, []).append(name)
    save_holiday_history(history)

# =========================================================
# IMAGE GENERATION (CALLED ONLY IF POSTING)
# =========================================================
def generate_image_from_scene(scene_prompt):
    theme = get_monthly_theme()
    prompt = f"{scene_prompt}. {STATIC_STYLE} {theme}"

    if DRY_RUN:
        print(f"[DRY RUN] Generating image for: {prompt}")
        # Return a blank dummy image for testing flow
        img = Image.new("RGB", (1024, 1792), color=(50, 50, 50))
        out = BytesIO()
        img.save(out, "JPEG")
        out.seek(0)
        return out

    r = client.images.generate(
        model="gpt-image-1",
        prompt=prompt,
        size="1024x1536",
        n=1,
    )

    return BytesIO(base64.b64decode(r.data[0].b64_json))

# =========================================================
# IMAGE PROCESSING
# =========================================================
FONT_MAIN = "fonts/LibreBaskerville-Regular.ttf"
FONT_MARK = "fonts/LibreBaskerville-Regular.ttf"
WATERMARK_TEXT = "© Yesterday's Letters"

def crop_to_4_5(img):
    target_h = int(img.width * 5 / 4)
    top = (img.height - target_h) // 2
    return img.crop((0, top, img.width, top + target_h))

def is_dark(img, box):
    crop = img.crop(box).convert("L")
    return ImageStat.Stat(crop).mean[0] < 130

def add_text(image_buffer, text):
    img = Image.open(image_buffer).convert("RGBA")
    img = crop_to_4_5(img)

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # ---- TYPOGRAPHY SCALE (smaller, calmer) ----
    FONT_SIZE = 38 if len(text) <= 90 else 34
    LINE_HEIGHT = int(FONT_SIZE * 1.35)

    font = ImageFont.truetype(FONT_MAIN, FONT_SIZE)

    # ---- FIXED TEXT BOX (prevents drift) ----
    BOX_WIDTH = int(img.width * 0.70)
    BOX_HEIGHT = LINE_HEIGHT * 4
    BOX_X = (img.width - BOX_WIDTH) // 2

    # ---- SMART VERTICAL ZONES (top / middle / lower-mid) ----
    candidate_ys = [
        int(img.height * 0.30),
        int(img.height * 0.45),
        int(img.height * 0.58),
    ]

    def zone_score(y):
        crop = img.crop((BOX_X, y, BOX_X + BOX_WIDTH, y + BOX_HEIGHT)).convert("L")
        stat = ImageStat.Stat(crop)
        edges = crop.filter(ImageFilter.FIND_EDGES)
        edge_stat = ImageStat.Stat(edges)
        return stat.stddev[0] + edge_stat.mean[0]

    BOX_Y = min(candidate_ys, key=zone_score)

    # ---- LIGHT / DARK AUTO-DETECT ----
    luminance = ImageStat.Stat(
        img.crop((BOX_X, BOX_Y, BOX_X + BOX_WIDTH, BOX_Y + BOX_HEIGHT)).convert("L")
    ).mean[0]

    TEXT_COLOR = (245, 245, 240, 255) if luminance < 135 else (30, 30, 30, 255)
    SHADOW_COLOR = (0, 0, 0, 70) if luminance < 135 else (0, 0, 0, 40)

    # ---- LINE WRAPPING ----
    words = text.split()
    lines, current = [], ""

    for w in words:
        test = f"{current} {w}".strip()
        if draw.textlength(test, font=font) <= BOX_WIDTH:
            current = test
        else:
            lines.append(current)
            current = w
    lines.append(current)

    # ---- VERTICAL CENTERING INSIDE BOX ----
    y = BOX_Y + (BOX_HEIGHT - len(lines) * LINE_HEIGHT) // 2

    for line in lines:
        w = draw.textlength(line, font=font)
        x = (img.width - w) // 2

        # subtle shadow (legibility only)
        draw.text((x + 2, y + 2), line, font=font, fill=SHADOW_COLOR)
        draw.text((x, y), line, font=font, fill=TEXT_COLOR)

        y += LINE_HEIGHT

    # ---- WATERMARK (unchanged, quieter) ----
    mark_font = ImageFont.truetype(FONT_MAIN, 26)
    mw = draw.textlength(WATERMARK_TEXT, font=mark_font)
    draw.text(
        ((img.width - mw) // 2, img.height - 58),
        WATERMARK_TEXT,
        font=mark_font,
        fill=(255, 255, 255, 130),
    )

    final = Image.alpha_composite(img, overlay)
    out = BytesIO()
    final.convert("RGB").save(out, "JPEG", quality=95)
    out.seek(0)
    return out

# =========================================================
# FACEBOOK POST
# =========================================================
def post_to_facebook(image_buffer):
    if DRY_RUN:
        print("[DRY RUN] Skipping Facebook upload.")
        return

    url = f"https://graph.facebook.com/v19.0/{FB_PAGE_ID}/photos"
    data = {"access_token": FB_TOKEN, "published": "true"}
    files = {"source": ("image.jpg", image_buffer, "image/jpeg")}
    
    try:
        r = requests.post(url, data=data, files=files)
        if r.status_code != 200:
            raise Exception(f"FB Error {r.status_code}: {r.text}")
    except Exception as e:
        print(f"CRITICAL: Facebook Post Failed. Enabling Kill Switch. Error: {e}")
        enable_kill_switch()
        raise e

# =========================================================
# MAIN (STRICT ORDER — DO NOT CHANGE)
# =========================================================
if __name__ == "__main__":
    print(f"Starting Bot. Dry Run: {DRY_RUN}")

    # 1. Safety Checks
    if check_kill_switch():
        print("KILL SWITCH ACTIVE. Posting disabled. Exiting.")
        exit(0)
    
    # Validate fonts exist before making any API calls
    validate_fonts()
    
    # Token Health Check
    if not check_token_health():
        print("Token Health Check Failed. Kill switch enabled. Exiting.")
        exit(1)

    if check_monthly_cap() and not DRY_RUN:
        print("MONTHLY CAP REACHED. Exiting.")
        exit(0)

    # 2. Time gate
    if not is_good_posting_time() and not DRY_RUN:
        print("Outside posting window. Skipping.")
        exit(0)

    # 3. Daily gate
    if already_posted_today() and not DRY_RUN:
        print("Already posted today. Skipping.")
        exit(0)

    # 4. Decide content (FREE)
    holiday = get_today_holiday()
    if holiday:
        text = holiday["text"]
        scene_prompt = holiday["scene"]
        is_holiday = True
        scene_name = "holiday_" + holiday["name"]
        print("HOLIDAY POST:", holiday["name"])
    else:
        scene_name, text = choose_scene_and_text()
        scene_prompt = SCENE_PROMPTS[scene_name]
        is_holiday = False
        print(f"REGULAR POST: {scene_name}")

    # 5. GENERATE & POST (COSTS MONEY)
    try:
        image_buffer = generate_image_from_scene(scene_prompt)
        final_image = add_text(image_buffer, text)
        post_to_facebook(final_image)

        # 6. Record state (Only on success)
        if not DRY_RUN:
            mark_posted_today()
            increment_monthly_cap()
            update_thought_history(text)
            if is_holiday:
                mark_holiday_used(holiday["name"])
            
            log_engagement(scene_name, text, "SUCCESS")
        else:
            log_engagement(scene_name, text, "DRY_RUN_SUCCESS")

        print("Post successful.")

    except Exception as e:
        print(f"Process failed: {e}")
        log_engagement(scene_name, text, f"FAILED: {e}")
        log_error(e)
        exit(1)

