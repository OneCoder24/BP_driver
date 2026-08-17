"""
Управление роботом RC через пульт SR-RC02 (RS-485 / Pelco-D)
Версия с сохранением состояния джойстика
"""
import serial
from time import sleep
from sys import stdout
from typing import Dict, Tuple
from API.rc_api import RobotApi
from API.source.models.type_aliases import JogAxis, JogDirection, JointIndex

# ─── КОНФИГУРАЦИЯ ─────────────────────────────────────────────────────────────
ROBOT_IP = "127.0.0.1"
COM_PORT = "/dev/ttyUSB0"
BAUDRATE = 9600

DEADZONE = 5
SPEED_STEP = 0.05
MIN_GLOBAL_SPEED = 0.1
MAX_GLOBAL_SPEED = 1.0
LOOP_DELAY = 0.01  # 100 Гц

# ─── РЕЖИМЫ УПРАВЛЕНИЯ ────────────────────────────────────────────────────────
MODES = {
    0: "LINEAR_BASE",
    1: "ANGULAR_BASE",
    2: "JOINT_123",
    3: "JOINT_456",
    4: "LINEAR_TCP",
    5: "ANGULAR_TCP"
}

MODE_AXIS_MAP = {
    "LINEAR_BASE": {
        "Y": ("Y", "+"), "-Y": ("Y", "-"),
        "X": ("X", "+"), "-X": ("X", "-"),
        "Z": ("Z", "+"), "-Z": ("Z", "-")
    },
    "ANGULAR_BASE": {
        "Y": ("Ry", "+"), "-Y": ("Ry", "-"),
        "X": ("Rx", "+"), "-X": ("Rx", "-"),
        "Z": ("Rz", "+"), "-Z": ("Rz", "-")
    },
    "JOINT_123": {
        "Y": (1, "+"), "-Y": (1, "-"),
        "X": (0, "+"), "-X": (0, "-"),
        "Z": (2, "+"), "-Z": (2, "-")
    },
    "JOINT_456": {
        "Y": (4, "+"), "-Y": (4, "-"),
        "X": (3, "+"), "-X": (3, "-"),
        "Z": (5, "+"), "-Z": (5, "-")
    },
    "LINEAR_TCP": {
        "Y": ("Y", "+"), "-Y": ("Y", "-"),
        "X": ("X", "+"), "-X": ("X", "-"),
        "Z": ("Z", "+"), "-Z": ("Z", "-")
    },
    "ANGULAR_TCP": {
        "Y": ("Ry", "+"), "-Y": ("Ry", "-"),
        "X": ("Rx", "+"), "-X": ("Rx", "-"),
        "Z": ("Rz", "+"), "-Z": ("Rz", "-")
    }
}


# ─── УТИЛИТЫ ──────────────────────────────────────────────────────────────────
class Coords:
    def __init__(self, values):
        self.v = list(values)
    
    def __repr__(self):
        x, y, z, *r = self.v
        return f"{'  '.join(f'{v*1000:.1f}' for v in (x, y, z)) + ' mm  ' + '  '.join(f'{v:.2f}' for v in r)}"


def draw_ui(status: str, coords: list, mode_info: str, speed_info: str):
    n = 4
    stdout.write("\033[F" * n)
    stdout.write(f"\033[2KSTATUS: {status}\n")
    stdout.write(f"\033[2KPOS   : {Coords(coords)}\n")
    stdout.write(f"\033[2KMODE  : {mode_info}\n")
    stdout.write(f"\033[2KSPEED : {speed_info}\n")
    stdout.flush()


# ─── ПАРСЕР ПУЛЬТА ───────────────────────────────────────────────────────────
class PelcoParser:
    def __init__(self, port: str, baud: int):
        self.ser = serial.Serial(port, baud, timeout=0.05)
        self._pkt = bytearray(7)
        # Сохранённое состояние джойстика
        self._last_cmd1 = 0
        self._last_cmd2 = 0
        self._last_pan = 0
        self._last_tilt = 0
        self._has_new_packet = False

    def read(self) -> bool:
        """Читает пакет. Возвращает True если есть НОВЫЙ пакет, иначе False.
        Но состояние джойстика всегда доступно через свойства."""
        if self.ser.in_waiting >= 7:
            self._pkt = bytearray(self.ser.read(7))
            if sum(self._pkt[1:6]) & 0xFF == self._pkt[6]:
                self._last_cmd1 = self._pkt[2]
                self._last_cmd2 = self._pkt[3]
                self._last_pan = self._pkt[4]
                self._last_tilt = self._pkt[5]
                self._has_new_packet = True
                return True
        self._has_new_packet = False
        return False

    @property
    def cmd1(self) -> int:
        return self._last_cmd1
    
    @property
    def cmd2(self) -> int:
        return self._last_cmd2
    
    @property
    def pan(self) -> int:
        return self._last_pan
    
    @property
    def tilt(self) -> int:
        return self._last_tilt
    
    @property
    def preset_num(self) -> int | None:
        return self._last_tilt if self._last_cmd2 == 0x07 else None
    
    @property
    def has_new_packet(self) -> bool:
        return self._has_new_packet


# ─── КОНТРОЛЛЕР ───────────────────────────────────────────────────────────────
def run(robot: RobotApi, joystick: PelcoParser):
    mode_idx = 0
    global_speed = 0.5
    last_coords: list = [0.0] * 6
    status = "INIT OK"

    print("\n[INFO] Готов к работе.")
    print("  Джойстик: X (LEFT/RIGHT), Y (UP/DOWN), Z (CW/CCW вращение)")
    print("  NEAR/FAR: Глобальная скорость -/+ (шаг 0.05)")
    print("  CLOSE: Переключение режима (циклически)")
    print("  Режимы: LINEAR_BASE → ANGULAR_BASE → JOINT_123 → JOINT_456 → LINEAR_TCP → ANGULAR_TCP\n")

    while robot.is_connected():
        # Читаем пакет (может быть новым или нет)
        joystick.read()
        
        cmd1, cmd2 = joystick.cmd1, joystick.cmd2
        pan_val = joystick.pan
        tilt_val = joystick.tilt
        preset = joystick.preset_num

        # === ДИСКРЕТНЫЕ КОМАНДЫ (обрабатываем только при новом пакете) ===
        if joystick.has_new_packet:
            # Переключение режимов (пресеты)
            if preset is not None and preset in (1, 2, 3, 4, 5, 6):
                mode_idx = preset - 1
                status = f"MODE: {MODES[mode_idx]}"
                coords = list(robot.motion.get_actual_position())
                draw_ui(status, coords, MODES[mode_idx], f"Global: {global_speed:.2f}")
                last_coords = coords
                continue

            # Переключение режимов (кнопка CLOSE)
            if cmd1 & 0x04:  # CLOSE
                mode_idx = (mode_idx + 1) % len(MODES)
                status = f"MODE: {MODES[mode_idx]}"
                coords = list(robot.motion.get_actual_position())
                draw_ui(status, coords, MODES[mode_idx], f"Global: {global_speed:.2f}")
                last_coords = coords
                continue

            # Глобальная скорость (NEAR/FAR)
            if cmd1 & 0x01:  # NEAR
                global_speed = max(MIN_GLOBAL_SPEED, round(global_speed - SPEED_STEP, 2))
                status = f"SPEED ↓ {global_speed:.2f}"
                coords = list(robot.motion.get_actual_position())
                draw_ui(status, coords, MODES[mode_idx], f"Global: {global_speed:.2f}")
                last_coords = coords
                continue
            if cmd2 & 0x80:  # FAR
                global_speed = min(MAX_GLOBAL_SPEED, round(global_speed + SPEED_STEP, 2))
                status = f"SPEED ↑ {global_speed:.2f}"
                coords = list(robot.motion.get_actual_position())
                draw_ui(status, coords, MODES[mode_idx], f"Global: {global_speed:.2f}")
                last_coords = coords
                continue

        # === НЕПРЕРЫВНЫЕ КОМАНДЫ (используем сохранённое состояние) ===
        
        def norm(val: int) -> float:
            return 0.0 if val <= DEADZONE else (val - DEADZONE) / (63 - DEADZONE)
        
        # Определяем ось и локальную скорость
        current_mode = MODES[mode_idx]
        axis_map = MODE_AXIS_MAP[current_mode]
        
        joy_axis, direction = "", ""
        local_speed = 0.0
        
        # Приоритет: Y (UP/DOWN) → X (RIGHT/LEFT) → Z (CW/CCW)
        if cmd2 & 0x08:  # UP
            joy_axis, direction = "Y", "+"
            local_speed = norm(tilt_val)
        elif cmd2 & 0x10:  # DOWN
            joy_axis, direction = "Y", "-"
            local_speed = norm(tilt_val)
        elif cmd2 & 0x02:  # RIGHT
            joy_axis, direction = "X", "+"
            local_speed = norm(pan_val)
        elif cmd2 & 0x04:  # LEFT
            joy_axis, direction = "X", "-"
            local_speed = norm(pan_val)
        elif cmd2 & 0x20:  # CW → Z+
            joy_axis, direction = "Z", "+"
            local_speed = 1.0
        elif cmd2 & 0x40:  # CCW → Z-
            joy_axis, direction = "Z", "-"
            local_speed = 1.0
        
        # Если нет движения
        if not joy_axis or local_speed == 0:
            if status != "HOLD":
                status = "HOLD"
            coords = list(robot.motion.get_actual_position())
            draw_ui(status, coords, MODES[mode_idx], f"Global: {global_speed:.2f}")
            last_coords = coords
            sleep(LOOP_DELAY)
            continue

        # === ПРИМЕНЕНИЕ СКОРОСТИ И ОТПРАВКА КОМАНДЫ ===
        final_scale = global_speed * local_speed
        robot.motion.scale_setup.set(velocity=final_scale, acceleration=final_scale)

        full_name = joy_axis if direction == "+" else f"-{joy_axis}"
        cfg = axis_map.get(full_name)
        
        if cfg is None:
            sleep(LOOP_DELAY)
            continue

        # Отправка команды
        if mode_idx in (0, 1, 4, 5):  # LINEAR или ANGULAR
            api_axis, api_dir = cfg
            if mode_idx in (0, 1):  # BASE
                robot.motion.linear.set_jog_param_in_tcp("base")
            else:  # TCP
                robot.motion.linear.set_jog_param_in_tcp("tcp")
            robot.motion.linear.jog_once(api_axis, api_dir)
            status = f"MOVING: {api_axis}{api_dir}"
        else:  # JOINT
            joint_idx, joint_dir = cfg
            robot.motion.joint.jog_once(joint_idx, joint_dir)
            status = f"MOVING: J{joint_idx+1}{joint_dir}"

        # === ОБНОВЛЕНИЕ UI ===
        coords = list(robot.motion.get_actual_position())
        draw_ui(
            status, coords,
            f"{MODES[mode_idx]} | Local: {local_speed:.2f}",
            f"Global: {global_speed:.2f} × {local_speed:.2f} = {final_scale:.2f}"
        )
        last_coords = coords
        sleep(LOOP_DELAY)


# ─── ИНИЦИАЛИЗАЦИЯ ────────────────────────────────────────────────────────────
def init_robot(ip: str) -> RobotApi:
    r = RobotApi(ip=ip, show_std_traceback=False, autoconnect=True, enable_logger=False)
    real = input("Is robot real? (any/no): ").strip().lower() == "no"
    
    if real:
        r.controller_state.set_confirm_position_callback(lambda x: True)
    
    sleep(0.2)
    r.payload.set(mass=0, tcp_mass_center=(0, 0.1, 0))
    r.motion.scale_setup.set(velocity=1, acceleration=1)
    sleep(0.3)
    r.controller_state.set("run", await_sec=120)
    
    if real:
        r.motion.joint.add_new_waypoint(
            angle_pose=(0, -90, 90, -90, -90, 0),
            speed=170, accel=100, blend=0, units="deg"
        )
        r.motion.mode.set("move")
        r.motion.wait_waypoint_completion(0)
        
    print(f"Robot {r.get_robot_info().robot_model} ready.")
    return r


if __name__ == "__main__":
    robot = None
    joy = None
    try:
        robot = init_robot(ROBOT_IP)
        joy = PelcoParser(COM_PORT, BAUDRATE)
        run(robot, joy)
    except KeyboardInterrupt:
        print("\n[STOP] Manual interrupt.")
    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
    finally:
        if robot is not None and robot.is_connected():
            robot.disconnect()
        if joy is not None:
            joy.ser.close()