from typing import Dict, Optional
from utils_ema.config_utils import DictConfig, load_yaml
import numpy as np
import time
from logging import Logger
from omegaconf import DictConfig
from light_controller import LightControllerAbstract
import threading
import serial
import serial.tools.list_ports

class LightController(LightControllerAbstract):

    def __init__(self, logger : Logger, cfg : DictConfig) -> None:
        # run init from parent class
        super().__init__(logger, cfg)

        self.led_status = np.full((self.cfg.n_channels,), False, dtype=bool)
        self.loop_interval = self.cfg.loop_interval_ms / 1000.0
        self.lock = threading.Lock()
        self.__run_loop()


    # check reachability
    def check_reachability(self) -> bool:
        ports = serial.tools.list_ports.comports()
        devices = [port.device for port in ports]
        if self.cfg.port in devices:
            self.logger.info(f"Using port {self.cfg.port} as light controller.")
            return True
        else:
            self.logger.error(f"Port {self.cfg.port} not found. Available ports: {devices}")
            return False

    def __create_message(self) -> bytes:
        """
        Crea un messaggio di 7 byte con header FA04, 4 byte di dati, e 1 byte di CRC.
        output_mask: intero a 32 bit che rappresenta le 32 uscite.
        """
        header = [0xFA, 0x04]

        with self.lock:
            led_status = self.led_status.copy()

        bit_string = ''.join(str(int(b)) for b in led_status[::-1])
        value = int(bit_string, 2)
        data_bytes = value.to_bytes(4, byteorder='little')

        first_six = header + list(data_bytes)
        crc = (~sum(first_six)) & 0xFF
        return bytes(first_six + [crc])

    # communication loop
    def __run_loop_aux(self, ser: serial.Serial):
        self.logger.info("Starting light controller loop.")
        while True:
            data = self.__create_message()
            ser.write(data)
            time.sleep(self.loop_interval)

    def __run_loop(self):

        self.logger.info("Configuring serial: " + self.cfg.port + " at " + str(self.cfg.baudrate) + " baudrate.")
        ser = serial.Serial(
            port=self.cfg.port,
            baudrate=self.cfg.baudrate,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            bytesize=serial.EIGHTBITS,
            timeout=1
        )

        time.sleep(2)  # Wait for connection to stabilize

        # launch loop process
        self.loop_thread = threading.Thread(target=self.__run_loop_aux, args=(ser,) ,daemon=True)
        self.loop_thread.start()


    # log led status
    def log_status(self):
        self.logger.info("LED Status:")
        with self.lock:
            for i, status in enumerate(self.led_status):
                self.logger.info(f"   channel {i}: {'ON' if status else 'OFF'}")

    # get number of leds
    def num_leds(self) -> int:
        return self.cfg.n_channels

    # set leds
    def leds_on(self) -> None:
        with self.lock:
            self.led_status[:] = True

    def leds_off(self) -> None:
        with self.lock:
            self.led_status[:] = False

    def led_on(self, channel, only=False) -> None:
        with self.lock:
            if only:
                self.led_status[:] = False
            self.led_status[channel] = True

    def led_off(self, channel) -> None:
        with self.lock:
            self.led_status[channel] = False

