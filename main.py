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
POST_WINDOWS = [(19, 24)]  # 7–10 PM (expanded for testing)
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
SCENE_COOLDOWN_DAYS = 5     # Avoid same scene within 5 days
SCENE_HISTORY_FILE = "scene_history.json"

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
# RANDOMIZED PROMPT COMPONENTS (HIGH-QUALITY TEMPLATE)
# =========================================================

# SCENES (what we're looking at)
SCENES = [
    {
        "name": "rural_path",
        "scene": "A winding dirt path through tall grass overlooking a rural town",
        "details": "Rolling hills, scattered rooftops, wildflowers along the path"
    },
    {
        "name": "calm_river",
        "scene": "A calm river with shimmering reflections and overhanging trees",
        "details": "Mossy riverbanks, water lilies, lush greenery"
    },
    {
        "name": "wooden_boat",
        "scene": "A small wooden boat drifting quietly beneath leafy branches",
        "details": "Crystal clear water, dappled light, overhanging fruit trees"
    },
    {
        "name": "countryside_hill",
        "scene": "A countryside hillside beneath a large shade tree",
        "details": "Grassy meadow, distant village rooftops, scattered wildflowers"
    },
    {
        "name": "seaside_cabin",
        "scene": "A cozy seaside cabin surrounded by plants",
        "details": "Weathered wood, potted flowers, ocean view, sandy path"
    },
    {
        "name": "ocean_kitchen",
        "scene": "An open kitchen interior overlooking the ocean",
        "details": "Warm sunlight streaming in, potted herbs, vintage details"
    },
    {
        "name": "campfire_van",
        "scene": "A parked van in an open field near a quiet campfire",
        "details": "Open countryside, distant hills, warm firelight glow"
    },
    {
        "name": "village_road",
        "scene": "A narrow village road winding through rolling hills",
        "details": "Stone walls, cottages, trees lining the road, peaceful atmosphere"
    },
]

# SEASONS + SKY
SEASONS = {
    "summer": [
        "Bright summer sky with towering white cumulus clouds",
        "Deep blue sky with soft atmospheric haze",
    ],
    "autumn": [
        "Soft golden sky with warm amber clouds",
        "Clear afternoon sky with drifting autumn leaves",
    ],
    "spring_rain": [
        "Overcast spring sky with gentle rainfall",
        "Soft gray-blue clouds with light mist",
    ],
    "winter": [
        "Clear winter sky with pale sunlight",
        "Cold blue sky with thin clouds and crisp air",
    ],
}

# LIGHTING OPTIONS
LIGHTING_OPTIONS = [
    "Warm midday sunlight with natural dappled shadows",
    "Low-angle golden hour sunlight casting long shadows",
    "Cool moonlight softly illuminating the landscape",
    "Diffused soft light through clouds and mist",
    "Crisp winter sunlight with cool, elongated shadows",
]

# ATMOSPHERE + MOTION
ATMOSPHERE_OPTIONS = [
    "Soft atmospheric haze with subtle lens flare",
    "Gentle breeze moving grass and leaves",
    "Light mist near the horizon with soft light bloom",
    "Rain ripples on water and wet reflective surfaces",
    "Still air with faint drifting particles",
]

# MOOD OPTIONS
MOOD_OPTIONS = [
    "Calm, nostalgic, peaceful mood",
    "Quiet, reflective, emotional mood",
    "Warm, comforting, tranquil mood",
    "Serene, contemplative, timeless mood",
]

def generate_image_prompt(scene_data):
    """Generate a complete prompt from randomized components."""
    # Pick random components
    season_key = random.choice(list(SEASONS.keys()))
    sky = random.choice(SEASONS[season_key])
    lighting = random.choice(LIGHTING_OPTIONS)
    atmosphere = random.choice(ATMOSPHERE_OPTIONS)
    mood = random.choice(MOOD_OPTIONS)
    
    # Build the master prompt
    prompt = (
        f"Cinematic anime-style illustration, ultra high detail, 8K quality, painterly digital art. "
        f"{scene_data['scene']}, with a wide sense of depth and scale. "
        f"{scene_data['details']}. "
        f"{sky}. "
        f"{lighting}. "
        f"{atmosphere}. "
        f"Rich saturated colors, detailed foliage and natural textures. "
        f"{mood}, slice-of-life atmosphere. "
        f"Anime background art quality, hand-painted look, soft brush textures, realistic lighting, no text, no watermark."
    )
    
    return prompt, season_key

# Keep SCENE_PROMPTS for backwards compatibility (holiday posts use this format)
SCENE_PROMPTS = {scene["name"]: scene["scene"] for scene in SCENES}

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
        scene_data = random.choice(SCENES)
        return scene_data, text
    
    # 3. Compute available scenes (with cooldown check)
    scene_history = load_json_file(SCENE_HISTORY_FILE)
    recent_scenes = []
    for s, date_str in scene_history.items():
        try:
            used_dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=pytz.timezone(TIMEZONE))
            if (today_dt - used_dt).days < SCENE_COOLDOWN_DAYS:
                recent_scenes.append(s)
        except:
            pass
    
    available_scenes = [s for s in SCENES if s["name"] not in recent_scenes]
    if not available_scenes:
        available_scenes = SCENES  # Fallback if all on cooldown
    
    # 4. Apply seasonal preference if applicable
    current_month = today_dt.strftime("%m")
    preferred_categories = SEASONAL_MAP.get(current_month, [])
    
    if preferred_categories:
        seasonal_eligible = [x for x in all_eligible if x[0] in preferred_categories]
        if seasonal_eligible:
            if DRY_RUN:
                print(f"Applying seasonal filter for month {current_month}: {preferred_categories}")
            category, text = random.choice(seasonal_eligible)
            scene_data = random.choice(available_scenes)
            return scene_data, text
    
    # 5. Pick random from full valid list
    category, text = random.choice(all_eligible)
    scene_data = random.choice(available_scenes)
    return scene_data, text

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
def generate_image_from_scene(prompt):
    """Generate image from a complete prompt string."""
    if DRY_RUN:
        print(f"[DRY RUN] Generating image for prompt ({len(prompt)} chars):")
        print(f"  {prompt[:150]}...")
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
        # For holidays, use the old-style direct prompt
        scene_prompt = (
            f"Cinematic anime-style illustration, ultra high detail, 8K quality, painterly digital art. "
            f"{holiday['scene']}, with a wide sense of depth and scale. "
            f"Rich saturated colors, detailed foliage and natural textures. "
            f"Calm, nostalgic, peaceful mood, slice-of-life atmosphere. "
            f"Anime background art quality, hand-painted look, soft brush textures, realistic lighting, no text, no watermark."
        )
        is_holiday = True
        scene_name = "holiday_" + holiday["name"]
        print("HOLIDAY POST:", holiday["name"])
    else:
        scene_data, text = choose_scene_and_text()
        # Generate randomized prompt from scene data
        scene_prompt, season = generate_image_prompt(scene_data)
        scene_name = scene_data["name"]
        is_holiday = False
        print(f"REGULAR POST: {scene_name} ({season})")

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
            # Track used scene to ensure variety
            scene_history = load_json_file(SCENE_HISTORY_FILE)
            scene_history[scene_name] = datetime.now(pytz.timezone(TIMEZONE)).strftime("%Y-%m-%d")
            save_json_file(SCENE_HISTORY_FILE, scene_history)
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
