import os, sys
import hydra
from hydra.core.hydra_config import HydraConfig
from omegaconf import DictConfig, OmegaConf
from pathlib import Path
from logging import Logger
from utils_ema.log import get_logger_default
from collector import Collector


# load conf with hydra and run
@hydra.main(version_base=None)
def main(cfg: DictConfig):

    os.environ["ROOT"] = str(os.getcwd())
    OmegaConf.resolve(cfg)

    # init logger
    logger = get_logger_default(out_path=cfg.paths.log_file)

    # run the program
    logger.info("Program started.")
    run(cfg, logger)
    logger.info("Program ended.")


# run the program
def run(cfg: DictConfig, logger: Logger):
    coll = Collector(logger=logger, cfg=cfg)

    # prototype
    if cfg.test_lights:
        if coll.light_controller is None:
            raise ValueError("No light controller specified in the config file.")
        else:
            coll.light_controller.test_leds()
    elif "lights_on" in cfg.keys():
        if coll.light_controller is None:
            raise ValueError("No light controller specified in the config file.")
        else:
            coll.light_controller.leds_on()
    elif "lights_off" in cfg.keys():
        if coll.light_controller is None:
            raise ValueError("No light controller specified in the config file.")
        else:
            coll.light_controller.leds_off()

    # collection
    else:
        images_list = []

        if cfg.mode.val == "manual":
            # coll.capture_manual()
            coll.capture_manual()
        elif cfg.mode.val == "light_sequence":
            # images_list, postprocessed = coll.capture_light_sequence()
            coll.capture_light_sequence()


if __name__ == "__main__":
    main()
