"""Perceptual image hashing (dHash) in pure Pillow — Option A reverse-image search.

WHY THIS EXISTS: Gemini tools take JSON arguments, not raw bytes, so an uploaded
image cannot be handed to a tool directly. Instead we reduce each image to a
compact 64-bit *difference hash*. The identical hash is computed for every
candidate photo inside the vetted Telegram channels, and two images are "the same
picture" when their hashes are within a small Hamming distance. This closes the
caption-LESS repost gap that search_telegram (which matches on the CAPTION a
channel typed) cannot: a channel that reposts the same infographic with a
different caption — or none — is invisible to text search but caught here.

dHash is deliberately robust to the distortions Telegram applies on repost — JPEG
recompression and mild rescaling barely move the hash — but it is NOT robust to
crops, rotations, or heavy edits. That is an honest limit the caller states; it is
exact-image matching within our own vetted set, not a general web reverse search.

NO NEW DEPENDENCY: Pillow is already installed and used by the Streamlit upload
preview; this module only formalises that use. No numpy — the hash is a plain
integer built from pixel comparisons.

ISAAC/AFC lesson: no `from __future__ import annotations` anywhere that feeds the
Gemini automatic-function-calling layer; nothing here uses deferred annotations.
"""
import io

try:
    from PIL import Image, ImageOps
except ImportError:  # pragma: no cover — Pillow is a declared dependency
    Image = None
    ImageOps = None

# 8x8 comparison grid -> 64 bits. The hash is computed from a (SIZE+1) x SIZE
# grayscale image (each row compares SIZE adjacent pairs).
HASH_SIZE = 8


def dhash_image(img, hash_size=HASH_SIZE):
    """Difference hash of a PIL image -> int with hash_size*hash_size bits.

    Reduce to a tiny grayscale image and, within each row, set a bit where a
    pixel is brighter than its right-hand neighbour. Grayscale + aggressive
    downscale is exactly what makes the hash survive recompression and resizing:
    fine detail is discarded, only the coarse light/dark gradient survives.
    """
    # Honour EXIF orientation so a photo re-uploaded from a phone hashes the same
    # as it is displayed (a sideways-stored image would otherwise mis-hash).
    if ImageOps is not None:
        try:
            img = ImageOps.exif_transpose(img)
        except Exception:
            pass
    img = img.convert("L").resize((hash_size + 1, hash_size), Image.LANCZOS)
    px = list(img.getdata())
    width = hash_size + 1
    bits = 0
    for row in range(hash_size):
        base = row * width
        for col in range(hash_size):
            bits = (bits << 1) | (1 if px[base + col] > px[base + col + 1] else 0)
    return bits


def dhash_bytes(data, hash_size=HASH_SIZE):
    """dHash raw image bytes, or None if the bytes are not a decodable image.

    Returning None (never raising) is deliberate: a PDF upload, a truncated
    download, or an unsupported codec must degrade to "no hash" so the caller
    simply skips it — it must never break the turn.
    """
    if Image is None or not data:
        return None
    try:
        with Image.open(io.BytesIO(data)) as img:
            return dhash_image(img, hash_size)
    except Exception:
        return None


def hamming(a, b):
    """Number of differing bits between two hashes (0 = identical images)."""
    x = a ^ b
    try:
        return x.bit_count()          # Python 3.10+
    except AttributeError:            # pragma: no cover — older interpreters
        return bin(x).count("1")


def best_distance(h, targets):
    """Smallest Hamming distance from hash `h` to any hash in `targets`.

    Returns None when there is nothing to compare (missing hash or empty
    targets), so callers can distinguish "no comparison possible" from "far".
    """
    if h is None:
        return None
    cand = [hamming(h, t) for t in (targets or []) if t is not None]
    return min(cand) if cand else None
