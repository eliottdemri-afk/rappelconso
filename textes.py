"""Petits outils partagés : normalisation de texte et validation de codes-barres."""
import re
import unicodedata


def normaliser(s: str) -> str:
    s = unicodedata.normalize("NFKD", (s or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def gs1_valide(code: str) -> bool:
    """Vérifie la clé de contrôle d'un code-barres (EAN-8, UPC-12, EAN-13, GTIN-14)."""
    if not code.isdigit() or len(code) not in (8, 12, 13, 14):
        return False
    chiffres = [int(c) for c in code]
    cle = chiffres.pop()
    total = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(chiffres)))
    return (10 - total % 10) % 10 == cle


def normaliser_gtin(code: str) -> str:
    """Forme unique sur 14 chiffres, pour comparer EAN-13 et GTIN-14."""
    return re.sub(r"\D", "", code or "").zfill(14)
