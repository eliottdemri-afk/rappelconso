"""Accès aux sources officielles de rappels produits (RappelConso - data.economie.gouv.fr)."""
import json
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import requests

from textes import gs1_valide, normaliser_gtin

BASE_API_GOUV = "https://data.economie.gouv.fr/api/explore/v2.1/catalog/datasets"
DATASET_RAPPELCONSO = "rappelconso-v2-gtin-espaces"
CODE_13_14 = re.compile(r"(?<!\d)(\d{13,14})(?!\d)")


@dataclass
class Rappel:
    id: str
    source: str
    marque: str
    produit: str
    categorie: str
    distributeurs: str
    zone: str
    date_publication: date | None
    date_debut: date | None
    date_fin: date | None
    lots: str
    motif: str
    risques: str
    conduite_a_tenir: str
    lien: str
    gtins: frozenset = frozenset()


class SourceRappels(ABC):
    """Interface extensible permettant d'ajouter d'autres sources officielles ultérieurement."""

    @abstractmethod
    def charger_rappels(self, depuis: date) -> list[Rappel]:
        pass


def _txt(rec: dict, *cles: str) -> str:
    for c in cles:
        v = rec.get(c)
        if v:
            return str(v).strip()
    return ""


def _date(valeur) -> date | None:
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(valeur or ""))
    return date(int(m[1]), int(m[2]), int(m[3])) if m else None


def _nettoyer(texte: str) -> str:
    texte = re.sub(r"\[[^\]]*\]", " ", texte)
    return re.sub(r"\s+", " ", texte).strip()


def _extraire_gtins(rec: dict) -> frozenset:
    trouves = set()
    for cle, valeur in rec.items():
        k = cle.lower()
        if "gtin" in k or k in ("identification_produits", "modeles_ou_references"):
            texte = valeur if isinstance(valeur, str) else json.dumps(valeur, ensure_ascii=False)
            for code in CODE_13_14.findall(texte):
                if gs1_valide(code):
                    trouves.add(normaliser_gtin(code))
    return frozenset(trouves)


def parser_rappel_gouv(rec: dict) -> Rappel:
    produit = " ".join(filter(None, [
        _txt(rec, "nom_produit", "noms_des_modeles_ou_references"),
        _txt(rec, "modeles_ou_references"),
        _txt(rec, "sous_categorie_produit"),
    ]))
    return Rappel(
        id=_txt(rec, "numero_fiche", "rappel_guid", "id", "reference_fiche"),
        source="RappelConso (Officiel)",
        marque=_txt(rec, "marque_produit"),
        produit=_nettoyer(produit),
        categorie=_txt(rec, "categorie_produit"),
        distributeurs=_txt(rec, "distributeurs"),
        zone=_txt(rec, "zone_geographique_de_vente"),
        date_publication=_date(rec.get("date_publication")),
        date_debut=_date(rec.get("date_debut_commercialisation")),
        date_fin=_date(rec.get("date_date_fin_commercialisation") or rec.get("date_fin_commercialisation")),
        lots=_txt(rec, "numero_de_lots", "identification_produits") or "Non précisé (voir fiche)",
        motif=_txt(rec, "motif_rappel") or "Non précisé",
        risques=_txt(rec, "risques_encourus") or "Non précisé",
        conduite_a_tenir=_txt(rec, "conduites_a_tenir_par_le_consommateur", "preconisations_sanitaires") or "Ne plus consommer / Rapporter au point de vente",
        lien=_txt(rec, "lien_vers_la_fiche_rappel") or "[https://rappel.conso.gouv.fr/](https://rappel.conso.gouv.fr/)",
        gtins=_extraire_gtins(rec),
    )


class SourceRappelConsoGouv(SourceRappels):
    """Implémentation officielle utilisant l'API Open Data de l'État français."""

    def __init__(self, dataset: str = DATASET_RAPPELCONSO, cache_dir: Path = Path(".cache"), duree_cache_h: float = 2.0):
        self.dataset = dataset
        self.cache_dir = cache_dir
        self.duree_cache_h = duree_cache_h

    def charger_rappels(self, depuis: date) -> list[Rappel]:
        self.cache_dir.mkdir(exist_ok=True)
        cache = self.cache_dir / f"{self.dataset}_{depuis.isoformat()}.json"
        if cache.exists() and (time.time() - cache.stat().st_mtime) < self.duree_cache_h * 3600:
            brut = json.loads(cache.read_text(encoding="utf-8"))
        else:
            url = f"{BASE_API_GOUV}/{self.dataset}/records"
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) MonTicketRappele/1.0"}
            brut = []
            offset = 0
            limit = 100
            while True:
                params = {
                    "where": f"date_publication >= date'{depuis.isoformat()}'",
                    "limit": limit,
                    "offset": offset,
                    "order_by": "date_publication DESC",
                }
                r = requests.get(url, params=params, headers=headers, timeout=30)
                r.raise_for_status()
                data = r.json()
                batch = data.get("results", [])
                brut.extend(batch)
                if len(batch) < limit or offset + limit >= data.get("total_count", 0):
                    break
                offset += limit
            cache.write_text(json.dumps(brut, ensure_ascii=False), encoding="utf-8")
        return [parser_rappel_gouv(rec) for rec in brut]     


def recuperer_tous_les_rappels(depuis: date) -> list[Rappel]:
    """Agrège les rappels depuis toutes les sources officielles configurées."""
    sources: list[SourceRappels] = [SourceRappelConsoGouv()]
    rappels: list[Rappel] = []
    for src in sources:
        rappels.extend(src.charger_rappels(depuis))
    return rappels