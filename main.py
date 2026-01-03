import os
import requests
import textwrap
from openai import OpenAI
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO

# =========================================================
# CONFIG
# =========================================================
OPENAI_KEY = os.environ.get("OPENAI_API_KEY")
FB_TOKEN = os.environ.get("FB_PAGE_ACCESS_TOKEN")
FB_PAGE_ID = os.environ.get("FB_PAGE_ID")

if not OPENAI_KEY:
    raise Exception("OPENAI_API_KEY missing")

client = OpenAI(api_key=OPENAI_KEY)

WATERMARK_TEXT = "© Yesterday's Letters"

STATIC_STYLE = (
    "Art style: High-fidelity modern Japanese anime digital painting. "
    "Cinematic lighting, soft bloom, volumetric rays, nostalgic mood. "
    "Vertical composition with clear space for text."
)

# =========================================================
# 1. CONCEPT
# =========================================================
def generate_concept():
    print("1. Generating Concept (GPT-5.2)...")
    try:
        r = client.chat.completions.create(
            model="gpt-5.2-chat-latest",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Output EXACT format:\n"
                        "TEXT: <sentence>\n"
                        "POSITION: TOP or BOTTOM\n"
                        "SCENE: <visual>"
                    ),
                },
                {
                    "role": "user",
                    "content": "Write a poetic sentence (max 15 words) about memory or time."
                }
            ],
        )

        content = r.choices[0].message.content.strip()
        lines = content.splitlines()

        text = lines[0].replace("TEXT:", "").strip()
        position = lines[1].replace("POSITION:", "").strip().upper()
        scene = lines[2].replace("SCENE:", "").strip()

        prompt = f"{scene}. {STATIC_STYLE}"

        print("TEXT:", text)
        print("POSITION:", position)
        return text, position, prompt

    except Exception as e:
        print("Concept error:", e)
        return None, None, None

# =========================================================
# 2. IMAGE
# =========================================================
def generate_image(prompt):
    print("2. Generating HD Image (URL Mode)...")
    try:
        r = client.images.generate(
            model="gpt-image-1",
            prompt=prompt,
            size="1024x1536",
            n=1,
        )

        image_url = r.data[0].url
        print("Image generation OK")
        print("IMAGE URL:", image_url)
        return image_url

    except Exception as e:
        print("Image error:", e)
        return None

# =========================================================
# 3. DESIGN
# =========================================================
def add_text_and_watermark(image_url, text, position):
    print("3. Designing typography...")

    r = requests.get(image_url, timeout=60)
    r.raise_for_status()

    img = Image.open(BytesIO(r.content)).convert("RGBA")

    canvas = Image.new("RGBA", (img.width * 2, img.height * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    try:
        font_main = ImageFont.truetype("font.ttf", 100)
        font_mark = ImageFont.truetype("font.ttf", 40)
    except:
        font_main = ImageFont.load_default()
        font_mark = ImageFont.load_default()

    lines = textwrap.wrap(text, 22)
    y = int(canvas.height * (0.18 if position == "TOP" else 0.72))

    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font_main)
        x = (canvas.width - (bbox[2] - bbox[0])) / 2

        for i in range(1, 6):
            draw.text((x+i, y+i), line, font=font_main, fill=(0, 0, 0, 120))
        draw.text((x, y), line, font=font_main, fill=(255, 255, 255, 255))
        y += 130

    mark_bbox = draw.textbbox((0, 0), WATERMARK_TEXT, font=font_mark)
    draw.text(
        ((canvas.width - (mark_bbox[2] - mark_bbox[0])) / 2, canvas.height - 120),
        WATERMARK_TEXT,
        font=font_mark,
        fill=(255, 255, 255, 140),
    )

    canvas = canvas.resize(img.size, Image.LANCZOS)
    final_img = Image.alpha_composite(img, canvas)

    buf = BytesIO()
    final_img.convert("RGB").save(buf, "JPEG", quality=98)
    buf.seek(0)
    return buf

# =========================================================
# 4. FACEBOOK
# =========================================================
def post_to_facebook(image_buffer):
    print("4. Posting to Facebook...")

    url = f"https://graph.facebook.com/v19.0/{FB_PAGE_ID}/photos"
    data = {
        "access_token": FB_TOKEN,
        "published": "true"
    }
    files = {
        "source": ("image.jpg", image_buffer, "image/jpeg")
    }

    r = requests.post(url, data=data, files=files)
    print("FB STATUS:", r.status_code)
    print("FB RESPONSE:", r.text)

    if r.status_code != 200:
        raise Exception("Facebook post failed")

    print("SUCCESS: Facebook post is live")

# =========================================================
# MAIN
# =========================================================
if __name__ == "__main__":
    if not FB_TOKEN or not FB_PAGE_ID:
        raise Exception("Facebook secrets missing")

    text, position, prompt = generate_concept()
    if not text or not prompt:
        raise Exception("Concept failed")

    image_url = generate_image(prompt)
    if image_url is None:
        raise Exception("Image generation failed")

    final_image = add_text_and_watermark(image_url, text, position)
    post_to_facebook(final_image)
