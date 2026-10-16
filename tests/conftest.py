import subprocess
from pathlib import Path

import pytest

from tutorial_editor.brand import load_brand

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def raw_video(tmp_path_factory) -> Path:
    """20 s synthetic 'raw footage' with a tone, so no real footage is needed to test."""
    p = tmp_path_factory.mktemp("media") / "raw.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=20",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=20", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(p)], check=True)
    return p


@pytest.fixture
def brand(tmp_path):
    b = load_brand(ROOT / "brand" / "brand.yaml")
    b.voiceover.provider = "preview"
    if not (b.base_dir / "assets" / "logo-horizontal-dark.png").exists():
        b.assets.logo_horizontal = None  # assets are rebuilt locally from private files; tests run without them
        b.assets.mascot = None
    return b
