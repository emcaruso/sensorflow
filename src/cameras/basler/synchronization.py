from pypylon import pylon
from tqdm import tqdm
from logging import Logger
import time
from typing import Tuple


def ip_to_hex(ip: str) -> int:
    parts = [int(p) for p in ip.split(".")]
    return (parts[0] << 24) | (parts[1] << 16) | (parts[2] << 8) | parts[3]


def check_synchronization(cams: pylon.InstantCameraArray) -> bool:
    stats = []
    locked = []
    for i, cam in enumerate(cams):
        if cam.BslPeriodicSignalSource.ToString() != "PtpClock":
            return False
        stat = cam.PtpStatus.GetValue()
        servo = cam.PtpServoStatus.GetValue()
        # print(f"Camera {i}: Status: {stat}, Servo: {servo}")
        stats.append(stat)
        locked.append(cam.PtpServoStatus.GetValue())

    # Check if there is exactly one master and all others are slaves
    master_count = stats.count("Master")
    slave_count = stats.count("Slave")
    locked_count = locked.count("Locked")

    # if (
    #     master_count == 1
    #     and slave_count == (len(stats) - 1)
    #     and locked_count == len(stats)
    # ):
    if locked_count == len(stats):
        time.sleep(0.5)
        return True
    else:
        return False


def synchronize_cameras(cams: pylon.InstantCameraArray, logger: Logger) -> bool:
    logger.info("Synchronizing cameras...")

    if not check_synchronization(cams):
        for cam in cams:
            cam.PtpEnable.Value = False

        for i, cam in enumerate(cams):
            synchronize_camera(cam)
        for i, cam in enumerate(cams):
            # logger.info(f"Waiting for Camera {i} to synchronize...")
            # role = wait_for_synchronized_camera(cam)
            # logger.info(f"Camera {i} synchronized as {role}")
            success = wait_synchronized_cameras(cams)

    offset_max = 9999999999
    # thresh = 1000
    # thresh = 1000000
    # logger.info(f"Waiting for offset < {thresh} ns")
    while offset_max >= 1000:
        time.sleep(0.5)
        offsets = [cam.PtpOffsetFromMaster.Value for cam in cams]
        for i, cam in enumerate(cams):
            cam.PtpDataSetLatch.Execute()
            offsets[i] = cam.PtpOffsetFromMaster.Value
        offset_max = max([o for o in offsets if o != 0])

    assert check_synchronization(cams)
    return True
    # if not success:
    #     logger.error("Slaves not synchronized")
    # else:
    #     logger.info("Slaves synchronized")
    #
    # return success


def synchronize_camera(cam: pylon.InstantCamera) -> None:
    # cam.PtpEnable.Value = False
    cam.BslPtpPriority1.Value = 128
    cam.BslPtpProfile.Value = "DelayRequestResponseDefaultProfile"
    # cam.BslPtpProfile.Value = "PeerToPeerDefaultProfile"
    # cam.BslPtpNetworkMode.Value = "Hybrid"
    # cam.BslPtpNetworkMode.Value = "Unicast"
    # cam.BslPtpNetworkMode.Value = "Multicast"
    # cam.BslPtpUcPortAddrIndex.Value = 0
    # cam.BslPtpUcPortAddr.Value = 0xC0A80A0C
    cam.BslPtpManagementEnable.Value = True
    cam.BslPtpTwoStep.Value = True
    cam.PtpEnable.Value = True
    cam.PtpDataSetLatch.Execute()


def get_cam_ptp_status(cam: pylon.InstantCamera) -> Tuple[str, str] | None:
    time1 = time.time()
    while True:
        cam.PtpDataSetLatch.Execute()
        synced = cam.PtpStatus.GetValue() in ["Master", "Slave"]
        locked = cam.PtpServoStatus.GetValue() == "Locked"
        if synced and locked:
            return cam.PtpStatus.GetValue(), cam.PtpServoStatus.GetValue()

        if (time.time() - time1) > 20.0:
            return None


def wait_synchronized_cameras(cameras: pylon.InstantCameraArray) -> bool:
    stats = []
    while True:
        for i, cam in enumerate(cameras):
            cam.PtpDataSetLatch.Execute()
            status = get_cam_ptp_status(cam)
            if status is None:
                return False
            stats.append(status)

        if check_synchronization(cameras):
            return True


def wait_for_synchronized_camera(cam: pylon.InstantCamera) -> bool:
    status = get_cam_ptp_status(cam)
    while True:
        val = cam.PtpStatus.GetValue()
        synced = val in ["Master", "Slave"]
        locked = cam.PtpServoStatus.GetValue() == "Locked"
        if synced and locked:
            return val
