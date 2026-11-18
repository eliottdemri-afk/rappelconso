"""Moteur de rapprochement fiable entre articles identifiés et fiches de rappel."""
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from rapidfuzz import fuzz

from rappelconso import Rappel
from textes import normaliser, normaliser_gtin

# Les 4 statuts officiels de rappel demandés
STATUT_CONFIRME = "rappel_confirme"        # ROUGE
STATUT_PROBABLE = "correspondance_probable" # ORANGE
STATUT_AUCUN = "aucun_rappel"              # VERT
STATUT_NON_VERIFIABLE = "non_verifiable"   # GRIS


@dataclass
class ResultatVerification:
    statut_rappel: str
    rappel: Rappel | None = None
    score_marque: int = 0
    score_produit: int = 0
    explication: str = ""
    hors_periode: bool = False


def _score_marque(a: str, b: str) -> int:
    a, b = normaliser(a), normaliser(b)
    if not a or not b:
        return 0
    if a == b:
        return 100
    ta, tb = set(a.split()), set(b.split())
    petit, grand = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    if petit <= grand and (len(petit) >= 2 or max(len(t) for t in petit) >= 5):
        return 95
    return int(fuzz.ratio(a, b))


def _periode_coherente(date_achat: date, r: Rappel, tolerance_jours: int = 20) -> bool:
    tol = timedelta(days=tolerance_jours)
    if r.date_debut and date_achat < (r.date_debut - tol):
        return False
    if r.date_fin:
        if date_achat > (r.date_fin + tol):
            return False
    elif r.date_publication and date_achat > (r.date_publication + timedelta(days=90)):
        return False
    return True


def _enseigne_coherente(r: Rappel, enseigne: str) -> bool:
    if not enseigne or enseigne == "Enseigne non reconnue" or not r.distributeurs:
        return True
    dist = normaliser(r.distributeurs)
    ens = normaliser(enseigne)
    # Correspondances larges courantes sur RappelConso
    generiques = ("tous", "france entiere", "ensemble", "grandes surfaces", "gms", "national")
    if any(g in dist for g in generiques):
        return True
    if "systeme u" in ens and ("super u" in dist or "hyper u" in dist or "u express" in dist or "systeme u" in dist):
        return True
    return ens in dist


def evaluer_article_contre_rappels(
    article_identifie: dict[str, Any],
    date_achat: date,
    enseigne: str,
    rappels: list[Rappel],
    seuil_marque: int = 85,
    seuil_produit: int = 60,
) -> ResultatVerification:
    """
    Évalue un article identifié face aux fiches de rappel en distinguant strictement :
    1. rappel_confirme (ROUGE)
    2. correspondance_probable (ORANGE)
    3. aucun_rappel (VERT)
    4. non_verifiable (GRIS)
    """
    if article_identifie.get("statut_identification") == "impossible" or not article_identifie.get("nom_complet"):
        return ResultatVerification(
            statut_rappel=STATUT_NON_VERIFIABLE,
            explication="Libellé insuffisant pour identifier le produit de manière fiable.",
        )

    ean_brut = article_identifie.get("ean")
    ean_verifie = bool(article_identifie.get("ean_verifie"))
    ean_norm = normaliser_gtin(ean_brut) if (ean_brut and ean_verifie) else None

    # 1. Recherche exacte par GTIN/EAN vérifié
    if ean_norm:
        for r in rappels:
            if ean_norm in r.gtins:
                hors_per = not _periode_coherente(date_achat, r)
                note_lot = "Attention : le ticket de caisse ne mentionne pas le numéro de lot, vérifiez-le sur l'emballage."
                if hors_per:
                    return ResultatVerification(
                        statut_rappel=STATUT_PROBABLE,
                        rappel=r,
                        score_marque=100,
                        score_produit=100,
                        hors_periode=True,
                        explication=f"Code-barres EAN identique, mais date d'achat ({date_achat:%d/%m/%Y}) hors période déclarée. {note_lot}",
                    )
                return ResultatVerification(
                    statut_rappel=STATUT_CONFIRME,
                    rappel=r,
                    score_marque=100,
                    score_produit=100,
                    explication=f"Correspondance exacte par code-barres EAN ({ean_brut}). {note_lot}",
                )

    # 2. Recherche par marque + nom de produit + enseigne + période
    nom_complet = article_identifie["nom_complet"]
    marque = article_identifie.get("marque") or (nom_complet.split()[0] if nom_complet else "")

    meilleur_candidat: ResultatVerification | None = None
    meilleur_score_global = 0

    for r in rappels:
        if not _enseigne_coherente(r, enseigne):
            continue
        if not _periode_coherente(date_achat, r):
            continue

        sm = _score_marque(marque, r.marque)
        if sm < seuil_marque:
            continue

        sp = int(fuzz.token_set_ratio(normaliser(nom_complet), normaliser(r.produit))) if r.produit else 0
        if sp < seuil_produit:
            continue

        score_global = (sm + sp) // 2
        if score_global > meilleur_score_global:
            meilleur_score_global = score_global
            # Sans GTIN identique confirmé, une correspondance textuelle reste "probable" (vérification emballage requise)
            meilleur_candidat = ResultatVerification(
                statut_rappel=STATUT_PROBABLE,
                rappel=r,
                score_marque=sm,
                score_produit=sp,
                explication=(
                    f"Correspondance par marque ({sm}%) et désignation ({sp}%). "
                    "Le ticket ne permet pas de lire le lot ni le GTIN exact : vérification requise sur l'emballage."
                ),
            )

    if meilleur_candidat:
        return meilleur_candidat

    return ResultatVerification(
        statut_rappel=STATUT_AUCUN,
        explication="Aucune correspondance trouvée dans les sources officielles consultées à ce jour (ne constitue pas une garantie absolue).",
    )