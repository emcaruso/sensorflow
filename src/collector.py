import os, sys
import torch
from typing import List, Optional

from tqdm import tqdm

sys.path.append(os.path.dirname(os.path.realpath(__file__)))
from shutil import rmtree
from pathlib import Path
from omegaconf import DictConfig
from logging import Logger
import multiprocessing as mp
import time
import omegaconf
from utils_ema.image import Image
from camera_controller import get_camera_controller
from light_controller import get_light_controller
from postprocessing import Postprocessing


class Collector:
    def __init__(self, logger: Logger, cfg: DictConfig):
        self.logger = logger
        self.cfg = cfg
        self.light_controller = get_light_controller(cfg=self.cfg.lights, logger=logger)
        self.cam_controller = get_camera_controller(cfg=self.cfg.cameras, logger=logger)
        self.preprocessing = Postprocessing(cfg=DictConfig({"functions": None}))
        self.postprocessing = Postprocessing(cfg=self.cfg.postprocessings)
        self.callback_collect = None
        self.collection_cfg = self.cfg.strategies
        self.processes = []
        self.images = []
        self.images_preprocessed = []
        self.images_postprocessed = []

    # decorator that perform function multiple times
    def collect_function(func):
        def wrapper(self, *args, **kwargs):
            if self.cfg.camera_ids is None:
                camera_ids_cfg = list(range(self.cam_controller.num_cameras))
            else:
                camera_ids_cfg = self.cfg.camera_ids

            os.makedirs(self.cfg.paths.save_dir, exist_ok=True)
            if self.cfg.mode.one_cam_at_time:
                camera_ids = [[i] for i in camera_ids_cfg]
            else:
                camera_ids = [camera_ids_cfg]
            for ids in camera_ids:
                self.__collect_init()
                self.logger.info(f"Collecting for cameras: {ids}")
                for id in ids:
                    raw_dir = Path(self.cfg.paths.save_dir) / "raw" / f"cam_{id:03}"
                    ppr_dir = (
                        Path(self.cfg.paths.save_dir) / "postprocessed" / f"cam_{id:03}"
                    )
                    rmtree(str(raw_dir), ignore_errors=True)
                    rmtree(str(ppr_dir), ignore_errors=True)

                self.camera_ids = ids

                func(self, *args, **kwargs)

                self.save(save_raw=True, save_postprocessed=True)

            self.cam_controller.stop_grabbing()
            self.cam_controller.close()

        return wrapper

    # def __led_sequence_updater(self):
    #     for _ in range(self.collection_cfg.light_sequence.rounds):
    #         for light_idx in self.collection_cfg.light_sequence.sequence:
    #             time1 = time.time()
    #             self.light_controller.led_on(light_idx, only=True)
    #             delta = time.time() - time1
    #             interval = self.period - 0.01
    #             if delta > interval:
    #                 self.logger.warning(
    #                     f"Light on took {delta} seconds, more than the maximum interval: {interval}"
    #                 )
    #             time.sleep(self.period - delta)

    def __collect(
        self,
        images: List[Image],
        images_preprocessed: Optional[List[Image]] = None,
        images_show: Optional[List[Image]] = None,
    ):
        if self.cfg.in_ram:
            self.images.append(images)
            if images_preprocessed is not None:
                self.images_preprocessed.append(images_preprocessed)
            if images_show is not None:
                self.images_postprocessed.append(images_show)

        else:
            out_dir = Path(self.cfg.paths.save_dir)
            if not os.path.exists(out_dir):
                os.makedirs(out_dir)

            self.__save(images, dir="raw", verbose=False)

            if images_preprocessed is not None:
                self.__save(images_preprocessed, dir="preprocessed", verbose=False)

            if images_show is not None:
                self.__save(images_show, dir="postprocessed", verbose=False)

        self.__counter += 1
        print(f"Images captured (total: {self.__counter} per cam)")
        # self.logger.info(f"Images captured (total: {self.__counter} per cam)")
        #
        if self.callback_collect is not None:
            self.callback_collect()

    def __collect_init(self):
        self.images = []
        self.images_preprocessed = []
        self.images_postprocessed = []
        self.__counter = 0
        self.previous_id = 0
        self.cam_controller.reset_buffer_id()
        os.makedirs(self.cfg.paths.save_dir, exist_ok=True)

    def get_images_with_preprocessing(self, show):

        while True:

            # grab images and collect them
            images, id = self.cam_controller.get_images()
            if id == self.previous_id:
                continue
            print(images)
            self.previous_id = id

            # postprocess
            images_preprocessed = self.preprocessing.postprocess(images)
            images_postprocessed = self.postprocessing.postprocess(images_preprocessed)

            # show images
            key = None
            if show:
                if images_postprocessed is not None:
                    images_show = images_postprocessed
                elif images_preprocessed is not None:
                    images_show = images_preprocessed
                else:
                    images_show = images
                key = Image.show_multiple_images(
                    [images_show[i] for i in self.camera_ids], wk=1
                )
            break

        return images, images_preprocessed, images_postprocessed, key

    def preliminary_show(self, trigger=None) -> bool:
        if trigger == None:
            self.logger.info(
                "Press space to exit the preliminary show, or press 'q' to exit."
            )

        images, _, _, key = self.get_images_with_preprocessing(show=True)

        while True:
            if trigger is not None:
                if trigger(images):
                    return True
                elif key == ord("q"):
                    return False
            else:
                if key == 32:
                    return True
                elif key == ord("q"):
                    return False

    @collect_function
    def capture_manual(self) -> bool:
        self.__set_lights()

        # show fake images
        fake_imgs = [Image(torch.zeros(1, 1, 3)) for i in range(len(self.camera_ids))]
        Image.show_multiple_images(fake_imgs, wk=1)

        # start grabbing images
        self.cam_controller.start_grabbing()

        while True:

            # grab images with postprocessing
            images, images_preprocessed, images_postprocessed, key = (
                self.get_images_with_preprocessing(show=True)
            )

            # preprocess
            images_preprocessed = self.preprocessing.postprocess(images)

            if key == ord("q"):
                break
            if key == 32:
                self.__collect(
                    images,
                    images_preprocessed,
                    images_postprocessed,
                )

        self.__lights_off()

        return True

    @collect_function
    def capture_till_q(
        self,
        trigger_start=None,
        trigger_capture=None,
        trigger_exit=None,
        postprocess=False,
        sync=True,
    ) -> bool:
        self.__set_lights()

        # show fake images
        fake_imgs = [Image(torch.zeros(1, 1, 3)) for i in range(len(self.camera_ids))]
        Image.show_multiple_images(fake_imgs, wk=1)

        # start cameras
        # self.cam_controller.start_cameras_synchronous_oneByOne()
        self.cam_controller.start_grabbing()

        # while True:
        # images = self.cam_controller.grab_images(self.camera_ids)
        # if images is None:
        #     self.logger.warning("No images grabbed, retrying...")
        #     self.cam_controller.stop_cameras()
        # else:
        #     break

        # # warmup
        # for _ in tqdm(range(6), desc="Warming up cameras"):
        #     images = self.cam_controller.grab_images(self.camera_ids)

        # preliminary show with trigger start
        res = self.preliminary_show(trigger=trigger_start)
        if not res:
            return False

        while True:

            # grab images with postprocessing
            images, images_preprocessed, images_postprocessed, key = (
                self.get_images_with_preprocessing(show=True)
            )

            # trigger capture
            if trigger_capture is None:
                self.__collect(
                    images,
                    images_preprocessed,
                    images_postprocessed,
                )
            else:
                if trigger_capture(images):
                    self.__collect(
                        images,
                        images_preprocessed,
                        images_postprocessed,
                    )

            # show + exit
            if key == ord("q"):
                break
            if trigger_exit is not None:
                if trigger_exit(images):
                    break

        self.__lights_off()

        return True

    def save(
        self,
        save_raw: bool = False,
        save_postprocessed: bool = True,
        save_preprocessed: bool = False,
        verbose=True,
    ) -> bool:
        self.logger.info(f"Saving images")

        dir = Path(self.cfg.paths.save_dir)
        # rmtree(str(dir), ignore_errors=True)

        # save data in ram
        self.__counter = 0
        if self.cfg.in_ram:
            os.makedirs(dir, exist_ok=True)
            for i in tqdm(range(len(self.images))):
                self.__save(self.images[i], dir="raw", verbose=False)

                if self.images_preprocessed != []:
                    self.__save(
                        self.images_preprocessed[i], dir="preprocessed", verbose=False
                    )

                if self.images_postprocessed != []:
                    self.__save(
                        self.images_postprocessed[i], dir="postprocessed", verbose=False
                    )
                self.__counter += 1
        #
        # # if not save_raw, delete raw images
        # if not save_raw:
        #     rmtree(str(Path(self.cfg.paths.save_dir) / "raw"), ignore_errors=True)
        #
        # # if not save_preprocessed, delete preprocessed images
        # if not save_preprocessed:
        #     rmtree(
        #         str(Path(self.cfg.paths.save_dir) / "preprocessed"), ignore_errors=True
        #     )
        #
        # # if not save_postprocessed, delete postprocessed images
        # if not save_postprocessed:
        #     rmtree(
        #         str(Path(self.cfg.paths.save_dir) / "postprocessed"), ignore_errors=True
        #     )

        # # save images (if any)
        # self.__counter = 0
        # for i in range(len(self.__images_list)):
        #     if save_raw:
        #         self.__save(self.__images_list[i], dir="raw", verbose=verbose)
        #     if save_preprocessed:
        #         self.__save(
        #             self.preprocessing.postprocess(self.__images_list[i]),
        #             dir="preprocessed",
        #             verbose=verbose,
        #         )
        #     if save_postprocessed:
        #         self.__save(
        #             self.__images_postprocessed_list[i],
        #             dir="postprocessed",
        #             verbose=verbose,
        #         )
        #     self.__counter += 1

        # save devices info
        devices_info = self.cam_controller.get_devices_info()
        with open(str(Path(self.cfg.paths.save_dir) / "devices_info.yaml"), "w") as f:
            omegaconf.OmegaConf.save(devices_info, f)

        # if not save_raw, delete raw images
        if not save_raw:
            rmtree(str(Path(self.cfg.paths.save_dir) / "raw"), ignore_errors=True)
        self.logger.info(f"Devices info saved in {self.cfg.paths.save_dir}")

        # save collection config
        if self.collection_cfg is not None:
            with open(
                str(Path(self.cfg.paths.save_dir) / "collection_cfg.yaml"), "w"
            ) as f:
                omegaconf.OmegaConf.save(self.collection_cfg, f)
            self.logger.info(f"Collection config saved in {self.cfg.paths.save_dir}")

        # self.cam_controller.stop_cameras()

        return True

    def __save(self, images: List[Image], dir: str, verbose: bool = False):
        subdir = dir
        img_name = str(self.__counter).zfill(3) + ".png"
        if images is not None:
            # for cam_id in range(len(images)):
            processes = []
            for cam_id in self.camera_ids:
                cam_name = "cam_" + str(cam_id).zfill(3)
                image = images[cam_id]
                # image.set_type(torch.float32)
                o_dir = Path(self.cfg.paths.save_dir) / subdir / cam_name
                if not o_dir.exists():
                    os.makedirs(o_dir)
                processes.append(image.save_parallel(o_dir / img_name, verbose=verbose))
            for p in processes:
                p.join()

    def __set_lights(self):
        if self.light_controller is not None:
            self.light_controller.leds_off()
            for channel in self.cfg.lights.channels:
                self.light_controller.led_on(channel)

    def __lights_off(self):
        if self.light_controller is not None:
            self.light_controller.leds_off()

    def close(self):
        self.cam_controller.close()


class CollectorLoader:
    n_cams: int = -1
    n_images: int = -1
    resolutions: List[int] = []

    @classmethod
    def load_info(cls, save_dir: str):
        path = Path(save_dir) / "devices_info.yaml"
        if not path.exists():
            raise ValueError(f"Cannot load devices info, path {path} does not exist")
        devices_info = omegaconf.OmegaConf.load(path)

        path = Path(save_dir) / "collection_cfg.yaml"
        if not path.exists():
            raise ValueError(
                f"Cannot load collection configuration, path {path} does not exist"
            )
        collection_info = omegaconf.OmegaConf.load(path)

        return devices_info, collection_info

    @classmethod
    def load_images(cls, save_dir: str, raw: bool = True):
        subdir = "raw" if raw else "postprocessed"
        dir = Path(save_dir) / subdir

        if not dir.exists():
            raise ValueError(f"Cannot load images, path {str(dir)} does not exist")

        cam_paths = sorted(dir.iterdir())
        cam_ids = [int(p.stem.split("cam_")[1]) for p in cam_paths]
        img_paths = [sorted(cam_path.iterdir()) for cam_path in cam_paths]
        cls.n_cams = len(cam_paths)
        cls.cam_ids = cam_ids
        cls.n_images = max([len(p) for p in img_paths])
        images = []

        # resolution
        resolutions = []
        for cam_dir in sorted(dir.iterdir()):
            img = Image.from_path(str(next(cam_dir.iterdir())))
            resolutions.append(img.resolution())
        cls.resolutions = resolutions

        yield True

        for i in range(cls.n_images):
            res = []
            for c in range(cls.n_cams):
                if i >= len(img_paths[c]):
                    res.append(Image.from_img(torch.zeros(1, 1, 3)))
                else:
                    res.append(Image.from_path(str(img_paths[c][i])))
            yield res
