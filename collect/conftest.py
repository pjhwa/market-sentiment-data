import os

# Unit tests must never run real hermes diagnoses or fire desktop alerts.
os.environ.setdefault("GROK_HEALTH", "0")
