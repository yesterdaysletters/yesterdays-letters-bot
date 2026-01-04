import os
import random
import base64
import requests
from io import BytesIO
from datetime import datetime
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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = BASE_DIR  # fonts are in repo root

FONT_MAIN = os.path.join(FONT_DIR, "LibreBaskerville-Regular.ttf")
FONT_WATERMARK = os.path.join(FONT_DIR, "IBMPlexMono-Regular.ttf")

WATERMARK_TEXT = "© Yesterday's Letters"

# =========================================================
# ENGAGEMENT WINDOWS (PH TIME)
# =========================================================
POST_WINDOWS = [
    (7, 9),    # morning
    (11, 13),  # midday
    (19, 21),  # evening
]

def is_good_posting_time():
    tz = pytz.timezone(TIMEZONE)
    hour = datetime.now(tz).hour
    return any(start <= hour < end for start, end in POST_WINDOWS)

def already_posted_today():
    today = datetime.now(pytz.timezone(TIMEZONE)).strftime("%Y-%m-%d")
    if os.path.exists("last_post.txt"):
        with open("last_post.txt") as f:
            return f.read().strip() == today
    return False

def mark_posted():
    today = datetime.now(pytz.timezone(TIMEZONE)).strftime("%Y-%m-%d")
    with open("last_post.txt", "w") as f:
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
        "I stayed long enough to hear myself think.",
        "Not everything that’s slow is lost."
    ],
    "road": [
        "I didn’t know where I was going, only that I had to keep walking.",
        "Faith sometimes looks like taking the next step.",
        "The road teaches patience."
    ],
    "water": [
        "Still waters teach louder lessons.",
        "I sat with my thoughts until they softened.",
        "Some answers come quietly."
    ],
    "night": [
        "God has a plan. Trust, wait, and believe.",
        "The stars remind me I’m not alone.",
        "I learned to rest under the same sky."
    ]
}

# =========================================================
# SCENE → EMOTION PAIRING
# =========================================================
SCENE_STYLES = {
    "rain": "rainy night street, umbrella, soft streetlights, reflective pavement",
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

def choose_scene_and_text():
    scene = random.choice(list(THOUGHT_BANK.keys()))
    text = random.choice(THOUGHT_BANK[scene])
    return scene, text

# =========================================================
# IMAGE GENERATION
# =========================================================
def generate_image(scene):
    prompt = f"{SCENE_STYLES[scene]}. {STATIC_STYLE}"

    r = client.images.generate(
        model="gpt-image-1",
        prompt=prompt,
        size="1024x1536",
        n=1,
    )

    image_b64 = r.data[0].b64_json
    return BytesIO(base64.b64decode(image_b64))

def crop_to_4_5(img):
    target_height = int(img.width * 5 / 4)
    top = (img.height - target_height) // 2
    return img.crop((0, top, img.width, top + target_height))

# =========================================================
# SMART TYPOGRAPHY
# =========================================================
def is_dark_region(img, box):
    crop = img.crop(box).convert("L")
    brightness = ImageStat.Stat(crop).mean[0]
    return brightness < 130

def add_text(image_buffer, text):
    img = Image.open(image_buffer).convert("RGBA")
    img = crop_to_4_5(img)

    draw_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(draw_layer)

    font_main = ImageFont.truetype(FONT_MAIN, 44)
    font_mark = ImageFont.truetype(FONT_WATERMARK, 22)

    # Fixed text box (prevents drift)
    box_width = int(img.width * 0.72)
    box_height = 220
    box_x = (img.width - box_width) // 2

    # Candidate Y positions (safe zones)
    y_positions = [
        int(img.height * 0.18),
        int(img.height * 0.35),
        int(img.height * 0.55)
    ]

    chosen_y = y_positions[0]
    for y in y_positions:
        region = (box_x, y, box_x + box_width, y + box_height)
        if is_dark_region(img, region):
            chosen_y = y
            break

    text_color = (245, 245, 240, 255) if is_dark_region(
        img, (box_x, chosen_y, box_x + box_width, chosen_y + box_height)
    ) else (20, 20, 20, 255)

    # Wrap manually
    words = text.split()
    lines, line = [], ""
    for w in words:
        test = f"{line} {w}".strip()
        if draw.textlength(test, font=font_main) <= box_width:
            line = test
        else:
            lines.append(line)
            line = w
    lines.append(line)

    total_text_height = len(lines) * 52
    start_y = chosen_y + (box_height - total_text_height) // 2

    for l in lines:
        w = draw.textlength(l, font=font_main)
        x = (img.width - w) // 2
        draw.text((x, start_y), l, font=font_main, fill=text_color)
        start_y += 52

    # Watermark
    wm_w = draw.textlength(WATERMARK_TEXT, font=font_mark)
    draw.text(
        ((img.width - wm_w) // 2, img.height - 50),
        WATERMARK_TEXT,
        font=font_mark,
        fill=(255, 255, 255, 140),
    )

    final = Image.alpha_composite(img, draw_layer)
    out = BytesIO()
    final.convert("RGB").save(out, "JPEG", quality=95)
    out.seek(0)
    return out

# =========================================================
# FACEBOOK POST
# =========================================================
def post_to_facebook(image_buffer):
    url = f"https://graph.facebook.com/v19.0/{FB_PAGE_ID}/photos"
    data = {
        "access_token": FB_TOKEN,
        "published": "true"
    }
    files = {
        "source": ("image.jpg", image_buffer, "image/jpeg")
    }

    r = requests.post(url, data=data, files=files)
    if r.status_code != 200:
        raise Exception(r.text)

# =========================================================
# MAIN
# =========================================================
if __name__ == "__main__":
    if not is_good_posting_time():
        print("Outside engagement window. Skipping.")
        exit(0)

    if already_posted_today():
        print("Already posted today. Skipping.")
        exit(0)

    scene, text = choose_scene_and_text()
    print("SCENE:", scene)
    print("TEXT:", text)

    image_buffer = generate_image(scene)
    final_image = add_text(image_buffer, text)
    post_to_facebook(final_image)
    mark_posted()

    print("Post successful.")
