# Transformation JobToMail en SaaS grand public — Design

Date: 2026-09-15
Statut: validé par l'utilisateur, prêt pour plan d'implémentation

## Contexte

JobToMail est aujourd'hui une app mono-utilisateur, self-hosted (Docker ou
Python local), pilotée par un `APP_PASSWORD` unique et des clés API
personnelles définies dans `.env`. L'objectif est de la transformer en
service hébergé (SaaS) utilisable par un utilisateur non technique, sans
installation, sans configuration de clés API complexe, financé par un usage
gratuit limité + publicité.

Ce document couvre uniquement les changements **applicatifs** (code). L'infra
(serveur, domaine, déploiement, paiement des coûts API) est hors scope et
sera traitée séparément.

## Décisions validées

- **Hébergement** : SaaS, un seul déploiement multi-utilisateurs.
- **Authentification** : magic link (email) + connexion Google OAuth. Plus de
  mot de passe applicatif unique.
- **Clés API externes** : hybride.
  - `INSEE_TOKEN` (Sirene) : fourni centralement par l'exploitant du service,
    partagé entre tous les utilisateurs (gratuit, pas de friction).
  - `SERPAPI_KEY` / `TOKEN_HUNTER_IO` : optionnels, fournis par l'utilisateur
    s'il veut l'enrichissement avancé. Absents → l'app dégrade proprement
    (pas d'enrichissement dirigeants/contacts, scan de base fonctionne quand
    même).
- **Envoi d'emails** : connexion Gmail OAuth uniquement (remplace le SMTP +
  mot de passe d'application). L'email part depuis la boîte Gmail réelle de
  l'utilisateur.
- **Modèle économique** : gratuit avec quotas mensuels, publicité intégrée
  pour financer les coûts (API tierces + hébergement).
- **Panneau admin** : oui — liste des utilisateurs, ajustement des quotas,
  vue d'usage global (appels API, emails envoyés).
- **Données existantes** (`jobtomail.db` actuel) : reset propre, pas de
  migration. Le nouveau schéma multi-tenant démarre vide.
- **Multi-tenant** : Approche A — schéma partagé, isolation par colonne
  `user_id`, compatible avec le support SQLite/Postgres/MariaDB existant.

## Architecture

### 1. Authentification & comptes (`jobtomail/routes/auth.py`)

Remplace le mot de passe unique par une table `users` :

```
users
  id            INTEGER PK
  email         TEXT UNIQUE NOT NULL
  google_sub    TEXT UNIQUE NULL      -- id Google si connecté via OAuth
  created_at    TEXT
  is_admin      INTEGER DEFAULT 0
```

Deux parcours de connexion :

- **Magic link** : l'utilisateur saisit son email → lien à usage unique
  envoyé (token signé, expiration 15 min) → clic connecte et crée le compte
  si inexistant.
- **Google OAuth** : flux OAuth2 standard (`google_sub` + email récupérés du
  token Google), crée le compte si inexistant.

La session Flask stocke `user_id` (remplace l'actuel `session["authenticated"]`
booléen). Le verrouillage anti-bruteforce actuel (`_failed_attempts`,
`_locked_until`) est conservé pour la génération de lien magique (limiter les
demandes de lien par IP/email).

Le compte Google connecté pour l'OAuth de connexion et celui utilisé pour
l'envoi Gmail (section 4) peuvent être le même flux de consentement,
demandant à la fois `openid email` et `gmail.send` d'un coup, pour éviter un
second aller-retour OAuth à l'utilisateur.

### 2. Multi-tenant — schéma de données (`jobtomail/schema.py`, `jobtomail/db.py`)

Ajout d'une colonne `user_id` (FK vers `users.id`) sur les tables existantes
qui contiennent des données propres à un utilisateur :

- `entreprises` : clé primaire actuelle `siret` devient `(user_id, siret)` —
  deux utilisateurs peuvent avoir scanné la même entreprise indépendamment.
- `processed_replies` : ajoute `user_id`.
- `jobs` : ajoute `user_id`.
- `config` (clé/valeur globale actuelle) : devient propre à l'utilisateur —
  soit ajout `user_id` à la clé primaire composite `(user_id, key)`, soit
  nouvelle table `user_config` dédiée (préféré, plus clair que de garder le
  nom `config` ambigu). Les réglages qui restent globaux (quotas par défaut,
  activation pub) vont dans une nouvelle table `app_config` séparée, réservée
  à l'admin.

Toute requête lisant/écrivant ces tables doit être filtrée par `user_id` de
la session courante. Point d'attention : centraliser ce filtrage (helper
`db.for_user(user_id)` ou équivalent) plutôt que de le répéter partout à la
main, pour éviter un oubli qui fuiterait des données entre utilisateurs.

### 3. Clés API hybrides (`jobtomail/db_config.py`, `jobtomail/services/`)

- `INSEE_TOKEN` : reste une variable d'environnement globale côté serveur
  (fournie par l'exploitant), jamais exposée à l'utilisateur.
- `SERPAPI_KEY`, `TOKEN_HUNTER_IO` : stockées par utilisateur dans
  `user_config` (chiffrées au repos, voir section 8). Les services
  d'enrichissement (`serpapi`, `hunter`) vérifient leur présence avant appel
  et retournent un résultat "dégradé" (pas d'erreur bloquante) si absentes.
  L'UI indique clairement "enrichissement avancé désactivé — ajoute ta clé
  SerpAPI dans Réglages pour l'activer" plutôt qu'un blocage.

### 4. Envoi email via Gmail OAuth (`jobtomail/services/mailer.py`)

Remplace `smtplib.SMTP_SSL` + mot de passe d'application par l'API Gmail
(`gmail.send` scope) :

- Stockage du `refresh_token` OAuth par utilisateur (chiffré, table
  `user_google_tokens`).
- `mailer.py` échange le refresh token contre un access token à chaque envoi
  (ou cache court), construit le message MIME identique à aujourd'hui, et
  l'envoie via `users.messages.send` de l'API Gmail au lieu de SMTP direct.
- Le comportement fonctionnel (relance, tracking `email_sent_at`,
  `relance_count`, détection de réponses) ne change pas, seul le transport
  change.
- Si le token est révoqué/expiré, message d'erreur clair invitant à
  reconnecter Gmail depuis Réglages.

### 5. Quotas d'usage (`jobtomail/services/quotas.py` — nouveau)

Nouvelle table :

```
usage_counters
  user_id       INTEGER
  period        TEXT   -- "2026-09" (mois courant)
  scans_count   INTEGER DEFAULT 0
  emails_count  INTEGER DEFAULT 0
  PRIMARY KEY (user_id, period)
```

Limites par défaut proposées (modifiables via panneau admin, stockées dans
`app_config`) :

- 30 recherches d'entreprises (scans Sirene) / mois / utilisateur.
- 50 emails envoyés (candidatures + relances confondues) / mois / utilisateur.

Un middleware/décorateur vérifie le compteur avant chaque action consommatrice
(lancement de scan, envoi d'email), incrémente après succès, et renvoie un
message clair côté UI ("Quota mensuel de recherches atteint (30/30). Réinitialisation le 1er du mois.")
plutôt qu'une erreur technique.

### 6. Panneau admin (`jobtomail/routes/admin.py` — nouveau)

Accessible seulement si `users.is_admin = 1`. Fonctionnalités :

- Liste des utilisateurs (email, date création, compteurs usage du mois).
- Ajustement des quotas par défaut globaux (`app_config`) et, si besoin,
  quota personnalisé pour un utilisateur précis (table `usage_counters`
  supporte une limite par-user optionnelle en plus du défaut global).
- Vue globale : nombre total d'appels INSEE/mois (pour surveiller le quota
  API partagé), nombre d'emails envoyés, nombre d'inscriptions.
- Toggle activation/désactivation des publicités.

### 7. Publicité (`templates/`, `jobtomail/static/`)

Composant "slot pub" générique et agnostique de régie :

```html
<div class="ad-slot" data-slot="sidebar-main"></div>
```

Le rendu réel (script AdSense ou autre) est injecté conditionnellement côté
template selon `app_config.ads_enabled` et un identifiant de régie stocké en
config admin — jamais codé en dur, pour pouvoir changer de régie sans
toucher au reste du code. Proposition par défaut : Google AdSense (gratuit,
pas de seuil de trafic minimum).

### 8. Sécurité & RGPD

- Clés API utilisateur (SerpAPI, Hunter.io) et refresh tokens Google :
  chiffrés au repos (ex: `cryptography.fernet` avec clé serveur dédiée,
  distincte de `SECRET_KEY` session).
- Isolation stricte par `user_id` (voir section 2) — un utilisateur ne doit
  jamais pouvoir lire les données d'un autre.
- Consentement RGPD explicite à la première connexion : case à cocher
  "j'utilise cet outil dans le cadre d'une recherche d'emploi personnelle et
  je m'engage à respecter le RGPD / les CNU des services tiers" (reprend
  l'avertissement déjà présent dans le README actuel, déplacé dans l'UI).
- Suppression de compte : bouton "Supprimer mon compte et mes données"
  (purge `entreprises`, `processed_replies`, `jobs`, `user_config`,
  `user_google_tokens`, `usage_counters` du user).

### 9. Simplification UX / vocabulaire (repris du plan initial)

En parallèle des changements structurels ci-dessus :

- Renommage jargon : "Scan Sirene" → "Rechercher des entreprises", masquage
  des codes NAF bruts derrière la recherche par mot-clé déjà existante,
  section "Base de données avancée" (choix Postgres/MariaDB) masquée par
  défaut (n'a plus de sens en SaaS géré — à supprimer entièrement de l'UI
  utilisateur, le backend DB devient un choix d'exploitant unique).
- Parcours guidé première connexion : 1) connexion (magic link/Google) →
  2) upload CV → 3) zone de recherche → 4) connecter Gmail (obligatoire pour
  envoyer) → 5) clés optionnelles (SerpAPI/Hunter) → 6) lancer une recherche.
- Messages d'erreur reformulés en langage clair partout où une clé API ou un
  quota bloque une action.

## Hors scope (ce document)

- Infrastructure d'hébergement, nom de domaine, CI/CD, certificats.
- Choix définitif du prestataire publicitaire (juste l'emplacement générique).
- Tarification payante / abonnement (uniquement gratuit+quota+pub pour cette
  phase).
- Migration des données de l'instance actuelle (reset propre décidé).

## Risques identifiés

- **Coût clé INSEE partagée** : quota API Sirene partagé entre tous les
  utilisateurs — surveiller consommation globale via panneau admin, prévoir
  cache/rate-limit interne si approche des limites INSEE.
- **Révocation token Gmail** : gérer proprement l'expiration/révocation côté
  utilisateur (message clair, pas de crash silencieux des relances
  planifiées).
- **Modification lourde du schéma `entreprises`** (clé primaire composite) :
  nécessite migration SQL soignée même en reset propre, car le code actuel
  suppose `siret` unique globalement à plusieurs endroits (services
  d'enrichissement, jobs, requêtes) — à auditer avant implémentation.
