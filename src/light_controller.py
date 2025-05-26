from pathlib import Path
from logging import Logger
from omegaconf import DictConfig
from abc import ABC, abstractmethod
import importlib
import sys
from utils_ema.image import Image
from utils_ema.config_utils import DictConfig, load_yaml
from utils_ema.log import get_logger_default
from utils_ema.user_interface import User
import time


class LightControllerAbstract(ABC):

    def __init__(self, logger : Logger, cfg : DictConfig) -> None:
        self.cfg = cfg
        self.logger = logger
        if not self.check_reachability():
            raise ConnectionError("Light controller is not reachable!")
        else:
            self.logger.info("Light controller connection is working")


    @abstractmethod
    def check_reachability(self) -> bool:
        """
            Check if the light controller is reachable.
            Returns True if reachable, False otherwise.
        """
        pass

    @abstractmethod
    def num_leds(self) -> int:
        """
            returns number of lights controlled by this controller
        """
        pass

    @abstractmethod
    def led_on(self, channel, only) -> None:
        """
            turn on a single LED                   
            channel: index of the LED to turn on   
            only: if True, turn off all other LEDs
        """
        pass

    @abstractmethod
    def led_off(self, channel) -> None:
        """
            turn off a single LED
            channel: index of the LED to turn off
        """
        pass

    @abstractmethod
    def leds_on(self) -> None:
        """
            turn on all LEDs
        """
        pass

    @abstractmethod
    def leds_off(self) -> None:
        """
            turn off all LEDs
        """
        pass

    def test_leds(self) -> None:
        """
        Test all LEDs by shifting channels with arrows on keyboard.
        """

        self.logger.info("Testing LEDs. Press 'h' or 'l' to switch channels, and press 'q' to quit.")
        ch = -1
        User.detect_key()
        time_sleep = 0.2
        while True:
            # if user press arrows
            if 'q' in User.keys:
                break
            elif 'l' in User.keys:
                ch += 1
                if ch >= self.num_leds():
                    ch = 0
                self.logger.info(f"Channel {ch}")
                self.led_on(ch, only=True)
                time.sleep(time_sleep)  # to avoid too fast switching
            elif 'h' in User.keys:
                ch -= 1
                if ch < 0:
                    ch = self.num_leds() - 1
                self.logger.info(f"Channel {ch}")
                self.led_on(ch, only=True)
                time.sleep(time_sleep)  # to avoid too fast switching
            elif 'k' in User.keys:
                ch = -1
                self.logger.info(f"All leds on")
                self.leds_on()
                time.sleep(time_sleep)  # to avoid too fast switching
            elif 'j' in User.keys:
                ch = -1
                self.logger.info(f"All leds off")
                self.leds_off()
                time.sleep(time_sleep)  # to avoid too fast switching


def get_light_controller(cfg: DictConfig, logger: Logger = None):
    """
        Get the light controller based on the configuration.
    """

    # null light controller
    if cfg["sensor_type"] == "none":
        logger.info("No light controller specified")
        return None

    # get logger
    if logger is None:
        logger = get_logger_default()

    sensor_type = cfg.sensor_type
    lights_dir = Path(__file__).parent / "lights" / sensor_type

    # check if sensor type is present in folder
    module_path = lights_dir / (sensor_type + ".py")
    if not (module_path).exists():
        raise FileNotFoundError(f"Sensor {sensor_type} not found in {lights_dir}")

    # Load module dynamically
    spec = importlib.util.spec_from_file_location(sensor_type, module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cls = getattr(module, "LightController")
    return cls(logger=logger, cfg=cfg)


# executable for debug
if __name__ == "__main__":
    logger = get_logger_default()
    lc = get_light_controller(logger=logger)
    lc.led_on(5, only=True)
