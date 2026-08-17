"""Task Execution Layer - Extensible Task Registry"""
from __future__ import annotations
import time
import logging
from typing import Dict, Callable, Tuple, Optional, cast, List
from dataclasses import dataclass
from scipy.spatial.transform import Rotation
from API import RobotApi
from API.types import DigitalIndex, PositionOrientation
from config import AdapterConfig, RobotConfig, set_gripper_state

logger = logging.getLogger("RoboProSCAPE.Executor")

# ==========================================
# СТРУКТУРА ЗАДАЧИ (DATACLASS)
# ==========================================
@dataclass
class Task:
    packet_id: int
    task_group_id: int
    task_type: int
    payload: List[float]
    remain_tasks: int

# ==========================================
# РЕЕСТР ЗАДАЧ
# ==========================================
TASK_REGISTRY: Dict[int, Callable[[RobotApi, Task, AdapterConfig], Tuple[bool, bool]]] = {}

def register_task(task_type: int):
    def decorator(func: Callable) -> Callable:
        TASK_REGISTRY[task_type] = func
        return func
    return decorator

def _wait_motion_complete(robot: RobotApi, timeout: Optional[float] = None) -> bool:
    """Ждёт завершения движения с таймаутом из конфига."""
    timeout = timeout or RobotConfig.TIMEOUT_MOTION
    start = time.time()
    while time.time() - start < timeout:
        if robot.safety.status.get() in ("emergency_stop", "safeguard_stop", "fault", "violation"):
            robot.motion.mode.set("hold")
            return False
        if robot.motion.check_waypoint_completion():
            return True
        time.sleep(RobotConfig.SAFETY_CHECK_INTERVAL)
    robot.motion.mode.set("hold")
    return False

def convert_XYZ_to_ABC(angles: list) -> list:
    """Конвертирует углы ориентации из формата робота в формат адаптера (ZXY Euler)."""
    rot = Rotation.from_euler("zxy", angles, degrees=True)
    return rot.as_euler("zxy", degrees=True).tolist()

def get_robot_pose(robot: RobotApi) -> Tuple[list, list]:
    """Возвращает (pose_mm_deg, joints_deg) в формате для uplink."""
    joints = list(robot.motion.joint.get_actual_position(units="deg"))
    pose = list(robot.motion.linear.get_actual_position(orientation_units="deg"))
    pose[3:6] = convert_XYZ_to_ABC(pose[3:6])
    pose[0:3] = [p * 1000 for p in pose[0:3]]
    return pose, joints

def connect_to_robot(ip: Optional[str] = None) -> Optional[RobotApi]:
    """Подключается к роботу с настройками из конфига."""
    ip = ip or RobotConfig.IP
    try:
        robot = RobotApi(ip, autoconnect=True, enable_logger=False)
        if robot.safety.status.get() not in ("normal", "reduced"):
            robot.controller.state.set("off")
            robot.controller.state.set("run", await_sec=30)
            robot.motion.scale_setup.set(velocity=RobotConfig.SPEED_GLOBAL[0], acceleration=RobotConfig.SPEED_GLOBAL[1])
            logger.info(f"Robot {ip} is connected successfully")
        if robot.controller.state.get() != "run":
            robot.controller.state.set("off")
            robot.controller.state.set("run", await_sec=30)
            robot.motion.scale_setup.set(velocity=RobotConfig.SPEED_GLOBAL[0], acceleration=RobotConfig.SPEED_GLOBAL[1])
            logger.info(f"Robot {ip} is connected successfully")
        return robot
    except Exception as e:
        logger.critical(f"Failed to connect to robot at {ip}: {e}")
        return None

def check_robot_safety(robot: RobotApi) -> bool:
    status = robot.safety.status.get()
    if status in ("emergency_stop", "safeguard_stop", "fault", "violation"):
        logger.critical(f"SAFETY TRIGGERED: {status}. Halting.")
        robot.motion.mode.set("hold")
        return False
    return True

def emergency_stop(robot: RobotApi):
    try: robot.motion.mode.set("hold")
    except: pass

def execute_task(robot: RobotApi, task: Task, cfg: AdapterConfig) -> Tuple[bool, bool]:
    t = task.task_type
    p = task.payload
    
    if t == 7 and len(p) > 0 and int(round(p[0])) == 113:
        t = 113
        
    handler = TASK_REGISTRY.get(t)
    if handler:
        try:
            return handler(robot, task, cfg)
        except Exception as e:
            logger.error(f"Handler for task {t} crashed: {e}", exc_info=True)
            try: robot.motion.mode.set("hold")
            except: pass
            return False, True
            
    logger.info(f"Task {t} not registered. Acknowledging.")
    return True, False

# ==========================================
# СТАНДАРТНЫЕ ОБРАБОТЧИКИ
# ==========================================
@register_task(0)
def _None(robot: RobotApi, task: Task, cfg: AdapterConfig) -> Tuple[bool, bool]:
    return True, False

@register_task(1)
def _move_joint(robot: RobotApi, task: Task, cfg: AdapterConfig) -> Tuple[bool, bool]:
    p = task.payload
    xyz_m = [p[4]/1000, p[5]/1000, p[6]/1000]
    abc_deg = [float(x) for x in p[7:10]]
    abc_converted = convert_XYZ_to_ABC(abc_deg)
    tcp_pose = xyz_m + abc_converted
    logger.info(f"MoveJoint: TCP={[round(v,3) for v in tcp_pose]}")
    try:
        robot.motion.joint.add_new_waypoint(
            tcp_pose=tcp_pose,
            speed=RobotConfig.SPEED_JOINT[0],
            accel=RobotConfig.SPEED_JOINT[1],
            blend=0.005,
            units="deg"
        )
        robot.motion.mode.set("move")
        success = _wait_motion_complete(robot)
        return success, False
    except Exception as e:
        logger.warning(f"⚠️ MoveJoint failed: {e}. Skipping to next cycle.")
        try: robot.motion.mode.set("hold")
        except: pass
        return False, False

@register_task(2)
def _move_linear(robot: RobotApi, task: Task, cfg: AdapterConfig) -> Tuple[bool, bool]:
    p = task.payload
    try:
        xyz_m = [p[4]/1000, p[5]/1000, p[6]/1000]
        abc_deg = [float(x) for x in p[7:10]]
        abc_converted = convert_XYZ_to_ABC(abc_deg)
        tcp_pose = xyz_m + abc_converted
        logger.info(f"MoveLinear: TCP={[round(v,3) for v in tcp_pose]}")
        robot.motion.linear.add_new_waypoint(
            tcp_pose=tcp_pose,
            speed=RobotConfig.SPEED[0],
            accel=RobotConfig.SPEED[1],
            blend=0.005,
            orientation_units="deg"
        )
        robot.motion.mode.set("move")
        success = _wait_motion_complete(robot)
        
        # Управление схватом после движения
        gripper_val = float(p[3]) if len(p) > 2 else 0.0
        if gripper_val == -1.0:
            logger.info(f"🤏 Gripper: DO {RobotConfig.GRIPPER_DO} -> CLOSE (payload[3]=-1)")
            set_gripper_state(robot, True)
        return success, False
    except Exception as e:
        logger.warning(f"⚠️ MoveLinear failed: {e}. Skipping to next cycle.")
        try: robot.motion.mode.set("hold")
        except: pass
        return False, False

@register_task(9)
def _set_do(robot: RobotApi, task: Task, cfg: AdapterConfig) -> Tuple[bool, bool]:
    p = task.payload
    ports = [int(round(x)) for x in p[:8]]
    values = [bool(int(round(x))) for x in p[8:16]]
    for i, port in enumerate(ports):
        if 0 <= port < 24:
            robot.io.digital.set_output(cast(DigitalIndex, port), values[i])
    return True, False

@register_task(15)
def _final_task(robot: RobotApi, task: Task, cfg: AdapterConfig) -> Tuple[bool, bool]:
    logger.info("FinalTask received. Cycle complete.")
    return True, True

@register_task(20)
def _sleep(robot: RobotApi, task: Task, cfg: AdapterConfig) -> Tuple[bool, bool]:
    ms = task.payload[0] if task.payload else 0
    time.sleep(ms / 1000)
    return True, False

# ==========================================
# КАСТОМНЫЕ ОБРАБОТЧИКИ (SCAPE)
# ==========================================
@register_task(7)
def _run_job(robot: RobotApi, task: Task, cfg: AdapterConfig) -> Tuple[bool, bool]:
    p = task.payload
    job_id = int(round(p[0])) if p else 0
    logger.info(f"RunJob {job_id} received, args: {p[1:]}")
    
    if job_id == 1:
        return True, False
    elif job_id == 100:
        logger.info("RunJob 100: Moving to home pose...")
        robot.motion.joint.add_new_waypoint(
            angle_pose=cfg.CALIBRATION_HOME_ANGLES, 
            speed=RobotConfig.CALIBRATION_SPEED, 
            accel=RobotConfig.CALIBRATION_ACCEL, 
            blend=RobotConfig.CALIBRATION_BLEND, 
            units="deg"
        )
        robot.motion.mode.set("move")
        success = _wait_motion_complete(robot)
        return success, True
    elif job_id == 113:
        return _calibration_113(robot, Task(task.packet_id, task.task_group_id, 113, p, task.remain_tasks), cfg)
        
    logger.warning(f"Unknown RunJob {job_id}, acknowledging.")
    return True, False

@register_task(113)
def _calibration_113(robot: RobotApi, task: Task, cfg: AdapterConfig) -> Tuple[bool, bool]:
    p = task.payload
    if not p: return True, False
    mode = int(round(p[1])) if len(p) > 1 else 0
    try:
        if mode == 0:
            logger.info("Calibration: Moving to home pose...")
            robot.motion.joint.add_new_waypoint(
                angle_pose=cfg.CALIBRATION_HOME_ANGLES,
                speed=RobotConfig.CALIBRATION_SPEED,
                accel=RobotConfig.CALIBRATION_ACCEL,
                blend=RobotConfig.CALIBRATION_BLEND,
                units="deg"
            )
            robot.motion.mode.set("move")
            success = _wait_motion_complete(robot)
            return success, False
        elif 1 <= mode <= 6:
            logger.info(f"Calibration: Rotating J{mode} +{cfg.CALIBRATION_JOINT_OFFSET}°...")
            joints = list(robot.motion.joint.get_actual_position(units="deg"))
            joints[mode - 1] += cfg.CALIBRATION_JOINT_OFFSET
            robot.motion.joint.add_new_waypoint(
                angle_pose=joints,
                speed=RobotConfig.CALIBRATION_SPEED,
                accel=RobotConfig.CALIBRATION_ACCEL,
                blend=RobotConfig.CALIBRATION_BLEND,
                units="deg"
            )
            robot.motion.mode.set("move")
            success = _wait_motion_complete(robot)
            return success, True
            
        logger.warning(f"Unknown calibration mode: {mode}")
        return True, False
    except Exception as e:
        logger.warning(f"⚠️ Calibration failed: {e}. Skipping to next step.")
        try: robot.motion.mode.set("hold")
        except: pass
        return False, True

@register_task(19)
def _teach_point(robot: RobotApi, task: Task, cfg: AdapterConfig) -> Tuple[bool, bool]:
    logger.info("📍 Teach Point Task received. Starting Simple Joystick...")
    robot.motion.simple_joystick()
    logger.info("✅ Teach complete. Pose will be sent in next uplink.")
    return True, False

@register_task(12)
def _internal_12(robot: RobotApi, task: Task, cfg: AdapterConfig) -> Tuple[bool, bool]:
    return True, False

@register_task(13)
def _internal_13(robot: RobotApi, task: Task, cfg: AdapterConfig) -> Tuple[bool, bool]:
    return True, False