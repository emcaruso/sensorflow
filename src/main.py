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

    os.environ["ROOT"] =str(os.getcwd()) 
    OmegaConf.resolve(cfg)

    # init logger
    logger = get_logger_default(out_path=cfg.paths.log_file)

    # run the program
    logger.info("Program started.")
    run(cfg, logger)
    logger.info("Program ended.")


# run the program
def run(cfg: DictConfig, logger: Logger):
    coll = Collector(logger=logger, cfg = cfg)

    images_list = []
    if cfg.test_lights: 
        if coll.light_controller is None:
            raise ValueError("No light controller specified in the config file.")
        else:
            coll.light_controller.test_leds()

    else:
        if cfg.mode.val == "manual":
            images_list, postprocessed = coll.capture_manual()
        elif cfg.mode.val == "light_sequence":
            images_list, postprocessed = coll.capture_light_sequence()

        if cfg.save.raw:
            res = coll.save(images_list)
            if res: 
                logger.info(f"Raw images saved in {cfg.paths.save_dir}")
        if cfg.save.postprocessed:
            res = coll.save(postprocessed)
            if res: 
                logger.info(f"Postprocessed images saved in {cfg.paths.save_dir}")


if __name__ == "__main__":
    main()
