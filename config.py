"""Configuration centralisée de l'application (variables d'environnement ou fichiers .env)."""
import os
from pathlib import Path


def _charger_env(chemins: list[Path]) -> dict[str, str]:
    valeurs: dict[str, str] = {}
    for chemin in chemins:
        if chemin.exists():
            for ligne in chemin.read_text(encoding="utf-8").splitlines():
                ligne = ligne.strip()
                if not ligne or ligne.startswith("#") or "=" not in ligne:
                    continue
                cle, val = ligne.split("=", 1)
                valeurs[cle.strip()] = val.strip().strip('"').strip("'")
    return valeurs


_BASE = Path(__file__).resolve().parent
_FICHIERS = _charger_env([_BASE / ".env", _BASE / "config.env"])


def lire(nom: str, defaut: str = "") -> str:
    return os.environ.get(nom) or _FICHIERS.get(nom) or defaut


# --- IA Gemini ---
GEMINI_API_KEY = lire("GEMINI_API_KEY")
GEMINI_MODEL = lire("GEMINI_MODEL", "gemini-flash-lite-latest")

# --- Sécurité Application ---
APP_PASSWORD = lire("APP_PASSWORD", "rappel2026")

# --- Base de données (SQLite par défaut ou PostgreSQL via DATABASE_URL) ---
# Sur Render avec Persistent Disk monté sur /data, définir SQLITE_PATH=/data/rappels.db
SQLITE_PATH = lire("SQLITE_PATH", str(_BASE / "data" / "rappels.db"))
DATABASE_URL = lire("DATABASE_URL", "")

# --- Alertes Email (SMTP Gmail) ---
SMTP_HOST = lire("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(lire("SMTP_PORT", "587"))
SMTP_USER = lire("SMTP_USER", "")
SMTP_PASSWORD = lire("SMTP_PASSWORD", "")
ALERT_EMAIL_TO = lire("ALERT_EMAIL_TO", "")


def cle_gemini_ok() -> bool:
    return bool(GEMINI_API_KEY) and not GEMINI_API_KEY.startswith("colle_")


def smtp_ok() -> bool:
    return bool(SMTP_USER and SMTP_PASSWORD and ALERT_EMAIL_TO)