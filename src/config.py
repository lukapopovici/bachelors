import os

APP_ENV = os.getenv("APP_ENV", "development").lower()
ORTHANC_URL = os.getenv("ORTHANC_URL", "http://localhost:8042")
ORTHANC_USER = os.getenv("ORTHANC_USER", "orthanc")
ORTHANC_PASS = os.getenv("ORTHANC_PASS", "orthanc")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
API_SECRET = os.getenv("API_SECRET", "changeme")
JWT_SECRET = os.getenv("JWT_SECRET", "changeme")
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "60"))
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")


def validate_security_settings() -> None:
    if APP_ENV == "development":
        return
    if len(API_SECRET) < 32 or len(JWT_SECRET) < 32:
        raise RuntimeError("API_SECRET and JWT_SECRET must each be at least 32 characters in production")
    if not ADMIN_PASSWORD or len(ADMIN_PASSWORD) < 12:
        raise RuntimeError("ADMIN_PASSWORD must be at least 12 characters in production")

def orthanc_auth():
    return (ORTHANC_USER, ORTHANC_PASS)
