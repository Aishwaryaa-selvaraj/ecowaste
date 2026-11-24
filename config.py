import os


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "change-this-secret-before-deploying")
    DB_HOST = os.environ.get("DB_HOST", "localhost")
    DB_PORT = int(os.environ.get("DB_PORT", 3306))
    DB_USER = os.environ.get("DB_USER", "root")
    DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
    DB_NAME = os.environ.get("DB_NAME", "ecowaste")

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    MAX_CONTENT_LENGTH = 2 * 1024 * 1024

    # Demo mode: show the password-reset link on screen because no email server is configured.
    DEMO_SHOW_RESET_LINK = os.environ.get("DEMO_SHOW_RESET_LINK", "1") == "1"
