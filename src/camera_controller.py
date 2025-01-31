from pathlib import Path
from logging import Logger
from log_default import get_logger_default
from abc import ABC, abstractmethod
import importlib
import sys


class CameraControllerAbstract(ABC):

    @property
    @abstractmethod
    def num_cameras(self):
        pass

    @num_cameras.setter
    @abstractmethod
    def num_cameras(self, val):
        pass
    #
    # @staticmethod
    # @abstractmethod
    # def init_safe(): # init cameras and check if they are available
    #     pass
    
    @abstractmethod
    def start_cameras_asynchronous():
        pass

    @abstractmethod
    def start_cameras_synchronous():
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

    # @abstractmethod
    # def show_stream():
    #     pass

    @abstractmethod
    def show_streams():
        pass

    pass

def get_camera_controller(sensor_type : str, logger : Logger = None, camera_configs : dict = None):

    if logger is None:
        logger = get_logger_default()

    camera_dir = Path(__file__).parent / 'cameras' / sensor_type

    # check if sensor is present in folder
    module_path = camera_dir / (sensor_type+".py")
    if not (module_path).exists():
        raise FileNotFoundError(f"Sensor {sensor_type} not found in {camera_dir}")

    # Load module dynamically
    spec = importlib.util.spec_from_file_location(sensor_type, module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cls = getattr(module, "CameraController")
    return cls(logger=logger)

# executable for debug
if __name__ == "__main__":
    logger = get_logger_default()
    cam_controller = get_camera_controller("basler", logger)
    cam_controller.load_devices()
    cam_controller.start_cameras_synchronous()
    cam_controller.show_streams()

