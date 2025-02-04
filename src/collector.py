from camera_controller import get_camera_controller
import os
from shutil import rmtree
from pathlib import Path
from light_controller import get_light_controller
from log_default import get_logger_default
from logging import Logger
from utils_ema.image import Image
from utils_ema.config_utils import load_yaml
from postprocessing import Postprocessing
import multiprocessing as mp
import time


class Collector():
    def __init__(self, logger : Logger, config_path : str = str(Path(__file__).parents[1] / "configs" / "collector_default.yaml")):
        self.logger = logger
        self.cfg = load_yaml(config_path)
        self.light_controller = get_light_controller(light_controller_cfg_path=self.cfg.paths.light_cfg, logger = logger)
        self.cam_controller = get_camera_controller(capture_cfg_path=self.cfg.paths.camera_cfg, logger = logger)
        self.postprocessing = Postprocessing(postprocessing_cfg_path=self.cfg.paths.postprocessing_cfg, logger = logger)
        self.load_collection_cfg()
        self.check_real_fps()

    def load_collection_cfg(self):
        self.collection_cfg = None
        if self.cfg.paths.collection_cfg is not None:
            self.collection_cfg = load_yaml(self.cfg.paths.collection_cfg)

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

    def capture_light_sequence(self, postprocess : bool = True):
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
        for _ in range(self.collection_cfg.light_sequence.rounds + len(self.collection_cfg.light_sequence.sequence)):
            images = self.cam_controller.grab_images()
            if postprocess:
                images = self.postprocessing.postprocess(images)
            images_list.append(images)
        for images in images_list:
            Image.show_multiple_images(images, wk = 0)
        self.cam_controller.stop_cameras()
        return images_list

    def capture_manual(self, postprocess : bool = True):
        self.cam_controller.start_cameras_synchronous_latest()
        images_list = []
        while True:
            images = self.cam_controller.grab_images()
            if postprocess:
                images = self.postprocessing.postprocess(images)
            key = Image.show_multiple_images(images, wk = 1)
            if key == ord('q'):
                break
            if key == 32:
                self.logger.info(f"Images captured (total: {len(images_list)} per cam)")
                images_list.append(images)
        return images_list

    def save(self, images_list):
        rmtree(self.cfg.paths.save_path, ignore_errors=True)
        os.makedirs(self.cfg.paths.save_path)

        # create folders
        for i in range(len(images_list)):
            os.makedirs(os.path.join(self.cfg.paths.save_path, f"cam_{i}"))

        # save images
        for i, images in enumerate(images_list):
            for j, image in enumerate(images):
                image.save(str(Path(self.cfg.paths.save_path) / f"cam_{j}" / f"img_{i}.png"))




# executable for debug
if __name__ == "__main__":
    logger = get_logger_default()
    c = Collector(logger)

    images_list = c.capture_manual()
    c.save(images_list)

    # time.sleep(1)
    # for i in range(1):
    #     for i in range(8):
    #         c.capture_with_light(i, 0)
    #
