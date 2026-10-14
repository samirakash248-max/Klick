"""Visual Grounding & Set-of-Mark (SoM) Engine for Klick / BrowserPilot AI.

Provides multimodal visual grounding capabilities:
1. Set-of-Mark (SoM) in-page visual element tagging with high-contrast bounding boxes & numeric badges.
2. Viewport screenshot capture (clean + annotated) with token-efficient JPEG compression.
3. PIL-based offline visual annotation fallback for testing and headless pipelines.
4. Perceptual visual change detection and similarity comparison.
5. Visual coordinate resolution for non-DOM / canvas / svg element targeting.

Owned by: AI Engineer & Browser Automation Engineer
"""

from __future__ import annotations

import base64
from io import BytesIO
import json
import logging
import math
import re
from typing import Any, Dict, List, Optional, Tuple, Union

from PIL import Image, ImageChops, ImageDraw, ImageFont

from .schemas import ElementCoordinates, ElementDescriptor, PageObservation

logger = logging.getLogger("browserpilot.visual")

# Palette of distinct, high-contrast neon colors for Set-of-Mark visual boundaries
SOM_COLOR_PALETTE = [
    "#00e5ff",  # Cyan
    "#76ff03",  # Bright Lime
    "#ffd600",  # Amber Gold
    "#ff4081",  # Vivid Pink
    "#d500f9",  # Deep Violet
    "#00e676",  # Emerald Green
    "#ff9100",  # Vivid Orange
    "#2979ff",  # Azure Blue
    "#ff1744",  # Neon Red
    "#1de9b6",  # Teal Accent
]

SOM_INJECT_SCRIPT = """
() => {
    // 1. Remove any previous overlay
    const existing = document.getElementById('klick-som-overlay-root');
    if (existing) existing.remove();

    // 2. Query visible interactive elements
    const selectorQuery = 'button, a[href], input, select, textarea, [role="button"], [role="link"], [role="checkbox"], [role="tab"], [data-agent-id]';
    const candidates = document.querySelectorAll(selectorQuery);

    const overlay = document.createElement('div');
    overlay.id = 'klick-som-overlay-root';
    overlay.style.position = 'fixed';
    overlay.style.top = '0';
    overlay.style.left = '0';
    overlay.style.width = '100vw';
    overlay.style.height = '100vh';
    overlay.style.pointerEvents = 'none';
    overlay.style.zIndex = '2147483647';
    overlay.style.overflow = 'hidden';

    const colors = [
        '#00e5ff', '#76ff03', '#ffd600', '#ff4081',
        '#d500f9', '#00e676', '#ff9100', '#2979ff'
    ];

    let markIndex = 0;
    const maxMarks = 50; // Cap to keep vision input clean and fast

    for (let i = 0; i < candidates.length && markIndex < maxMarks; i++) {
        const el = candidates[i];

        // Check visibility
        if (typeof el.checkVisibility === 'function') {
            if (!el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) continue;
        } else {
            const style = window.getComputedStyle(el);
            if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') continue;
            if (el.offsetParent === null && style.position !== 'fixed') continue;
        }

        const rect = el.getBoundingClientRect();
        if (rect.width < 10 || rect.height < 10) continue;
        // Skip elements completely off-screen
        if (rect.bottom < 0 || rect.top > window.innerHeight || rect.right < 0 || rect.left > window.innerWidth) continue;

        const color = colors[markIndex % colors.length];

        // Bounding box border
        const box = document.createElement('div');
        box.style.position = 'absolute';
        box.style.left = `${Math.max(0, rect.left)}px`;
        box.style.top = `${Math.max(0, rect.top)}px`;
        box.style.width = `${rect.width}px`;
        box.style.height = `${rect.height}px`;
        box.style.border = `2px solid ${color}`;
        box.style.backgroundColor = 'rgba(0, 0, 0, 0.05)';
        box.style.boxSizing = 'border-box';
        box.style.pointerEvents = 'none';

        // Badge pill
        const badge = document.createElement('div');
        badge.innerText = `[${markIndex}]`;
        badge.style.position = 'absolute';
        badge.style.left = `${Math.max(0, rect.left)}px`;
        badge.style.top = `${Math.max(0, rect.top - 18)}px`;
        badge.style.backgroundColor = color;
        badge.style.color = '#000000';
        badge.style.fontFamily = 'monospace, ui-monospace, sans-serif';
        badge.style.fontSize = '11px';
        badge.style.fontWeight = '900';
        badge.style.padding = '1px 5px';
        badge.style.borderRadius = '3px';
        badge.style.boxShadow = '0 1px 3px rgba(0,0,0,0.6)';
        badge.style.pointerEvents = 'none';
        badge.style.lineHeight = '14px';
        badge.style.whiteSpace = 'nowrap';
        badge.style.zIndex = '2147483647';

        overlay.appendChild(box);
        overlay.appendChild(badge);
        markIndex++;
    }

    document.documentElement.appendChild(overlay);
    return markIndex;
}
"""

SOM_CLEANUP_SCRIPT = """
() => {
    const existing = document.getElementById('klick-som-overlay-root');
    if (existing) existing.remove();
}
"""


async def capture_page_screenshots(
    page: Any,
    annotate_som: bool = True,
    quality: int = 75,
) -> Tuple[Optional[str], Optional[str], int]:
    """Capture clean and Set-of-Mark annotated screenshots of the active page.

    Returns:
        (clean_base64_jpeg, annotated_base64_jpeg, visual_marks_count)
    """
    if not page or getattr(page, "is_closed", lambda: True)():
        return None, None, 0

    clean_b64: Optional[str] = None
    annotated_b64: Optional[str] = None
    marks_count = 0

    try:
        # 1. Capture clean screenshot
        clean_bytes = await page.screenshot(type="jpeg", quality=quality)
        clean_b64 = base64.b64encode(clean_bytes).decode("ascii")
    except Exception as exc:
        logger.debug("Failed capturing clean screenshot: %s", exc)
        return None, None, 0

    if not annotate_som:
        return clean_b64, None, 0

    try:
        # 2. Inject Set-of-Mark visual overlay
        marks_count = await page.evaluate(SOM_INJECT_SCRIPT)

        # 3. Capture annotated screenshot with visual badges
        annotated_bytes = await page.screenshot(type="jpeg", quality=quality)
        annotated_b64 = base64.b64encode(annotated_bytes).decode("ascii")
    except Exception as exc:
        logger.debug("Failed capturing SoM annotated screenshot: %s", exc)
        annotated_b64 = None
    finally:
        # 4. Remove overlay immediately so page remains untouched
        try:
            await page.evaluate(SOM_CLEANUP_SCRIPT)
        except Exception:
            pass

    return clean_b64, annotated_b64, marks_count


def draw_som_annotations_pil(
    image_input: Union[Image.Image, bytes, str],
    elements: List[ElementDescriptor],
    max_marks: int = 50,
) -> Tuple[str, int]:
    """Annotate an image with bounding boxes and numeric visual badges using Pillow.

    Args:
        image_input: PIL Image, raw image bytes, or base64 string.
        elements: List of observed interactive elements with coordinates.
        max_marks: Max elements to annotate.

    Returns:
        (annotated_base64_jpeg, annotated_count)
    """
    # 1. Resolve PIL Image
    if isinstance(image_input, Image.Image):
        img = image_input.copy()
    elif isinstance(image_input, bytes):
        img = Image.open(BytesIO(image_input)).convert("RGB")
    elif isinstance(image_input, str):
        raw = base64.b64decode(image_input)
        img = Image.open(BytesIO(raw)).convert("RGB")
    else:
        raise ValueError("Unsupported image input type for visual annotation")

    draw = ImageDraw.Draw(img)
    annotated_count = 0

    # Load default monospace font
    font = ImageFont.load_default()

    for idx, el in enumerate(elements[:max_marks]):
        if not el.coordinates:
            continue

        c = el.coordinates
        if c.width <= 0 or c.height <= 0:
            continue

        color = SOM_COLOR_PALETTE[idx % len(SOM_COLOR_PALETTE)]
        x0 = max(0, int(c.x))
        y0 = max(0, int(c.y))
        x1 = min(img.width, int(c.x + c.width))
        y1 = min(img.height, int(c.y + c.height))

        # Draw 2px bounding box
        draw.rectangle([x0, y0, x1, y1], outline=color, width=2)

        # Draw badge label
        label = f"[{idx}]"
        badge_w = len(label) * 7 + 6
        badge_h = 14
        badge_y0 = max(0, y0 - badge_h)
        badge_y1 = badge_y0 + badge_h
        badge_x1 = min(img.width, x0 + badge_w)

        draw.rectangle([x0, badge_y0, badge_x1, badge_y1], fill=color)
        draw.text((x0 + 3, badge_y0 + 1), label, fill="#000000", font=font)
        annotated_count += 1

    # Export to base64 JPEG
    buffer = BytesIO()
    img.save(buffer, format="JPEG", quality=80)
    b64_out = base64.b64encode(buffer.getvalue()).decode("ascii")
    return b64_out, annotated_count


def compute_visual_similarity(img_b64_a: Optional[str], img_b64_b: Optional[str]) -> float:
    """Compute visual similarity score between two base64 images (0.0 = completely different, 1.0 = identical).

    Used for visual stagnation detection, UI state change validation, and overlay detection.
    """
    if not img_b64_a or not img_b64_b:
        return 0.0

    if img_b64_a == img_b64_b:
        return 1.0

    try:
        img_a = Image.open(BytesIO(base64.b64decode(img_b64_a))).convert("RGB")
        img_b = Image.open(BytesIO(base64.b64decode(img_b64_b))).convert("RGB")

        # Resize to standard thumbnail for fast comparison
        size = (160, 100)
        thumb_a = img_a.resize(size)
        thumb_b = img_b.resize(size)

        diff = ImageChops.difference(thumb_a, thumb_b)
        stat = diff.getbbox()
        if stat is None:
            return 1.0

        # Calculate average root-mean-square diff across pixels
        hist = diff.histogram()
        sum_sq = sum(h * (i ** 2) for i, h in enumerate(hist))
        rms = math.sqrt(sum_sq / (float(size[0] * size[1]) * 3))
        # RMS ranges from 0 (identical) to ~255 (completely inverted)
        similarity = max(0.0, min(1.0, 1.0 - (rms / 255.0)))
        return round(similarity, 4)
    except Exception as exc:
        logger.debug("Visual similarity calculation failed: %s", exc)
        return 0.0


def resolve_visual_target(
    target_str: Optional[str],
    elements: List[ElementDescriptor],
) -> Optional[Tuple[ElementDescriptor, int]]:
    """Resolve a target string like '[3]', '3', 'mark-3', or 'elem-3' to its corresponding element.

    Returns:
        (ElementDescriptor, index) if resolved, else None.
    """
    if not target_str:
        return None

    cleaned = target_str.strip()

    # Pattern 1: '[0]', '[12]'
    m = re.match(r"^\[(\d+)\]$", cleaned)
    if m:
        idx = int(m.group(1))
        if 0 <= idx < len(elements):
            return elements[idx], idx

    # Pattern 2: 'mark-0', 'visual-0', 'elem-0'
    m = re.match(r"^(?:mark|visual|elem|id)[-_](\d+)$", cleaned, re.IGNORECASE)
    if m:
        idx = int(m.group(1))
        if 0 <= idx < len(elements):
            return elements[idx], idx

    # Pattern 3: Pure integer '0', '1'
    if cleaned.isdigit():
        idx = int(cleaned)
        if 0 <= idx < len(elements):
            return elements[idx], idx

    return None
