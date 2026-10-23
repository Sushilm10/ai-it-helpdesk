"""
Test package for the Helix IT helpdesk.

Importing this package points the application at a throwaway SQLite file
so the automated tests never touch the real workspace database. It must
be imported before `app`, which reads the setting at import time.
"""
import os

os.environ.setdefault(
    "HELIX_DATABASE_URI",
    "sqlite:///test_helpdesk.db",
)
