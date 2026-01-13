import numpy as np
from multiprocessing import Manager, Value, Lock, shared_memory
import time


class SharedCircularBuffer:
    def __init__(self, N: int, K: int):
        """
        N = number of time slots (circular buffer length)
        K = number of images per slot (fixed)
        """
        self.N = N
        self.K = K

        self.manager = Manager()
        self.buffer = self.manager.list([None] * N)
        self.index = Value("i", 0)
        self.lock = Lock()

    def _create_image_shm(self, image: np.ndarray):
        shm = shared_memory.SharedMemory(create=True, size=image.nbytes)
        shm_arr = np.ndarray(image.shape, dtype=image.dtype, buffer=shm.buf)
        shm_arr[:] = image
        return {"shm_name": shm.name, "shape": image.shape, "dtype": str(image.dtype)}

    def _cleanup_slot(self, slot):
        if slot is None:
            return
        for img_meta in slot["images"]:
            try:
                shm = shared_memory.SharedMemory(name=img_meta["shm_name"])
                shm.close()
                shm.unlink()
            except FileNotFoundError:
                pass

    def append(self, images: list[np.ndarray], slot_id):
        """
        images: list of length K, variable resolutions allowed
        slot_id: timestamp or frame index
        """
        assert len(images) == self.K, "Wrong number of images"

        image_metas = [self._create_image_shm(img) for img in images]

        with self.lock:
            idx = self.index.value

            # cleanup overwritten slot
            self._cleanup_slot(self.buffer[idx])

            # store new slot
            self.buffer[idx] = {"id": slot_id, "images": image_metas}

            self.index.value = (idx + 1) % self.N

    def get_buffer(self, idx: int):
        with self.lock:
            slot = self.buffer[idx]
            if slot is None:
                return None
            images = []
            for img_meta in slot["images"]:
                shm = shared_memory.SharedMemory(name=img_meta["shm_name"])
                img = np.ndarray(
                    img_meta["shape"], dtype=img_meta["dtype"], buffer=shm.buf
                )
                images.append(img.copy())
            return images

    def close(self):
        with self.lock:
            for slot in self.buffer:
                self._cleanup_slot(slot)
        self.manager.shutdown()
