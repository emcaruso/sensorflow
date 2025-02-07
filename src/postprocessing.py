from utils_ema.config_utils import DictConfig, load_yaml
from utils_ema.image import Image


class Postprocessing():
    def __init__(self, cfg : DictConfig):
        self.functions = []
        self.kwargs = []
        self.cfg = cfg
        self.init_postprocessings()

    def init_postprocessings(self) -> bool:
        if "functions" not in self.cfg:
            raise ValueError("No functions in postprocessing config")
        if self.cfg.functions is None:
            return False

        for k,v in self.cfg.functions.items():
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
