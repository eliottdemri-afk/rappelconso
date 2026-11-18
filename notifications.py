"""Système d'alertes par email via SMTP Gmail avec aperçu et déduplication."""
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import config
from database import BaseDeDonnees


def construire_contenu_mail(alertes: list[dict[str, Any]]) -> tuple[str, str]:
    """Construit le sujet et le corps texte d'un email d'alerte."""
    nb_conf = sum(1 for a in alertes if a["statut_rappel"] == "rappel_confirme")
    nb_prob = sum(1 for a in alertes if a["statut_rappel"] == "correspondance_probable")

    sujet = f"[Alerte Rappel Produit] {nb_conf} rappel(s) confirmé(s), {nb_prob} à vérifier"

    lignes = [
        "Bonjour,",
        "",
        "L'application « Mon ticket est-il rappelé ? » a détecté des alertes sur vos achats :",
        "",
    ]

    for a in alertes:
        r = a.get("rappel_detail") or {}
        niveau = (
            "RAPPEL CONFIRMÉ (ROUGE)"
            if a["statut_rappel"] == "rappel_confirme"
            else "CORRESPONDANCE PROBABLE À VÉRIFIER (ORANGE)"
        )
        lignes.extend([
            "------------------------------------------------------------",
            f"Niveau de certitude : {niveau}",
            f"Produit identifié    : {a.get('nom_complet') or a.get('libelle')}",
            f"Marque               : {a.get('marque') or r.get('marque') or 'Non précisée'}",
            f"GTIN / EAN           : {a.get('ean') or 'Non disponible'}",
            f"Motif du rappel      : {r.get('motif', 'Non précisé')}",
            f"Risques encourus     : {r.get('risques', 'Non précisés')}",
            f"Lots concernés       : {r.get('lots', 'Voir fiche officielle')}",
            f"Fiche officielle     : {r.get('lien', 'https://rappel.conso.gouv.fr/')}",
            "Recommandation       : Vérifiez impérativement le numéro de lot et la date limite sur l'emballage avant consommation.",
            "",
        ])

    lignes.append("Ceci est un message automatique généré par « Mon ticket est-il rappelé ? ».")
    return sujet, "\n".join(lignes)


def envoyer_alertes_mail(alertes: list[dict[str, Any]], db: BaseDeDonnees) -> tuple[bool, str]:
    """Envoie un e-mail uniquement pour les nouvelles alertes non encore notifiées."""
    if not alertes:
        return False, "Aucune alerte à envoyer."

    nouvelles = []
    for a in alertes:
        r_id = (a.get("rappel_detail") or {}).get("id") or a.get("rappel_id") or "inconnu"
        cle = f"{a.get('enseigne')}|{a.get('date_achat')}|{a.get('libelle')}|{r_id}|{a['statut_rappel']}"
        if not db.alerte_deja_notifiee(cle):
            a["_cle_notif"] = cle
            nouvelles.append(a)

    if not nouvelles:
        return False, "Toutes ces alertes ont déjà fait l'objet d'un envoi par mail."

    sujet, corps = construire_contenu_mail(nouvelles)

    if not config.smtp_ok():
        return False, "SMTP non configuré dans les variables d'environnement (aperçu disponible)."

    msg = MIMEMultipart()
    msg["From"] = config.SMTP_USER
    msg["To"] = config.ALERT_EMAIL_TO
    msg["Subject"] = sujet
    msg.attach(MIMEText(corps, "plain", "utf-8"))

    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20) as server:
            server.starttls()
            server.login(config.SMTP_USER, config.SMTP_PASSWORD)
            server.send_message(msg)
        statut = "Envoyé"
        succes = True
        message_retour = f"E-mail envoyé à {config.ALERT_EMAIL_TO} ({len(nouvelles)} alerte(s))."
    except Exception as exc:
        statut = f"Erreur : {exc}"
        succes = False
        message_retour = f"Échec de l'envoi SMTP : {exc}"

    for a in nouvelles:
        db.enregistrer_notification(a["_cle_notif"], config.ALERT_EMAIL_TO, sujet, statut)

    return succes, message_retour