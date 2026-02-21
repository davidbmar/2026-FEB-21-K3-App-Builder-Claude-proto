import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


def main():
    log.info("Job starting: %s", os.environ.get("APP_NAME", "job"))
    # TODO: Add your job logic here
    log.info("Job complete")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.error("Job failed: %s", e)
        sys.exit(1)
