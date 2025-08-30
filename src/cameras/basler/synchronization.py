from pypylon import pylon
from tqdm import tqdm
from logging import Logger
import time
from typing import Tuple


def check_synchronization(cams: pylon.InstantCameraArray) -> bool:
    stats = []
    locked = []
    for cam in cams:
        if cam.BslPeriodicSignalSource.ToString() != "PtpClock":
            return False
        stats.append(cam.PtpStatus.GetValue())
        locked.append(cam.PtpServoStatus.GetValue())

    # Check if there is exactly one master and all others are slaves
    master_count = stats.count("Master")
    slave_count = stats.count("Slave")
    locked_count = locked.count("Locked")

    if (
        master_count == 1
        and slave_count == (len(stats) - 1)
        and locked_count == len(stats)
    ):
        return True
    else:
        return False


def synchronize_cameras(cams: pylon.InstantCameraArray, logger: Logger) -> bool:
    if not check_synchronization(cams):
        logger.info("Synchronizing cameras...")
    else:
        return True

    synchronize_camera(cams[0])  # Master
    status = get_cam_ptp_status(cams[0])
    if status is not None and status[0] == "Master" and status[1] == "Locked":
        logger.info("Master synchronized")
    else:
        logger.error("Master not synchronized")

    for i, cam in enumerate(cams):
        if i == 0:
            continue
        synchronize_camera(cam)
        logger.info(f"Synchronizing slave {i}")
        wait_for_synchronized_camera(cam)
        logger.info(f"Slave {i} synchronized")
    success = wait_synchronized_cameras(cams)

    # logger.info("Setting sync free-run timer on all cameras.")
    # for cam in cams:
    #     cam.Open()
    # cam.TriggerSelector.SetValue("FrameStart")
    # cam.TriggerMode.SetValue("Off")
    # cam.SyncFreeRunTimerStartTimeLow.Value = 0
    # cam.SyncFreeRunTimerStartTimeHigh.SetValue(0)
    # cam.SyncFreeRunTimerTriggerRateAbs.SetValue(3.0)
    # cam.SyncFreeRunTimerUpdate.Execute()
    # cam.SyncFreeRunTimerEnable.SetValue(True)

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
    # cam.BslPtpNetworkMode.Value = "Unicast"
    # cam.BslPtpNetworkMode.Value = "Hybrid"
    cam.BslPtpNetworkMode.Value = "Multicast"
    # cam.BslPtpUcPortAddrIndex.Value = 0
    # cam.BslPtpUcPortAddr.Value = 0xC0A80A0C
    # cam.BslPtpManagementEnable.Value = True
    cam.BslPtpManagementEnable.Value = False
    # cam.BslPtpTwoStep.Value = False
    cam.PtpEnable.Value = True


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
    for i, cam in enumerate(cameras):
        status = get_cam_ptp_status(cam)
        if status is None:
            return False
        stats.append(status)

    return check_synchronization(cameras)


def wait_for_synchronized_camera(cam: pylon.InstantCamera) -> bool:
    status = get_cam_ptp_status(cam)
    while True:
        cam.PtpDataSetLatch.Execute()
        synced = cam.PtpStatus.GetValue() in ["Master", "Slave"]
        locked = cam.PtpServoStatus.GetValue() == "Locked"
        if synced and locked:
            return True
