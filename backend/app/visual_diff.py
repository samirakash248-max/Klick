"""Visual Diff & Webpage Change Monitoring Engine for Klick Browser Agent.

Compares baseline screenshots against current page captures to detect visual alterations,
price fluctuations, dynamic content updates, and altered UI layouts using perceptual
and pixel-level difference analysis. Generates side-by-side diff overlays with
bounding-box highlighted regions and change percentages.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import io
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw

from .schemas import BaselineSaveRequest, VisualDiffRequest, VisualDiffResult

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
BASELINES_DIR = DATA_DIR / "visual_baselines"
BASELINES_INDEX = BASELINES_DIR / "baselines.json"


def _decode_base64_image(b64_str: str) -> Image.Image:
    """Decode a base64 data string or raw base64 into a PIL RGB Image."""
    if "," in b64_str:
        b64_str = b64_str.split(",", 1)[1]
    img_bytes = base64.b64decode(b64_str)
    return Image.open(io.BytesIO(img_bytes)).convert("RGB")


def _encode_image_base64(img: Image.Image, format: str = "JPEG") -> str:
    """Encode a PIL Image into a base64 string."""
    buf = io.BytesIO()
    img.save(buf, format=format, quality=85)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def compute_visual_diff(
    baseline_b64: str,
    current_b64: str,
    threshold: float = 0.05,
    baseline_id: Optional[str] = None,
    url: Optional[str] = None,
) -> VisualDiffResult:
    """Compare two base64-encoded screenshots and generate visual diff metrics and overlay.
    
    Args:
        baseline_b64: Baseline reference screenshot.
        current_b64: Current active page screenshot.
        threshold: Pixel delta tolerance (0.0 to 1.0).
        baseline_id: Optional identifier of baseline.
        url: Optional page URL.
        
    Returns:
        VisualDiffResult containing similarity score, change percentage, bounding boxes, and diff overlay.
    """
    img_base = _decode_base64_image(baseline_b64)
    img_curr = _decode_base64_image(current_b64)

    # Normalize dimensions to the smaller dimensions to align pixels
    w = min(img_base.width, img_curr.width)
    h = min(img_base.height, img_curr.height)

    if img_base.size != (w, h):
        img_base = img_base.crop((0, 0, w, h))
    if img_curr.size != (w, h):
        img_curr = img_curr.crop((0, 0, w, h))

    arr_base = np.array(img_base, dtype=np.int16)
    arr_curr = np.array(img_curr, dtype=np.int16)

    # Compute absolute RGB difference
    abs_diff = np.abs(arr_base - arr_curr)
    max_channel_diff = np.max(abs_diff, axis=2)

    # Create boolean change mask
    delta_threshold = 255.0 * max(0.01, threshold)
    change_mask = max_channel_diff > delta_threshold

    total_pixels = w * h
    changed_pixels = int(np.count_nonzero(change_mask))
    change_pct = round((changed_pixels / total_pixels) * 100.0, 2)
    similarity = round(max(0.0, 1.0 - (change_pct / 100.0)), 4)
    has_changed = change_pct > (threshold * 100.0)

    # Detect bounding boxes of changed clusters using a grid-based spatial aggregation
    grid_size = 32
    bounding_boxes: List[Dict[str, float]] = []

    grid_h = h // grid_size
    grid_w = w // grid_size

    for gy in range(grid_h):
        for gx in range(grid_w):
            sub_mask = change_mask[
                gy * grid_size : (gy + 1) * grid_size,
                gx * grid_size : (gx + 1) * grid_size,
            ]
            if np.count_nonzero(sub_mask) > (grid_size * grid_size * 0.15):
                # Region has significant change
                box = {
                    "x": float(gx * grid_size),
                    "y": float(gy * grid_size),
                    "width": float(grid_size),
                    "height": float(grid_size),
                }
                bounding_boxes.append(box)

    # Consolidate overlapping / adjacent boxes
    consolidated_boxes = _merge_bounding_boxes(bounding_boxes)

    # Generate composite diff image: Current image with translucent red mask & green bounding boxes
    diff_canvas = img_curr.copy().convert("RGBA")
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)

    # Paint red tint on changed areas
    red_mask = Image.fromarray((change_mask * 140).astype(np.uint8), mode="L")
    red_layer = Image.new("RGBA", (w, h), (255, 30, 30, 140))
    overlay.paste(red_layer, (0, 0), red_mask)

    # Draw neon green bounding boxes around consolidated change regions
    for box in consolidated_boxes:
        x0 = box["x"]
        y0 = box["y"]
        x1 = x0 + box["width"]
        y1 = y0 + box["height"]
        overlay_draw.rectangle([x0, y0, x1, y1], outline=(0, 255, 120, 255), width=3)

    composite = Image.alpha_composite(diff_canvas, overlay).convert("RGB")
    diff_b64 = _encode_image_base64(composite)

    return VisualDiffResult(
        similarity_score=similarity,
        change_percentage=change_pct,
        has_changed=has_changed,
        changed_regions_count=len(consolidated_boxes),
        changed_bounding_boxes=consolidated_boxes,
        diff_image_base64=diff_b64,
        baseline_id=baseline_id,
        url=url,
    )


def _merge_bounding_boxes(boxes: List[Dict[str, float]], margin: int = 16) -> List[Dict[str, float]]:
    """Merge adjacent bounding boxes within a spatial margin into bounding hulls."""
    if not boxes:
        return []

    rects = [[b["x"], b["y"], b["x"] + b["width"], b["y"] + b["height"]] for b in boxes]
    merged = True

    while merged:
        merged = False
        new_rects = []
        while rects:
            first = rects.pop(0)
            did_combine = False
            for i, other in enumerate(new_rects):
                # Check bounding box overlap with margin
                if not (
                    first[2] + margin < other[0]
                    or first[0] - margin > other[2]
                    or first[3] + margin < other[1]
                    or first[1] - margin > other[3]
                ):
                    # Combine rectangles
                    new_rects[i] = [
                        min(first[0], other[0]),
                        min(first[1], other[1]),
                        max(first[2], other[2]),
                        max(first[3], other[3]),
                    ]
                    did_combine = True
                    merged = True
                    break
            if not did_combine:
                new_rects.append(first)
        rects = new_rects

    results = []
    for r in rects:
        results.append({
            "x": float(r[0]),
            "y": float(r[1]),
            "width": float(r[2] - r[0]),
            "height": float(r[3] - r[1]),
        })
    return results


class VisualBaselineManager:
    """Manages persistent visual baseline screenshots and comparison operations."""

    def __init__(self, baselines_dir: Path = BASELINES_DIR):
        self.baselines_dir = baselines_dir
        self.index_file = self.baselines_dir / "baselines.json"
        self.baselines: Dict[str, Dict[str, Any]] = {}
        self._load_index()

    def _load_index(self):
        """Load baselines catalogue."""
        if self.index_file.exists():
            try:
                self.baselines = json.loads(self.index_file.read_text(encoding="utf-8"))
            except Exception as e:
                logger.error(f"Failed to load visual baselines index: {e}")
                self.baselines = {}
        else:
            self.baselines = {}

    def _save_index(self):
        """Save catalogue to disk."""
        self.baselines_dir.mkdir(parents=True, exist_ok=True)
        self.index_file.write_text(json.dumps(self.baselines, indent=2), encoding="utf-8")

    def save_baseline(
        self,
        baseline_id: str,
        image_b64: str,
        label: Optional[str] = None,
        url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Save a screenshot as a named baseline reference."""
        self.baselines_dir.mkdir(parents=True, exist_ok=True)
        file_path = self.baselines_dir / f"{baseline_id}.png"

        # Save image file to disk
        img = _decode_base64_image(image_b64)
        img.save(file_path, format="PNG")

        entry = {
            "id": baseline_id,
            "label": label or baseline_id,
            "url": url or "",
            "file_name": file_path.name,
            "width": img.width,
            "height": img.height,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self.baselines[baseline_id] = entry
        self._save_index()
        return entry

    def get_baseline_image_b64(self, baseline_id: str) -> Optional[str]:
        """Retrieve base64 image data of a stored baseline."""
        entry = self.baselines.get(baseline_id)
        if not entry:
            return None
        file_path = self.baselines_dir / entry["file_name"]
        if not file_path.exists():
            return None
        img_bytes = file_path.read_bytes()
        return base64.b64encode(img_bytes).decode("utf-8")

    def list_baselines(self) -> List[Dict[str, Any]]:
        """List all available baseline snapshots."""
        return list(self.baselines.values())

    def delete_baseline(self, baseline_id: str) -> bool:
        """Remove a baseline snapshot."""
        entry = self.baselines.pop(baseline_id, None)
        if entry:
            file_path = self.baselines_dir / entry.get("file_name", "")
            file_path.unlink(missing_ok=True)
            self._save_index()
            return True
        return False

    def compare(
        self,
        baseline_id: str,
        current_b64: str,
        threshold: float = 0.05,
        url: Optional[str] = None,
    ) -> VisualDiffResult:
        """Compare a current screenshot against a registered baseline."""
        baseline_b64 = self.get_baseline_image_b64(baseline_id)
        if not baseline_b64:
            raise ValueError(f"Baseline '{baseline_id}' does not exist")
        return compute_visual_diff(
            baseline_b64=baseline_b64,
            current_b64=current_b64,
            threshold=threshold,
            baseline_id=baseline_id,
            url=url,
        )


# Singleton visual baseline manager instance
visual_baseline_manager = VisualBaselineManager()
