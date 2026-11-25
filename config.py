
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

# Membaca konfigurasi lokal dari file .env jika tersedia
load_dotenv(BASE_DIR / ".env")


class Config:
    # Khusus development lokal
    SECRET_KEY = os.getenv(
        "SECRET_KEY",
        "dss-local-development-key-only"
    )

    # Database lokal, terpisah dari PythonAnywhere
    DATABASE_PATH = os.getenv(
        "DATABASE_PATH",
        str(BASE_DIR / "instance" / "dss-local.sqlite3")
    )

    # Frontend lokal VS Code / Live Server
    FRONTEND_ORIGINS = [
        origin.strip()
        for origin in os.getenv(
            "FRONTEND_ORIGINS",
            (
                "http://127.0.0.1:5500,"
                "http://localhost:5500,"
                "http://127.0.0.1:5501,"
                "http://localhost:5501"
            )
        ).split(",")
        if origin.strip()
    ]
