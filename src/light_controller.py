from pathlib import Path
from logging import Logger
from omegaconf import DictConfig
from abc import ABC, abstractmethod
import importlib
import sys
from utils_ema.image import Image
from utils_ema.config_utils import DictConfig, load_yaml
from utils_ema.log import get_logger_default

class LightControllerAbstract(ABC):

    @property
    @abstractmethod
    def num_lights(self):
        pass

    @num_lights.setter
    @abstractmethod
    def num_lights(self, val):
        pass
    
    @abstractmethod
    def led_on():
        pass

    @abstractmethod
    def led_off():
        pass

    @abstractmethod
    def leds_on():
        pass

    @abstractmethod
    def leds_off():
        pass


def get_light_controller(cfg : DictConfig = None, logger : Logger = None):

    # null light controller
    if cfg is None:
        return None

    # get logger
    if logger is None:
        logger = get_logger_default()

    sensor_type = cfg.sensor_type
    lights_dir = Path(__file__).parent / 'lights' / sensor_type

    # check if sensor type is present in folder
    module_path = lights_dir / (sensor_type+".py")
    if not (module_path).exists():
        raise FileNotFoundError(f"Sensor {sensor_type} not found in {lights_dir}")


    # Load module dynamically
    spec = importlib.util.spec_from_file_location(sensor_type, module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cls = getattr(module, "LightController")
    return cls(logger=logger, cfg=cfg )

# executable for debug
if __name__ == "__main__":
    logger = get_logger_default()
    lc = get_light_controller(logger = logger)
    lc.led_on(5, only=True)
