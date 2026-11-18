"""Extraction multi-enseignes des tickets de caisse PDF et Photos (texte natif + fallback OCR)."""
import io
import re
from dataclasses import dataclass, field
from datetime import date
import fitz  # PyMuPDF
from PIL import Image, ImageOps
import pillow_heif
import pdfplumber
import pytesseract

from textes import normaliser

pillow_heif.register_heif_opener()

# Dictionnaire de détection des enseignes françaises
ENSEIGNES_CONNUES: dict[str, list[str]] = {
    "Intermarché": ["intermarche", "itm", "mousquetaires"],
    "Carrefour": ["carrefour", "carrefour market", "carrefour city", "carrefour express"],
    "E.Leclerc": ["leclerc", "e leclerc", "eleclerc", "scarmor", "galec"],
    "Auchan": ["auchan", "auchan supermarche", "my auchan"],
    "Lidl": ["lidl"],
    "Aldi": ["aldi"],
    "Système U": ["super u", "hyper u", "u express", "marche u", "cooperative u", "systeme u"],
    "Casino": ["casino", "geant casino", "petit casino", "casino shop"],
    "Monoprix": ["monoprix", "monop"],
    "Franprix": ["franprix"],
    "Picard": ["picard"],
    "Grand Frais": ["grand frais"],
}

# Formats de lignes d'articles courants en France :
# 1. "PAQUITO ABC ORANGE B 2,04 EUR A" ou "BOUTON D'OR TUILES S 1,47 EUR"
# 2. "LAIT DEMI ECREME 1,15 €" ou "YAOURT NATURE X4 2,30 A"
PATTERNS_ARTICLES = [
    re.compile(r"^(?P<libelle>[A-Z0-9À-Ÿ\*][A-Z0-9À-Ÿ\s\.\-\'\/\&\+\*%]{2,}?)\s+(?P<prix>-?\d+[,\.]\d{2})\s*(?:EUR|€)?(?:\s+[A-Z0-9]{1,2})?$"),
]

# Rayons à la coupe sans marque ni référence exploitable
GENERIQUE = re.compile(
    r"\bTRAD\b|^(POISSONNERIE|BOUCHERIE|BOULANGERIE|CHARCUTERIE|FRUITS|LEGUMES|COUPE)\b",
    re.IGNORECASE,
)

DATE_REGEX = [
    re.compile(r"\b(\d{2})[/\-\.](\d{2})[/\-\.](20\d{2})\b"),
    re.compile(r"\b(\d{2})[/\-\.](\d{2})[/\-\.](\d{2})\b"),
]

# Marqueurs de fin de liste des articles (tout ce qui suit est du paiement, TVA ou fidélité)
FIN_LISTE_ARTICLES = re.compile(
    r"\b(TOTAL|SOUS[\s\-]?TOTAL|MONTANT\s+DU|MONTANT\s+DÛ|NET\s+A\s+PAYER|A\s+PAYER|"
    r"CB\s+EMV|CB\s+SANS\s+CONTACT|CARTE\s+BANCAIRE|PAIEMENT|REGLEMENT|A\s+RENDRE|RENDU\s+MONNAIE|"
    r"NOMBRE\s+D['\s]?ARTICLES|NB\s+ARTICLES|ARTICLES\s+VENDUS|RECAPITULATIF\s+TVA)\b",
    re.IGNORECASE,
)

# Extraction du compteur officiel X (ex: "NOMBRE D'ARTICLES VENDUS : 9", "9 ARTICLES", "ARTICLES : 9")
COMPTEUR_ARTICLES = re.compile(
    r"(?:NOMBRE\s+D['\s]?ARTICLES(?:\s+VENDUS)?|NB\s+ARTICLES|ARTICLES\s+VENDUS)\s*[:=]?\s*(\d+)"
    r"|\b(\d+)\s+ARTICLES?\b",
    re.IGNORECASE,
)

# Lignes parasites éventuelles au sein même de la liste
EXCLUSIONS_LIGNES = re.compile(
    r"\b(TVA|FIDELITE|CARTE\s+FID|PORTEUR|COMPTE\s+FID|CAGNOTTE|AVANTAGE|REMISE\s+FID|"
    r"IL\s+VOUS\s+RESTAIT|SOLDE|TICKET|DUPLICATA|SIRET|CAISSE|HOTESSE|TEL)\b|\d{2}/\d{2}/\d{4}",
    re.IGNORECASE,
)


@dataclass
class Article:
    libelle: str
    prix: float | None = None
    generique: bool = False


@dataclass
class Ticket:
    nom_fichier: str
    date: date
    enseigne: str
    articles: list[Article] = field(default_factory=list)
    methode_extraction: str = "texte_pdf"


def charger_images_ticket(contenu_bytes: bytes, nom_fichier: str) -> list[Image.Image]:
    nom_lower = nom_fichier.lower()
    images = []

    if nom_lower.endswith(".pdf"):
        doc = fitz.open(stream=contenu_bytes, filetype="pdf")
        for page in doc:
            pix = page.get_pixmap(dpi=250)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            images.append(img.convert("RGB"))
    else:
        img = Image.open(io.BytesIO(contenu_bytes))
        img = ImageOps.exif_transpose(img)
        images.append(img.convert("RGB"))

    return images


def preparer_image_ocr(img: Image.Image) -> Image.Image:
    img_gris = ImageOps.grayscale(img)
    img_contraste = ImageOps.autocontrast(img_gris, cutoff=2)
    return img_contraste


def _extraire_texte_ocr(contenu: bytes, nom_fichier: str) -> str:
    """Exécute l'OCR ligne par ligne (--psm 6) sur un PDF ou une photo."""
    images = charger_images_ticket(contenu, nom_fichier)
    lignes_ocr = []
    for img in images:
        img_prete = preparer_image_ocr(img)
        txt = pytesseract.image_to_string(img_prete, lang="fra", config="--psm 6")
        lignes_ocr.append(txt)
    return "\n".join(lignes_ocr)


def detecter_enseigne(texte: str, nom_fichier: str = "") -> str:
    """Identifie l'enseigne à partir du contenu du ticket ou du nom de fichier."""
    zone_recherche = normaliser(texte[:1200] + " " + nom_fichier)
    for enseigne_officielle, mots_cles in ENSEIGNES_CONNUES.items():
        for mc in mots_cles:
            if re.search(rf"\b{re.escape(mc)}\b", zone_recherche):
                return enseigne_officielle
    return "Enseigne non reconnue"


def extraire_date(texte: str, nom_fichier: str = "") -> date:
    """Extrait la date d'achat depuis le texte ou le nom du fichier (fallback: date du jour)."""
    for source in (texte, nom_fichier):
        for pat in DATE_REGEX:
            for m in pat.finditer(source):
                j, mo, a = int(m.group(1)), int(m.group(2)), int(m.group(3))
                if a < 100:
                    a += 2000
                try:
                    d = date(a, mo, j)
                    if date(2020, 1, 1) <= d <= date.today():
                        return d
                except ValueError:
                    continue
    return date.today()


def extraire_nb_articles_attendu(texte: str) -> int | None:
    """Lit le nombre exact X d'articles vendus inscrit en bas du ticket."""
    for ligne in texte.splitlines():
        m = COMPTEUR_ARTICLES.search(ligne)
        if m:
            val = m.group(1) or m.group(2)
            if val and 1 <= int(val) <= 200:
                return int(val)
    return None


def _parser_articles(lignes: list[str], nb_attendu: int | None = None) -> list[Article]:
    articles: list[Article] = []
    for ligne in lignes:
        l_clean = ligne.strip()
        if len(l_clean) < 4:
            continue

        # 1. Coupure immédiate dès qu'on atteint le total ou le paiement
        if FIN_LISTE_ARTICLES.search(l_clean):
            break

        if EXCLUSIONS_LIGNES.search(l_clean):
            continue

        match_trouve = None
        for pat in PATTERNS_ARTICLES:
            m = pat.match(l_clean)
            if m:
                match_trouve = m
                break

        if not match_trouve:
            continue

        libelle = re.sub(r"\s+", " ", match_trouve.group("libelle")).strip(" .-")
        if len(libelle) < 3 or EXCLUSIONS_LIGNES.search(libelle):
            continue

        prix_str = match_trouve.group("prix").replace(",", ".")
        try:
            prix = float(prix_str)
        except ValueError:
            prix = None

        # Ignorer les lignes à 0,00 € (infos fidélité) ou montants aberrants
        if prix is not None and (prix == 0.0 or prix > 500):
            continue

        # Annulation / remise immédiate négative
        if prix is not None and prix < 0:
            for i in range(len(articles) - 1, -1, -1):
                if articles[i].libelle == libelle and articles[i].prix and abs(articles[i].prix + prix) < 0.01:
                    del articles[i]
                    break
            continue

        articles.append(Article(
            libelle=libelle,
            prix=prix,
            generique=bool(GENERIQUE.search(libelle)),
        ))

    # 2. Si le ticket annonce exactement X articles, on tronque tout résidu au-delà de X
    if nb_attendu is not None and len(articles) > nb_attendu:
        articles = articles[:nb_attendu]

    return articles


def lire_ticket_bytes(contenu: bytes, nom_fichier: str) -> Ticket:
    texte = ""
    methode = "texte_pdf"
    articles: list[Article] = []

    # 1. Si c'est un PDF, on tente d'abord pdfplumber (texte natif)
    if nom_fichier.lower().endswith(".pdf"):
        with pdfplumber.open(io.BytesIO(contenu)) as pdf:
            pages_txt = [p.extract_text() or "" for p in pdf.pages]
            texte = "\n".join(pages_txt).strip()
        if texte:
            nb_attendu = extraire_nb_articles_attendu(texte)
            articles = _parser_articles(texte.splitlines(), nb_attendu=nb_attendu)

    # 2. Si c'est une photo OU un PDF sans texte natif OU un PDF dont les colonnes étaient désalignées -> OCR
    if not articles:
        texte_ocr = _extraire_texte_ocr(contenu, nom_fichier)
        if texte_ocr.strip():
            texte = texte_ocr
            methode = "ocr"
            nb_attendu = extraire_nb_articles_attendu(texte)
            articles = _parser_articles(texte.splitlines(), nb_attendu=nb_attendu)

    enseigne = detecter_enseigne(texte, nom_fichier)
    d = extraire_date(texte, nom_fichier)

    return Ticket(
        nom_fichier=nom_fichier,
        date=d,
        enseigne=enseigne,
        articles=articles,
        methode_extraction=methode,
    )
