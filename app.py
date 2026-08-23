"""Point d'entrée : python app.py"""

from __future__ import annotations

import logging
import os

from jobtomail import create_app

app = create_app()
logger = logging.getLogger(__name__)


if __name__ == "__main__":
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    logger.info("Démarrage serveur → http://127.0.0.1:5001 (debug=%s)", debug)
    if debug:
        logger.warning(
            "FLASK_DEBUG=1 — débogueur Werkzeug actif. NE JAMAIS exposer ce port "
            "hors localhost dans ce mode (exécution de code arbitraire)."
        )
    app.run(host="127.0.0.1", port=5001, debug=debug)
