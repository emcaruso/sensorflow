import sys
import time
import torch
from pathlib import Path
from logging import Logger
from pypylon import pylon
from omegaconf import DictConfig
from utils_ema.image import Image
from utils_ema.config_utils import load_yaml
from utils_ema.multiprocess import run_in_multiprocess

# local imports
sys.path.append(Path(__file__).parents[2].as_posix())
sys.path.append(Path(__file__).parent.as_posix())
from camera_controller import CameraControllerAbstract
from utils_basler import fps2microseconds, microseconds2fps
from synchronization import synchronize_cameras
import multiprocessing as mp


class CameraController(CameraControllerAbstract):

    def __init__(self, capture_cfg_path : str, logger : Logger):
        self.capture_cfg_path =capture_cfg_path 
        self.cfg = load_yaml(capture_cfg_path)
        self.logger = logger
        self.load_devices()


    @property
    def num_cameras(self) -> int:
        return self.n_devices

    @num_cameras.setter
    def num_cameras(self, val : int):
        self.n_devices = val

    def load_features(self):
        if not Path(self.cfg.pfs_path).exists():
            raise ValueError(f"Path {self.cfg.pfs_path} does not exist")
        else:
            for i, cam in enumerate(self.cam_array):
                device = self.devices[i]
                sn = device.GetSerialNumber()
                mn = device.GetModelName()
                iden = f"{mn}_{sn}"
                path = Path(self.cfg.pfs_path) / f"{iden}.pfs"
                if path.exists():
                    self.logger.info(f"Loading features for camera {iden}")
                    pylon.FeaturePersistence_Load(str(path), cam.GetNodeMap(), True)

    def load_devices(self) -> None:

        # get cameras
        self.tlf = pylon.TlFactory.GetInstance()
        self.devices = self.tlf.EnumerateDevices([pylon.DeviceInfo(),])
        self.n_devices = len(self.devices)
        if self.n_devices == 0:
            error_msg = "No devices detected!"
            self.logger.error(error_msg)
            raise ValueError(error_msg)

        self.cam_array = pylon.InstantCameraArray(self.n_devices)
        for i, cam in enumerate(self.cam_array):
            cam.Attach(self.tlf.CreateDevice(self.devices[i]))

        # set converter
        self.converter = pylon.ImageFormatConverter()
        self.converter.OutputPixelFormat = getattr(pylon,self.cfg.converter.val)

        # logger
        self.logger.info(f"{self.n_devices} Basler camera detected")

        # load pfs files
        self.open_cameras()
        self.load_features()

    def set_camera_fps(self, cam: pylon.InstantCamera, fps : float) -> None:
        cam.BslPeriodicSignalPeriod = fps2microseconds(fps)
        cam.BslPeriodicSignalDelay = self.cfg.trigger.delay
        cam.TriggerSelector.Value = "FrameStart"
        cam.TriggerMode.Value = "On"
        cam.TriggerSource.Value = "PeriodicSignal1"

    def set_trigger_ouput(self, cam : pylon.InstantCamera) -> None:
        cam.BslPeriodicSignalDelay.Value = 0
        cam.LineSelector.Value = self.cfg.trigger.line
        cam.LineMode.Value = "Output"
        cam.LineSource.Value = "ExposureActive"

    def camera_is_exposing(self, cam_id : int) -> bool:
        cam = self.cam_array[cam_id]
        cam.LineSelector.SetValue(self.cfg.trigger.line)
        return cam.LineStatus.GetValue()

    def wait_exposure_end(self, cam_id : int) -> bool:
        cam = self.cam_array[cam_id]
        cam.LineSelector.SetValue(self.cfg.trigger.line)
        wasexposing = cam.LineStatus.GetValue()
        while True:
            isexposing = cam.LineStatus.GetValue()
            if wasexposing and not isexposing:
                return True
            wasexposing = isexposing

    def set_camera_crop(self):
        if self.cfg.crop.do:
            slot = self.cfg.crop.slot
            for cam in self.cam_array:
                cam.BslMultipleROIRowsEnable.Value = True
                cam.BslMultipleROIColumnsEnable.Value = True
                cam.BslMultipleROIColumnSelector.Value = "Column"+str(slot)
                cam.BslMultipleROIRowSelector.Value = "Row"+str(slot)
        else:
            for cam in self.cam_array:
                cam.BslMultipleROIRowsEnable.Value = False
                cam.BslMultipleROIColumnsEnable.Value = False
                cam.Height.Value = cam.SensorHeight.Value
                cam.Width.Value = cam.SensorWidth.Value

    def set_cameras_config(self) -> bool:

        for i, cam in enumerate(self.cam_array):
            self.set_camera_fps(cam, self.cfg.trigger.fps)
            cam.BslColorSpace.Value = self.cfg.color_space.val
            cam.PixelFormat.Value = self.cfg.pixel_format.val
            cam.ExposureTime.SetValue(self.cfg.exposure_time)
            self.set_camera_crop()
            self.set_trigger_ouput(cam) # set output trigger from master
            cam.SetCameraContext(i)

        return True

    def open_cameras(self) -> None:
        if not self.cam_array.IsOpen():
            self.cam_array.Open()

    def stop_cameras(self) -> None:
        if self.cam_array.IsOpen():
            self.cam_array.Close()

    def __start_base(self, strategy : str, synch : bool, verbose : bool = True):
        self.open_cameras()
        if synch:
            success = synchronize_cameras(self.cam_array, self.logger)
            if not success:
                error_msg = "Cameras could not be synchronized"
                self.logger.error(error_msg)
                raise ValueError(error_msg)
        self.set_cameras_config()
        self.cam_array.StartGrabbing(getattr(pylon,strategy))  # fast
        if verbose:
            self.logger.info(f"Cameras started, synch = {synch}, strategy = {strategy}")

    def start_cameras_asynchronous_latest(self, verbose : bool = True) -> None:
        self.__start_base(synch = False, strategy = "GrabStrategy_LatestImages", verbose=verbose)
    
    def start_cameras_synchronous_latest(self, verbose : bool = True) -> None:
        self.__start_base(synch = True, strategy = "GrabStrategy_LatestImages", verbose=verbose)

    def start_cameras_asynchronous_oneByOne(self, verbose : bool = True) -> None:
        self.__start_base(synch = False, strategy = "GrabStrategy_OneByOne", verbose=verbose)

    def start_cameras_synchronous_oneByOne(self, verbose : bool = True) -> None:
        self.__start_base(synch = True, strategy = "GrabStrategy_OneByOne", verbose=verbose)


    def __grab_image_base(self, cam : pylon.InstantCamera, dtype=torch.float32 ) -> Image:
        grabResult = cam.RetrieveResult(
            self.cfg.timeout, pylon.TimeoutHandling_ThrowException
        )
        return grabResult

    def __process_result(self, grabResult : pylon.GrabResult, dtype=torch.float32) -> Image:
        if grabResult.GrabSucceeded():
            if self.converter is not None:
                img = self.converter.Convert(grabResult).GetArray()
            else:
                img = grabResult.GetArray()
            grabResult.Release()
            img = Image(img=torch.from_numpy(img), dtype=dtype)
            return img
        return None

    def grab_image(self, cam_id : int, dtype=torch.float32 ) -> Image:
        cam = self.cam_array[cam_id]
        grabResult = self.__grab_image_base(cam, dtype)
        img = self.__process_result(grabResult, dtype)
        return img

    
    def grab_images(self, dtype=torch.float32):
        res = []
        imgs = []
        for cam in self.cam_array:
            res.append(self.__grab_image_base(cam, dtype))
        for r in res:
            imgs.append(self.__process_result(r, dtype))
        return imgs


    def show_stream(self, cam_id : int):
        cam = self.cam_array[cam_id]
        while True:
            img = self.grab_image(cam)
            k = img.show(wk=1)

            if k == ord("q"):
                break
        pass

    def show_streams(self):
        while True:
            imgs = self.grab_images()
            k = Image.show_multiple_images(imgs, wk=1)

            if k == ord("q"):
                break
