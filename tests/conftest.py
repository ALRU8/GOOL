import os
import sys
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="chron-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["STORAGE_DIR"] = f"{_tmp}/storage"
os.environ["YC_API_KEY"] = ""
os.environ["YC_FOLDER_ID"] = ""
os.environ["LLM_ENABLED"] = "0"
os.environ["LIBRARY_DIR"] = f"{_tmp}/library"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as c:
        yield c
