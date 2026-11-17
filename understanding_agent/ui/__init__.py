# UI package: dashboard web server and static assets
try:
    from .server import run, app
    __all__ = ["run", "app"]
except ImportError:
    run = None
    app = None
    __all__ = []
