"""Unit tests for Visual Diff & Webpage Change Monitoring Engine."""

import base64
import io
from pathlib import Path
from PIL import Image, ImageDraw
import pytest

from backend.app.schemas import VisualDiffResult
from backend.app.visual_diff import (
    VisualBaselineManager,
    compute_visual_diff,
)


def _create_sample_image(color=(255, 255, 255), add_shape=False) -> str:
    """Helper to generate a base64 encoded test image."""
    img = Image.new("RGB", (200, 200), color=color)
    if add_shape:
        draw = ImageDraw.Draw(img)
        draw.rectangle([50, 50, 100, 100], fill=(255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def test_visual_diff_identical_images():
    """Verify comparing identical images yields 100% similarity and 0% change."""
    img_b64 = _create_sample_image(color=(100, 150, 200))
    result = compute_visual_diff(baseline_b64=img_b64, current_b64=img_b64)

    assert isinstance(result, VisualDiffResult)
    assert result.similarity_score == 1.0
    assert result.change_percentage == 0.0
    assert result.has_changed is False
    assert result.changed_regions_count == 0
    assert result.diff_image_base64 is not None


def test_visual_diff_detects_changes():
    """Verify comparing different images detects change and highlights bounding boxes."""
    img1 = _create_sample_image(color=(255, 255, 255), add_shape=False)
    img2 = _create_sample_image(color=(255, 255, 255), add_shape=True)

    result = compute_visual_diff(baseline_b64=img1, current_b64=img2, threshold=0.02)

    assert result.similarity_score < 1.0
    assert result.change_percentage > 0.0
    assert result.has_changed is True
    assert result.changed_regions_count > 0
    assert len(result.changed_bounding_boxes) > 0


def test_visual_baseline_manager(tmp_path: Path):
    """Verify baseline manager stores, retrieves, compares, and deletes snapshots."""
    manager = VisualBaselineManager(baselines_dir=tmp_path)
    img_b64 = _create_sample_image(color=(50, 100, 150))

    # 1. Save baseline
    entry = manager.save_baseline("test_home", img_b64, label="Homepage", url="http://test.com")
    assert entry["id"] == "test_home"
    assert entry["width"] == 200

    # 2. List baselines
    all_b = manager.list_baselines()
    assert len(all_b) == 1
    assert all_b[0]["id"] == "test_home"

    # 3. Retrieve base64
    retrieved_b64 = manager.get_baseline_image_b64("test_home")
    assert retrieved_b64 is not None

    # 4. Compare
    diff = manager.compare("test_home", img_b64)
    assert diff.similarity_score == 1.0

    # 5. Delete
    deleted = manager.delete_baseline("test_home")
    assert deleted is True
    assert len(manager.list_baselines()) == 0
