from pathlib import Path
from logging import Logger
from log_default import get_logger_default
from abc import ABC, abstractmethod
import importlib
import sys
from utils_ema.image import Image
from utils_ema.config_utils import load_yaml


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


def get_light_controller(light_controller_cfg_path : str = str(Path(__file__).parents[1] / "configs" / "gardasoft_default.yaml"), logger : Logger = None):

    # get logger
    if logger is None:
        logger = get_logger_default()

    # get proper sensor type
    if not Path(light_controller_cfg_path).exists():
        raise FileNotFoundError(f"Light controller config file {light_controller_cfg_path} not found")

    capture_cfg = load_yaml(light_controller_cfg_path)
    sensor_type = capture_cfg.sensor_type
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
    return cls(logger=logger, light_controller_cfg_path=light_controller_cfg_path )

# executable for debug
if __name__ == "__main__":
    logger = get_logger_default()
    lc = get_light_controller(logger = logger)
    lc.led_on(5, only=True)
