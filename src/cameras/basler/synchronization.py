from pypylon import pylon
from logging import Logger
import time

def check_synchronization(cams : pylon.InstantCameraArray) -> bool:
    stats = []
    for cam in cams:
        if cam.BslPeriodicSignalSource.ToString() != 'PtpClock':
            return False
        stats.append(cam.PtpStatus.GetValue())

    # Check if there is exactly one master and all others are slaves
    master_count = stats.count('Master')
    slave_count = stats.count('Slave')

    if master_count == 1 and slave_count == (len(stats) - 1):
         return True
    else:
        return False
    
def synchronize_cameras(cams : pylon.InstantCameraArray, logger : Logger) -> bool:
    if not check_synchronization(cams):
        logger.info("Synchronizing cameras...")
    else:
        return True

    synchronize_camera(cams[0]) # Master
    status = get_cam_ptp_status(cams[0])
    if status == 'Master':
        logger.info("Master synchronized")
    else:
        logger.error("Master not synchronized")

    for i, cam in enumerate(cams):
        if i == 0:
            continue
        synchronize_camera(cam)
    success = wait_synchronized_cameras(cams)

    if not success:
        logger.error("Slaves not synchronized")
    else:
        logger.info("Slaves synchronized")

    return success
         

def synchronize_camera(cam : pylon.InstantCamera) -> None:
    cam.PtpEnable.Value = False
    cam.BslPtpPriority1.Value = 128
    cam.BslPtpProfile.Value = "DelayRequestResponseDefaultProfile"
    cam.BslPtpNetworkMode.Value = "Multicast"
    cam.BslPtpTwoStep.Value = False
    cam.PtpEnable.Value = True

def get_cam_ptp_status(cam : pylon.InstantCamera) -> str:
    time1 = time.time()
    while True:
        cam.PtpDataSetLatch.Execute()
        synced = (cam.PtpStatus.GetValue() in ['Master', 'Slave'])
        if synced:
            return cam.PtpStatus.GetValue() 

        if (time.time() - time1) > 30:
            return None

def wait_synchronized_cameras(cameras : pylon.InstantCameraArray) -> bool:
    stats = []
    for i, cam in enumerate(cameras):
        status = get_cam_ptp_status(cam)
        if status is None:
            return False
        stats.append(status)

    return check_synchronization(cameras)

