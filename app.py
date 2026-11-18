"""Application web Streamlit : « Mon ticket est-il rappelé ? »"""
import json
from dataclasses import asdict
from datetime import date, timedelta

import streamlit as st

import config
from database import BaseDeDonnees
from matcher import (
    STATUT_AUCUN,
    STATUT_CONFIRME,
    STATUT_NON_VERIFIABLE,
    STATUT_PROBABLE,
    evaluer_article_contre_rappels,
)
from notifications import construire_contenu_mail, envoyer_alertes_mail
from rappelconso import Rappel, recuperer_tous_les_rappels
from resolveur import identifier_article
from textes import normaliser
from tickets import Article, Ticket, lire_ticket_bytes

st.set_page_config(
    page_title="Mon ticket est-il rappelé ?",
    page_icon="🧾",
    layout="wide",
)

# Style sobre pour les cartes et badges de couleur
st.markdown("""
<style>
.carte-produit {
    border-radius: 10px;
    padding: 16px 20px;
    margin-bottom: 14px;
    border-left: 8px solid #888;
    background-color: #f8f9fa;
    color: #1a1a1a !important;
    box-shadow: 0 2px 6px rgba(0, 0, 0, 0.25);
}
.carte-produit strong,
.carte-produit b,
.carte-produit div {
    color: #1a1a1a !important;
}
.carte-produit code {
    background-color: #212529 !important;
    color: #ffffff !important;
    padding: 2px 6px;
    border-radius: 4px;
}
.carte-rouge { border-left-color: #d93025; background-color: #fad2cf; }
.carte-orange { border-left-color: #f29900; background-color: #feefc3; }
.carte-vert { border-left-color: #188038; background-color: #ceead6; }
.carte-gris { border-left-color: #5f6368; background-color: #e8eaed; }
.badge {
    display: inline-block;
    padding: 4px 12px;
    border-radius: 12px;
    font-size: 0.85em;
    font-weight: 700;
    color: #ffffff !important;
}
.badge-rouge { background-color: #c5221f; }
.badge-orange { background-color: #d56e00; }
.badge-vert { background-color: #137333; }
.badge-gris { background-color: #4a4e52; }
</style>
""", unsafe_allow_html=True)


@st.cache_resource
def get_db() -> BaseDeDonnees:
    return BaseDeDonnees()


def verifier_authentification() -> bool:
    if st.session_state.get("authentifie"):
        return True

    st.title("🧾 Mon ticket est-il rappelé ?")
    st.info("Application privée de vérification automatique des rappels produits sur tickets de caisse.")
    with st.form("login_form"):
        mdp = st.text_input("Mot de passe d'accès", type="password")
        valider = st.form_submit_button("Se connecter")
        if valider:
            if mdp == config.APP_PASSWORD:
                st.session_state["authentifie"] = True
                st.rerun()
            else:
                st.error("Mot de passe incorrect.")
    return False


def _serialiser_rappel(r: Rappel | None) -> dict | None:
    if not r:
        return None
    d = asdict(r)
    d["date_publication"] = r.date_publication.isoformat() if r.date_publication else None
    d["date_debut"] = r.date_debut.isoformat() if r.date_debut else None
    d["date_fin"] = r.date_fin.isoformat() if r.date_fin else None
    d["gtins"] = sorted(list(r.gtins))
    return d


def traiter_ticket(ticket: Ticket, db: BaseDeDonnees, est_simulation: bool = False, rappel_simule: Rappel | None = None) -> list[dict]:
    """Exécute le pipeline complet (identification + recherche rappels + sauvegarde DB)."""
    depuis = ticket.date - timedelta(days=30)
    try:
        rappels = recuperer_tous_les_rappels(depuis)
    except Exception as exc:
        st.warning(f"Impossible de joindre l'API RappelConso pour le moment ({exc}).")
        rappels = []

    if est_simulation and rappel_simule:
        rappels = [rappel_simule] + rappels

    resultats_articles = []
    for art in ticket.articles:
        if art.generique:
            res_id = {
                "libelle": art.libelle,
                "enseigne": ticket.enseigne,
                "prix": art.prix,
                "nom_complet": None,
                "marque": None,
                "format": None,
                "categorie": "Rayon traditionnel / vrac",
                "reference_fabricant": None,
                "ean": None,
                "ean_verifie": False,
                "statut_identification": "impossible",
                "candidats": [],
                "source_identification": "Article générique sans référence",
            }
        else:
            cle_cache = f"{normaliser(ticket.enseigne)}|{normaliser(art.libelle)}"
            res_id = db.lire_cache_produit(cle_cache)
            if not res_id:
                res_id = identifier_article(art.libelle, ticket.enseigne, art.prix)
                if res_id["statut_identification"] != "impossible" and not res_id.get("erreur"):
                    db.sauver_cache_produit(cle_cache, res_id)
            else:
                res_id["prix"] = art.prix
                res_id["enseigne"] = ticket.enseigne

        verif = evaluer_article_contre_rappels(res_id, ticket.date, ticket.enseigne, rappels)
        rappel_dict = _serialiser_rappel(verif.rappel)

        ligne_complete = {
            **res_id,
            "date_achat": ticket.date.isoformat(),
            "statut_rappel": verif.statut_rappel,
            "rappel_id": verif.rappel.id if verif.rappel else None,
            "rappel_detail": rappel_dict,
            "explication_rappel": verif.explication,
        }
        resultats_articles.append(ligne_complete)

    db.enregistrer_ticket_complet(
        nom_fichier=ticket.nom_fichier,
        date_achat=ticket.date.isoformat(),
        enseigne=ticket.enseigne,
        articles_resultats=resultats_articles,
        est_simulation=est_simulation,
    )
    return resultats_articles


def afficher_carte_article(art: dict):
    statut = art["statut_rappel"]
    if statut == STATUT_CONFIRME:
        css_carte, css_badge, lib_statut = "carte-rouge", "badge-rouge", "ROUGE — Rappel confirmé"
    elif statut == STATUT_PROBABLE:
        css_carte, css_badge, lib_statut = "carte-orange", "badge-orange", "ORANGE — Correspondance probable"
    elif statut == STATUT_AUCUN:
        css_carte, css_badge, lib_statut = "carte-vert", "badge-vert", "VERT — Aucun rappel identifié"
    else:
        css_carte, css_badge, lib_statut = "carte-gris", "badge-gris", "GRIS — Non vérifiable"

    map_id = {
        "confirmee": "Identification confirmée (GTIN vérifié)",
        "probable": "Identification probable",
        "impossible": "Identification impossible",
    }
    confiance_id = map_id.get(art.get("statut_identification", ""), "Non évaluée")
    prix_aff = f"{art['prix']:.2f} €" if art.get("prix") is not None else "Prix non lu"
    ean_aff = art.get("ean") or "Non trouvé"
    nom_aff = art.get("nom_complet") or "Produit non identifié (produit frais ou générique)"
    source_aff = art.get("source_identification") or "Aucune"

    st.markdown(f"""
    <div class="carte-produit {css_carte}">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
            <strong style="font-size:1.1em;">{nom_aff}</strong>
            <span class="badge {css_badge}">{lib_statut}</span>
        </div>
        <div><b>Libellé ticket :</b> <code>{art.get('libelle') or art.get('libelle_original')}</code> | <b>Enseigne :</b> {art.get('enseigne', 'Inconnue')} | <b>Prix :</b> {prix_aff}</div>
        <div><b>GTIN / EAN :</b> {ean_aff} | <b>Identification :</b> {confiance_id}</div>
        <div style="font-size:0.9em; color:#4810; margin-top:4px;"><b>Source :</b> {source_aff}</div>
    </div>
    """, unsafe_allow_html=True)

    r = art.get("rappel_detail")
    if isinstance(r, str):
        r = json.loads(r)

    if statut == STATUT_CONFIRME and r:
        with st.container(border=True):
            st.error("ALERTE RAPPEL OFFICIEL — Action requise")
            st.write(f"**Produit officiel :** {r.get('marque', '')} - {r.get('produit', '')}")
            st.write(f"**Motif du rappel :** {r.get('motif', 'Non précisé')}")
            st.write(f"**Risques encourus :** {r.get('risques', 'Non précisés')}")
            st.write(f"**Lots concernés :** {r.get('lots', 'Voir fiche')}")
            st.write(f"**Période de vente :** {r.get('date_debut') or '?'} au {r.get('date_fin') or '?'}")
            st.write(f"**Conduite à tenir :** {r.get('conduite_a_tenir', 'Ne plus consommer')}")
            st.markdown(f"[Consulter la fiche officielle sur RappelConso]({r.get('lien', 'https://rappel.conso.gouv.fr/')})")

    elif statut == STATUT_PROBABLE:
        st.warning("Correspondance possible : vérifiez la marque, le GTIN et le numéro de lot sur l'emballage avant de conclure.")
        if r:
            with st.expander("Voir les détails de la fiche RappelConso suspectée"):
                st.write(f"**Produit officiel :** {r.get('marque', '')} - {r.get('produit', '')}")
                st.write(f"**Motif :** {r.get('motif', '')} | **Risques :** {r.get('risques', '')}")
                st.write(f"**Lots concernés :** {r.get('lots', '')}")
                st.write(f"**Distributeurs :** {r.get('distributeurs', '')}")
                st.markdown(f"[Lien vers la fiche officielle]({r.get('lien', 'https://rappel.conso.gouv.fr/')})")


def afficher_compteurs(articles: list[dict]):
    nb_conf = sum(1 for a in articles if a["statut_rappel"] == STATUT_CONFIRME)
    nb_prob = sum(1 for a in articles if a["statut_rappel"] == STATUT_PROBABLE)
    nb_aucun = sum(1 for a in articles if a["statut_rappel"] == STATUT_AUCUN)
    nb_non_verif = sum(1 for a in articles if a["statut_rappel"] == STATUT_NON_VERIFIABLE)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("🔴 Alertes confirmées", nb_conf)
    c2.metric("🟠 Produits à vérifier", nb_prob)
    c3.metric("🟢 Sans rappel identifié", nb_aucun)
    c4.metric("⚪ Non vérifiables", nb_non_verif)


def main():
    if not verifier_authentification():
        return

    db = get_db()
    st.title("🧾 Mon ticket est-il rappelé ?")
    st.caption("Vérification automatique des produits achetés face aux rappels officiels français (RappelConso).")

    with st.sidebar:
        st.subheader("État du système")
        st.write("API Gemini :", "Configurée" if config.cle_gemini_ok() else "❌ Clé manquante")
        st.write("Modèle IA :", f"`{config.GEMINI_MODEL}`")
        st.write("Alertes Mail :", "SMTP prêt" if config.smtp_ok() else "⚠️ Aperçu seul (SMTP non configuré)")
        st.divider()
        st.subheader("Mode Démonstration")
        st.caption("Permet de visualiser le déclenchement d'une alerte et l'aperçu d'e-mail sans attendre un rappel réel.")
        if st.button("Simuler un ticket avec rappel (Démo)"):
            ticket_simu = Ticket(
                nom_fichier="[SIMULATION] ticket_intermarche_demo.pdf",
                date=date(2025, 2, 28),
                enseigne="Intermarché",
                articles=[
                    Article(libelle="BOUTON D'OR CHIPS SEL VINAIGRE", prix=1.47),
                    Article(libelle="PAQUITO ABC ORANGE B", prix=2.04),
                ],
            )
            rappel_demo = Rappel(
                id="2025-02-0268-SIMU",
                source="SIMULATION (inspiré d'une fiche RappelConso réelle)",
                marque="Bouton d'Or",
                produit="Chips saveur sel et vinaigre 135g Bouton d'Or",
                categorie="Alimentation",
                distributeurs="Intermarché",
                zone="France entière",
                date_publication=date(2025, 3, 3),
                date_debut=date(2025, 1, 15),
                date_fin=date(2025, 3, 2),
                lots="Tous les lots — DDM 08/2025",
                motif="[SIMULATION] Présence d'allergène non déclaré / corps étranger",
                risques="[SIMULATION] Risque allergique ou blessure",
                conduite_a_tenir="[SIMULATION] Ne plus consommer et rapporter le produit au point de vente",
                lien="https://rappel.conso.gouv.fr/",
            )
            with st.spinner("Exécution de la démonstration..."):
                res = traiter_ticket(ticket_simu, db, est_simulation=True, rappel_simule=rappel_demo)
                st.session_state["derniers_resultats"] = res
                st.info("Mode SIMULATION activé : un produit en alerte (orange) et un produit sain (vert) sont affichés.")

    onglet1, onglet2, onglet3 = st.tabs(["📤 ANALYSER", "🗂️ MES TICKETS", "🔔 ALERTES"])

    # ==================== ONGLET 1 : ANALYSER ====================
    with onglet1:
        st.subheader("Importer vos tickets de caisse (PDF)")
        fichiers = st.file_uploader(
            "Glissez-déposez un ou plusieurs tickets au format PDF (les fichiers originaux ne sont jamais conservés)",
            type=["pdf"],
            accept_multiple_files=True,
        )

        if st.button("Analyser mes tickets", type="primary", disabled=not fichiers):
            tous_resultats = []
            barre = st.progress(0, text="Lecture des tickets en cours...")
            total = len(fichiers)

            for idx, f in enumerate(fichiers):
                contenu = f.read()
                ticket = lire_ticket_bytes(contenu, f.name)
                barre.progress((idx + 0.3) / total, text=f"Analyse de {f.name} ({ticket.enseigne} - {len(ticket.articles)} articles)...")
                if not ticket.articles:
                    st.warning(f"Aucun article n'a pu être extrait de `{f.name}` (format non reconnu ou ticket vide).")
                    continue
                res_ticket = traiter_ticket(ticket, db, est_simulation=False)
                tous_resultats.extend(res_ticket)
                barre.progress((idx + 1) / total, text=f"{f.name} terminé.")

            st.session_state["derniers_resultats"] = tous_resultats

            # Envoi automatique d'e-mail en cas d'alerte rouge ou orange
            alertes_a_notifier = [
                a for a in tous_resultats if a["statut_rappel"] in (STATUT_CONFIRME, STATUT_PROBABLE)
            ]
            if alertes_a_notifier:
                ok, info_mail = envoyer_alertes_mail(alertes_a_notifier, db)
                if ok:
                    st.success(info_mail)
                else:
                    st.info(info_mail)

        resultats = st.session_state.get("derniers_resultats")
        if resultats:
            st.divider()
            afficher_compteurs(resultats)
            st.divider()

            alertes_detectees = [a for a in resultats if a["statut_rappel"] in (STATUT_CONFIRME, STATUT_PROBABLE)]
            if alertes_detectees:
                with st.expander("✉️ Aperçu de l'e-mail d'alerte généré"):
                    sujet, corps = construire_contenu_mail(alertes_detectees)
                    st.text_input("Sujet", value=sujet, disabled=True)
                    st.text_area("Contenu du message", value=corps, height=220, disabled=True)

            for art in resultats:
                afficher_carte_article(art)

    # ==================== ONGLET 2 : MES TICKETS ====================
    with onglet2:
        st.subheader("Historique des tickets analysés")
        tickets_db = db.lister_tickets()
        if not tickets_db:
            st.info("Aucun ticket enregistré pour le moment.")
        else:
            for t in tickets_db:
                nb_al = t.get("nb_alertes") or 0
                etat_global = f"🚨 {nb_al} alerte(s)" if nb_al > 0 else "RAS"
                badge_simu = " [SIMULATION]" if t.get("est_simulation") else ""
                with st.expander(f"{t['date_achat']} — {t['enseigne']}{badge_simu} ({t['nb_articles']} articles) | {etat_global}"):
                    st.caption(f"Fichier : {t['nom_fichier']} | Analysé le : {t['date_analyse']}")
                    articles_t = db.lire_articles_ticket(t["id"])
                    for a in articles_t:
                        a["libelle"] = a["libelle_original"]
                        a["enseigne"] = t["enseigne"]
                        afficher_carte_article(a)
                    if st.button("🗑️ Supprimer ce ticket et ses données", key=f"del_{t['id']}"):
                        db.supprimer_ticket(t["id"])
                        st.rerun()

    # ==================== ONGLET 3 : ALERTES ====================
    with onglet3:
        st.subheader("Suivi des alertes et veille sur les anciens achats")
        if st.button("🔄 Vérifier les nouveaux rappels", type="primary"):
            tous_articles = db.lister_tous_articles_reels()
            if not tous_articles:
                st.info("Aucun article réel enregistré dans l'historique.")
            else:
                dates = [date.fromisoformat(a["date_achat"]) for a in tous_articles]
                depuis = min(dates) - timedelta(days=30)
                with st.spinner("Interrogation de RappelConso pour tous les produits enregistrés..."):
                    rappels_actuels = recuperer_tous_les_rappels(depuis)
                    nouvelles_alertes = []

                    for a in tous_articles:
                        art_dict = {
                            "nom_complet": a["nom_complet"],
                            "marque": a["marque"],
                            "ean": a["ean"],
                            "ean_verifie": bool(a["ean_verifie"]),
                            "statut_identification": a["statut_identification"],
                        }
                        verif = evaluer_article_contre_rappels(
                            art_dict,
                            date.fromisoformat(a["date_achat"]),
                            a["enseigne"],
                            rappels_actuels,
                        )
                        r_dict = _serialiser_rappel(verif.rappel)
                        if verif.statut_rappel != a["statut_rappel"]:
                            db.mettre_a_jour_statut_article(
                                a["id"],
                                verif.statut_rappel,
                                verif.rappel.id if verif.rappel else None,
                                r_dict,
                                verif.explication,
                            )
                            if verif.statut_rappel in (STATUT_CONFIRME, STATUT_PROBABLE):
                                nouvelles_alertes.append({
                                    **a,
                                    "libelle": a["libelle_original"],
                                    "statut_rappel": verif.statut_rappel,
                                    "rappel_detail": r_dict,
                                })

                st.success(f"Vérification terminée sur {len(tous_articles)} article(s). {len(nouvelles_alertes)} nouvelle(s) alerte(s) détectée(s).")
                if nouvelles_alertes:
                    ok, msg = envoyer_alertes_mail(nouvelles_alertes, db)
                    st.info(msg)

        st.divider()
        st.markdown("### Alertes actives dans votre historique")
        alertes_hist = [
            a for a in db.lister_tous_articles_reels()
            if a["statut_rappel"] in (STATUT_CONFIRME, STATUT_PROBABLE)
        ]
        if not alertes_hist:
            st.success("Aucune alerte active sur vos tickets enregistrés.")
        else:
            for a in alertes_hist:
                a["libelle"] = a["libelle_original"]
                afficher_carte_article(a)

        st.divider()
        st.markdown("### Historique des notifications par e-mail")
        notifs = db.lister_notifications()
        if not notifs:
            st.caption("Aucun e-mail envoyé pour le moment.")
        else:
            st.dataframe(notifs, use_container_width=True)


if __name__ == "__main__":
    main()