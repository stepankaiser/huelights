"""Guess a company's brand colour from its domain.

Order: manual override -> cache -> website <meta theme-color> / manifest -> logo/favicon
dominant colour -> deterministic colour from the domain name.

  python brand.py spotify.com google.com
"""
import colorsys
import hashlib
import io
import json
import pathlib
import re
import sys
import urllib.parse
import urllib.request

from PIL import Image

CACHE = pathlib.Path(__file__).with_name("brand_cache.json")
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/130 Safari/537.36"}


def fetch(url, limit=600_000):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=8) as r:
        return r.geturl(), r.read(limit)


def to_hex(rgb):
    return "#%02x%02x%02x" % tuple(int(c) for c in rgb)


def parse_hex(value):
    value = value.strip().lower()
    if re.fullmatch(r"#[0-9a-f]{3}", value):
        value = "#" + "".join(c * 2 for c in value[1:])
    if re.fullmatch(r"#[0-9a-f]{6}", value):
        return value
    m = re.fullmatch(r"rgba?\((\d+)[ ,]+(\d+)[ ,]+(\d+).*\)", value)
    return to_hex(map(int, m.groups())) if m else None


def vivid(hex_value, min_sat=0.3, min_val=0.25):
    r, g, b = (int(hex_value[i:i + 2], 16) / 255 for i in (1, 3, 5))
    _, s, v = colorsys.rgb_to_hsv(r, g, b)
    return s >= min_sat and v >= min_val


def image_colour(data):
    """Most prominent vivid colour in an image.

    Pixels are grouped into hue/saturation/value buckets; each bucket scores its pixel count
    weighted by vividness, so a small bright accent beats a large dull background.
    """
    img = Image.open(io.BytesIO(data)).convert("RGBA")
    img.thumbnail((96, 96))
    pixels = img.get_flattened_data() if hasattr(img, "get_flattened_data") else img.getdata()
    buckets = {}
    for r, g, b, a in pixels:
        if a < 200:
            continue
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        if s < 0.3 or v < 0.35:
            continue
        key = (round(h * 24) % 24, round(s * 3), round(v * 3))
        bucket = buckets.setdefault(key, [0, 0.0, 0, 0, 0])
        bucket[0] += 1
        bucket[1] += (s * v) ** 2
        bucket[2] += r
        bucket[3] += g
        bucket[4] += b
    if not buckets:
        return None
    n, _, r, g, b = max(buckets.values(), key=lambda x: x[1])
    return to_hex((r / n, g / n, b / n))


def from_website(domain):
    base, html = f"https://{domain}", b""
    for url in (f"https://{domain}", f"https://www.{domain}"):
        try:
            base, html = fetch(url)
            break
        except Exception:
            continue
    html = html.decode("utf-8", "ignore")

    for pattern in (
        r'<meta[^>]+name=["\']theme-color["\'][^>]*content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*name=["\']theme-color',
        r'<meta[^>]+name=["\']msapplication-TileColor["\'][^>]*content=["\']([^"\']+)',
    ):
        for m in re.finditer(pattern, html, re.I):
            colour = parse_hex(m.group(1))
            if colour and vivid(colour):
                return colour, "theme-color"

    m = re.search(r'<link[^>]+rel=["\']manifest["\'][^>]*href=["\']([^"\']+)', html, re.I)
    if m:
        try:
            manifest = json.loads(fetch(urllib.parse.urljoin(base, m.group(1)))[1])
            colour = parse_hex(manifest.get("theme_color", ""))
            if colour and vivid(colour):
                return colour, "manifest"
        except Exception:
            pass

    icons = re.findall(r'<link[^>]+rel=["\'][^"\']*(?:apple-touch-icon|icon)[^"\']*["\'][^>]*>', html, re.I)
    hrefs = [re.search(r'href=["\']([^"\']+)', tag).group(1) for tag in icons if "href=" in tag and not tag.lower().count(".svg")]
    hrefs.sort(key=lambda h: "apple-touch" not in h)
    candidates = [urllib.parse.urljoin(base, h) for h in hrefs]
    candidates.append(f"https://www.google.com/s2/favicons?domain={urllib.parse.urlparse(base).netloc or domain}&sz=128")
    for url in candidates:
        try:
            colour = image_colour(fetch(url)[1])
            if colour:
                return colour, "logo"
        except Exception:
            continue
    return None, None


def fallback(domain):
    hue = int(hashlib.sha1(domain.encode()).hexdigest()[:4], 16) / 0xFFFF
    return to_hex(c * 255 for c in colorsys.hsv_to_rgb(hue, 0.85, 1))


def brand_colour(domain, overrides=None):
    domain = domain.lower()
    if overrides and domain in overrides:
        return overrides[domain]
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    if domain in cache:
        return cache[domain]["colour"]
    colour, source = from_website(domain)
    if not colour:
        colour, source = fallback(domain), "hash"
    cache[domain] = {"colour": colour, "source": source}
    CACHE.write_text(json.dumps(cache, indent=2, sort_keys=True))
    return colour


if __name__ == "__main__":
    for d in sys.argv[1:]:
        c = brand_colour(d)
        print(d, c, json.loads(CACHE.read_text())[d]["source"])
