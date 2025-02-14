import os, sys
from typing import List
sys.path.append(os.path.dirname(os.path.realpath(__file__)))
from shutil import rmtree
from pathlib import Path
from omegaconf import DictConfig
from logging import Logger
import multiprocessing as mp
import time
import omegaconf
from utils_ema.image import Image
from utils_ema.config_utils import load_yaml
from utils_ema.log import get_logger_default
from camera_controller import get_camera_controller
from light_controller import get_light_controller
from postprocessing import Postprocessing


class Collector():
    def __init__(self, logger : Logger, cfg : DictConfig):
        self.logger = logger
        self.cfg = cfg
        self.light_controller = get_light_controller(cfg=self.cfg.lights, logger = logger)
        self.cam_controller = get_camera_controller(cfg=self.cfg.cameras, logger = logger)
        self.postprocessing = Postprocessing(cfg=self.cfg.postprocessings)
        self.collection_cfg = self.cfg.strategies
        self.check_real_fps()

    def check_real_fps(self):
        self.logger.info("Checking real fps...")
        self.cam_controller.start_cameras_synchronous_latest(verbose = False)
        period_nominal = (1/self.cam_controller.cfg.trigger.fps)

        # get real fps
        self.cam_controller.wait_exposure_end(0)
        t1 = time.time()
        self.cam_controller.wait_exposure_end(0)
        period_real = time.time() - t1
        if period_real > period_nominal + 0.05:
            error_msg = f"Real fps is {1/period_real}, less than nominal fps: {1/period_nominal}"
            self.logger.warning(error_msg)
        self.fps = 1/period_real
        self.period = period_real
        self.cam_controller.stop_cameras()

    def __led_sequence_updater(self):
        for _ in range(self.collection_cfg.light_sequence.rounds):
            for light_idx in self.collection_cfg.light_sequence.sequence:
                time1 = time.time()
                self.light_controller.led_on(light_idx, only = True)
                delta = time.time() - time1
                interval = self.period - 0.01
                if delta > interval:
                    self.logger.warning(f"Light on took {delta} seconds, more than the maximum interval: {interval}")
                time.sleep(self.period - delta)

    def __collect(self, images : List[Image], images_show : List[Image], in_ram : bool = True):

        out_dir = Path(self.cfg.paths.save_dir)
        if not os.path.exists(out_dir):
            os.makedirs(out_dir)

        if in_ram:
            self.__images_list.append(images)
            self.__images_postprocessed_list.append(images_show)
        else:
            self.__save(images, raw = True)
            self.__save(images_show, raw = False)

        self.logger.info(f"Images captured (total: {self.__counter} per cam)")

        self.__counter += 1

    def __collect_init(self):
        self.__images_list = []
        self.__images_postprocessed_list = []
        self.__counter = 0
        rmtree(self.cfg.paths.save_dir, ignore_errors=True)
        os.makedirs(self.cfg.paths.save_dir)

    def capture_light_sequence(self, in_ram : bool = True, show : bool = False):
        self.__collect_init()

        if self.collection_cfg is None:
            error_msg = "Not able to collect light sequence: Collection config not found"
            self.logger.error(error_msg)
            raise ValueError(error_msg)

        self.cam_controller.start_cameras_synchronous_oneByOne()
        self.cam_controller.wait_exposure_end(0)
        p = mp.Process(target=self.__led_sequence_updater, args=[])
        p.start()
        self.cam_controller.grab_images() # remove first image from buffer
        for _ in range(self.collection_cfg.light_sequence.rounds + len(self.collection_cfg.light_sequence.sequence)):
            images = self.cam_controller.grab_images()
            images_postprocessed = self.postprocessing.postprocess(images)
            self.__collect(images, images_postprocessed, in_ram)
            if show:
                Image.show_multiple_images(images_postprocessed, wk = 0)
        self.cam_controller.stop_cameras()

    def capture_manual(self, in_ram : bool = True):
        self.__collect_init()

        self.cam_controller.start_cameras_synchronous_latest()
        while True:
            images = self.cam_controller.grab_images()
            images_postprocessed = self.postprocessing.postprocess(images)
            key = Image.show_multiple_images(images_postprocessed, wk = 1)
            if key == ord('q'):
                break
            if key == 32:
                self.__collect(images, images_postprocessed, in_ram)

    def preliminary_show(self):
        while True:
            images = self.cam_controller.grab_images()
            images_postprocessed = self.postprocessing.postprocess(images)
            key = Image.show_multiple_images(images_postprocessed, wk = 1)
            if key == 32:
                break

    def capture_n_images(self, n : int, in_ram : bool = True, show : bool = False):
        self.__collect_init()
        self.cam_controller.start_cameras_synchronous_latest()

        self.preliminary_show()

        for _ in range(n):
            images = self.cam_controller.grab_images()
            images_postprocessed = self.postprocessing.postprocess(images)
            if show:
                Image.show_multiple_images(images_postprocessed, wk = 1)
            self.__collect(images, images_postprocessed, in_ram)

    def capture_till_q(self, in_ram : bool = True):
        self.__collect_init()
        self.cam_controller.start_cameras_synchronous_latest()

        self.preliminary_show()

        while True:
            images = self.cam_controller.grab_images()
            images_postprocessed = self.postprocessing.postprocess(images)
            self.__collect(images, images_postprocessed, in_ram)
            wk = Image.show_multiple_images(images_postprocessed, wk = 1)
            if wk == ord('q'):
                break


    def save(self, save_raw : bool = False, save_postprocessed : bool = True, verbose = True) -> bool:

        # save images (if any)
        self.__counter = 0
        for i in range(len(self.__images_list)):
            if save_raw:
                self.__save(self.__images_list[i], raw = True, verbose = verbose)
            if save_postprocessed:
                self.__save(self.__images_postprocessed_list[i], raw = False, verbose = verbose)
            self.__counter += 1

        # save devices info
        devices_info = self.cam_controller.get_devices_info()
        with open(str(Path(self.cfg.paths.save_dir) / "devices_info.yaml"), 'w') as f:
            omegaconf.OmegaConf.save(devices_info, f)
        self.logger.info(f"Devices info saved in {self.cfg.paths.save_dir}")

        # save collection config
        if self.collection_cfg is not None:
            with open(str(Path(self.cfg.paths.save_dir) / "collection_cfg.yaml"), 'w') as f:
                omegaconf.OmegaConf.save(self.collection_cfg, f)
            self.logger.info(f"Collection config saved in {self.cfg.paths.save_dir}")

        return True

    def __save(self, images : List[Image], raw : bool, verbose : bool = False):
        subdir = "raw" if raw else "postprocessed"
        img_name = str(self.__counter).zfill(3) + ".png"
        if images is not None:
            for cam_id in range(len(images)):
                cam_name = "cam_" + str(cam_id).zfill(3)
                image = images[cam_id]
                o_dir = Path(self.cfg.paths.save_dir) / subdir / cam_name 
                if not os.path.exists(o_dir):
                    os.makedirs(o_dir)
                image.save_parallel(o_dir / img_name, verbose = verbose)

