from pathlib import Path
from logging import Logger
from abc import ABC, abstractmethod
import importlib
from omegaconf import DictConfig
from utils_ema.log import get_logger_default


class CameraControllerAbstract(ABC):

    @property
    @abstractmethod
    def num_cameras(self):
        pass

    @num_cameras.setter
    @abstractmethod
    def num_cameras(self, val):
        pass
    
    @abstractmethod
    def start_cameras_asynchronous_oneByOne():
        pass

    @abstractmethod
    def start_cameras_synchronous_oneByOne():
        pass

    @abstractmethod
    def start_cameras_asynchronous_latest():
        pass

    @abstractmethod
    def start_cameras_synchronous_latest():
        pass

    @abstractmethod
    def wait_exposure_end():
        pass

    @abstractmethod
    def open_cameras():
        pass

    @abstractmethod
    def stop_cameras():
        pass

    @abstractmethod
    def grab_images():
        pass

    @abstractmethod
    def grab_image():
        pass

    @abstractmethod
    def show_stream():
        pass

    @abstractmethod
    def show_streams():
        pass

    @abstractmethod
    def get_devices_info():
        pass



def get_camera_controller(cfg : DictConfig, logger : Logger = None):

    # get logger
    if logger is None:
        logger = get_logger_default()

    # get proper sensor type
    sensor_type = cfg.sensor_type
    camera_dir = Path(__file__).parent / 'cameras' / sensor_type

    # check if sensor type is present in folder
    module_path = camera_dir / (sensor_type+".py")
    if not (module_path).exists():
        raise FileNotFoundError(f"Sensor {sensor_type} not found in {camera_dir}")

    # Load module dynamically
    spec = importlib.util.spec_from_file_location(sensor_type, module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cls = getattr(module, "CameraController")
    return cls(logger=logger, cfg=cfg)

