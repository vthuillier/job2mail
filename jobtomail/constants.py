"""Domaines / annuaires à ignorer pour trouver le site officiel."""

from __future__ import annotations

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "jobtomail.db"
CV_PATH = BASE_DIR / "cv.pdf"
LOGS_DIR = BASE_DIR / "logs"
TEMPLATES_DIR = BASE_DIR / "templates"

DEFAULT_NAF_CODES = {
    "62.01Z": "Programmation informatique",
    "62.02A": "Conseil en systèmes et logiciels informatiques",
    "62.02B": "Tierce maintenance de systèmes informatiques",
    "62.03Z": "Gestion d'installations informatiques",
    "62.09Z": "Autres activités informatiques",
    "63.11Z": "Traitement de données, hébergement",
    "58.29C": "Édition de logiciels applicatifs",
    "71.12B": "Ingénierie, études techniques",
}

# NAF reconnus comme « informatique / numérique » pour JobToMail
IT_NAF_CODES = frozenset(DEFAULT_NAF_CODES.keys())
IT_NAF_PREFIXES = ("62.", "63.11", "58.29", "71.12")

# Catégories juridiques INSEE ciblées hors entreprises privées
# https://www.insee.fr/fr/information/2028129
MAIRIE_CATEGORIES_JURIDIQUES = {
    "7210": "Commune et commune nouvelle",
}

ASSOCIATION_CATEGORIES_JURIDIQUES = {
    "9220": "Association déclarée",
    "9221": "Association déclarée d'insertion par l'économique",
    "9222": "Association déclarée reconnue d'utilité publique",
    "9230": "Association de droit local",
    "9260": "Association de droit étranger",
}

NATURE_LABELS = {
    "entreprise": "Entreprise",
    "mairie": "Mairie",
    "association": "Association",
}

TRANCHE_EFFECTIFS = {
    "NN": "Non renseigné",
    "00": "0 salarié",
    "01": "1 ou 2 salariés",
    "02": "3 à 5 salariés",
    "03": "6 à 9 salariés",
    "11": "10 à 19 salariés",
    "12": "20 à 49 salariés",
    "21": "50 à 99 salariés",
    "22": "100 à 199 salariés",
    "31": "200 à 249 salariés",
    "32": "250 à 499 salariés",
    "41": "500 à 999 salariés",
    "42": "1 000 à 1 999 salariés",
    "51": "2 000 à 4 999 salariés",
    "52": "5 000 à 9 999 salariés",
    "53": "10 000 salariés et plus",
}

MOTS_CLES_POSTE = [
    "CTO",
    "tech lead",
    "responsable technique",
    "directeur technique",
    "VP Engineering",
    "Head of Engineering",
    "RH",
    "recrutement",
    "recruteur",
    "talent acquisition",
    "People Manager",
    "fondateur",
    "co-fondateur",
    "PDG",
    "directeur",
    "gérant",
]

# Cadence relances (jours depuis dernier contact)
RELANCE_1_DELAY_DAYS = 5
RELANCE_2_DELAY_DAYS = 7
MAX_RELANCES = 2

# Statuts jamais supprimés par le prune (candidatures en cours ou passées)
PRUNE_PROTECTED_STATUSES = frozenset({
    "postule",
    "relance",
    "entretien",
    "offre",
    "refus",
    "hors_champs",
})

# Seuil Hunter.io en dessous duquel on bloque l'envoi (sauf force)
HUNTER_SCORE_MIN_OK = 70
HUNTER_SCORE_MIN_WARN = 50

EMAIL_DOMAINES_GENERIQUES = frozenset({
    "gmail.com",
    "googlemail.com",
    "yahoo.fr",
    "yahoo.com",
    "hotmail.com",
    "hotmail.fr",
    "outlook.com",
    "outlook.fr",
    "live.com",
    "live.fr",
    "msn.com",
    "icloud.com",
    "me.com",
    "protonmail.com",
    "proton.me",
    "orange.fr",
    "wanadoo.fr",
    "free.fr",
    "sfr.fr",
    "laposte.net",
    "aol.com",
})

EMAIL_PREFIXES_ROLE = frozenset({
    "contact",
    "info",
    "hello",
    "bonjour",
    "admin",
    "office",
    "accueil",
    "support",
    "sales",
    "commercial",
    "noreply",
    "no-reply",
    "donotreply",
})

# Annuaires, réseaux sociaux, presse — tout sauf le site de la boîte
DOMAINES_BLOQUES = (
    "linkedin.com",
    "facebook.com",
    "twitter.com",
    "x.com",
    "instagram.com",
    "youtube.com",
    "tiktok.com",
    "wikipedia.org",
    "wikidata.org",
    "societe.com",
    "societe-info.com",
    "pappers.fr",
    "verif.com",
    "manageo.fr",
    "infogreffe.fr",
    "infonet.fr",
    "rubypayeur.com",
    "kompass.com",
    "pagesjaunes.fr",
    "118000.fr",
    "118712.fr",
    "annuaire-entreprises.data.gouv.fr",
    "info-mairie.com",
    "entreprises.lefigaro.fr",
    "entreprises.lagazettefrance.fr",
    "score3.fr",
    "eterritoire.fr",
    "jenprofite.com",
    "societe-info.com",
    "companycheck.co.uk",
    "crunchbase.com",
    "glassdoor.",
    "indeed.com",
    "welcometothejungle.com",
    "monster.fr",
    "apec.fr",
    "pole-emploi.fr",
    "francetravail.fr",
    "legifrance.gouv.fr",
    "service-public.fr",
    "data.gouv.fr",
    "bdc.ca",
    "zoominfo.com",
    "dnb.com",
    "bloomberg.com",
    "lesechos.fr",
    "latribune.fr",
    "usinenouvelle.com",
    "journaldunet.com",
    "bfmtv.com",
)

# Pour l'opérateur Google -site: (les plus fréquents)
DOMAINES_EXCLUS_RECHERCHE = (
    "societe.com",
    "pappers.fr",
    "verif.com",
    "manageo.fr",
    "infogreffe.fr",
    "pagesjaunes.fr",
    "linkedin.com",
    "facebook.com",
    "annuaire-entreprises.data.gouv.fr",
    "info-mairie.com",
    "entreprises.lefigaro.fr",
)

# Alias rétrocompat
DIRECTORIES_BLOQUES = DOMAINES_BLOQUES

SIRENE_BASE_URL = "https://api.insee.fr/api-sirene/3.11/siret"
RECHERCHE_ENTREPRISES_URL = "https://recherche-entreprises.api.gouv.fr/search"
GEO_API_URL = "https://geo.api.gouv.fr/communes"
BAN_API_URL = "https://api-adresse.data.gouv.fr/search/"
SERPAPI_URL = "https://serpapi.com/search"
HUNTER_EMAIL_FINDER_URL = "https://api.hunter.io/v2/email-finder"
OLLAMA_BASE_URL = "http://127.0.0.1:11434"
OLLAMA_MODEL = "qwen3:0.6b"

FRENCHTECH_BASE_URL = "https://www.frenchtechtoulon.fr"
FRENCHTECH_ANNUAIRE_URL = f"{FRENCHTECH_BASE_URL}/annuaire-general"
# Thèmes annuaire retenus si tech_only=True (sous-chaînes, accents ignorés)
FRENCHTECH_TECH_THEMES = (
    "informatique",
    "cybersecurite",
    "deeptech",
    "edtech",
    "fintech",
    "healthtech",
    "e-sante",
    "smarttech",
    "greentech",
    "cleantech",
    "biotechnologies",
    "proptech",
    "foodtech",
    "iot",
    "mobilite",
    "communication / graphisme",
    "conseil & accompagnement",
    "conseil au developpement",
)

MAIL_SUBJECT = "Pas le profil parfait sur le papier, mais j'aimerais vous convaincre autrement"

DEFAULT_MAIL_BODY = """Bonjour {salutation} {nom},

{accroche}
Je vous écris directement plutôt que de laisser un CV parler seul pour moi, parce qu'il y a des choses qu'un CV ne dit pas toujours.

[Ici, en 2-3 phrases : votre parcours, ce qui vous motive concrètement pour ce poste, et ce qui vous différencie.]

Je sais que mon profil ne coche pas forcément toutes les cases sur le papier, mais je suis quelqu'un qui apprend vite, qui s'investit à fond une fois qu'on lui fait confiance. Si vous me donnez une chance de vous le prouver, même juste lors d'un échange téléphonique, je suis convaincu que vous ne le regretterez pas.

Je suis disponible [rapidement / à partir de telle date] et je reste à votre écoute, même pour 10 minutes au téléphone si ça vous convient mieux qu'un mail.

Merci pour votre temps, et merci de m'avoir lu jusqu'au bout.

[Votre nom]
[Votre téléphone]

P.S. : mon CV est en pièce jointe, mais j'espère que ce mail vous aura dit quelque chose qu'il ne dit pas.
"""

DEFAULT_RELANCE_BODY = """Bonjour {salutation} {nom},

Je me permets de revenir vers vous suite à mon message concernant une opportunité au sein de {denomination}.

Je comprends que vous êtes certainement très sollicité(e), mais je reste sincèrement motivé et disponible pour un échange, même bref, si mon profil peut vous intéresser.

Merci encore pour votre temps.

[Votre nom]
[Votre téléphone]
"""

DEFAULT_RELANCE2_BODY = """Bonjour {salutation} {nom},

Je reviens une dernière fois vers vous au sujet de {denomination}.

Pour être concret : je suis disponible rapidement et j'apprends très vite ce qui peut me manquer. Si un besoin existe côté équipe — même junior / mid — je serais ravi d'échanger 10 minutes.

Sinon, n'hésitez pas à me le dire : je ne relancerai pas davantage.

Merci encore pour votre attention.

[Votre nom]
[Votre téléphone]
"""
