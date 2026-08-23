"""Delete companies with no reported headcount (effectif_code empty, null, or 'NN').

Usage:
    python prune_sans_effectif.py
    python prune_sans_effectif.py --dry-run
    python prune_sans_effectif.py --verbose --log-file prune_sans_effectif.log
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

from jobtomail import db

logger = logging.getLogger("prune_sans_effectif")

# Codes INSEE consideres comme "pas d'effectif renseigne".
# "" / None : champ vide.
# "NN"      : effectif non diffusible (secret statistique) -> traite comme non renseigne.
MISSING_EFFECTIF_CODES = {"", "NN"}


def setup_logging(verbose: bool, quiet: bool, log_file: str | None) -> None:
    if quiet:
        level = logging.WARNING
    elif verbose:
        level = logging.DEBUG
    else:
        level = logging.INFO

    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(fmt)
    logger.addHandler(console)

    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)
        logger.debug("Logging vers fichier active: %s", log_file)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Supprime les entreprises sans effectif (employe) renseigne."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Ne supprime rien, affiche seulement ce qui serait supprime.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Afficher les logs detailles (DEBUG), entreprise par entreprise.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="N'afficher que les avertissements/erreurs.",
    )
    parser.add_argument(
        "--log-file",
        type=str,
        default="",
        help="Chemin d'un fichier ou ecrire tous les logs (niveau DEBUG complet).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    setup_logging(args.verbose, args.quiet, args.log_file or None)

    t_start = time.monotonic()
    logger.info("=== Demarrage prune_sans_effectif ===")
    if args.dry_run:
        logger.info("Mode dry-run: aucune suppression ne sera effectuee")

    logger.debug("Chargement des entreprises depuis la base...")
    rows = [dict(r) for r in db.list_entreprises()]
    initial_count = len(rows)
    logger.info("Entreprises en base: %d", initial_count)

    deleted = 0
    for item in rows:
        code = (item.get("effectif_code") or "").strip().upper()
        if code in MISSING_EFFECTIF_CODES:
            nom = item.get("nom") or item.get("denomination") or item.get("siret")
            if args.dry_run:
                logger.debug("A supprimer (effectif non renseigne): siret=%s nom=%s", item.get("siret"), nom)
            else:
                logger.debug("Suppression (effectif non renseigne): siret=%s nom=%s", item.get("siret"), nom)
                db.delete_entreprise(item["siret"])
            deleted += 1

    final_count = initial_count - deleted if args.dry_run else len(db.list_entreprises())
    elapsed = time.monotonic() - t_start

    logger.info("=== Resume ===")
    logger.info("initial=%d", initial_count)
    logger.info("sans_effectif=%d", deleted)
    logger.info("final=%d", final_count)
    logger.info("duree totale=%.1fs", elapsed)

    print("DONE")
    print(f"initial={initial_count}")
    print(f"deleted_no_effectif={deleted}")
    print(f"final={final_count}")
    print(f"dry_run={args.dry_run}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
