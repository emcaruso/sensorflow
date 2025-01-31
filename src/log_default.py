import logging
from logging import Logger
from pathlib import Path


def get_logger_default() -> Logger:
    """
    Initialize the global logger with custom settings.
    """

    # Create a new logger
    logger = logging.getLogger("global_logger")
    logger.setLevel(logging.DEBUG)

    # Create the log format
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    # Create a handler for writing logs to a file (if log_file is provided)
    fh = logging.FileHandler(Path(__file__).parent.parent / "data" / "run.log")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    # Create a console handler
    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    return logger
