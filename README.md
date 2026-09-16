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

Ouvrez ensuite [http://localhost:5001](http://localhost:5001) : renseignez votre nom et vos clés SerpAPI/Hunter.io dans l'onglet **Réglages** directement depuis l'interface. Les variables opérateur (`SECRET_KEY`, `GOOGLE_CLIENT_*`, `APP_ENCRYPTION_KEY`, `INSEE_TOKEN`) restent à définir dans `.env` avant le premier lancement — voir [Configuration](#configuration).

> ⚠️ **Mise à jour depuis une version mono-utilisateur antérieure** : un fichier `jobtomail.db` préexistant (avant ce passage au multi-tenant) doit être **supprimé** avant de lancer cette version — il n'existe pas de chemin de migration automatique depuis le schéma mono-tenant.

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

Ces variables sont **opérateur-only** : elles se définissent uniquement dans `.env` (ou l'environnement du conteneur) et ne sont jamais exposées ni modifiables depuis l'onglet Réglages :

| Clé | Utilité | Où l'obtenir |
|---|---|---|
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `GOOGLE_REDIRECT_URI` | **Requis.** Connexion "Se connecter avec Google" — l'application renvoie une erreur 500 dès le premier login sans ces variables. | [console.cloud.google.com](https://console.cloud.google.com/apis/credentials) |
| `APP_ENCRYPTION_KEY` | **Requis.** Chiffrement au repos des refresh tokens Google et des clés SerpAPI/Hunter.io stockées par utilisateur — 500 dès la première sauvegarde de clé sans elle. | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `SECRET_KEY` | Optionnel. Clé de session Flask ; si absente, une clé est générée et persistée en base au premier démarrage (partagée entre workers gunicorn) — à définir explicitement en prod pour un contrôle explicite. | `python -c "import secrets; print(secrets.token_hex(32))"` |
| `INSEE_TOKEN` | Optionnel mais nécessaire pour scanner. Recherche d'entreprises (API Sirene) — clé partagée par l'exploitant, jamais lue ni affichée côté client. | [api.insee.fr](https://api.insee.fr) |

Ces valeurs sont, elles, propres à chaque utilisateur et se règlent depuis l'onglet **Réglages** de l'application (un `.env` peut aussi en définir un override opérateur pour `SERPAPI_KEY`/`TOKEN_HUNTER_IO`, mais ce n'est en général pas ce que vous voulez sur un déploiement multi-utilisateur) :

| Clé | Utilité | Où l'obtenir |
|---|---|---|
| `SERPAPI_KEY` | Enrichissement web des entreprises | [serpapi.com](https://serpapi.com) |
| `TOKEN_HUNTER_IO` | Recherche d'emails de contact | [hunter.io](https://hunter.io) |
| `EMAIL_ADDRESS` / `EMAIL_PASSWORD` | Fonctionnalité de vérification des réponses par IMAP (`/api/check-replies`) — **actuellement inerte** : il n'y a plus d'UI pour renseigner ces valeurs par utilisateur depuis le passage à l'envoi via Gmail OAuth, et elles ne sont volontairement plus lues depuis les variables d'environnement (ça reviendrait à lire la boîte mail de l'exploitant pour tous les utilisateurs). Nécessite un [mot de passe d'application Gmail](https://myaccount.google.com/apppasswords). |

## Base de données

Par défaut, Job2Mail utilise SQLite (`jobtomail.db`), sans configuration nécessaire.

Pour utiliser PostgreSQL ou MariaDB à la place, définissez ces variables
d'environnement (dans `.env` ou l'environnement du conteneur) :

```
DB_BACKEND=postgres   # ou mariadb
DB_HOST=localhost
DB_PORT=5432          # 3306 pour mariadb
DB_USER=jobtomail
DB_PASSWORD=jobtomail
DB_NAME=jobtomail
```

Le choix du backend DB est une décision **exploitant/ops**, pas un réglage
utilisateur : il n'y a pas d'UI pour le changer, et l'endpoint
`/api/db/backend` (utilisé en interne pour tester une connexion hors
`DB_BACKEND`) est réservé aux comptes admin. Sur un déploiement
multi-tenant, changer de backend en cours de route affecte **tous les
utilisateurs** — à ne faire que via les variables d'environnement
ci-dessus, jamais à l'exécution.

Changer de backend démarre avec des tables vides — pas de migration
automatique des données existantes.

### Tester en local avec Docker Compose

```bash
docker compose --profile postgres up      # démarre aussi un conteneur postgres:16-alpine
docker compose --profile mariadb up       # démarre aussi un conteneur mariadb:11
```

Puis dans `.env` : `DB_BACKEND=postgres`, `DB_HOST=postgres`, `DB_PORT=5432`,
`DB_USER=jobtomail`, `DB_PASSWORD=jobtomail`, `DB_NAME=jobtomail` (adapter
pour mariadb : `DB_HOST=mariadb`, `DB_PORT=3306`).

## Architecture

- `app.py` — point d'entrée (`python app.py`).
- `jobtomail/app_factory.py` — création de l'application Flask et middlewares (auth, sessions).
- `jobtomail/routes/` — endpoints HTTP, un blueprint par domaine (config, scans, entreprises, emails, auth).
- `jobtomail/services/` — logique métier (Sirene, géolocalisation, enrichissement, envoi d'emails…).
- `jobtomail/db.py` — accès SQLite (`jobtomail.db`, jamais commité) et configuration persistée.
- `templates/` + `jobtomail/static/` — interface web (une seule page, JS vanilla).

## Licence

[MIT](LICENSE)
