from camera_controller import get_camera_controller
import os
from shutil import rmtree
from pathlib import Path
from light_controller import get_light_controller
from omegaconf import DictConfig
from logging import Logger
from utils_ema.image import Image
from utils_ema.config_utils import load_yaml
from utils_ema.log import get_logger_default
from postprocessing import Postprocessing
import multiprocessing as mp
import time
import omegaconf


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

    def capture_light_sequence(self):
        if self.collection_cfg is None:
            error_msg = "Not able to collect light sequence: Collection config not found"
            self.logger.error(error_msg)
            raise ValueError(error_msg)

        self.cam_controller.start_cameras_synchronous_oneByOne()
        self.cam_controller.wait_exposure_end(0)
        p = mp.Process(target=self.__led_sequence_updater, args=[])
        p.start()
        self.cam_controller.grab_images() # remove first image from buffer
        images_list = []
        images_postprocessed_list = []
        for _ in range(self.collection_cfg.light_sequence.rounds + len(self.collection_cfg.light_sequence.sequence)):
            images = self.cam_controller.grab_images()
            images_list.append(images)
            images_postprocessed = self.postprocessing.postprocess(images)
            images_postprocessed_list.append(images_postprocessed)
        for images in images_list:
            Image.show_multiple_images(images_postprocessed, wk = 0)
        self.cam_controller.stop_cameras()
        return images_list

    def capture_manual(self):
        self.cam_controller.start_cameras_synchronous_latest()
        images_list = []
        images_postprocessed_list = []
        while True:
            images = self.cam_controller.grab_images()
            images_postprocessed = self.postprocessing.postprocess(images)
            key = Image.show_multiple_images(images, wk = 1)
            if key == ord('q'):
                break
            if key == 32:
                self.logger.info(f"Images captured (total: {len(images_list)} per cam)")
                images_list.append(images)
                images_postprocessed_list.append(images_postprocessed)
        return images_list, images_postprocessed_list

    def save(self, images_list) -> bool:
        if images_list == []:
            self.logger.info(f"Not saving because no images are captured")
            return False

        rmtree(self.cfg.paths.save_path, ignore_errors=True)
        os.makedirs(self.cfg.paths.save_path)

        # save data
        for i, images in enumerate(images_list):
            img_name = "img_" + str(i).zfill(3)

            # save images
            for j, image in enumerate(images):
                cam_name = "cam_" + str(j).zfill(3)
                image.save(str(Path(self.cfg.paths.save_path) / f"{cam_name}" / f"{img_name}.png"))

        # save devices info
        devices_info = self.cam_controller.get_devices_info()
        with open(str(Path(self.cfg.paths.save_path) / "devices_info.yaml"), 'w') as f:
            omegaconf.OmegaConf.save(devices_info, f)
        self.logger.info(f"Devices info saved in {self.cfg.paths.save_path}")

        # save collection config
        if self.collection_cfg is not None:
            with open(str(Path(self.cfg.paths.save_path) / "collection_cfg.yaml"), 'w') as f:
                omegaconf.OmegaConf.save(self.collection_cfg, f)
            self.logger.info(f"Collection config saved in {self.cfg.paths.save_path}")
         
        return True

