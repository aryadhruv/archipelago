import os

# ============================================================================
# Mail Storage Configuration
# ============================================================================

# Root directory for mail data storage
# Falls back to APP_APPS_DATA_ROOT/mail if APP_MAIL_DATA_ROOT is not set
_apps_data_root = os.getenv("APP_APPS_DATA_ROOT", "/.apps_data")
MAIL_DATA_ROOT = os.getenv("APP_MAIL_DATA_ROOT") or os.path.join(
    _apps_data_root, "mail"
)

# Default mbox filename for storing emails
MBOX_FILENAME = os.getenv("APP_MAIL_MBOX_FILENAME", "sent.mbox")

# ============================================================================
# Email Validation Configuration
# ============================================================================

# RFC 5322 specifies a maximum line length of 998 characters for email headers
# This is a practical limit to prevent issues with email servers and clients
MAX_SUBJECT_LENGTH = 998


# ============================================================================
# List Pagination Configuration
# ============================================================================

# Default number of emails to return when listing (if not specified)
DEFAULT_LIST_LIMIT = int(os.getenv("APP_MAIL_LIST_DEFAULT_LIMIT", "50"))

# Maximum number of emails that can be returned in a single list request
MAX_LIST_LIMIT = int(os.getenv("APP_MAIL_LIST_MAX_LIMIT", "100"))


# ============================================================================
# Mailbox Locking
# ============================================================================

# How long a call waits for the mbox lock before reporting the mailbox busy
LOCK_TIMEOUT_SEC = float(os.getenv("APP_MAIL_LOCK_TIMEOUT_SEC", "10"))

# A dot-lock older than this is taken to be left by a killed process and removed
STALE_LOCK_SEC = float(os.getenv("APP_MAIL_STALE_LOCK_SEC", "60"))
