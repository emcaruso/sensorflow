from utils_ema.config_utils import load_yaml
from utils_ema.image import Image
from logging import Logger

class Postprocessing():
    def __init__(self, postprocessing_cfg_path : str, logger : Logger):
        self.logger = logger
        self.functions = []
        self.kwargs = []
        self.init_postprocessings(postprocessing_cfg_path)

    def init_postprocessings(self, postprocessing_cfg_path : str) -> bool:
        if postprocessing_cfg_path is None:
            return False
        self.postprocessing_cfg = load_yaml(postprocessing_cfg_path)
        if "functions" not in self.postprocessing_cfg:
            raise ValueError("No functions in postprocessing config")
        if self.postprocessing_cfg.functions is None:
            return False

        for k,v in self.postprocessing_cfg.functions.items():
            if k not in dir(self):
                raise ValueError(f"Function {k} not found in postprocessing class")
            self.functions.append(getattr(self, k))
            if v is None:
                self.kwargs.append({})
            else:
                self.kwargs.append(v)
        return True

    def undistort(self, images):
        pass

    def color_correction(self, images):
        pass

    def sobel(self, images):
        for i, img in enumerate(images):
            images[i] = img.sobel()
        return images

    def postprocess(self, images):
        for fn in self.functions:
            images = fn(images)
        return images

    def add_function(self, fn):
        self.functions.append(fn)
