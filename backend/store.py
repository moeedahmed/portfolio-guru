"""
Unified credential store — picks backend based on environment.
Local dev: SQLite via credentials.py
Render production: Render API env var via render_store.py
"""
import os

IS_RENDER = bool(os.environ.get("RENDER"))

if IS_RENDER:
    from render_store import store_credentials, get_credentials, has_credentials, init_store as _init
    def init():
        _init()
else:
    from credentials import store_credentials, get_credentials, has_credentials, init_db as _init
    def init():
        _init()
