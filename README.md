# crous-telegram-bot

Bot qui surveille les nouveaux logements publiés sur [trouverunlogement.lescrous.fr](https://trouverunlogement.lescrous.fr/)
(toute la France, années 2025‑2026 et 2026‑2027) et envoie une notification Telegram à chaque nouvelle annonce.

Aucun serveur à héberger : le bot tourne gratuitement via **GitHub Actions**, toutes les 15 minutes.

## 1. Créer le bot Telegram

1. Ouvre Telegram et parle à [@BotFather](https://t.me/BotFather).
2. Envoie `/newbot`, choisis un nom et un identifiant (doit finir par `bot`).
3. BotFather te donne un **token** du type `123456789:AAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx` → garde-le.
4. Démarre une conversation avec ton nouveau bot (cherche son `@username` et clique "Démarrer").
5. Récupère ton **chat_id** :
   - Envoie n'importe quel message à ton bot.
   - Va sur `https://api.telegram.org/bot<TON_TOKEN>/getUpdates` dans ton navigateur.
   - Cherche `"chat":{"id":123456789,...}` → c'est ton `chat_id`.

## 2. Mettre le code sur GitHub

```bash
cd crous-telegram-bot
git init
git add .
git commit -m "init crous telegram bot"
gh repo create crous-telegram-bot --private --source=. --push
```

(ou crée un repo **privé** sur github.com et fais `git push`)

## 3. Ajouter les secrets

Dans le repo GitHub : **Settings → Secrets and variables → Actions → New repository secret**

- `TELEGRAM_BOT_TOKEN` = le token de BotFather
- `TELEGRAM_CHAT_ID` = ton chat_id

## 4. C'est tout

Le workflow `.github/workflows/check.yml` tourne toutes les 15 minutes (`workflow_dispatch` permet aussi
de le lancer manuellement depuis l'onglet **Actions**). Au premier lancement, le bot enregistre les
annonces déjà présentes sans notifier (pour éviter un spam de toutes les annonces existantes), puis
notifie seulement les **nouvelles** annonces à chaque exécution suivante.

## Tester en local

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN=xxx
export TELEGRAM_CHAT_ID=xxx
python crous_notifier.py
```

## Personnaliser la recherche

Par défaut le bot suit **toute la France**. Pour filtrer par ville/zone, fais une recherche sur
[trouverunlogement.lescrous.fr](https://trouverunlogement.lescrous.fr/), clique "Rechercher dans la zone"
sur la carte, et récupère l'URL (elle contiendra `?bounds=...`). Puis ajoute le secret `SEARCH_URLS` avec
une ou plusieurs URLs séparées par des virgules, et passe-le en variable d'environnement dans le workflow.
