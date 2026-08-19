"""Shared configuration and state for SCAPE driver."""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, cast, Callable, Literal
from enum import Enum, IntEnum
from time import sleep
from API.types import DigitalIndex

logger = logging.getLogger("RoboProSCAPE.Config")

# ==========================================
# ТИПИЗИРОВАННЫЕ ПЕРЕЧИСЛЕНИЯ (ENUMS)
# ==========================================
class ActiveMode(str, Enum):
    IDLE = "IDLE"
    CALIBRATING = "CALIBRATING"
    PICKING = "PICKING"

class PickConfig(IntEnum):
    BP_ONLY = 1
    OC_ONLY = 2
    BP_OC = 3

class BpPhase(IntEnum):
    RESET = 0
    SEND_20 = 1
    WAIT_15 = 2
    PROCESS = 3

class CalibPhase(IntEnum):
    RESET = 0
    SEND_10 = 1
    PROCESS = 2
    DONE = 3

# ==========================================
# КЛАССЫ КОНФИГУРАЦИИ
# ==========================================
@dataclass
class AdapterConfig:
    """Настройки подключения к адаптеру и протокола."""
    IP: str = "192.168.55.136"
    PORT: int = 14666
    SOCKET_TIMEOUT: float = 10.0
    READ_TIMEOUT: float = 1.0
    PACKET_FLOAT_COUNT: int = 60  # Фиксированная длина пакета
    PREFIX: str = "[["
    SUFFIX: str = "]]"
    SEPARATOR: str = ","
    HEARTBEAT_INTERVAL: float = 0.2
    RECONNECT_BACKOFF_MAX: float = 30.0
    TASK_TIMEOUT: float = 120.0
    GRIPPER_CLOSED_DO: int = 10  # Для uplink-статуса схвата
    CALIBRATION_HOME_ANGLES: Tuple[float, ...] = (45, -90.0, 90.0, -90.0, -90.0, 0.0)
    CALIBRATION_JOINT_OFFSET: float = 30.0

@dataclass
class RobotConfig:
    """Настройки робота: подключение, скорости, безопасность."""
    IP: str = "127.0.0.1"
    SPEED: Tuple[float, float] = (0.15, 0.15)
    SPEED_JOINT: Tuple[float, float] = (25, 80)
    SPEED_GLOBAL: Tuple[float, float] = (0.5, 0.5)
    CALIBRATION_SPEED: float = 15.0
    CALIBRATION_ACCEL: float = 15.0
    CALIBRATION_BLEND: float = 0.0
    GRIPPER_DO: int = 7
    GRIPPER_WAIT_SEC: float = 0.3
    TIMEOUT_MOTION: float = 30.0
    SAFETY_CHECK_INTERVAL: float = 0.1
    JOINT_LIMITS: Optional[Tuple[Tuple[float, float], ...]] = (
        (-180, 180), (-180, 180), (-160, 160),
        (-180, 180), (-180, 180), (-180, 180)
    )
    TCP_OFFSET: Optional[Tuple[float, float, float, float, float, float]] = None

@dataclass
class ProductConfig:
    """Параметры продукта для задачи бин-пикинга."""
    product_id: int = 1
    rescan: bool = True
    config: PickConfig = PickConfig.BP_OC
    path_planning: bool = True
    dump_back_bin: bool = False
    start_highest_zone: int = 0

@dataclass
class SessionState:
    """Состояние сессии драйвера."""
    mode: ActiveMode = ActiveMode.IDLE
    mode_after_reset: Optional[ActiveMode] = None
    
    bp_phase: BpPhase = BpPhase.RESET
    calib_phase: CalibPhase = CalibPhase.RESET
    first_iteration: bool = True
    
    calibration_step: str = "IDLE"
    last_down_packet_id: int = 0
    executing_group_id: int = 0
    buffer_free_slots: int = 5
    robot_status: int = 1
    pending_command: Optional[List[float]] = None
    grip_status: float = 0.0
    reconnect_backoff: float = 1.0
    
    count_of_parts: int = 0
    
    # Явные коллбэки для пользовательской логики
    on_pick_success: Optional[Callable] = None
    on_pick_failure: Optional[Callable] = None

# ==========================================
# ГЛОБАЛЬНЫЕ НАСТРОЙКИ ЗАПУСКА
# ==========================================
ACTIVE_MODE: Literal["CALIBRATE", "PICK"] = "CALIBRATE"
ACTIVE_PRODUCT: ProductConfig = ProductConfig()
AUTO_START: bool = True

# ==========================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ==========================================
def set_gripper_state(robot, grip_state: bool):
    """Устанавливает DO схвата и ждёт GRIPPER_WAIT_SEC."""
    grip_states_list: list = ["OPEN", "CLOSE"]
    logger.info(f"🤏 Gripper: DO {RobotConfig.GRIPPER_DO} -> {grip_states_list[int(grip_state)]})")
    robot.io.digital.set_output(cast(DigitalIndex, RobotConfig.GRIPPER_DO), grip_state)
    sleep(RobotConfig.GRIPPER_WAIT_SEC)
