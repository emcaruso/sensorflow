import sys
import numpy as np
import torch
import os
from pathlib import Path
from logging import Logger
from pypylon import pylon
from omegaconf import DictConfig, OmegaConf
from utils_ema.multiprocess import map_unordered, run_function_in_parallel
from utils_ema.image import Image

# local imports
sys.path.append(Path(__file__).parents[2].as_posix())
sys.path.append(Path(__file__).parent.as_posix())
from camera_controller import CameraControllerAbstract
from utils_basler import fps2microseconds, microseconds2fps
import multiprocessing as mp


def get_default_dict():
    return OmegaConf.load(Path(__file__).parent / "config_default.yaml")

class CameraController(CameraControllerAbstract):

    def __init__(self, logger : Logger, capture_cfg : DictConfig = OmegaConf.load(Path(__file__).parent / "capture_cfg_default.yaml")):
        
        self.capture_cfg = capture_cfg
        self.logger = logger
        self.cams_available = False
        self.image_buffer = mp.Queue()
        


    @property
    def num_cameras(self) -> int:
        return self.n_devices

    @num_cameras.setter
    def num_cameras(self, val : int):
        self.n_devices = val


    def load_devices(self) -> bool:

        # get cameras
        self.tlf = pylon.TlFactory.GetInstance()
        self.devices = self.tlf.EnumerateDevices([pylon.DeviceInfo(),])
        self.n_devices = len(self.devices)
        if self.n_devices == 0:
            self.logger.error("No devices detected!")
            self.cams_available = False
            return False
        self.cam_array = pylon.InstantCameraArray(self.n_devices)
        for i, cam in enumerate(self.cam_array):
            cam.Attach(self.tlf.CreateDevice(self.devices[i]))
        self.cams_available = True

        # set converter
        self.converter = pylon.ImageFormatConverter()
        self.converter.OutputPixelFormat = getattr(pylon,self.capture_cfg.converter.val)

        # image array
        self.image_array = mp.Array("i", self.n_devices)

        self.logger.info(f"{self.n_devices} devices detected")
        return True

    def synchronize_cameras(self) -> bool:
        for cam in self.cam_array:
            if cam.BslPeriodicSignalSource.Value != 'PtpClock':
                cam.BslPtpPriority1.Value = 128
                cam.BslPtpProfile.Value = "DelayRequestResponseDefaultProfile"
                cam.BslPtpNetworkMode.Value = "Unicast"
                cam.BslPtpUcPortAddrIndex.Value = 0
                cam.BslPtpUcPortAddr.Value = 0xC0A80A0C
                cam.BslPtpManagementEnable.Value = True
                cam.BslPtpTwoStep.Value = False
                cam.PtpEnable.Value = True
        return True

    def set_camera_fps(self, cam: pylon.InstantCamera, fps : float) -> None:
        cam.BslPeriodicSignalPeriod = fps2microseconds(fps)
        cam.BslPeriodicSignalDelay = 0
        cam.TriggerSelector.Value = "FrameStart"
        cam.TriggerMode.Value = "On"
        cam.TriggerSource.Value = "PeriodicSignal1"

    def set_camera_crop(self):
        if self.capture_cfg.crop.do:
            slot = self.capture_cfg.crop.slot
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
            self.set_camera_fps(cam, self.capture_cfg.fps)
            cam.BslColorSpace.Value = self.capture_cfg.color_space.val
            cam.PixelFormat.Value = self.capture_cfg.pixel_format.val
            cam.ExposureTime.SetValue(self.capture_cfg.exposure_time)
            self.set_camera_crop()
            cam.SetCameraContext(i)

        return True

    def __open_cameras(self) -> bool:
        if not self.cams_available:
            self.logger.error("Cannot start camera, no cameras available!")
            return False

        if self.cam_array.IsGrabbing():
            self.logger.error("Starting alreading grabbing cameras!")
            return False

        self.cam_array.Open()
        return True

    def stop_cameras(self) -> None:
        self.cam_array.Close()

    def start_cameras_asynchronous(self, cfg = None) -> bool:
        opened = self.__open_cameras()
        if opened:
            self.cam_array.StartGrabbing(getattr(pylon,self.capture_cfg.grab_strategy.val))  # fast
            self.logger.info("Cameras started asynchronously")
            return True
        return False
    
    def start_cameras_synchronous(self, cfg = None) -> bool:

        opened = self.__open_cameras()
        if opened:
            self.synchronize_cameras()
            self.set_cameras_config()
            self.cam_array.StartGrabbing(getattr(pylon,self.capture_cfg.grab_strategy.val))  # fast
            self.logger.info("Cameras started synchronously")
            return True
        return False

    def __grab_image_base(self, cam : pylon.InstantCamera, dtype=torch.float32 ) -> Image:
        grabResult = cam.RetrieveResult(
            self.capture_cfg.timeout, pylon.TimeoutHandling_ThrowException
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

    def grab_image(self, cam : pylon.InstantCamera, dtype=torch.float32 ) -> Image:
        # self.logger.debug("Grabbing image with camera: "+ str(cam.GetContextInfo()) )
        grabResult = self.__grab_image_base(cam, dtype)
        img = self.__process_result(grabResult, dtype)
        return img

    
    def grab_images(self, dtype=torch.float32):
        # self.logger.debug("Grabbing images")
        res = []
        imgs = []
        for cam in self.cam_array:
            res.append( self.__grab_image_base(cam, dtype) )
        for r in res:
            imgs.append(self.__process_result(r, dtype))
        return imgs


    def show_stream(self):
        pass

    def show_streams(self):
        while True:
            imgs = self.grab_images()
            k = Image.show_multiple_images(imgs, wk=1)

            if k == ord("q"):
                break
