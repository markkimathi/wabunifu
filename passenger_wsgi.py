"""
Entry point for cPanel's "Setup Python App" (Phusion Passenger). Passenger
looks for a module-level `application` object in this exact file, at the
Application Root you configure in cPanel. Passenger auto-detects that this is
an ASGI app (not WSGI) from the object itself, so no extra adapter is needed;
just point Application Startup File at this file, Application Entry point at
"application", and Application Root at the directory this file lives in
(the repo root).
"""
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from api.main import app as application  # noqa: E402
