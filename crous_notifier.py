#!/usr/bin/env python3
"""Scrape trouverunlogement.lescrous.fr and notify new listings on Telegram."""
import json
import logging
import os
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("crous_notifier")

STATE_FILE = Path(__file__).parent / "seen_ids.json"

DEFAULT_SEARCH_URLS = [
    "https://trouverunlogement.lescrous.fr/tools/42/search",  # rentree 2025-2026
    "https://trouverunlogement.lescrous.fr/tools/47/search",  # rentree 2026-2027
]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def get_search_urls() -> list[str]:
    raw = os.environ.get("SEARCH_URLS")
    if raw:
        return [u.strip() for u in raw.split(",") if u.strip()]
    return DEFAULT_SEARCH_URLS


def load_seen_ids() -> set[str]:
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text()))
    return set()


def save_seen_ids(ids: set[str]) -> None:
    STATE_FILE.write_text(json.dumps(sorted(ids), indent=2))


def fetch_listings(search_url: str) -> list[dict]:
    resp = requests.get(search_url, headers={"User-Agent": USER_AGENT}, timeout=30)
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
                "price": price,
                "details": details,
                "url": f"https://trouverunlogement.lescrous.fr{href}",
            }
        )
    return listings


def send_telegram_message(token: str, chat_id: str, text: str) -> None:
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
    if not resp.ok:
        log.error("Echec envoi Telegram: %s", resp.text)
    resp.raise_for_status()


def format_message(listing: dict) -> str:
    lines = [f"🏠 <b>Nouveau logement CROUS</b>", f"<b>{listing['title']}</b>"]
    if listing["address"]:
        lines.append(listing["address"])
    if listing["price"]:
        lines.append(f"💶 {listing['price']}")
    if listing["details"]:
        lines.append(" · ".join(listing["details"]))
    lines.append(listing["url"])
    return "\n".join(lines)


def main() -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log.error("TELEGRAM_BOT_TOKEN et TELEGRAM_CHAT_ID doivent etre definis")
        return 1

    seen_ids = load_seen_ids()
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
        save_seen_ids(current_ids)
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

    save_seen_ids(seen_ids | current_ids)
    return 0


if __name__ == "__main__":
    sys.exit(main())
