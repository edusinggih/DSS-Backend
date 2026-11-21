import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    SECRET_KEY = os.getenv("SECRET_KEY")
    DATABASE_URL = os.getenv("DATABASE_URL")
    FRONTEND_ORIGINS = [
        origin.strip()
        for origin in os.getenv(
            "FRONTEND_ORIGINS",
            "http://127.0.0.1:5500"
        ).split(",")
        if origin.strip()
    ]

    @classmethod
    def validate(cls):
        missing = []
        if not cls.SECRET_KEY:
            missing.append("SECRET_KEY")
        if not cls.DATABASE_URL:
            missing.append("DATABASE_URL")
        if missing:
            raise RuntimeError(
                "Missing required environment variables: " + ", ".join(missing)
            )
