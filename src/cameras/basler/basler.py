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
import time

# local imports
sys.path.append(Path(__file__).parents[2].as_posix())
sys.path.append(Path(__file__).parent.as_posix())
from camera_controller import CameraControllerAbstract
from utils_basler import fps2microseconds, microseconds2fps
import multiprocessing as mp


class CameraController(CameraControllerAbstract):

    def __init__(self, logger : Logger, capture_cfg : DictConfig = OmegaConf.load(Path(__file__).parent / "capture_cfg_default.yaml"), cameras_config_path : str = str(Path(__file__ ).parents[3] / "data" / "pfs_files")):
        
        self.capture_cfg = capture_cfg
        self.cameras_config_path = cameras_config_path
        self.logger = logger
        self.cams_available = False
        self.image_buffer = mp.Queue()
        


    @property
    def num_cameras(self) -> int:
        return self.n_devices

    @num_cameras.setter
    def num_cameras(self, val : int):
        self.n_devices = val

    def load_features(self):
        if Path(self.cameras_config_path).exists():
            for i, cam in enumerate(self.cam_array):
                device = self.devices[i]
                sn = device.GetSerialNumber()
                mn = device.GetModelName()
                iden = f"{mn}_{sn}"
                path = Path(self.cameras_config_path) / f"{iden}.pfs"
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
        self.converter.OutputPixelFormat = getattr(pylon,self.capture_cfg.converter.val)

        # logger
        self.logger.info(f"{self.n_devices} Basler camera detected")

        # load pfs files
        self.__open_cameras()
        self.load_features()
        


    def synchronize_cameras(self) -> bool:
        for i, cam in enumerate(self.cam_array):
            if cam.BslPeriodicSignalSource.Value != 'PtpClock':
                self.logger.info(f"Syncing camera {i}...")
                cam.PtpEnable.Value = False
                cam.BslPtpPriority1.Value = 128
                cam.BslPtpProfile.Value = "DelayRequestResponseDefaultProfile"
                cam.BslPtpNetworkMode.Value = "Multicast"
                cam.BslPtpTwoStep.Value = False
                cam.PtpEnable.Value = True

                # Wait until correctly initialized or timeout
                time1 = time.time()
                while True:
                    cam.PtpDataSetLatch.Execute()
                    synced = (cam.PtpStatus.GetValue() in ['Master', 'Slave'])
                    if synced:
                        self.logger.info(f"Camera {i} synced as {cam.PtpStatus.GetValue()}")
                        break 

                    if (time.time() - time1) > 30:
                        self.logger.warning('PTP not locked -> Timeout')
                        return False

        return True

    def set_camera_fps(self, cam: pylon.InstantCamera, fps : float, i : int) -> None:
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
            self.set_camera_fps(cam, self.capture_cfg.fps, i)
            cam.BslColorSpace.Value = self.capture_cfg.color_space.val
            cam.PixelFormat.Value = self.capture_cfg.pixel_format.val
            cam.ExposureTime.SetValue(self.capture_cfg.exposure_time)
            self.set_camera_crop()
            cam.SetCameraContext(i)

        return True

    def __open_cameras(self) -> bool:
        self.cam_array.Open()
        return True

    def stop_cameras(self) -> None:
        self.cam_array.Close()

    def start_cameras_asynchronous(self, cfg = None) -> None:
        self.cam_array.StartGrabbing(getattr(pylon,self.capture_cfg.grab_strategy.val))  # fast
        self.logger.info("Cameras started asynchronously")
    
    def start_cameras_synchronous(self, cfg = None) -> None:
        self.synchronize_cameras()
        self.set_cameras_config()
        self.cam_array.StartGrabbing(getattr(pylon,self.capture_cfg.grab_strategy.val))  # fast
        self.logger.info("Cameras started synchronously")

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
