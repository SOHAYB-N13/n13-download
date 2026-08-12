from PIL import Image, ImageDraw

def create_icon(size):
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    r = size // 6
    for y in range(size):
        for x in range(size):
            nx = x / size
            ny = y / size
            t = (nx + ny) / 2
            r_c = int(59 + (139 - 59) * t)
            g_c = int(130 + (92 - 130) * t)
            b_c = int(246 + (246 - 246) * t)
            img.putpixel((x, y), (r_c, g_c, b_c, 255))
    mask = Image.new('L', (size, size), 0)
    mdraw = ImageDraw.Draw(mask)
    mdraw.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=255)
    img.putalpha(mask)
    draw = ImageDraw.Draw(img)
    shaft_w = max(2, size // 14)
    cx = size // 2
    shaft_top = int(size * 0.28)
    shaft_bot = int(size * 0.52)
    draw.rectangle([cx - shaft_w, shaft_top, cx + shaft_w, shaft_bot], fill=(255, 255, 255, 255))
    head_top = shaft_bot
    head_bot = int(size * 0.72)
    head_w = int(size * 0.22)
    draw.polygon([(cx, head_bot), (cx - head_w, head_top), (cx + head_w, head_top)], fill=(255, 255, 255, 255))
    base_y = int(size * 0.79)
    base_w = int(size * 0.34)
    draw.rounded_rectangle([cx - base_w, base_y, cx + base_w, base_y + max(2, size // 18)],
                           radius=max(1, size // 40), fill=(255, 255, 255, 230))
    return img

sizes = [16, 24, 32, 48, 64, 128, 256]
imgs = [create_icon(s) for s in sizes]
imgs[0].save('assets/icon.ico', format='ICO', sizes=[(s, s) for s in sizes], append_images=imgs[1:])
print('Created assets/icon.ico')
