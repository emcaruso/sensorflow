from utils_ema.net_controller import NetController
from utils_ema.config_utils import load_yaml
from pathlib import Path
import time
from logging import Logger


class LightController:

    def __init__(self, logger : Logger, light_controller_cfg_path : str = ""):
        if light_controller_cfg_path == "":
            light_controller_cfg_path =str(Path(__file__).parent / "light_controller_default.yaml") 

        self.light_controller_cfg_path =light_controller_cfg_path 
        self.cfg = load_yaml(light_controller_cfg_path)
        self.logger = logger

        if not NetController.check_reachability(self.cfg.ip):
            raise ConnectionError("Light controller is not reachable!")
        else:
            self.logger.info("Light controller connection is working")

    def __send_message(self, message, log=False):
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

    def log_channel_status(self, channel):
        res = self.__send_message("ST" + str(channel))
        self.logger.info("   channel: " + str(channel).zfill(2) + res.split("M")[1][:-3])

    def log_all_channel_status(self):
        for i in range(self.cfg.n_channels):
            self.log_channel_status(i)

    def log_trigger_status(self, log=True):
        res = self.__send_message("ST16")
        if log:
            self.logger.info("   " + res.split("ST16")[1][3:-3])
        return res

    def log_status(self):
        self.log_all_channel_status()
        self.log_trigger_status()

    # set triggers

    def set_default_trigger(self):
        self.__send_message("TT0")

    def set_trigger_groups(self, mode):
        self.__send_message("FP" + str(mode))

    def send_trigger_pulse(self, trigger_id):
        self.__send_message("TR" + str(trigger_id))

    # test trigger
    def set_trigger_test(self, milliseconds):
        self.__send_message(
            "TT1" + "," + str(milliseconds) + "MS"
        )

    def test_lights_and_trigger(
        self, amp=0.0001, period_ms=40, pulse_width_ms=20, time_s=1
    ):
        for i in range(self.cfg.n_channels):
            # self.set_led_pulse(channel=i, amp=amp, width_ms=pulse_width_ms)
            self.set_led_switch(channel=i, amp=amp)
        self.set_trigger_test(period_ms)
        time.sleep(time_s)
        self.clear_settings()

    # set leds

    def set_led_pulse(
        self, channel, amp=None, width_ms=200, trig_delay=0, retrig_delay=0
    ):
        if amp is None:
            amp = self.cfg.ampere_max
        self.__send_message(
            "RT"
            + str(channel)
            + ","
            + str(width_ms * 1000)
            + ","
            + str(trig_delay)
            + ","
            + str(amp)
            + ","
            + str(retrig_delay),
            protocol=self.cfg.protocol,
        )

    def set_led_continuous(self, channel, amp=None):
        if amp is None:
            amp = self.cfg.ampere_max
        self.__send_message(
            "RS" + str(channel) + "," + str(amp)
        )

    def set_led_switch(self, channel, amp=None):
        if amp is None:
            amp = self.cfg.ampere_max
        self.__send_message(
            "RW" + str(channel) + "," + str(amp)
        )

    def set_led_pulse_all(self, amp=None, width_ms=200, trig_delay=0, retrig_delay=0):
        if amp is None:
            amp = self.cfg.ampere_max
        for i in range(self.cfg.n_channels):
            self.set_led_pulse(
                i,
                amp=amp,
                width_ms=width_ms,
                trig_delay=trig_delay,
                retrig_delay=retrig_delay,
            )

    def leds_on(self, amp=None):
        if amp is None:
            amp = self.cfg.ampere_max

        for i in range(self.cfg.n_channels):
            self.set_led_continuous(i, amp=amp)

    def leds_off(self):
        for i in range(self.cfg.n_channels):
            self.set_led_continuous(i, amp=0)

    def led_on(self, channel, amp=None, only=False, wait=0.0):
        if amp is None:
            amp = self.cfg.ampere_max

        for i in range(self.cfg.n_channels):
            if i == channel:
                self.set_led_continuous(i, amp=amp)
            elif only:
                self.led_off(i)

        time.sleep(wait)

    def all_leds_on(self, amp=None):
        for i in range(self.cfg.n_channels):
            self.led_on(i)

    def all_leds_off(self, wait=0.0):
        for i in range(self.cfg.n_channels):
            self.led_off(i)
        time.sleep(wait)


    def led_off(self, channel):
        self.set_led_continuous(channel=channel, amp=0)

    # def led_pulse(self, channel,

    # def led_pulse(self, channel,

    def clear_settings(self):
        self.__send_message("CL")
