#!/usr/bin/env python3
"""
Moniteur de disponibilité STUDEFI Île-de-France.
Scrape toutes les résidences, détecte les disponibilités
et envoie des notifications Telegram.
"""

import json
import os
import random
import re
import time
import urllib.request
from datetime import datetime, timezone

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup

# ── Configuration ────────────────────────────────────────────────────────────

BASE_URL = "https://www.studefi.fr"
RESIDENCE_URL = f"{BASE_URL}/main.php?srv=Residence&op=show&cdGroupe={{code}}"

# Toutes les résidences STUDEFI IDF (extraites du dropdown)
RESIDENCES = [
    {"code": "807G", "nom": "Algo", "ville": "Paris 13e"},
    {"code": "788G", "nom": "Irène et François Joliot Curie", "ville": "Arcueil"},
    {"code": "802G", "nom": "Anne Franck", "ville": "Aubervilliers"},
    {"code": "804G", "nom": "Jean Moulin", "ville": "Aubervilliers"},
    {"code": "794G", "nom": "Les enfants du Paradis", "ville": "Aubervilliers"},
    {"code": "793G", "nom": "Roger Hanin", "ville": "Aubervilliers"},
    {"code": "798G", "nom": "Sequana", "ville": "Boulogne-Billancourt"},
    {"code": "797G", "nom": "Les Closbilles", "ville": "Cergy"},
    {"code": "806G", "nom": "Modigliani", "ville": "Courbevoie"},
    {"code": "805G", "nom": "Odyssée", "ville": "Deuil-la-Barre"},
    {"code": "A812", "nom": "L'Alouette 2", "ville": "Drancy"},
    {"code": "809G", "nom": "L'Alouette (étudiants)", "ville": "Drancy"},
    {"code": "800G", "nom": "Victoire Daubié", "ville": "La Verrière"},
    {"code": "962G", "nom": "Le Concorde", "ville": "Dugny"},
    {"code": "795G", "nom": "Maurice Denis", "ville": "Le Raincy"},
    {"code": "963G", "nom": "Les fils d'Icare (étudiants)", "ville": "Vélizy"},
    {"code": "843G", "nom": "Les Fils d'Icare (jeunes actifs)", "ville": "Vélizy"},
    {"code": "785G", "nom": "Eric Tabarly", "ville": "Massy"},
    {"code": "801G", "nom": "Océane", "ville": "Massy"},
    {"code": "787G", "nom": "Blaise Pascal", "ville": "Montigny-le-Bretonneux"},
    {"code": "796G", "nom": "Le Galibier", "ville": "Montigny-le-Bretonneux"},
    {"code": "964G", "nom": "Andrée Michel", "ville": "Montreuil"},
    {"code": "789G", "nom": "Gallieni 1", "ville": "Neuilly-Plaisance"},
    {"code": "799G", "nom": "Gallieni 2", "ville": "Neuilly-Plaisance"},
    {"code": "803G", "nom": "Clémence Royer - Le Luzard II", "ville": "Noisiel"},
    {"code": "791G", "nom": "Pierre-Gilles de Gennes", "ville": "Orsay"},
    {"code": "784G", "nom": "Évariste Galois", "ville": "Paris 18e"},
    {"code": "786G", "nom": "François Rabelais", "ville": "Pontoise"},
    {"code": "808G", "nom": "Paulette Fost", "ville": "Saint-Ouen"},
    {"code": "790G", "nom": "Les Cantilènes", "ville": "Ville d'Avray"},
    {"code": "792G", "nom": "Camille Claudel", "ville": "Villiers-sur-Marne"},
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}

REQUEST_TIMEOUT = 60


def create_session():
    """Crée une session requests avec retry automatique."""
    session = requests.Session()
    session.headers.update(HEADERS)
    retry_strategy = Retry(
        total=5,
        backoff_factor=2,  # 2s, 4s, 8s, 16s, 32s
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

STATE_FILE = "studefi_last_state.json"


# ── Scraping ─────────────────────────────────────────────────────────────────

def fetch_with_retry(session, url, max_attempts=3):
    """Fetch une URL avec retry manuel en plus du retry de la session."""
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            last_error = e
            wait = 5 * attempt + random.uniform(1, 3)
            print(f"\n    [!] Tentative {attempt}/{max_attempts} échouée: {e}")
            if attempt < max_attempts:
                print(f"    [!] Nouvelle tentative dans {wait:.0f}s...")
                time.sleep(wait)
    raise last_error


def get_available_codes(session):
    """Récupère la liste des codes résidences avec dispo depuis une page."""
    url = RESIDENCE_URL.format(code=RESIDENCES[0]["code"])
    resp = fetch_with_retry(session, url)
    codes = re.findall(r"tabLogementsDisponibles\.push\('([^']+)'\)", resp.text)
    return set(codes)


def get_residence_details(session, code):
    """Récupère les détails des logements disponibles pour une résidence."""
    url = RESIDENCE_URL.format(code=code)
    resp = fetch_with_retry(session, url)
    soup = BeautifulSoup(resp.text, "html.parser")

    logements = []

    # Chercher le tableau des tarifs
    table = soup.find("table", class_="table-tarifs")
    if not table:
        return logements

    rows = table.find_all("tr", id=re.compile(r"^tr\d+"))
    for row in rows:
        cols = row.find_all("td")
        if len(cols) < 5:
            continue

        type_log = cols[0].get_text(strip=True)
        nb_dispo = cols[1].get_text(strip=True)
        surface = cols[2].get_text(strip=True)
        meuble = cols[3].get_text(strip=True)
        loyer = cols[4].get_text(strip=True).replace("\n", "").replace("\t", "").strip()

        logements.append({
            "type": type_log,
            "nb_dispo": nb_dispo,
            "surface": surface,
            "meuble": meuble,
            "loyer": loyer,
        })

    # Chercher les liens "Réserver en ligne" pour les détails
    reserve_links = soup.find_all("a", class_="mini-button", string=re.compile(r"Réserver"))
    details_table = soup.find_all("table", class_="table-tarifs-detail")

    for dt in details_table:
        detail_rows = dt.find_all("tr")
        for dr in detail_rows[1:]:  # Skip header
            dcols = dr.find_all("td")
            if len(dcols) < 4:
                continue
            surface_d = dcols[0].get_text(strip=True)
            etage = dcols[1].get_text(strip=True)
            date_dispo = dcols[2].get_text(strip=True)
            loyer_d = dcols[3].get_text(strip=True)

            reserve_link = dr.find("a", class_="mini-button")
            reserve_url = ""
            if reserve_link:
                reserve_url = f"{BASE_URL}/{reserve_link.get('href', '')}"

            # Mettre à jour le dernier logement avec les détails
            if logements:
                logements[-1]["details"] = logements[-1].get("details", [])
                logements[-1]["details"].append({
                    "surface": surface_d,
                    "etage": etage,
                    "date_dispo": date_dispo,
                    "loyer": loyer_d,
                    "reserve_url": reserve_url,
                })

    return logements


# ── Telegram ─────────────────────────────────────────────────────────────────

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[TELEGRAM] Token ou Chat ID manquant, notification ignorée.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode("utf-8")

    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                print("[TELEGRAM] Message envoyé !")
            else:
                print(f"[TELEGRAM] Erreur: {resp.status}")
    except Exception as e:
        print(f"[TELEGRAM] Erreur d'envoi: {e}")


# ── État ─────────────────────────────────────────────────────────────────────

def load_previous_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ── HTML ─────────────────────────────────────────────────────────────────────

def generate_html(all_results, scan_time):
    disponibles = []
    indisponibles = []

    for res, logements in all_results:
        if logements:
            disponibles.append((res, logements))
        else:
            indisponibles.append((res, []))

    def render_card(res, logements, card_class):
        url = RESIDENCE_URL.format(code=res["code"])
        rows = ""
        for l in logements:
            details_html = ""
            for d in l.get("details", []):
                link = f'<a href="{d["reserve_url"]}" target="_blank" class="btn btn-sm btn-success">Réserver</a>' if d.get("reserve_url") else ""
                details_html += f"<br><small>Étage {d['etage']} | Dispo: {d['date_dispo']} | {d['loyer']} {link}</small>"
            rows += f"""<tr>
                <td>{l['type']}</td>
                <td><strong>{l['nb_dispo']}</strong></td>
                <td>{l['surface']} m²</td>
                <td>{l['loyer']}</td>
                <td>{details_html}</td>
            </tr>"""

        return f"""
        <div class="col-md-6 col-lg-4 mb-4">
            <div class="card {card_class} h-100">
                <div class="card-body">
                    <h5 class="card-title">{res['nom']}</h5>
                    <p class="card-text text-muted">{res['ville']}</p>
                    <table class="table table-sm table-bordered">
                        <thead><tr><th>Type</th><th>Dispo</th><th>Surface</th><th>Loyer</th><th>Détails</th></tr></thead>
                        <tbody>{rows}</tbody>
                    </table>
                    <a href="{url}" target="_blank" class="btn btn-sm btn-outline-primary">Voir sur STUDEFI</a>
                </div>
            </div>
        </div>"""

    dispo_cards = "".join(render_card(r, l, "border-success") for r, l in disponibles)
    indispo_cards = "".join(f"""
        <div class="col-md-6 col-lg-4 mb-4">
            <div class="card border-secondary h-100">
                <div class="card-body">
                    <h5 class="card-title">{r['nom']}</h5>
                    <p class="card-text text-muted">{r['ville']}</p>
                    <span class="badge bg-secondary">Aucun logement disponible</span>
                </div>
            </div>
        </div>""" for r, _ in indisponibles)

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>STUDEFI IDF — Disponibilités</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body {{ background: #f4f6f9; }}
        .card.border-success {{ border-width: 3px; }}
        .hero {{ background: linear-gradient(135deg, #1291c0, #0d6efd); color: white; padding: 2rem 0; }}
    </style>
</head>
<body>
    <div class="hero text-center">
        <div class="container">
            <h1>STUDEFI Île-de-France</h1>
            <p class="lead">Disponibilités des résidences étudiantes</p>
            <p>Dernier scan : <strong>{scan_time}</strong></p>
            <div class="row justify-content-center mt-3">
                <div class="col-auto"><span class="badge bg-success fs-6">{len(disponibles)}</span> Disponible(s)</div>
                <div class="col-auto"><span class="badge bg-secondary fs-6">{len(indisponibles)}</span> Indisponible(s)</div>
            </div>
        </div>
    </div>
    <div class="container mt-4">
        {"<h2 class='text-success mb-3'>Logements disponibles</h2><div class='row'>" + dispo_cards + "</div>" if disponibles else ""}
        {"<h2 class='text-secondary mb-3 mt-4'>Indisponible</h2><div class='row'>" + indispo_cards + "</div>" if indisponibles else ""}
    </div>
    <footer class="text-center text-muted py-4">
        <small>Mise à jour automatique via GitHub Actions.</small>
    </footer>
</body>
</html>"""
    return html


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  STUDEFI Monitor — Île-de-France")
    print("=" * 60)

    session = create_session()

    # Etape 1 : récupérer les codes avec dispo (1 seule requête)
    print("\n[*] Vérification des résidences avec disponibilité...")
    available_codes = get_available_codes(session)
    print(f"[*] {len(available_codes)} résidence(s) avec dispo : {available_codes}\n")

    previous_state = load_previous_state()
    current_state = {}
    all_results = []
    new_availabilities = []

    for i, res in enumerate(RESIDENCES, 1):
        label = f"{res['nom']} ({res['ville']})"
        print(f"  [{i:2d}/{len(RESIDENCES)}] {label}...", end=" ", flush=True)

        has_dispo = res["code"] in available_codes
        current_state[res["code"]] = "DISPONIBLE" if has_dispo else "INDISPONIBLE"

        if has_dispo:
            try:
                logements = get_residence_details(session, res["code"])
                all_results.append((res, logements))

                nb = sum(int(l.get("nb_dispo", 0)) for l in logements)
                print(f"DISPO ({nb} logement(s))")

                prev = previous_state.get(res["code"], "INDISPONIBLE")
                if prev != "DISPONIBLE":
                    new_availabilities.append({
                        "residence": res["nom"],
                        "ville": res["ville"],
                        "logements": logements,
                        "url": RESIDENCE_URL.format(code=res["code"]),
                    })

                time.sleep(random.uniform(1.0, 3.0))
            except Exception as e:
                print(f"erreur: {e}")
                all_results.append((res, []))
        else:
            print("indisponible")
            all_results.append((res, []))

    # Sauvegarder l'état
    save_state(current_state)

    # Générer HTML
    scan_time = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    html = generate_html(all_results, scan_time)
    os.makedirs("public", exist_ok=True)
    with open("public/studefi.html", "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n[*] Page HTML générée dans public/studefi.html")

    # Notifications Telegram
    if new_availabilities:
        print(f"\n[!] {len(new_availabilities)} NOUVELLE(S) DISPONIBILITÉ(S) STUDEFI !")

        msg = "<b>🏠 STUDEFI — Nouvelles disponibilités !</b>\n\n"
        for a in new_availabilities:
            msg += f"🟢 <b>{a['residence']}</b> — {a['ville']}\n"
            for l in a["logements"]:
                msg += f"   {l['type']} | {l['nb_dispo']} dispo | {l['loyer']}\n"
                for d in l.get("details", []):
                    msg += f"   → Étage {d['etage']}, dispo {d['date_dispo']}\n"
            msg += f"   <a href=\"{a['url']}\">Voir / Réserver</a>\n\n"

        send_telegram(msg)
    else:
        print("\n[*] Pas de nouvelle disponibilité STUDEFI.")

    nb_dispo = sum(1 for _, l in all_results if l)
    print(f"\n[*] Résumé : {nb_dispo} résidence(s) avec dispo sur {len(RESIDENCES)}")


if __name__ == "__main__":
    main()
