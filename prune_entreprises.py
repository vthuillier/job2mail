"""Filter companies by headcount and travel time.

Usage:
    python prune_entreprises.py
    python prune_entreprises.py --min-employees 3 --max-travel-min 45
    python prune_entreprises.py --origin "La Crau" --force-recompute
    python prune_entreprises.py --verbose
    python prune_entreprises.py --quiet
    python prune_entreprises.py --log-file prune.log
"""

from __future__ import annotations

import argparse
import logging
import sys

from jobtomail.services.prune import run_prune

logger = logging.getLogger("prune_entreprises")


def setup_logging(verbose: bool, quiet: bool, log_file: str | None) -> None:
    """Configure the module logger with console + optional file output."""
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

    # Remonte aussi les logs du service
    svc = logging.getLogger("jobtomail.services.prune")
    svc.setLevel(logging.DEBUG)
    svc.handlers.clear()
    svc.addHandler(console)
    svc.propagate = False

    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)
        svc.addHandler(file_handler)
        logger.debug("Logging vers fichier active: %s", log_file)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Delete companies with too small headcount and too long travel time."
    )
    parser.add_argument("--min-employees", type=int, default=3, help="Minimum company size to keep.")
    parser.add_argument(
        "--max-travel-min",
        type=float,
        default=45.0,
        help="Maximum travel time (minutes) to keep.",
    )
    parser.add_argument(
        "--origin",
        type=str,
        default="",
        help="Travel origin (default: config point_ref or 'La Crau').",
    )
    parser.add_argument(
        "--force-recompute",
        action="store_true",
        help="Ignore cached travel values and recompute all routes.",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.05,
        help="Pause between route calls (seconds).",
    )
    parser.add_argument(
        "--delete-unavailable",
        action="store_true",
        help="Also delete companies whose travel could not be computed.",
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

    logger.info("=== Demarrage prune_entreprises ===")
    try:
        result = run_prune(
            min_employees=args.min_employees,
            max_travel_min=args.max_travel_min,
            origin=args.origin or None,
            force_recompute=args.force_recompute,
            sleep=args.sleep,
            delete_unavailable_travel=args.delete_unavailable,
        )
    except ValueError as e:
        logger.error("%s", e)
        return 1

    print("DONE")
    for key in (
        "origin",
        "initial",
        "deleted_lt_min_employees",
        "after_headcount_filter",
        "travel_processed",
        "travel_computed",
        "travel_unavailable",
        "deleted_gt_max_travel",
        "final",
    ):
        print(f"{key}={result[key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
