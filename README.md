# JobToMail

Outil de prospection et candidature spontanée automatisée : recherche d'entreprises via l'API Sirene (INSEE), enrichissement (dirigeants, contacts, qualité d'email), puis envoi d'emails de candidature ciblés et de relances, le tout piloté depuis une interface web.

> ⚠️ **Usage responsable** : cet outil scrape et traite des données d'entreprises et de contacts (RGPD). À utiliser dans le cadre d'une recherche d'emploi personnelle, dans le respect du RGPD/CNIL et des conditions d'utilisation des services tiers (INSEE, SerpAPI, Hunter.io, Gmail).

## Fonctionnalités

- Recherche d'entreprises par zone géographique et code NAF via l'API Sirene, avec des requêtes groupées (plusieurs communes/codes par appel) pour rester rapide même sur de grandes zones.
- Détection des mairies et associations employeuses.
- Enrichissement : dirigeants, contacts RH/tech (SerpAPI), score de qualité d'email (Hunter.io).
- Génération et envoi d'emails de candidature personnalisés + relances automatiques, avec suivi des réponses.
- Interface web unique pour tout configurer (clés API, zone de recherche, modèles d'emails) — aucune ligne de commande requise après l'installation.

## Démarrage rapide (Docker — recommandé)

Aucune installation de Python requise, seulement [Docker](https://www.docker.com/products/docker-desktop/).

```bash
git clone <url-du-depot>
cd Job2Mail
cp .env.example .env
```

Placez votre CV au format PDF à la racine du projet sous le nom `cv.pdf` (il sera joint automatiquement aux emails, et n'est jamais commité dans le dépôt).

```bash
docker compose up --build
```

Ouvrez ensuite [http://localhost:5001](http://localhost:5001) : renseignez vos clés API et votre nom dans l'onglet **Réglages** directement depuis l'interface (pas besoin de modifier le fichier `.env` à la main).

## Démarrage sans Docker (pour les développeurs)

```bash
python3 -m venv .venv
source .venv/bin/activate  # Windows : .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
python app.py
```

L'application est alors disponible sur `http://127.0.0.1:5001`.

## Configuration

Toutes les clés ci-dessous peuvent être définies dans `.env` **ou** directement depuis l'onglet Réglages de l'application (le web UI a priorité et ne nécessite aucune connaissance technique) :

| Clé | Utilité | Où l'obtenir |
|---|---|---|
| `INSEE_TOKEN` | Recherche d'entreprises (API Sirene) | [api.insee.fr](https://api.insee.fr) |
| `SERPAPI_KEY` | Enrichissement web des entreprises | [serpapi.com](https://serpapi.com) |
| `TOKEN_HUNTER_IO` | Recherche d'emails de contact | [hunter.io](https://hunter.io) |
| `EMAIL_ADDRESS` / `EMAIL_PASSWORD` | Envoi des emails (SMTP Gmail) | Compte Gmail + [mot de passe d'application](https://myaccount.google.com/apppasswords) |
| `APP_PASSWORD` | Protège l'accès à l'interface web | À choisir vous-même |
| `SECRET_KEY` | Clé de session Flask | `python -c "import secrets; print(secrets.token_hex(32))"` |

## Architecture

- `app.py` — point d'entrée (`python app.py`).
- `jobtomail/app_factory.py` — création de l'application Flask et middlewares (auth, sessions).
- `jobtomail/routes/` — endpoints HTTP, un blueprint par domaine (config, scans, entreprises, emails, auth).
- `jobtomail/services/` — logique métier (Sirene, géolocalisation, enrichissement, envoi d'emails…).
- `jobtomail/db.py` — accès SQLite (`jobtomail.db`, jamais commité) et configuration persistée.
- `templates/` + `jobtomail/static/` — interface web (une seule page, JS vanilla).

## Licence

[MIT](LICENSE)
