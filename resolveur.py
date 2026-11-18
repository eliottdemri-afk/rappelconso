"""Identification des produits et recherche vérifiée de GTIN/EAN (Gemini + Open Food Facts)."""
import json
import re
import time
from typing import Any

import requests
from rapidfuzz import fuzz

import config
from textes import gs1_valide, normaliser

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{modele}:generateContent"
OFF_SEARCH_URL = "https://fr.openfoodfacts.org/cgi/search.pl"

PROMPT_IDENTIFICATION = """Tu es un expert de la grande distribution française.
Analyse ce libellé abrégé extrait d'un ticket de caisse :
- Enseigne : {enseigne}
- Libellé ticket : « {libelle} »
- Prix payé : {prix_str}

Décode les abréviations de caisse (ex: S = Salé ou Sucre, 1/2EC = demi-écrémé, ALS = Alsace, etc.) pour identifier le produit commercial exact.
Règles strictes :
1. N'invente JAMAIS de code-barres (GTIN/EAN). Laisse toujours "ean" à null.
2. Si plusieurs produits très différents peuvent correspondre ou si le libellé est trop flou, indique "ambigu": true et liste les candidats possibles.
3. Réponds UNIQUEMENT avec un objet JSON valide, sans texte ni balise autour :
{{
  "nom_complet": "Marque + Désignation exacte + parfum/variété + conditionnement",
  "marque": "Marque seule ou null",
  "format": "Poids ou volume (ex: 12x25cl, 150g) ou null",
  "categorie": "Catégorie alimentaire ou non-alimentaire",
  "reference_fabricant": "Référence éventuelle ou null",
  "ambigu": false,
  "candidats": ["Candidat 1", "Candidat 2"]
}}"""


class ErreurGemini(Exception):
    pass


def _extraire_json(texte: str) -> dict[str, Any]:
    texte = re.sub(r"```(?:json)?", "", texte).strip()
    debut, fin = texte.find("{"), texte.rfind("}")
    if debut < 0 or fin < debut:
        raise ValueError("Réponse sans objet JSON exploitable")
    return json.loads(texte[debut:fin + 1])


def interroger_gemini_produit(libelle: str, enseigne: str, prix: float | None = None) -> dict[str, Any]:
    """Appelle l'API Gemini pour décoder un libellé de ticket de caisse."""
    if not config.cle_gemini_ok():
        raise ErreurGemini("Clé API Gemini absente ou non configurée.")

    prix_str = f"{prix:.2f} €" if prix is not None else "Non précisé"
    prompt = PROMPT_IDENTIFICATION.format(enseigne=enseigne, libelle=libelle, prix_str=prix_str)

    corps = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.1},
    }
    entetes = {"x-goog-api-key": config.GEMINI_API_KEY, "Content-Type": "application/json"}
    url = GEMINI_URL.format(modele=config.GEMINI_MODEL)

    dernier_msg = ""
    for tentative in range(4):
        try:
            r = requests.post(url, headers=entetes, json=corps, timeout=45)
        except requests.RequestException as exc:
            dernier_msg = str(exc)
            time.sleep(3 * (tentative + 1))
            continue

        if r.status_code in (429, 500, 503):
            dernier_msg = f"HTTP {r.status_code} : {r.text[:200]}"
            time.sleep(5 * (tentative + 1))
            continue

        if r.status_code != 200:
            raise ErreurGemini(f"Erreur API Gemini HTTP {r.status_code} : {r.text[:250]}")

        data = r.json()
        try:
            cand = data["candidates"][0]
            texte = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))
            return _extraire_json(texte)
        except (KeyError, IndexError, ValueError, json.JSONDecodeError) as exc:
            raise ErreurGemini(f"Réponse Gemini illisible : {exc}")

    raise ErreurGemini(f"Quota ou serveur Gemini indisponible ({dernier_msg})")


def rechercher_gtin_openfoodfacts(nom_complet: str, marque: str | None = None) -> dict[str, Any] | None:
    """Recherche un produit sur Open Food Facts et valide strictement son EAN-13."""
    if not nom_complet:
        return None

    requete = f"{marque} {nom_complet}".strip() if marque and normaliser(marque) not in normaliser(nom_complet) else nom_complet
    params = {
        "search_terms": requete,
        "search_simple": 1,
        "action": "process",
        "json": 1,
        "page_size": 5,
        "fields": "code,product_name,brands,quantity,url",
    }
    try:
        r = requests.get(
            OFF_SEARCH_URL,
            params=params,
            headers={"User-Agent": "MonTicketEstIlRappele/1.0 (contact@example.com)"},
            timeout=12,
        )
        if r.status_code != 200:
            return None
        produits = r.json().get("products", [])
    except (requests.RequestException, ValueError):
        return None

    meilleur = None
    meilleur_score = 0
    cible_norm = normaliser(nom_complet)

    for p in produits:
        code = re.sub(r"\D", "", str(p.get("code") or ""))
        if len(code) != 13 or not gs1_valide(code):
            continue

        nom_off = f"{p.get('brands', '')} {p.get('product_name', '')} {p.get('quantity', '')}".strip()
        if not nom_off:
            continue

        score = fuzz.token_set_ratio(cible_norm, normaliser(nom_off))
        if marque:
            score_marque = fuzz.partial_ratio(normaliser(marque), normaliser(p.get("brands", "")))
            if score_marque < 70:
                continue

        if score > meilleur_score and score >= 75:
            meilleur_score = score
            meilleur = {
                "ean": code,
                "nom_off": nom_off,
                "score": score,
                "source_url": p.get("url") or f"[https://fr.openfoodfacts.org/produit/](https://fr.openfoodfacts.org/produit/){code}",
            }

    return meilleur


def identifier_article(libelle: str, enseigne: str, prix: float | None = None, pause: float = 2.0) -> dict[str, Any]:
    """
    Pipeline complet d'identification d'un article :
    1. Interprétation sémantique par Gemini
    2. Recherche & vérification du GTIN/EAN-13 sur Open Food Facts
    Retourne un dictionnaire normalisé avec statut_identification in {'confirmee', 'probable', 'impossible'}.
    """
    try:
        analyse = interroger_gemini_produit(libelle, enseigne, prix)
        time.sleep(pause)
    except ErreurGemini as err:
        return {
            "libelle": libelle,
            "enseigne": enseigne,
            "prix": prix,
            "nom_complet": None,
            "marque": None,
            "format": None,
            "categorie": None,
            "reference_fabricant": None,
            "ean": None,
            "ean_verifie": False,
            "statut_identification": "impossible",
            "candidats": [],
            "source_identification": None,
            "erreur": str(err),
        }

    nom_complet = (analyse.get("nom_complet") or "").strip() or None
    marque = (analyse.get("marque") or "").strip() or None
    fmt = (analyse.get("format") or "").strip() or None
    categorie = (analyse.get("categorie") or "").strip() or None
    ref_fab = (analyse.get("reference_fabricant") or "").strip() or None
    ambigu = bool(analyse.get("ambigu"))
    candidats = analyse.get("candidats") or []

    if not nom_complet:
        return {
            "libelle": libelle,
            "enseigne": enseigne,
            "prix": prix,
            "nom_complet": None,
            "marque": marque,
            "format": fmt,
            "categorie": categorie,
            "reference_fabricant": ref_fab,
            "ean": None,
            "ean_verifie": False,
            "statut_identification": "impossible",
            "candidats": candidats,
            "source_identification": "Gemini (libellé insuffisant)",
            "erreur": None,
        }

    off_match = rechercher_gtin_openfoodfacts(nom_complet, marque)
    ean = off_match["ean"] if off_match else None
    ean_verifie = bool(off_match)
    source_url = off_match["source_url"] if off_match else "Identification par API Gemini, EAN non trouvé sur Open Food Facts"

    if ean_verifie and not ambigu:
        statut_id = "confirmee"
    else:
        statut_id = "probable"

    return {
        "libelle": libelle,
        "enseigne": enseigne,
        "prix": prix,
        "nom_complet": nom_complet,
        "marque": marque,
        "format": fmt,
        "categorie": categorie,
        "reference_fabricant": ref_fab,
        "ean": ean,
        "ean_verifie": ean_verifie,
        "statut_identification": statut_id,
        "candidats": candidats,
        "source_identification": source_url,
        "erreur": None,
    }