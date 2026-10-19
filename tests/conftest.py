import os
import tempfile

# The module-level `app = create_app()` in app.py runs at import time. Point it
# at a throwaway file so importing it can never touch real development data.
os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(prefix="pow-import-"), "import.db")
os.environ.pop("SECRET_KEY", None)
os.environ.pop("POW_ENV", None)

import pytest  # noqa: E402

from app import create_app  # noqa: E402


@pytest.fixture
def app(tmp_path):
    return create_app({
        "TESTING": True,
        "DATABASE": str(tmp_path / "test.db"),
        "SECRET_KEY": "test-secret",
        "REVIEWER_EMAILS": frozenset({"rev@example.com"}),
    })
