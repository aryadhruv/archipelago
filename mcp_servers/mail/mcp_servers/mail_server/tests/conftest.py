import os
import tempfile

# path.py reads the data root at import time, so set it before any server import.
os.environ.setdefault("APP_MAIL_DATA_ROOT", tempfile.mkdtemp(prefix="mail-test-"))
