"""Couche de persistance des données (SQLite persistant ou PostgreSQL)."""
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from contextlib import contextmanager

import config


class BaseDeDonnees:
    def __init__(self):
        self.use_postgres = bool(config.DATABASE_URL)
        if self.use_postgres:
            import psycopg2
            from psycopg2.extras import RealDictCursor
            self._pg = psycopg2
            self._cursor_cls = RealDictCursor
        else:
            db_path = Path(config.SQLITE_PATH)
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self.sqlite_path = str(db_path)
        self._init_schema()

    @contextmanager
    def _connect(self):
        if self.use_postgres:
            conn = self._pg.connect(config.DATABASE_URL)
        else:
            conn = sqlite3.connect(self.sqlite_path)
            conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _ph(self) -> str:
        return "%s" if self.use_postgres else "?"

    def _init_schema(self):
        pk = "SERIAL PRIMARY KEY" if self.use_postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
        statements = [
            f"""
            CREATE TABLE IF NOT EXISTS tickets (
                id {pk},
                nom_fichier TEXT NOT NULL,
                date_achat TEXT NOT NULL,
                enseigne TEXT NOT NULL,
                date_analyse TEXT NOT NULL,
                est_simulation INTEGER DEFAULT 0
            )
            """,
            f"""
            CREATE TABLE IF NOT EXISTS articles (
                id {pk},
                ticket_id INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
                libelle_original TEXT NOT NULL,
                prix REAL,
                nom_complet TEXT,
                marque TEXT,
                format_produit TEXT,
                categorie TEXT,
                reference_fabricant TEXT,
                ean TEXT,
                ean_verifie INTEGER DEFAULT 0,
                statut_identification TEXT NOT NULL,
                source_identification TEXT,
                candidats_json TEXT,
                statut_rappel TEXT NOT NULL,
                rappel_id TEXT,
                rappel_json TEXT,
                explication_rappel TEXT,
                derniere_verification TEXT NOT NULL
            )
            """,
            f"""
            CREATE TABLE IF NOT EXISTS cache_produits (
                cle_cache TEXT PRIMARY KEY,
                donnees_json TEXT NOT NULL,
                date_maj TEXT NOT NULL
            )
            """,
            f"""
            CREATE TABLE IF NOT EXISTS notifications_mail (
                id {pk},
                cle_alerte TEXT UNIQUE NOT NULL,
                date_envoi TEXT NOT NULL,
                destinataire TEXT NOT NULL,
                sujet TEXT NOT NULL,
                statut_envoi TEXT NOT NULL
            )
            """,
        ]
        with self._connect() as conn:
            cur = conn.cursor()
            for stmt in statements:
                cur.execute(stmt)
            conn.commit()

    def lire_cache_produit(self, cle: str) -> dict[str, Any] | None:
        p = self._ph()
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT donnees_json FROM cache_produits WHERE cle_cache = {p}", (cle,))
            row = cur.fetchone()
            if not row:
                return None
            val = row["donnees_json"] if isinstance(row, dict) else row[0]
            return json.loads(val)

    def sauver_cache_produit(self, cle: str, donnees: dict[str, Any]) -> None:
        p = self._ph()
        now = datetime.now().isoformat(timespec="seconds")
        payload = json.dumps(donnees, ensure_ascii=False)
        with self._connect() as conn:
            cur = conn.cursor()
            if self.use_postgres:
                cur.execute(
                    f"""INSERT INTO cache_produits (cle_cache, donnees_json, date_maj)
                        VALUES ({p}, {p}, {p})
                        ON CONFLICT (cle_cache) DO UPDATE SET donnees_json = EXCLUDED.donnees_json, date_maj = EXCLUDED.date_maj""",
                    (cle, payload, now),
                )
            else:
                cur.execute(
                    f"INSERT OR REPLACE INTO cache_produits (cle_cache, donnees_json, date_maj) VALUES ({p}, {p}, {p})",
                    (cle, payload, now),
                )
            conn.commit()

    def enregistrer_ticket_complet(
        self,
        nom_fichier: str,
        date_achat: str,
        enseigne: str,
        articles_resultats: list[dict[str, Any]],
        est_simulation: bool = False,
    ) -> int:
        p = self._ph()
        now = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            cur = conn.cursor()
            if self.use_postgres:
                cur.execute(
                    f"INSERT INTO tickets (nom_fichier, date_achat, enseigne, date_analyse, est_simulation) VALUES ({p},{p},{p},{p},{p}) RETURNING id",
                    (nom_fichier, date_achat, enseigne, now, int(est_simulation)),
                )
                ticket_id = cur.fetchone()[0]
            else:
                cur.execute(
                    f"INSERT INTO tickets (nom_fichier, date_achat, enseigne, date_analyse, est_simulation) VALUES ({p},{p},{p},{p},{p})",
                    (nom_fichier, date_achat, enseigne, now, int(est_simulation)),
                )
                ticket_id = cur.lastrowid

            for art in articles_resultats:
                cur.execute(
                    f"""INSERT INTO articles (
                        ticket_id, libelle_original, prix, nom_complet, marque, format_produit,
                        categorie, reference_fabricant, ean, ean_verifie, statut_identification,
                        source_identification, candidats_json, statut_rappel, rappel_id,
                        rappel_json, explication_rappel, derniere_verification
                    ) VALUES ({','.join([p]*18)})""",
                    (
                        ticket_id,
                        art["libelle"],
                        art.get("prix"),
                        art.get("nom_complet"),
                        art.get("marque"),
                        art.get("format"),
                        art.get("categorie"),
                        art.get("reference_fabricant"),
                        art.get("ean"),
                        int(bool(art.get("ean_verifie"))),
                        art["statut_identification"],
                        art.get("source_identification"),
                        json.dumps(art.get("candidats") or [], ensure_ascii=False),
                        art["statut_rappel"],
                        art.get("rappel_id"),
                        json.dumps(art.get("rappel_detail"), ensure_ascii=False) if art.get("rappel_detail") else None,
                        art.get("explication_rappel", ""),
                        now,
                    ),
                )
            conn.commit()
            return ticket_id

    def lister_tickets(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT t.*,
                       COUNT(a.id) AS nb_articles,
                       SUM(CASE WHEN a.statut_rappel IN ('rappel_confirme', 'correspondance_probable') THEN 1 ELSE 0 END) AS nb_alertes
                FROM tickets t
                LEFT JOIN articles a ON a.ticket_id = t.id
                GROUP BY t.id
                ORDER BY t.date_achat DESC, t.id DESC
            """)
            rows = cur.fetchall()
            return [dict(r) for r in rows]

    def lire_articles_ticket(self, ticket_id: int) -> list[dict[str, Any]]:
        p = self._ph()
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT * FROM articles WHERE ticket_id = {p} ORDER BY id ASC", (ticket_id,))
            return [dict(r) for r in cur.fetchall()]

    def supprimer_ticket(self, ticket_id: int) -> None:
        p = self._ph()
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(f"DELETE FROM articles WHERE ticket_id = {p}", (ticket_id,))
            cur.execute(f"DELETE FROM tickets WHERE id = {p}", (ticket_id,))
            conn.commit()

    def lister_tous_articles_reels(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT a.*, t.date_achat, t.enseigne, t.nom_fichier
                FROM articles a
                JOIN tickets t ON t.id = a.ticket_id
                WHERE t.est_simulation = 0
                ORDER BY t.date_achat DESC
            """)
            return [dict(r) for r in cur.fetchall()]

    def mettre_a_jour_statut_article(
        self,
        article_id: int,
        statut_rappel: str,
        rappel_id: str | None,
        rappel_detail: dict[str, Any] | None,
        explication: str,
    ) -> None:
        p = self._ph()
        now = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""UPDATE articles
                    SET statut_rappel = {p}, rappel_id = {p}, rappel_json = {p},
                        explication_rappel = {p}, derniere_verification = {p}
                    WHERE id = {p}""",
                (
                    statut_rappel,
                    rappel_id,
                    json.dumps(rappel_detail, ensure_ascii=False) if rappel_detail else None,
                    explication,
                    now,
                    article_id,
                ),
            )
            conn.commit()

    def alerte_deja_notifiee(self, cle_alerte: str) -> bool:
        p = self._ph()
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT 1 FROM notifications_mail WHERE cle_alerte = {p}", (cle_alerte,))
            return cur.fetchone() is not None

    def enregistrer_notification(self, cle_alerte: str, destinataire: str, sujet: str, statut_envoi: str) -> None:
        p = self._ph()
        now = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            cur = conn.cursor()
            if self.use_postgres:
                cur.execute(
                    f"""INSERT INTO notifications_mail (cle_alerte, date_envoi, destinataire, sujet, statut_envoi)
                        VALUES ({p},{p},{p},{p},{p}) ON CONFLICT (cle_alerte) DO NOTHING""",
                    (cle_alerte, now, destinataire, sujet, statut_envoi),
                )
            else:
                cur.execute(
                    f"INSERT OR IGNORE INTO notifications_mail (cle_alerte, date_envoi, destinataire, sujet, statut_envoi) VALUES ({p},{p},{p},{p},{p})",
                    (cle_alerte, now, destinataire, sujet, statut_envoi),
                )
            conn.commit()

    def lister_notifications(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM notifications_mail ORDER BY id DESC")
            return [dict(r) for r in cur.fetchall()]