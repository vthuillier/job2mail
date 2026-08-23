"""Point d'entrée : python app.py"""

from __future__ import annotations

import logging

from jobtomail import create_app

app = create_app()
logger = logging.getLogger(__name__)


if __name__ == "__main__":
    logger.info("Démarrage serveur → http://127.0.0.1:5001")
    app.run(host="0.0.0.0", port=5001, debug=True)
