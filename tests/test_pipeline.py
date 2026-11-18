"""Tests unitaires et d'intégration du pipeline « Mon ticket est-il rappelé ? »"""
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from database import BaseDeDonnees
from matcher import STATUT_AUCUN, STATUT_CONFIRME, STATUT_PROBABLE, evaluer_article_contre_rappels
from rappelconso import Rappel
from textes import gs1_valide
from tickets import _parser_articles, detecter_enseigne, extraire_date


def test_detection_enseignes():
    assert detecter_enseigne("BIENVENUE CHEZ CARREFOUR MARKET LYON") == "Carrefour"
    assert detecter_enseigne("E.LECLERC SAS DISTRIBUTION") == "E.Leclerc"
    assert detecter_enseigne("SUPER U CHASSIEU") == "Système U"
    assert detecter_enseigne("INTERMARCHE HYPER") == "Intermarché"
    assert detecter_enseigne("EPICERIE DU COIN") == "Enseigne non reconnue"


def test_extraction_articles_et_anonymisation():
    lignes = [
        "BOUTON D'OR TUILES S 1,47 EUR A",
        "CARTE FIDELITE 9876543210987 0,00 EUR",
        "TOTAL A PAYER 1,47 EUR",
    ]
    articles = _parser_articles(lignes)
    assert len(articles) == 1
    assert articles[0].libelle == "BOUTON D'OR TUILES S"
    assert articles[0].prix == 1.47


def test_validation_gs1():
    assert gs1_valide("3250390001232") is True
    assert gs1_valide("3250390001235") is False
    assert gs1_valide("12345") is False


def test_matcher_confirme_et_probable():
    rappel_test = Rappel(
        id="2025-02-001",
        source="RappelConso",
        marque="Bouton d'Or",
        produit="Tuiles salées nature Bouton d'Or",
        categorie="Alimentation",
        distributeurs="Intermarché",
        zone="France entière",
        date_publication=date(2025, 2, 18),
        date_debut=date(2025, 1, 1),
        date_fin=date(2025, 2, 20),
        lots="Lot 123",
        motif="Corps étranger",
        risques="Blessure",
        conduite_a_tenir="Ne pas consommer",
        lien="[https://rappel.conso.gouv.fr/](https://rappel.conso.gouv.fr/)",
        gtins=frozenset(["03250390001232"]),
    )

    # Cas 1 : Match exact par EAN vérifié -> Rappel confirmé (ROUGE)
    art_ean = {
        "nom_complet": "Tuiles salées Bouton d'Or",
        "marque": "Bouton d'Or",
        "ean": "3250390001232",
        "ean_verifie": True,
        "statut_identification": "confirmee",
    }
    res1 = evaluer_article_contre_rappels(art_ean, date(2025, 2, 14), "Intermarché", [rappel_test])
    assert res1.statut_rappel == STATUT_CONFIRME

    # Cas 2 : Match par marque + nom sans EAN -> Correspondance probable (ORANGE)
    art_nom = {
        "nom_complet": "Tuiles salées nature Bouton d'Or",
        "marque": "Bouton d'Or",
        "ean": None,
        "ean_verifie": False,
        "statut_identification": "probable",
    }
    res2 = evaluer_article_contre_rappels(art_nom, date(2025, 2, 14), "Intermarché", [rappel_test])
    assert res2.statut_rappel == STATUT_PROBABLE

    # Cas 3 : Enseigne différente -> Aucun rappel (VERT)
    res3 = evaluer_article_contre_rappels(art_nom, date(2025, 2, 14), "Lidl", [rappel_test])
    assert res3.statut_rappel == STATUT_AUCUN


def test_base_de_donnees_sqlite():
    with tempfile.TemporaryDirectory() as tmpdir:
        config.SQLITE_PATH = str(Path(tmpdir) / "test.db")
        config.DATABASE_URL = ""
        db = BaseDeDonnees()
        tid = db.enregistrer_ticket_complet(
            nom_fichier="ticket.pdf",
            date_achat="2025-02-14",
            enseigne="Intermarché",
            articles_resultats=[{
                "libelle": "BOUTON D'OR TUILES S",
                "prix": 1.47,
                "nom_complet": "Tuiles salées Bouton d'Or",
                "marque": "Bouton d'Or",
                "statut_identification": "probable",
                "statut_rappel": STATUT_AUCUN,
            }],
        )
        assert tid == 1
        assert len(db.lister_tickets()) == 1
        db.supprimer_ticket(tid)
        assert len(db.lister_tickets()) == 0