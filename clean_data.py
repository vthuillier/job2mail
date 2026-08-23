"""
Nettoyage et priorisation du CSV d'entreprises généré par sirene_search.py.

Ce que fait ce script :
1. Filtre les entreprises 0-2 salariés (trop petit/solo, mauvais fit pour candidater)
2. Déduplique/fusionne les établissements d'une même entreprise (même SIREN) :
   garde l'établissement le plus pertinent (siège en priorité, sinon le plus ancien),
   liste les autres adresses en complément
3. Reformate proprement : colonnes réordonnées, encodage UTF-8, valeurs vides normalisées
4. Calcule un score de pertinence et trie le fichier du meilleur au moins bon candidat

Usage :
    python nettoyer_csv.py entreprises_tech_var.csv
    (produit entreprises_tech_var_nettoye.csv à côté)
"""

import csv
import sys
from pathlib import Path

# --- Config du scoring ----------------------------------------------------

# Poids relatifs de chaque critère dans le score final (somme = 1.0, ajustable)
POIDS_EFFECTIF = 0.4
POIDS_ANCIENNETE = 0.3
POIDS_SIEGE = 0.2
POIDS_CATEGORIE = 0.1

# Score par tranche d'effectif : on favorise les structures ni trop petites
# (10-19 = trop artisanal pour un process de recrutement structuré) ni énormes
# (10 000+ = process RH lourd, moins accessible pour un junior en direct)
# Le "sweet spot" pour un junior dev est autour de 20-500 salariés.
SCORE_EFFECTIF = {
    "NN": 0.3,
    "00": 0.0,   # exclu de toute façon par le filtre
    "01": 0.0,   # exclu de toute façon par le filtre
    "02": 0.0,   # exclu de toute façon par le filtre
    "03": 0.5,   # 6-9 : petite structure, contact direct facile mais process peu formalisé
    "11": 0.8,   # 10-19
    "12": 1.0,   # 20-49 : sweet spot ESN/startup tech
    "21": 1.0,   # 50-99 : sweet spot
    "22": 0.9,   # 100-199
    "31": 0.8,   # 200-249
    "32": 0.7,   # 250-499
    "41": 0.6,   # 500-999
    "42": 0.5,   # 1000-1999
    "51": 0.4,   # 2000-4999
    "52": 0.3,   # 5000-9999
    "53": 0.3,   # 10000+
}

TRANCHES_EXCLUES = {"00", "01", "02"}  # 0-2 salariés


def score_anciennete(annees) -> float:
    """Favorise les entreprises établies (>2 ans) sans survaloriser à l'infini."""
    if annees is None:
        return 0.3
    try:
        annees = int(annees)
    except (ValueError, TypeError):
        return 0.3
    if annees < 1:
        return 0.2  # très jeune, un peu risqué
    if annees < 3:
        return 0.6
    if annees < 10:
        return 1.0  # établie mais pas figée
    return 0.8  # solide mais moins de mouvement/embauche potentiel


def score_categorie(categorie: str) -> float:
    return {
        "PME": 1.0,       # bon fit général
        "Entreprise de taille intermédiaire": 0.8,
        "Grande entreprise": 0.6,  # process RH plus lourd pour candidature directe
    }.get(categorie, 0.5)


def calculer_score(row: dict) -> float:
    s_effectif = SCORE_EFFECTIF.get(row.get("effectif_code", "NN"), 0.3)
    s_anciennete = score_anciennete(row.get("anciennete_ans"))
    s_siege = 1.0 if row.get("est_siege") == "Oui" else 0.7
    s_categorie = score_categorie(row.get("categorie_entreprise", ""))

    score = (
        s_effectif * POIDS_EFFECTIF
        + s_anciennete * POIDS_ANCIENNETE
        + s_siege * POIDS_SIEGE
        + s_categorie * POIDS_CATEGORIE
    )
    return round(score * 100, 1)  # sur 100 pour lisibilité


def normaliser_valeur(v: str) -> str:
    """Nettoie les valeurs vides/aberrantes en chaînes cohérentes."""
    if v is None:
        return ""
    v = v.strip()
    if v.upper() in ("N/A", "NONE", "NULL", "[ND]", "NAN"):
        return ""
    return v


def charger_csv(chemin: Path) -> list[dict]:
    with open(chemin, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = []
        for row in reader:
            row = {k: normaliser_valeur(v) for k, v in row.items()}
            rows.append(row)
    return rows


def filtrer_effectif(rows: list[dict]) -> list[dict]:
    avant = len(rows)
    filtres = [r for r in rows if r.get("effectif_code", "NN") not in TRANCHES_EXCLUES]
    print(f"  Filtre effectif (exclu 0-2 salariés) : {avant} → {len(filtres)}")
    return filtres


def dedupliquer_par_siren(rows: list[dict]) -> list[dict]:
    """
    Regroupe les établissements par SIREN (= même entreprise, plusieurs adresses).
    Garde l'établissement le plus pertinent comme ligne principale :
    priorité au siège, sinon le plus ancien. Les autres adresses sont listées
    dans une colonne 'autres_adresses'.
    """
    par_siren = {}
    for row in rows:
        siren = row.get("siren") or row.get("siret", "")[:9]
        par_siren.setdefault(siren, []).append(row)

    avant = len(rows)
    resultat = []

    for siren, etabs in par_siren.items():
        if len(etabs) == 1:
            principal = etabs[0]
            principal["nb_etablissements_trouves"] = "1"
            principal["autres_adresses"] = ""
            resultat.append(principal)
            continue

        # Plusieurs établissements pour ce SIREN : on choisit le principal
        sieges = [e for e in etabs if e.get("est_siege") == "Oui"]
        if sieges:
            principal = sieges[0]
        else:
            # sinon le plus ancien (date de création la plus petite)
            def cle_date(e):
                d = e.get("date_creation_etablissement", "")
                return d if d else "9999-99-99"
            principal = sorted(etabs, key=cle_date)[0]

        autres = [e for e in etabs if e is not principal]
        autres_adresses = " | ".join(
            f"{e['adresse']} ({'siège' if e.get('est_siege') == 'Oui' else 'secondaire'})"
            for e in autres
            if e.get("adresse")
        )

        principal["nb_etablissements_trouves"] = str(len(etabs))
        principal["autres_adresses"] = autres_adresses
        resultat.append(principal)

    print(f"  Déduplication par SIREN : {avant} établissement(s) → {len(resultat)} entreprise(s) unique(s)")
    return resultat


def nettoyer(chemin_entree: Path, chemin_sortie: Path):
    print(f"Lecture de {chemin_entree}...")
    rows = charger_csv(chemin_entree)
    print(f"  {len(rows)} ligne(s) chargée(s)\n")

    print("Étape 1 : filtre effectif")
    rows = filtrer_effectif(rows)

    print("\nÉtape 2 : déduplication par SIREN")
    rows = dedupliquer_par_siren(rows)

    print("\nÉtape 3 : calcul du score de pertinence")
    for row in rows:
        row["score_pertinence"] = calculer_score(row)

    print("\nÉtape 4 : tri par score décroissant")
    rows.sort(key=lambda r: r["score_pertinence"], reverse=True)

    # Colonnes finales, dans un ordre pensé pour la prospection :
    # d'abord l'identité et le contact potentiel, puis les critères de qualification
    colonnes_finales = [
        "score_pertinence",
        "denomination",
        "adresse",
        "commune_recherche",
        "effectif_libelle",
        "categorie_entreprise",
        "anciennete_ans",
        "naf_libelle",
        "est_siege",
        "nb_etablissements_trouves",
        "autres_adresses",
        "siret",
        "siren",
        "date_creation_etablissement",
    ]

    with open(chemin_sortie, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=colonnes_finales, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n✓ {len(rows)} entreprise(s) nettoyée(s) et triée(s)")
    print(f"📄 Export : {chemin_sortie}")

    if rows:
        print("\nTop 5 à contacter en priorité :")
        for row in rows[:5]:
            print(f"  [{row['score_pertinence']}/100] {row['denomination']} "
                  f"— {row['effectif_libelle']} — {row['commune_recherche']}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage : python nettoyer_csv.py <fichier_entrant.csv>")
        sys.exit(1)

    chemin_entree = Path(sys.argv[1])
    if not chemin_entree.exists():
        print(f"✗ Fichier introuvable : {chemin_entree}")
        sys.exit(1)

    chemin_sortie = chemin_entree.with_stem(chemin_entree.stem + "_nettoye")
    nettoyer(chemin_entree, chemin_sortie)