import sys
from pathlib import Path
sys.path.append(Path(__file__).parents[2].as_posix())
from camera_controller import get_camera_controller

def test_basler_availability():
    camera_controller = get_camera_controller('basler')
    assert(camera_controller.load_devices())

def test_images():
    camera_controller = get_camera_controller('basler')
    camera_controller.load_devices()
    camera_controller.grab_images()
