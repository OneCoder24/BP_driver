"""RoboPro SCAPE Adapter Driver (Pure Protocol Layer)"""
from __future__ import annotations
import socket, time, logging, os
from collections import deque
from typing import List, Dict, Optional, Tuple, Iterable, cast
from API.types import DigitalIndex
from config import (
    AdapterConfig, SessionState, ACTIVE_MODE, ACTIVE_PRODUCT, AUTO_START,
    ActiveMode, BpPhase, CalibPhase
)
import task_executor
from task_executor import Task

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("RoboProSCAPE.Driver")

class LineRotatingLogger:
    def __init__(self, filepath: str, max_lines: int = 1000):
        self.filepath = filepath
        self.buffer = deque(maxlen=max_lines)
        if os.path.exists(filepath):
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    for line in f: self.buffer.append(line.strip())
            except: pass

    def log(self, message: str):
        try:
            ts = time.strftime("%H:%M:%S")
            self.buffer.append(f"[{ts}] {message}")
            with open(self.filepath, 'w', encoding='utf-8') as f:
                f.write('\n'.join(self.buffer) + '\n')
        except: pass

def build_uplink(state: SessionState, joints: Iterable[float], pose: Iterable[float], cfg: AdapterConfig) -> str:
    joints = tuple(joints)
    pose = tuple(pose)
    payload = [float(state.last_down_packet_id), 0.0]
    payload += [1.0, 5.0, float(state.robot_status), float(state.buffer_free_slots), state.grip_status, 0.0, float(state.executing_group_id)]
    payload += [2.0, float(len(joints))] + [float(j) for j in joints]
    payload += [3.0, 6.0, pose[0], pose[1], pose[2], pose[3], pose[4], pose[5]]
    cmd = state.pending_command if state.pending_command else [0.0]*11
    cmd_safe = ([float(x) for x in cmd] + [0.0]*11)[:11]
    payload += [4.0, 11.0] + cmd_safe
    payload = (payload + [0.0] * cfg.PACKET_FLOAT_COUNT)[:cfg.PACKET_FLOAT_COUNT]
    return f"{cfg.PREFIX}{cfg.SEPARATOR.join(f'{v:.6g}' for v in payload)}{cfg.SUFFIX}"

def receive_framed(sock: socket.socket, buffer: bytearray, cfg: AdapterConfig) -> Tuple[Optional[str], bytearray]:
    suffix_bytes = cfg.SUFFIX.encode()
    sock.settimeout(cfg.READ_TIMEOUT)
    while suffix_bytes not in buffer:
        try:
            chunk = sock.recv(1024)
            if not chunk: return None, buffer
            buffer.extend(chunk)
            if len(buffer) > 4096: buffer[:] = buffer[-1024:]
        except socket.timeout: return None, buffer
        
    end_idx = buffer.find(suffix_bytes) + len(suffix_bytes)
    packet = buffer[:end_idx].decode("utf-8", errors="ignore")
    del buffer[:end_idx]
    return packet, buffer

def parse_downlink(raw: str, cfg: AdapterConfig) -> Optional[Task]:
    clean = raw.replace(cfg.PREFIX, " ").replace(cfg.SUFFIX, " ").strip()
    if not clean: return None
    try: 
        data = [float(x) for x in clean.split(cfg.SEPARATOR) if x.strip()]
    except ValueError: 
        return None
        
    if len(data) < 9: return None
    
    meta = int(round(data[1]))
    valid_num = max(meta // 1000, len(data)) if meta > 0 else len(data)
    data = data[:valid_num]
    
    packet_id, task_group_id, task_type = int(round(data[0])), int(round(data[7])), int(round(data[8]))
    
    if task_group_id == 0 or task_type == 0:
        return Task(
            packet_id=packet_id,
            task_group_id=0,
            task_type=0,
            payload=[],
            remain_tasks=int(round(data[6]))
        )
        
    payload_start = 10 if task_type in (16, 17, 18) else 9
    return Task(
        packet_id=packet_id,
        task_group_id=task_group_id,
        task_type=task_type,
        payload=data[payload_start:],
        remain_tasks=int(round(data[6]))
    )

def main():
    cfg = AdapterConfig()
    state = SessionState()
    
    # 🔹 Явная регистрация коллбэков (вместо неявного импорта на уровне модуля)
    try:
        import user_logic
        state.on_pick_success = getattr(user_logic, "on_pick_success", None)
        state.on_pick_failure = getattr(user_logic, "on_pick_failure", None)
        logger.info("✅ user_logic.py loaded. Post-pick actions enabled.")
    except ImportError:
        logger.warning("⚠️ user_logic.py not found. Post-pick actions will be skipped.")

    logger.info(f"Initializing SCAPE Driver [Adapter: {cfg.IP}:{cfg.PORT}, Robot: 192.168.55.124]")
    uplink_log = LineRotatingLogger("uplink_commands.log", max_lines=10000)
    downlink_log = LineRotatingLogger("downlink_commands.log", max_lines=10000)
    
    robot = task_executor.connect_to_robot()
    if robot is None: return
    
    sock, buffer = None, bytearray()
    
    if AUTO_START and ACTIVE_MODE == "PICK":
        state.mode = ActiveMode.PICKING
        state.bp_phase = BpPhase.RESET
    if AUTO_START and ACTIVE_MODE == "CALIBRATE":
        state.mode = ActiveMode.CALIBRATING
        state.calib_phase = CalibPhase.RESET

    try:
        while True:
            if sock is None or sock.fileno() == -1:
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(cfg.SOCKET_TIMEOUT)
                    sock.connect((cfg.IP, cfg.PORT))
                    logger.info(f"Connected to adapter at {cfg.IP}:{cfg.PORT}")
                    state.reconnect_backoff, state.buffer_free_slots, state.executing_group_id = 1.0, 5, 0
                    buffer.clear()
                except Exception as e:
                    logger.error(f"Connection failed: {e}. Retrying in {state.reconnect_backoff:.1f}s")
                    time.sleep(state.reconnect_backoff)
                    state.reconnect_backoff = min(state.reconnect_backoff * 2, cfg.RECONNECT_BACKOFF_MAX)
                    continue
                    
            while task_executor.check_robot_safety(robot):
                pose, joints = task_executor.get_robot_pose(robot)
                state.grip_status = 1.0 if robot.io.digital.get_output(cast(DigitalIndex, cfg.GRIPPER_CLOSED_DO)) else 0.0
                
                # 1. Упаковываем пакет
                uplink = build_uplink(state, joints, pose, cfg)
                
                # 2. Очищаем команду СРАЗУ после упаковки
                if state.pending_command:
                    state.pending_command = None
                    
                uplink_log.log(uplink)
                sock.sendall(uplink.encode("utf-8"))
                
                # 3. Читаем ответ
                raw_down, buffer = receive_framed(sock, buffer, cfg)
                if raw_down: downlink_log.log(raw_down)
                
                task = parse_downlink(raw_down, cfg) if raw_down else None
                
                if task:
                    state.last_down_packet_id = task.packet_id
                    state.buffer_free_slots = max(state.buffer_free_slots, 1)
                    success, cycle_complete = task_executor.execute_task(robot, task, cfg)
                    state.executing_group_id = -task.task_group_id
                    
                    # 🔹 КОНЕЧНЫЙ АВТОМАТ КАЛИБРОВКИ
                    if state.mode == ActiveMode.CALIBRATING:
                        if state.calib_phase == CalibPhase.RESET:
                            state.pending_command = [-1.0] + [0.0]*10
                            state.calib_phase = CalibPhase.SEND_10
                            logger.info("🔄 Calib Phase 0: Sent reset (-1)")
                        elif state.calib_phase == CalibPhase.SEND_10:
                            state.pending_command = [10.0] + [0.0]*10
                            state.calib_phase = CalibPhase.PROCESS
                            logger.info("🔄 Calib Phase 1: Sent calibrate command (10)")
                        elif state.calib_phase == CalibPhase.PROCESS:
                            if cycle_complete:
                                state.pending_command = [10.0] + [0.0]*10
                                logger.info("✅ Calib step complete. Sent confirmation (10)")
                            if task.task_type == 15:
                                state.calib_phase = CalibPhase.DONE
                                state.mode = ActiveMode.IDLE
                                logger.info("✅ Calibration complete. Ready.")
                                
                    # 🔹 КОНЕЧНЫЙ АВТОМАТ БИН-ПИКИНГА
                    if state.mode == ActiveMode.PICKING:
                        if state.bp_phase == BpPhase.RESET:
                            state.pending_command = [-1.0] + [0.0]*10
                            state.bp_phase = BpPhase.SEND_20
                            logger.info("🔄 Phase 0: Sent reset (-1)")
                        elif state.bp_phase == BpPhase.SEND_20:
                            state.pending_command = [20.0, 1.0, 1.0, 9.0] + [0.0]*7
                            state.bp_phase = BpPhase.WAIT_15
                            logger.info("🔄 Phase 1: Sent sleep (20)")
                        elif state.bp_phase == BpPhase.WAIT_15:
                            if task.task_type == 15 or state.first_iteration:
                                state.first_iteration = False
                                state.pending_command = [
                                    30.0, float(ACTIVE_PRODUCT.product_id), 
                                    float(int(ACTIVE_PRODUCT.rescan)), 
                                    float(ACTIVE_PRODUCT.config),
                                    float(int(ACTIVE_PRODUCT.path_planning)),
                                    float(int(ACTIVE_PRODUCT.dump_back_bin)),
                                    float(int(ACTIVE_PRODUCT.start_highest_zone))
                                ] + [0.0]*4
                                state.bp_phase = BpPhase.PROCESS
                                logger.info("✅ Phase 2: Received 15. Sent pick (30)")
                        elif state.bp_phase == BpPhase.PROCESS:
                            if task.task_type == 15:
                                task_payload = task.payload
                                
                                # Вызов явных коллбэков
                                if state.on_pick_success or state.on_pick_failure:
                                    try:
                                        if task_payload and task_payload[1] == 0.0 and task_payload[2] == 1.0:
                                            logger.info("✅ Pick successful. Calling on_pick_success()...")
                                            if state.on_pick_success:
                                                state.on_pick_success(robot, cfg, state, task_payload)
                                        else:
                                            logger.info("⚠️ Pick failed. Calling on_pick_failure()...")
                                            if state.on_pick_failure:
                                                state.on_pick_failure(robot, cfg, state, task_payload)
                                    except Exception as e:
                                        logger.error(f"❌ Error in user_logic: {e}", exc_info=True)
                                        
                                state.bp_phase = BpPhase.WAIT_15
                                state.first_iteration = True
                                logger.info("✅ Phase 3: Cycle complete. Ready for next trigger.")
            time.sleep(cfg.HEARTBEAT_INTERVAL)
            
    except (ConnectionResetError, BrokenPipeError, OSError) as e:
        logger.warning(f"Connection lost: {e}")
    except Exception as e:
        logger.critical(f"Unhandled loop error: {e}", exc_info=True)
        task_executor.emergency_stop(robot)
        state.mode = ActiveMode.IDLE
    finally:
        if sock:
            try: sock.close()
            except: pass
        sock = None
        state.robot_status = 1
        time.sleep(2)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Прервано пользователем (Ctrl+C). Корректное завершение...")
    except Exception as e:
        logger.critical(f"Непредвиденная ошибка: {e}", exc_info=True)