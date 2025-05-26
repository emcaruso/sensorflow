from typing import Dict, Optional
from utils_ema.net_controller import NetController
from utils_ema.config_utils import DictConfig, load_yaml
from pathlib import Path
import time
from logging import Logger
from omegaconf import DictConfig
from light_controller import LightControllerAbstract


class LightController(LightControllerAbstract):

    def check_reachability(self) -> bool:
        if not NetController.check_reachability(self.cfg.ip):
            return False
        return True


    def __send_message(self, message: str, log=False) -> Optional[str]:
        if self.cfg.protocol == "tcp":
            res = NetController.send_tcp_message(self.cfg.ip, self.cfg.port_in, message)
        elif self.cfg.protocol == "udp":
            res = NetController.send_udp_message(self.cfg.ip, self.cfg.port_in, message)
        else:
            raise ValueError(f"{self.cfg.protocol} is not a known protocol (tcp, udp)")

        if res is None:
            self.logger.error("Communication with light controller is not working!")
            return None

        if log:
            self.logger.info(res)
        return res

    # get status
    def log_channel_status(self, channel: int) -> None:
        res = self.__send_message("ST" + str(channel))
        self.logger.info("   channel: " + str(channel).zfill(2) + res.split("M")[1][:-3])
        return None

    def log_status(self):
        for i in range(self.cfg.n_channels):
            self.log_channel_status(i)
        return None

    # get number of leds
    def num_leds(self) -> int:
        return self.cfg.n_channels

    # set leds
    def set_led_continuous(self, channel: int, amp: float) -> None:
        self.__send_message(
            "RS" + str(channel) + "," + str(amp)
        )
        return None

    def leds_on(self) -> None:
        for i in range(self.cfg.n_channels):
            self.set_led_continuous(i, amp=self.cfg.ampere_max)
        return None

    def leds_off(self) -> None:
        for i in range(self.cfg.n_channels):
            self.set_led_continuous(i, amp=0)
        return None

    def led_on(self, channel, only=False) -> None:

        for i in range(self.cfg.n_channels):
            if i == channel:
                self.set_led_continuous(i, amp=self.cfg.ampere_max)
            elif only:
                self.led_off(i)
        return None

    def led_off(self, channel) -> None:
        self.set_led_continuous(channel=channel, amp=0)
        return None

    def clear_settings(self) -> None:
        self.__send_message("CL")
        return None
