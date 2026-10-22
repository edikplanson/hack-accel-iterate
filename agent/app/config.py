"""Server configuration. Keys never imply permission to enable inference."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
ROOT = Path.cwd()


def mock_mode() -> bool:
    return os.getenv('APP_MODE', 'live') == 'mock'
