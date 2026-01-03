import os
import requests
import textwrap
from openai import OpenAI
from PIL import Image, ImageDraw, ImageFont, ImageEnhance
from io import BytesIO

# --- CONFIGURATION ---
OPENAI_KEY = os.environ.get("OPENAI_API_KEY")
FB_TOKEN = os.environ.get("FB_PAGE_ACCESS_TOKEN")
FB_PAGE_ID = os.environ.get("FB_PAGE_ID")

client = OpenAI(api_key=OPENAI_KEY)
WATERMARK_TEXT = "© Yesterday's Letters"

# --- STYLE LOCK (4:5 Ratio Optimized) ---
STATIC_STYLE = (
    "Art style: High-fidelity digital anime art (Makoto Shinkai inspired). "
    "Cinematic lighting, heavy bloom, volumetric rays. "
    "Nostalgic, dreamy atmosphere. Aspect ratio is vertical 4:5. "
    "Ensure there is a large, clean negative space in either the TOP or BOTTOM for text."
)

def generate_concept():
    """ 1. The Author: Writes the letter and decides layout """
    print("1. Generating Concept...")
    response = client.chat.completions.create(
        model="gpt-5.2-chat-latest",
        messages=[
            {"role": "system", "content": "You are the writer for 'Yesterday's Letters'. Output format: TEXT: [poetic sentence] | POSITION: [TOP or BOTTOM] | SCENE: [visual description]"},
            {"role": "user", "content": "Write a poetic sentence (max 15 words) and decide if the text should be at the TOP or BOTTOM based on the scene's composition."}
        ]
    )
    content = response.choices[0].message.content
    try:
        parts = content.split("|")
        text = parts[0].replace("TEXT:", "").strip()
        pos = parts[1].replace("POSITION:", "").strip().upper()
        scene = parts[2].replace("SCENE:", "").strip()
        return text, pos, f"{scene}. {STATIC_STYLE}"
    except:
        return None, None, None

def generate_image(prompt):
    """ 2. The Artist: Perfect 4:5 Ratio (1200x1500) """
    print("2. Generating HD Image (4:5 Ratio)...")
    response = client.images.generate(
        model="gpt-image-1.5", 
        prompt=prompt,
        size="1024x1536",  # Real 4:5 resolution for Facebook
        quality="high",
        n=1,
    )
    return response.data[0].url

def add_text_and_watermark(image_url, text, position):
    """ 3. The Graphic Designer: Anti-Aliased High-Contrast Text """
    print(f"3. Designing HD Typography (Position: {position})...")
    response = requests.get(image_url)
    img = Image.open(BytesIO(response.content)).convert("RGBA")
    
    # We create a 2x larger canvas for 'Super-Sampling' (makes text crisp)
    canvas_size = (img.size[0] * 2, img.size[1] * 2)
    text_layer = Image.new('RGBA', canvas_size, (0,0,0,0))
    draw = ImageDraw.Draw(text_layer)
    
    try:
        # Doubled font size for the super-sampling process
        font_main = ImageFont.truetype("font.ttf", 100) 
        font_mark = ImageFont.truetype("font.ttf", 45)
    except:
        font_main = ImageFont.load_default()
        font_mark = ImageFont.load_default()

    lines = textwrap.wrap(text, width=22)
    line_height = 130 
    
    # Smart Y-positioning adjusted for 4:5 ratio
    if position == "TOP":
        current_y = canvas_size[1] * 0.18
    else:
        current_y = canvas_size[1] * 0.72

    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font_main)
        x_pos = (canvas_size[0] - (bbox[2] - bbox[0])) / 2
        
        # PRO-LEVEL SHADOW: Soft Outer Glow effect
        for off in range(1, 6): # Multi-layered shadow for depth
            draw.text((x_pos+off, current_y+off), line, font=font_main, fill=(0,0,0,100))
        
        # MAIN TEXT: Pure white with slight tracking (spacing)
        draw.text((x_pos, current_y), line, font=font_main, fill=(255, 255, 255, 255))
        current_y += line_height

    # Watermark
    mark_text = WATERMARK_TEXT
    mark_bbox = draw.textbbox((0, 0), mark_text, font=font_mark)
    draw.text(((canvas_size[0]-(mark_bbox[2]-mark_bbox[0]))/2, canvas_size[1]-140), 
              mark_text, font=font_mark, fill=(255,255,255,140))

    # Downscale the text layer back to original size (This creates the 'Clean' edge)
    text_layer = text_layer.resize(img.size, resample=Image.LANCZOS)
    
    # Combine
    final_img = Image.alpha_composite(img, text_layer)
    buffer = BytesIO()
    final_img.convert("RGB").save(buffer, format="JPEG", quality=98)
    buffer.seek(0)
    return buffer

def post_to_facebook(image_buffer):
    """ 4. The Publisher: NO CAPTION """
    print("4. Posting Image only...")
    url = f"https://graph.facebook.com/{FB_PAGE_ID}/photos"
    # Note: 'message' is removed to keep the post clean
    payload = { 'access_token': FB_TOKEN }
    files = { 'source': ('image.jpg', image_buffer, 'image/jpeg') }
    r = requests.post(url, data=payload, files=files)
    if r.status_code == 200: print("SUCCESS!")
    else: print(f"FAILED: {r.text}")

if __name__ == "__main__":
    text, pos, prompt = generate_concept()
    if text:
        img_url = generate_image(prompt)
        final_img = add_text_and_watermark(img_url, text, pos)
        post_to_facebook(final_img)




