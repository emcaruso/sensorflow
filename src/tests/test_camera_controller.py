from camera_controller import get_camera_controller
from pathlib import Path
import pytest

def test_camera_controller():

    camera_dir = Path(__file__).parent / 'cameras'
    
    camera_controller = get_camera_controller(Path(__file__).stem, camera_dir)
    camera_controller.load_devices()
    camera_controller.grab_images()
