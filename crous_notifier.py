#!/usr/bin/env python3
"""Scrape trouverunlogement.lescrous.fr and notify new listings on Telegram."""
import json
import logging
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qsl, urlunparse

import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("crous_notifier")

BASE_DIR = Path(__file__).parent
SEEN_IDS_FILE = BASE_DIR / "seen_ids.json"
SUBSCRIBERS_FILE = BASE_DIR / "subscribers.json"
OFFSET_FILE = BASE_DIR / "update_offset.json"

DEFAULT_SEARCH_URLS = [
    "https://trouverunlogement.lescrous.fr/tools/42/search",  # rentree 2025-2026
    "https://trouverunlogement.lescrous.fr/tools/47/search",  # rentree 2026-2027
]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Departements d'outre-mer sur 3 chiffres (971 Guadeloupe, 972 Martinique, ...)
OVERSEAS_PREFIXES = ("971", "972", "973", "974", "975", "976", "977", "978", "984", "986", "987", "988")


def get_search_urls() -> list[str]:
    raw = os.environ.get("SEARCH_URLS")
    if raw:
        return [u.strip() for u in raw.split(",") if u.strip()]
    return DEFAULT_SEARCH_URLS


def load_json(path: Path, default):
    if path.exists():
        return json.loads(path.read_text())
    return default


def save_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True))


def department_from_address(address: str) -> str | None:
    match = re.search(r"\b(\d{5})\b", address)
    if not match:
        return None
    postal_code = match.group(1)
    for prefix in OVERSEAS_PREFIXES:
        if postal_code.startswith(prefix):
            return prefix[:3]
    return postal_code[:2]


def _with_page(search_url: str, page: int) -> str:
    parts = urlparse(search_url)
    query = dict(parse_qsl(parts.query))
    query["page"] = str(page)
    return urlunparse(parts._replace(query=urlencode(query)))


def fetch_listings(search_url: str) -> list[dict]:
    """Fetches every page of results for a search URL (the site paginates at 24 per page)."""
    all_listings: list[dict] = []
    page = 1
    while True:
        page_url = _with_page(search_url, page)
        page_listings = fetch_listings_page(page_url, search_url)
        if not page_listings:
            break
        all_listings.extend(page_listings)
        page += 1
    return all_listings


def fetch_listings_page(page_url: str, search_url: str) -> list[dict]:
    resp = requests.get(page_url, headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    soup = BeautifulSoup(resp.text, "html.parser")

    listings = []
    for card in soup.find_all("div", class_="fr-card"):
        title_tag = card.find("h3", class_="fr-card__title")
        if not title_tag or not title_tag.find("a"):
            continue

        link = title_tag.find("a")
        href = link.get("href", "")
        listing_id = href.rstrip("/").split("/")[-1]
        if not listing_id:
            continue

        title = link.get_text(strip=True)
        address_tag = card.find("p", class_="fr-card__desc")
        address = address_tag.get_text(strip=True) if address_tag else ""

        details = [d.get_text(strip=True) for d in card.find_all("li", class_="fr-card__detail")]

        price_tag = card.find("p", class_="fr-badge")
        price = price_tag.get_text(strip=True) if price_tag else ""

        listings.append(
            {
                "id": f"{search_url}#{listing_id}",
                "title": title,
                "address": address,
                "department": department_from_address(address),
                "price": price,
                "details": details,
                "url": f"https://trouverunlogement.lescrous.fr{href}",
            }
        )
    return listings


def send_telegram_message(token: str, chat_id: str | int, text: str) -> bool:
    """Returns True on success. Returns False (without raising) if Telegram rejects the chat_id
    (e.g. user blocked the bot or never started a private chat with it)."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(
        url,
        data={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        },
        timeout=30,
    )
    if resp.ok:
        return True

    if resp.status_code == 403:
        log.warning("Bot bloque ou chat inaccessible pour %s: %s", chat_id, resp.text)
        return False

    log.error("Echec envoi Telegram vers %s: %s", chat_id, resp.text)
    resp.raise_for_status()
    return False


def format_message(listing: dict) -> str:
    lines = ["🏠 <b>Nouveau logement CROUS</b>", f"<b>{listing['title']}</b>"]
    if listing["address"]:
        lines.append(listing["address"])
    if listing["price"]:
        lines.append(f"💶 {listing['price']}")
    if listing["details"]:
        lines.append(" · ".join(listing["details"]))
    lines.append(listing["url"])
    return "\n".join(lines)


# --- Gestion des commandes utilisateur (/notifyadd, /notifyremove, /notifylist) ---

COMMAND_RE = re.compile(r"^/(notifyadd|notifyremove|notifylist|start|help)(?:@\w+)?(?:\s+(.*))?$")


def fetch_updates(token: str, offset: int) -> list[dict]:
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    resp = requests.get(
        url,
        params={"offset": offset, "timeout": 0, "allowed_updates": json.dumps(["message"])},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        log.error("Echec getUpdates: %s", data)
        return []
    return data["result"]


def handle_commands(token: str, subscribers: dict[str, list[str]]) -> int:
    """Processes pending private-chat commands, mutates `subscribers` in place.
    Returns the next update offset to persist."""
    offset_data = load_json(OFFSET_FILE, {"offset": 0})
    updates = fetch_updates(token, offset_data["offset"])

    next_offset = offset_data["offset"]
    for update in updates:
        next_offset = update["update_id"] + 1

        message = update.get("message")
        if not message or message.get("chat", {}).get("type") != "private":
            continue

        text = message.get("text", "")
        match = COMMAND_RE.match(text.strip())
        if not match:
            continue

        command, arg = match.group(1), (match.group(2) or "").strip()
        chat_id = str(message["chat"]["id"])

        if command in ("start", "help"):
            reply = (
                "👋 Salut ! Je notifie les nouveaux logements CROUS.\n\n"
                "Commandes :\n"
                "/notifyadd 59 — recevoir une notif privee pour les nouveaux logements du departement 59\n"
                "/notifyremove 59 — arreter les notifs pour ce departement\n"
                "/notifylist — voir tes departements suivis"
            )
        elif command == "notifyadd":
            if not re.fullmatch(r"\d{2,3}", arg):
                reply = "⚠️ Utilise un numero de departement, ex: /notifyadd 59"
            else:
                depts = subscribers.setdefault(chat_id, [])
                if arg not in depts:
                    depts.append(arg)
                reply = f"✅ Tu seras notifie pour les nouveaux logements du departement {arg}."
        elif command == "notifyremove":
            depts = subscribers.get(chat_id, [])
            if arg in depts:
                depts.remove(arg)
                if not depts:
                    subscribers.pop(chat_id, None)
                reply = f"🛑 Notifications arretees pour le departement {arg}."
            else:
                reply = f"Tu n'etais pas abonne au departement {arg}."
        elif command == "notifylist":
            depts = subscribers.get(chat_id, [])
            reply = (
                f"📋 Departements suivis : {', '.join(depts)}" if depts else "Tu ne suis aucun departement pour l'instant. Utilise /notifyadd 59"
            )
        else:
            continue

        send_telegram_message(token, chat_id, reply)

    save_json(OFFSET_FILE, {"offset": next_offset})
    return next_offset


def notify_subscribers(token: str, subscribers: dict[str, list[str]], new_listings: list[dict]) -> None:
    blocked_chat_ids = []
    for chat_id, depts in subscribers.items():
        matches = [listing for listing in new_listings if listing["department"] in depts]
        for listing in matches:
            ok = send_telegram_message(token, chat_id, format_message(listing))
            if not ok:
                blocked_chat_ids.append(chat_id)
                break

    for chat_id in blocked_chat_ids:
        subscribers.pop(chat_id, None)


def main() -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log.error("TELEGRAM_BOT_TOKEN et TELEGRAM_CHAT_ID doivent etre definis")
        return 1

    subscribers = load_json(SUBSCRIBERS_FILE, {})
    handle_commands(token, subscribers)
    save_json(SUBSCRIBERS_FILE, subscribers)

    seen_ids = load_json(SEEN_IDS_FILE, [])
    seen_ids = set(seen_ids)
    is_first_run = len(seen_ids) == 0

    all_listings: list[dict] = []
    for search_url in get_search_urls():
        try:
            all_listings.extend(fetch_listings(search_url))
        except requests.RequestException as e:
            log.error("Echec recuperation %s: %s", search_url, e)

    current_ids = {listing["id"] for listing in all_listings}
    new_ids = current_ids - seen_ids

    if is_first_run:
        log.info("Premiere execution: %d logements references sans notification", len(current_ids))
        save_json(SEEN_IDS_FILE, sorted(current_ids))
        send_telegram_message(
            token,
            chat_id,
            f"✅ Bot CROUS initialise. {len(current_ids)} logements actuellement suivis. "
            "Tu recevras une notification a chaque nouvelle publication.",
        )
        return 0

    new_listings = [listing for listing in all_listings if listing["id"] in new_ids]
    log.info("%d nouveaux logements sur %d au total", len(new_listings), len(current_ids))

    for listing in new_listings:
        send_telegram_message(token, chat_id, format_message(listing))

    notify_subscribers(token, subscribers, new_listings)
    save_json(SUBSCRIBERS_FILE, subscribers)

    save_json(SEEN_IDS_FILE, sorted(seen_ids | current_ids))
    return 0


if __name__ == "__main__":
    sys.exit(main())
