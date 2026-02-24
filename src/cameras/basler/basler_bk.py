import sys
import time
from collections import defaultdict
import os
import torch
from pathlib import Path
from logging import Logger
from pypylon import pylon
from omegaconf import DictConfig
from typing import Dict, List, Optional, Tuple
from utils_ema.image import Image
from utils_ema.config_utils import load_yaml
from copy import deepcopy
import threading
import multiprocessing as mp
import queue

# local imports
sys.path.append(Path(__file__).parents[2].as_posix())
sys.path.append(Path(__file__).parent.as_posix())
from camera_controller import CameraControllerAbstract
from utils_basler import fps2microseconds
from synchronization import synchronize_cameras
from circular_buffer import SharedCircularBuffer


class StoppableThread(threading.Thread):
    def __init__(self, stop_event, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.stop_event = stop_event

    def stop(self):
        self.stop_event.set()


class CameraController:
    def __init__(self, logger: Logger, cfg: DictConfig):
        self.cfg = cfg
        self.logger = logger

        # --------------------------------------------------
        # 1️⃣ Detect cameras in parent process
        # --------------------------------------------------
        tlf = pylon.TlFactory.GetInstance()
        devices = tlf.EnumerateDevices([pylon.DeviceInfo()])
        self.num_cameras = len(devices)

        if self.num_cameras == 0:
            raise ValueError("No cameras detected")

        # --------------------------------------------------
        # 2️⃣ Create shared circular buffer using real count
        # --------------------------------------------------
        self.circular_buffer = SharedCircularBuffer(
            self.cfg.buffer_size, self.num_cameras
        )

        self.buffer_id = mp.Value("i", 0)
        self.lock = mp.Lock()

        # --------------------------------------------------
        # 3️⃣ IPC primitives
        # --------------------------------------------------
        event_init = mp.Event()
        pipe_child, pipe_parent = mp.Pipe()

        self.event_start_grabbing = mp.Event()
        self.event_stop_grabbing = mp.Event()

        # --------------------------------------------------
        # 4️⃣ Start worker process
        # --------------------------------------------------
        self.process = mp.Process(
            target=self.init_worker,
            daemon=True,
            args=(
                event_init,
                pipe_child,
                self.event_start_grabbing,
                self.event_stop_grabbing,
                self.circular_buffer,
                self.buffer_id,
                self.lock,
            ),
        )

        self.process.start()

        # Wait for worker initialization
        event_init.wait()

        # Receive devices info from worker
        self.devices_info = pipe_parent.recv()

    def init_worker(
        self,
        event_init: mp.Event,
        pipe_child,
        event_start_grabbing,
        event_stop_grabbing,
        circular_buffer,
        buffer_id,
        lock,
    ) -> None:
        worker = CameraControllerWorker(self.logger, self.cfg, event_init, pipe_child)
        worker.run(
            event_start_grabbing, event_stop_grabbing, circular_buffer, buffer_id, lock
        )

    def start_grabbing(self) -> None:
        self.event_stop_grabbing.clear()
        self.event_start_grabbing.set()

    def stop_grabbing(self) -> None:
        self.event_start_grabbing.clear()
        self.event_stop_grabbing.set()

    def close(self):
        self.circular_buffer.close()
        self.process.join()

    def get_images(self) -> Tuple[List[Image], int]:
        with self.lock:
            id = self.buffer_id.value
        images = self.circular_buffer.get_buffer(id)
        if images is not None:
            images = [Image(img) for img in images]
        return images, id

    def get_devices_info(self):
        return self.devices_info


class CameraControllerWorker(CameraControllerAbstract):
    def __init__(
        self,
        logger: Logger,
        cfg: DictConfig,
        event_init: mp.Event,
        pipe_child,
    ) -> None:
        self.cfg = cfg
        self.logger = logger
        self.load_devices()
        self.cam_results = None
        self.cam_ids = None
        event_init.set()
        time.sleep(1)
        pipe_child.send(self.get_devices_info())
        pipe_child.close()
        self.logger.info("Basler camera controller worker initialized")

    def run(
        self,
        event_start: mp.Event,
        event_stop: mp.Event,
        circular_buffer: SharedCircularBuffer,
        buffer_id: mp.Value,
        lock: mp.Lock,
        verbose: bool = True,
    ) -> None:

        # while True:

        self.logger.info("Camera worker waiting to start grabbing...")
        event_start.wait()
        if self.cfg.synch:
            self.start_cameras_synchronous_oneByOne(verbose=verbose)
        else:
            self.start_cameras_asynchronous_oneByOne(verbose=verbose)
        self.logger.info("Camera worker started grabbing...")

        counter = 0
        while not event_stop.is_set():
            images = self.grab_images()
            id = counter % self.cfg.buffer_size
            circular_buffer.append(images, id)
            with lock:
                buffer_id.value = id
            counter += 1
        self.logger.info("Camera worker stopped grabbing...")
        self.stop_grabbing()

        circular_buffer.close()

    @property
    def num_cameras(self) -> int:
        return self.n_devices

    @num_cameras.setter
    def num_cameras(self, val: int):
        self.n_devices = val

    def load_features(self):
        for i, cam in enumerate(self.cam_array):
            device = self.devices[i]
            sn = device.GetSerialNumber()
            mn = device.GetModelName()
            iden = f"{mn}_{sn}"
            path = Path(self.cfg.pfs_dir) / f"{iden}.pfs"
            os.makedirs(self.cfg.pfs_dir, exist_ok=True)
            if path.exists():
                self.logger.info(f"Loading features for camera {iden}")
                pylon.FeaturePersistence_Load(str(path), cam.GetNodeMap(), True)
            else:
                self.logger.info(f"Saving features for camera {iden}")
                pylon.FeaturePersistence_Save(str(path), cam.GetNodeMap())

    def load_devices(self) -> None:
        # get devices
        self.tlf = pylon.TlFactory.GetInstance()
        self.devices = self.tlf.EnumerateDevices(
            [
                pylon.DeviceInfo(),
            ]
        )
        self.n_devices = len(self.devices)
        if self.n_devices == 0:
            error_msg = "No devices detected!"
            self.logger.error(error_msg)
            raise ValueError(error_msg)

        # get cameras
        self.cam_array = pylon.InstantCameraArray(self.n_devices)
        for i, cam in enumerate(self.cam_array):
            cam.Attach(self.tlf.CreateDevice(self.devices[i]))

        # set converter
        self.converter = pylon.ImageFormatConverter()
        self.converter.OutputPixelFormat = getattr(pylon, self.cfg.converter.val)

        # logger
        self.logger.info(f"{self.n_devices} Basler camera detected")

        # load pfs files
        self.open_cameras()
        self.load_features()
        self.get_devices_info()
        self.set_cameras_config()

    def set_camera_fps(self, cam: pylon.InstantCamera, fps: float) -> None:
        cam.AcquisitionFrameRateEnable.Value = True
        cam.AcquisitionFrameRate.Value = fps
        # cam.AcquisitionFrameRate.Value = 500
        cam.BslPeriodicSignalPeriod = fps2microseconds(fps)
        # cam.BslPeriodicSignalPeriod = fps2microseconds(10)
        cam.BslPeriodicSignalDelay = self.cfg.trigger.delay
        # cam.BslPeriodicSignalDelay = 100000
        # cam.TriggerSelector.Value = "FrameStart"
        # cam.TriggerSelector.Value = "ExposureStart"
        cam.TriggerSource.Value = "PeriodicSignal1"
        cam.TriggerMode.Value = "On"

    def set_trigger_ouput(self, cam: pylon.InstantCamera) -> None:
        cam.BslPeriodicSignalDelay.Value = 0
        cam.LineSelector.Value = self.cfg.trigger.line
        cam.LineMode.Value = "Output"
        cam.LineSource.Value = "ExposureActive"

    def camera_is_exposing(self, cam_id: int) -> bool:
        cam = self.cam_array[cam_id]
        cam.LineSelector.SetValue(self.cfg.trigger.line)
        return cam.LineStatus.GetValue()

    def wait_exposure_end(self, cam_id: int) -> bool:
        cam = self.cam_array[cam_id]
        cam.LineSelector.SetValue(self.cfg.trigger.line)
        wasexposing = cam.LineStatus.GetValue()
        while True:
            isexposing = cam.LineStatus.GetValue()
            if wasexposing and not isexposing:
                return True
            wasexposing = isexposing

    def set_camera_crop(self) -> None:
        if self.cfg.crop.do:
            slot = self.cfg.crop.slot
            for cam in self.cam_array:
                cam.BslMultipleROIRowsEnable.Value = True
                cam.BslMultipleROIColumnsEnable.Value = True
                cam.BslMultipleROIColumnSelector.Value = "Column" + str(slot)
                cam.BslMultipleROIRowSelector.Value = "Row" + str(slot)
        else:
            for cam in self.cam_array:
                cam.BslMultipleROIRowsEnable.Value = False
                cam.BslMultipleROIColumnsEnable.Value = False
                cam.Height.Value = cam.SensorHeight.Value
                cam.Width.Value = cam.SensorWidth.Value

    # def check_real_fps(self):
    #     self.logger.info("Checking real fps...")
    #     self.start_cameras_synchronous_latest(verbose=False)
    #     period_nominal = 1 / self.cfg.trigger.fps
    #
    #     # get real fps
    #     self.wait_exposure_end(0)
    #     t1 = time.time()
    #     self.wait_exposure_end(0)
    #     period_real = time.time() - t1
    #     if period_real > period_nominal + 0.05:
    #         error_msg = f"Real fps is {1 / period_real}, less than nominal fps: {1 / period_nominal}"
    #         self.logger.warning(error_msg)
    #     fps = 1 / period_real
    #     self.stop_cameras()
    #     return fps

    def set_cameras_config(self) -> bool:
        # fps = self.check_real_fps()
        for i, cam in enumerate(self.cam_array):

            # self.set_camera_fps(cam, fps)
            self.set_camera_fps(cam, self.cfg.trigger.fps)
            cam.BslColorSpace.Value = "Off"
            cam.Gain.Value = self.cfg.gain
            cam.Gamma.Value = self.cfg.gamma
            cam.BslColorSpace.Value = self.cfg.color_space.val
            cam.PixelFormat.SetValue(self.cfg.pixel_format.val)
            cam.ExposureTime.SetValue(self.cfg.exposure_time)
            self.set_camera_crop()
            self.set_trigger_ouput(cam)  # set output trigger from master
            cam.SetCameraContext(i)

        return True

    def open_cameras(self) -> None:
        if not self.cam_array.IsOpen():
            self.cam_array.Open()

    def stop_grabbing(self) -> None:
        self.cam_array.StopGrabbing()
        for t in self.threads:
            t.stop()
            t.join()

    def start_grabbing(self) -> None:
        if not self.cam_array.IsGrabbing():
            self.cam_array.StartGrabbing()

    def stop_cameras(self) -> None:
        # self.is_running = False
        # if self.thread_collector is not None:
        #     self.thread_collector.join()

        # if self.cam_array.IsOpen():
        self.cam_array.Close()

    def __results_collector(self) -> None:

        camera_ids = list(range(self.n_devices))

        # status = []
        # for cam in self.cam_array:
        #     cam.PtpDataSetLatch()
        #     status.append(cam.PtpStatus.GetValue())
        #     print(cam.PtpOffsetFromMaster.GetValue())
        #     if cam.PtpServoStatus.GetValue() != "Locked":
        #         self.logger.warning("Camera not locked")

        # # count masters and slaves
        # n_masters = sum([1 for s in status if s == "Master"])
        # n_slaves = sum([1 for s in status if s == "Slave"])
        # assert n_masters == 1 and n_slaves == (self.n_devices - 1)

        results = {}
        ids = []
        # cam_ids = []

        for i, q in enumerate(self.queues):
            results[i] = q.get()

        for i in camera_ids:
            #     res = self.__grab_image_base(self.cam_array)
            id = results[i].GetBlockID()
            ids.append(id)
        #     cam_id = res.GetCameraContext()
        #     self.logger.info(f"Grabbed image ID {id} from camera {cam_id}")
        #     cam_ids.append(cam_id)
        #     results[cam_id] = res
        # self.logger.info(f" ")
        if len(set(ids)) > 1:
            self.logger.warning(
                f"Grabbed images have different IDs: {ids}, possible synchronization issue"
            )
        results = [results[cam_id] for cam_id in camera_ids]
        # # for _ in range(15):
        # results = [
        #     self.__grab_image_base(self.cam_array[cam_id]) for cam_id in camera_ids
        # ]
        return results
        # while self.is_running:

        # for cam in self.cam_array:
        #     cam.PtpDataSetLatch()

        # results = [self.__grab_image_base(self.cam_array) for _ in camera_ids]
        # with self.lock:
        #     self.cam_results = results

        # ids = [r.GetID() for r in results]
        # cam_ids = [r.GetCameraContext() for r in results]
        # images = {k: self.__process_result(v) for k, v in zip(cam_ids, results)}
        # print(ids)
        # return images, ids

        # # cam_id = res.GetCameraContext()
        # id = res.GetID()
        # print(id)
        # import ipdb
        #
        # ipdb.set_trace()
        # print(ids)

        #     results[cam_id] = res
        # ids = [r.GetID() for r in results.values()]
        # print(ids)
        # self.cam_results = {k: self.__process_result(v) for k, v in results.items()}
        # self.cam_ids = ids

    def __start_base(self, strategy: str, synch: bool, verbose: bool = True) -> None:
        self.open_cameras()

        if synch:
            success = synchronize_cameras(self.cam_array, self.logger)
            if not success:
                error_msg = "Cameras could not be synchronized"
                self.logger.error(error_msg)
                raise ValueError(error_msg)

        self.queues = [queue.Queue() for _ in range(self.n_devices)]
        stop_event = threading.Event()
        self.threads = [
            StoppableThread(
                stop_event=stop_event,
                target=self.__grab_image_base,
                args=(stop_event, self.cam_array[i], self.queues[i]),
                daemon=True,
            )
            for i in range(self.n_devices)
        ]

        if not self.cam_array.IsGrabbing():
            self.cam_array.StartGrabbing(getattr(pylon, strategy))

        for t in self.threads:
            t.start()

        if verbose:
            self.logger.info(f"Cameras started, synch = {synch}, strategy = {strategy}")

        # self.__results_collector()
        # self.thread_collector = threading.Thread(target=self.__results_collector)
        # self.thread_collector.start()
        # while self.cam_results is None:
        #     time.sleep(0.1)

    def start_cameras_asynchronous_latest(self, verbose: bool = True) -> None:
        self.__start_base(
            synch=False,
            # strategy="GrabStrategy_LatestImages",
            strategy="GrabStrategy_LatestImageOnly",
            # strategy="GrabStrategy_UpcomingImage",
            # strategy="GrabStrategy_OneByOne",
            verbose=verbose,
            # synch=False, strategy="GrabStrategy_UpcomingImage",verbose=verbose,
        )

    def start_cameras_synchronous_latest(self, verbose: bool = True) -> None:
        self.__start_base(
            synch=True,
            # strategy="GrabStrategy_LatestImages",
            strategy="GrabStrategy_LatestImageOnly",
            # strategy="GrabStrategy_UpcomingImage",
            # strategy="GrabStrategy_OneByOne",
            verbose=verbose,
            # synch=False, strategy="GrabStrategy_UpcomingImage",verbose=verbose,
        )

    def start_cameras_asynchronous_oneByOne(self, verbose: bool = True) -> None:
        self.__start_base(
            synch=False, strategy="GrabStrategy_OneByOne", verbose=verbose
        )

    def start_cameras_synchronous_oneByOne(self, verbose: bool = True) -> None:
        self.__start_base(synch=True, strategy="GrabStrategy_OneByOne", verbose=verbose)

    def __grab_image_base(self, stop_event, cam: pylon.InstantCamera, queue) -> Image:
        while not stop_event.is_set():
            grabResult = cam.RetrieveResult(
                self.cfg.timeout, pylon.TimeoutHandling_ThrowException
            )
            if grabResult is not None:
                queue.put(grabResult)

    def __process_result(
        self, grabResult: pylon.GrabResult, dtype=torch.uint8
    ) -> Image:
        if grabResult.GrabSucceeded():
            if self.converter is not None:
                img = self.converter.Convert(grabResult).GetArray()
            else:
                img = grabResult.GetArray()
            grabResult.Release()
            # img = Image(img=torch.from_numpy(img), dtype=dtype)
            return img
        return None

    def grab_image(self, cam_id: int, dtype=torch.float32) -> Image:
        cam = self.cam_array[cam_id]
        while True:
            grabResult, _ = self.__grab_image_base(cam)
            if grabResult.GrabSucceeded():
                break
        img = self.__process_result(grabResult, dtype)
        return img

    def grab_images(
        self, camera_ids: Optional[List[int]] = None, dtype=torch.float32
    ) -> List[Image]:
        camera_ids = list(range(self.n_devices)) if camera_ids is None else camera_ids

        cam_results = self.__results_collector()
        images = [self.__process_result(res) for res in cam_results]
        return images

    def show_stream(self, cam_id: int) -> None:
        cam = self.cam_array[cam_id]
        while True:
            img = self.grab_image(cam)
            k = img.show(wk=1)
            if k == ord("q"):
                break

    def show_streams(self) -> None:
        while True:
            imgs = self.grab_images()
            k = Image.show_multiple_images(imgs, wk=1)
            if k == ord("q"):
                break

    def get_devices_info(self) -> Dict:
        devices_info = {}

        # get cam sensorsize
        path = Path(__file__).parent / "basler_sensorsizes.yaml"
        assert path.exists()
        pixelsizes = load_yaml(str(path))

        # get cam infos
        for i, device in enumerate(self.devices):
            cam_name = "cam_" + str(i).zfill(3)
            devices_info[cam_name] = {}

            for info_key in self.cfg.camera_info:
                info = None
                try:
                    info = getattr(device, "Get" + info_key)()
                except:
                    info = getattr(self.cam_array[i], info_key)
                devices_info[cam_name][info_key] = info

            # crop info
            cam = self.cam_array[i]
            cam_info = {}
            cam_info["resolution_native"] = [
                cam.SensorWidth.GetValue(),
                cam.SensorHeight.GetValue(),
            ]
            cam_info["crop_resolution"] = [
                cam.BslMultipleROIColumnSize.GetValue(),
                cam.BslMultipleROIRowSize.GetValue(),
            ]
            cam_info["crop_offset"] = [
                cam.BslMultipleROIColumnOffset.GetValue(),
                cam.BslMultipleROIRowOffset.GetValue(),
            ]
            cam_info["crop_selection"] = [
                cam.BslMultipleROIColumnsEnable.GetValue(),
                cam.BslMultipleROIRowsEnable.GetValue(),
            ]
            for key in cam_info:
                devices_info[cam_name][key] = cam_info[key]

            # pixelsize info
            assert "ModelName" in devices_info[cam_name]
            model_name = devices_info[cam_name]["ModelName"]
            if model_name not in pixelsizes:
                error_msg = f"Model {model_name} not found in pixelsizes, put the sensorsize in file {path}"
                self.logger.error(error_msg)
                raise ValueError(error_msg)
            sensor_size = pixelsizes[model_name]
            devices_info[cam_name]["PixelSizeMicrometers"] = sensor_size
        return devices_info
