"""
SCAPE Bin-Picking GUI Controller (Modern Layout)
Запускает драйвер в отдельном потоке и предоставляет Tkinter интерфейс.

Архитектура работы с адаптером:
1. Движок стартует автоматически при подключении к роботу и работает постоянно.
2. last_down_packet_id живёт в рамках TCP-сессии, не сбрасывается при переключении режимов.
3. Кнопка "Сброс" отправляет -1 на адаптер, не закрывая соединение.
4. Кнопки "Калибровка"/"Пикинг" меняют state.mode, движок сам начинает с фазы RESET (отправка -1).
5. Корректное завершение только при закрытии окна GUI.
"""
import tkinter as tk
from tkinter import ttk, scrolledtext
import threading
import queue
import time
import logging
import socket
from typing import Optional
import config
from config import AdapterConfig, SessionState, ACTIVE_MODE, RobotConfig, ActiveMode, BpPhase, CalibPhase
import task_executor
from task_executor import Task
import user_logic
from jogging_panel import JoggingPanel


class QueueHandler(logging.Handler):
    def __init__(self, log_queue: queue.Queue):
        super().__init__()
        self.log_queue = log_queue
        self.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", datefmt="%H:%M:%S"))

    def emit(self, record):
        self.log_queue.put(self.format(record))


class ScapeGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("SCAPE Bin-Picking Controller")
        self.root.geometry("1100x750")
        self.root.minsize(1000, 700)
        
        self.root.attributes('-fullscreen', True)
        self.root.bind('<Escape>', lambda e: self.on_closing())

        self.log_queue = queue.Queue(maxsize=1000)
        self.state_queue = queue.Queue(maxsize=10)
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.driver_thread = None

        self.cfg = config.AdapterConfig()
        self.state = config.SessionState()
        self.is_running = False
        self.is_paused = False
        self.robot = None

        self._setup_styles()
        self._build_ui()
        self._setup_logging()
        self._poll_queues()

        # 🔹 Автоподключение и запуск движка при старте GUI
        self.root.after(500, self._auto_start)

    def _auto_start(self):
        """Пытается подключиться к роботу и запустить движок при старте GUI."""
        ip = self.entry_robot_ip.get()
        self.log_queue.put_nowait(f"🔄 Попытка автоподключения к роботу {ip}...")
        self.robot = task_executor.connect_to_robot(ip)
        if self.robot:
            self.log_queue.put_nowait(f"✅ Автоподключение к роботу {ip} успешно")
            self.lbl_robot_status.config(text=f"Робот: ✅ {ip}")
            self._update_control_buttons()
            # 🔹 Передаём ссылку на робота в джойстик
            if hasattr(self, 'jogging_panel'):
                self.jogging_panel.set_robot(self.robot)
            # 🔹 Запускаем движок сразу после подключения
            self._start_engine()
        else:
            self.log_queue.put_nowait(f"⚠️ Автоподключение не удалось. Нажмите 'Подключиться' вручную.")
            self.lbl_robot_status.config(text="Робот:  Отключен")

    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use('clam')
        style.configure("TFrame", background="#f0f0f0")
        style.configure("TLabelframe", background="#f0f0f0")
        style.configure("TLabelframe.Label", background="#f0f0f0", font=("Segoe UI", 9, "bold"))
        style.configure("TLabel", background="#f0f0f0", font=("Segoe UI", 12))
        style.configure("TButton", padding=12, font=("Segoe UI", 15))
        style.configure("Header.TLabel", font=("Segoe UI", 11, "bold"), background="#f0f0f0")
        style.configure("Run.TButton", foreground="white", background="#4CAF50")
        style.map("Run.TButton", background=[('active', '#45a049')])
        style.configure("Stop.TButton", foreground="white", background="#f44336")
        style.map("Stop.TButton", background=[('active', '#d32f2f')])
        style.configure("Pause.TButton", foreground="white", background="#FF9800")
        style.map("Pause.TButton", background=[('active', '#F57C00')])

    def _build_ui(self):
        main_paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main_paned.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        left_panel = ttk.Frame(main_paned)
        main_paned.add(left_panel, weight=1)

        conn_frame = ttk.LabelFrame(left_panel, text="Настройки подключения", padding=10)
        conn_frame.pack(fill=tk.X, padx=5, pady=(5, 5))

        ttk.Label(conn_frame, text="Robot IP:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.entry_robot_ip = ttk.Entry(conn_frame, width=15)
        self.entry_robot_ip.insert(0, config.RobotConfig.IP)
        self.entry_robot_ip.grid(row=0, column=1, padx=5, pady=2)

        ttk.Label(conn_frame, text="Adapter IP:").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.entry_adapter_ip = ttk.Entry(conn_frame, width=15)
        self.entry_adapter_ip.insert(0, self.cfg.IP)
        self.entry_adapter_ip.grid(row=1, column=1, padx=5, pady=2)

        ttk.Label(conn_frame, text="Adapter Port:").grid(row=2, column=0, sticky=tk.W, pady=2)
        self.entry_adapter_port = ttk.Entry(conn_frame, width=15)
        self.entry_adapter_port.insert(0, str(self.cfg.PORT))
        self.entry_adapter_port.grid(row=2, column=1, padx=5, pady=2)

        control_frame = ttk.LabelFrame(left_panel, text="Управление", padding=10)
        control_frame.pack(fill=tk.X, padx=5, pady=5)

        self.btn_connect = ttk.Button(control_frame, text="🔌 Подключиться", command=self.action_connect)
        self.btn_connect.pack(fill=tk.X, pady=2)

        # 🔹 Кнопка сброса — просто отправляет -1, не останавливает движок
        self.btn_reset = ttk.Button(control_frame, text="🔄 Сброс", command=self.action_reset, style="Stop.TButton")
        self.btn_reset.pack(fill=tk.X, pady=2)

        self.btn_pause = ttk.Button(control_frame, text="⏸️ Пауза", command=self.action_pause, style="Pause.TButton", state=tk.DISABLED)
        self.btn_pause.pack(fill=tk.X, pady=2)

        ttk.Separator(control_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=5)

        # 🔹 Кнопки режимов — не останавливают движок, просто меняют state.mode
        self.btn_calibrate = ttk.Button(control_frame, text=" Калибровка",
                                        command=lambda: self.action_start("CALIBRATE"),
                                        state=tk.DISABLED)
        self.btn_calibrate.pack(fill=tk.X, pady=2)

        self.btn_pick = ttk.Button(control_frame, text="📦 Пикинг",
                                   command=lambda: self.action_start("PICK"),
                                   style="Run.TButton", state=tk.DISABLED)
        self.btn_pick.pack(fill=tk.X, pady=2)

        status_frame = ttk.LabelFrame(left_panel, text="Состояние", padding=10)
        status_frame.pack(fill=tk.X, padx=5, pady=5)

        self.lbl_robot_status = ttk.Label(status_frame, text="Робот: ❌ Отключен")
        self.lbl_robot_status.pack(anchor=tk.W, pady=2)

        self.lbl_adapter_status = ttk.Label(status_frame, text="Адаптер: ❌ Отключен")
        self.lbl_adapter_status.pack(anchor=tk.W, pady=2)

        self.lbl_mode_status = ttk.Label(status_frame, text="Режим: IDLE", font=("Segoe UI", 9, "bold"))
        self.lbl_mode_status.pack(anchor=tk.W, pady=(5, 2))

        state_frame = ttk.LabelFrame(left_panel, text="Состояние автомата", padding=10)
        state_frame.pack(fill=tk.X, padx=5, pady=5)

        # --- Кнопка выхода ---
        exit_frame = ttk.Frame(left_panel)
        exit_frame.pack(fill=tk.X, padx=5, pady=(5, 5), side=tk.BOTTOM)

        self.btn_exit = ttk.Button(exit_frame, text=" Выход", command=self.on_closing, style="Stop.TButton")
        self.btn_exit.pack(fill=tk.X, pady=5)

        self.status_squares = {}

        row_modes = ttk.Frame(state_frame)
        row_modes.pack(fill=tk.X, pady=2)
        for label, key in [("IDLE", "IDLE"), ("CALIB", "CALIBRATING"), ("PICK", "PICKING")]:
            square = tk.Label(row_modes, width=2, height=1, bg="#888888", relief="solid", borderwidth=1)
            square.pack(side=tk.LEFT, padx=(0, 5))
            lbl = ttk.Label(row_modes, text=label)
            lbl.pack(side=tk.LEFT, padx=(0, 15))
            self.status_squares[key] = square

        ttk.Separator(state_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=5)

        row_calib = ttk.Frame(state_frame)
        row_calib.pack(fill=tk.X, pady=2)
        ttk.Label(row_calib, text="Calibration:", width=12, anchor=tk.W).pack(side=tk.LEFT, padx=(0, 10))
        for i in range(3):
            sq = tk.Label(row_calib, width=2, height=1, bg="#888888", relief="solid", borderwidth=1)
            sq.pack(side=tk.LEFT, padx=(0, 3))
            self.status_squares[f"CALIB_{i}"] = sq

        row_pick = ttk.Frame(state_frame)
        row_pick.pack(fill=tk.X, pady=2)
        ttk.Label(row_pick, text="Picking:", width=12, anchor=tk.W).pack(side=tk.LEFT, padx=(0, 10))
        for i in range(4):
            sq = tk.Label(row_pick, width=2, height=1, bg="#888888", relief="solid", borderwidth=1)
            sq.pack(side=tk.LEFT, padx=(0, 3))
            self.status_squares[f"PICK_{i}"] = sq

        right_paned = ttk.PanedWindow(main_paned, orient=tk.VERTICAL)
        main_paned.add(right_paned, weight=2)

    # 🔹 Реальный джойстик вместо заглушки
        jogging_frame = ttk.LabelFrame(right_paned, text="Джойстик", padding=10)
        right_paned.add(jogging_frame, weight=3)

        self.jogging_panel = JoggingPanel(
            jogging_frame,
            robot=self.robot,
            log_queue=self.log_queue,
            on_teach_point=self._on_teach_point
        )
        self.jogging_panel.pack(fill=tk.BOTH, expand=True)

        log_frame = ttk.LabelFrame(right_paned, text="Логи", padding=5)
        right_paned.add(log_frame, weight=1)

        self.log_text = scrolledtext.ScrolledText(
            log_frame, height=10, state=tk.DISABLED,
            font=("Consolas", 9), bg="#1e1e1e", fg="#d4d4d4"
        )
        self.log_text.bind("<Control-c>", lambda e: self._copy_log())
        self.log_text.bind("<Control-C>", lambda e: self._copy_log())
        self.log_text.bind("<Button-3>", lambda e: self._show_log_menu(e))
        self._log_menu = tk.Menu(self.root, tearoff=0)
        self._log_menu.add_command(label="Копировать", command=self._copy_log)
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def _on_teach_point(self, pose, joints):
        """Callback при нажатии 'Захватить позу' в джойстике."""
        self.log_queue.put_nowait(f"📍 Teach point: TCP={[round(p, 4) for p in pose]}")
        # Пока просто логируем. Позже можно сделать диалог сохранения точки.

    def _setup_logging(self):
        handler = QueueHandler(self.log_queue)
        for name in ["RoboProSCAPE.Driver", "RoboProSCAPE.Executor",
                     "RoboProSCAPE.Config", "RoboProSCAPE.UserLogic",
                     "RoboProSCAPE.GUI_Engine"]:
            logger = logging.getLogger(name)
            logger.setLevel(logging.INFO)
            logger.addHandler(handler)

    def _poll_queues(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.log_text.config(state=tk.NORMAL)
                self.log_text.insert(tk.END, msg + "\n")
                self.log_text.see(tk.END)
                self.log_text.config(state=tk.DISABLED)
        except queue.Empty:
            pass

        try:
            while True:
                state_data = self.state_queue.get_nowait()
                self._update_status_indicators(state_data)
        except queue.Empty:
            pass

        self.root.after(100, self._poll_queues)

    def _update_status_indicators(self, data):
        for square in self.status_squares.values():
            square.config(bg="#888888")

        mode = data.get("mode", ActiveMode.IDLE)
        if mode == ActiveMode.IDLE:
            self._set_square("IDLE", "#4CAF50")
        elif mode == ActiveMode.CALIBRATING:
            self._set_square("CALIBRATING", "#4CAF50")
        elif mode == ActiveMode.PICKING:
            self._set_square("PICKING", "#4CAF50")

        calib_p = data.get("calib_phase", CalibPhase.RESET)
        if mode == ActiveMode.CALIBRATING:
            self._set_square(f"CALIB_{calib_p.value}", "#FFC107")

        pick_p = data.get("bp_phase", BpPhase.RESET)
        if mode == ActiveMode.PICKING:
            self._set_square(f"PICK_{pick_p.value}", "#FFC107")

        self.lbl_mode_status.config(text=f"Режим: {mode.value}")
        parts = data.get("parts_count", 0)
        if mode == ActiveMode.PICKING:
            self.lbl_mode_status.config(text=f"Режим: {mode.value} | Деталей: {parts}")

        # 🔹 Обновляем статус адаптера (перенесено из DriverEngine._send_state_to_gui)
        connected = data.get("connected", False)
        if connected:
            self.lbl_adapter_status.config(text=f"Адаптер: ✅ {self.cfg.IP}:{self.cfg.PORT}")
        else:
            self.lbl_adapter_status.config(text="Адаптер: ❌ Отключен")

    def _set_square(self, key, color):
        if key in self.status_squares:
            self.status_squares[key].config(bg=color)

    def _copy_log(self):
        try:
            text = self.log_text.selection_get()
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
        except tk.TclError:
            pass

    def _show_log_menu(self, event):
        try:
            self._log_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._log_menu.grab_release()

    def _update_control_buttons(self):
        state = tk.NORMAL if self.is_robot_connected() else tk.DISABLED
        self.btn_calibrate.config(state=state)
        self.btn_pick.config(state=state)
        self.btn_pause.config(state=state if self.is_running else tk.DISABLED)

    def is_robot_connected(self) -> bool:
        return self.robot is not None and self.robot.is_connected()

    def action_connect(self):
        """Подключение к роботу и запуск движка."""
        if self.is_robot_connected():
            self.log_queue.put_nowait("ℹ️ Уже подключено к роботу.")
            return
        ip = self.entry_robot_ip.get()
        self.robot = task_executor.connect_to_robot(ip)
        if self.robot:
            self.log_queue.put_nowait(f"✅ Подключено к роботу {ip}")
            self.lbl_robot_status.config(text=f"Робот: ✅ {ip}")
            self._update_control_buttons()
            # 🔹 Передаём ссылку на робота в джойстик
            if hasattr(self, 'jogging_panel'):
                self.jogging_panel.set_robot(self.robot)
            # 🔹 Запускаем движок после подключения
            self._start_engine()
        else:
            self.log_queue.put_nowait(f"❌ Не удалось подключиться к роботу {ip}")
            self.lbl_robot_status.config(text="Робот: ❌ Ошибка подключения")

    def action_reset(self):
        """
        Сброс: отправляет -1 на адаптер и сбрасывает режим в IDLE.
        Движок НЕ останавливается — TCP-сессия сохраняется.
        """
        self.log_queue.put_nowait("🔄 Сброс состояния...")

        #  Отправляем -1 через pending_command
        self.state.pending_command = [-1.0] + [0.0] * 10

        #  Сбрасываем режим и фазы
        self.state.mode = ActiveMode.IDLE
        self.state.bp_phase = BpPhase.RESET
        self.state.calib_phase = CalibPhase.RESET
        self.state.first_iteration = True
        self.state.executing_group_id = 0
        self.state.count_of_parts = 0
        # last_down_packet_id НЕ сбрасываем — он живёт в рамках TCP-сессии

        self.is_paused = False
        self.pause_event.clear()
        self.btn_pause.config(text="⏸️ Пауза")

        self.log_queue.put_nowait("✅ Сброс отправлен. Режим: IDLE")

    def action_pause(self):
        if not self.is_running:
            return
        if self.is_paused:
            self.pause_event.clear()
            self.is_paused = False
            self.btn_pause.config(text="⏸️ Пауза")
            if self.robot:
                try:
                    self.robot.motion.mode.set("move")
                except:
                    pass
            self.log_queue.put_nowait("▶️ Работа возобновлена.")
        else:
            self.pause_event.set()
            self.is_paused = True
            self.btn_pause.config(text="▶️ Продолжить")
            if self.robot:
                try:
                    self.robot.motion.mode.set("pause")
                except:
                    pass
            self.log_queue.put_nowait("⏸️ Работа приостановлена.")

    def action_start(self, mode_str: str):
        """
        Запуск режима (Калибровка/Пикинг).
        НЕ останавливает движок — просто меняет state.mode и фазы.
        Движок сам увидит смену и начнёт с фазы RESET (отправка -1).
        """
        if not self.is_robot_connected():
            self.log_queue.put_nowait("⚠️ Сначала подключитесь к роботу.")
            return

        if not self.is_running:
            self._start_engine()
            time.sleep(0.3)  # Даём движку время на подключение к адаптеру

        #  Применяем настройки из GUI
        self.cfg.IP = self.entry_adapter_ip.get()
        try:
            self.cfg.PORT = int(self.entry_adapter_port.get())
        except ValueError:
            self.cfg.PORT = 14998

        # 🔹 Меняем режим и сбрасываем фазы
        # Движок увидит смену state.mode и начнёт с фазы RESET (отправка -1)
        self.state.first_iteration = True
        self.state.executing_group_id = 0
        self.state.pending_command = None

        if mode_str == "PICK":
            self.state.mode = ActiveMode.PICKING
            self.state.bp_phase = BpPhase.RESET
            self.state.calib_phase = CalibPhase.RESET
            self.log_queue.put_nowait("📦 Режим: Пикинг. Движок начнёт с отправки -1.")
        elif mode_str == "CALIBRATE":
            self.state.mode = ActiveMode.CALIBRATING
            self.state.calib_phase = CalibPhase.RESET
            self.state.bp_phase = BpPhase.RESET
            self.log_queue.put_nowait("📐 Режим: Калибровка. Движок начнёт с отправки -1.")

        self.is_paused = False
        self.pause_event.clear()
        self.btn_pause.config(text="⏸️ Пауза")

    def _start_engine(self):
        """Запуск движка в отдельном потоке."""
        if self.is_running:
            return
        self.stop_event.clear()
        self.is_running = True
        self.driver_thread = threading.Thread(
            target=DriverEngine(self.cfg, self.state, self.robot,
                                self.log_queue, self.state_queue,
                                self.stop_event, self.pause_event, self).run,
            daemon=True
        )
        self.driver_thread.start()
        self._update_control_buttons()

    def _stop_engine(self):
        if self.is_running and self.driver_thread:
            self.stop_event.set()
            try:
                self.driver_thread.join(timeout=3.0)
            except Exception:
                pass  # Поток уже завершён или ошибка
            self.is_running = False
            try:
                self._update_control_buttons()
            except:
                pass  # GUI может быть уже уничтожен

    def on_closing(self):
        """Корректное завершение при закрытии окна GUI или Ctrl+C."""
        # Защита от повторного вызова
        if hasattr(self, '_closing') and self._closing:
            return
        self._closing = True

        try:
            # Останавливаем движок (с таймаутом, чтобы не зависнуть)
            if self.is_running:
                self._stop_engine()

            # Отключаем робота
            if self.robot:
                try:
                    self.robot.disconnect()
                except:
                    pass
                
            # Уничтожаем окно
            try:
                self.root.attributes('-fullscreen', False)
                self.root.destroy()
            except:
                pass
        except Exception as e:
            print(f"Ошибка при завершении: {e}")

class DriverEngine:
    def __init__(self, cfg: AdapterConfig, state: SessionState, robot,
                 log_queue: queue.Queue, state_queue: queue.Queue,
                 stop_event: threading.Event, pause_event: threading.Event,
                 gui_ref):
        self.cfg = cfg
        self.state = state
        self.robot = robot
        self.log_queue = log_queue
        self.state_queue = state_queue
        self.stop_event = stop_event
        self.pause_event = pause_event
        self.gui_ref = gui_ref
        self.sock: Optional[socket.socket] = None
        self.buffer = bytearray()

        self.logger = logging.getLogger("RoboProSCAPE.GUI_Engine")
        self.logger.setLevel(logging.INFO)
        if not self.logger.handlers:
            self.logger.addHandler(QueueHandler(log_queue))

    def _send_state_to_gui(self):
        try:
            connected = self.sock is not None and self.sock.fileno() != -1
            self.state_queue.put_nowait({
                "mode": self.state.mode,
                "bp_phase": self.state.bp_phase,
                "calib_phase": self.state.calib_phase,
                "connected": connected,
                "parts_count": self.state.count_of_parts
            })
            # ❌ УБРАЛИ root.after() — обновление GUI только через очередь!
        except queue.Full:
            pass

    def run(self):
        self.logger.info(f"🚀 Engine started. Mode: {config.ACTIVE_MODE}")

        # 🔹 Устанавливаем коллбэки пользовательской логики
        if hasattr(user_logic, 'on_pick_success'):
            self.state.on_pick_success = user_logic.on_pick_success
            self.logger.info("✅ on_pick_success callback registered")
        if hasattr(user_logic, 'on_pick_failure'):
            def safe_fail(r, c, s, p):
                self.logger.warning("⚠️ Pick failed! (UI suppressed input() call)")
                return True
            self.state.on_pick_failure = safe_fail
            self.logger.info("✅ on_pick_failure callback registered")

        try:
            while not self.stop_event.is_set():
                if self.pause_event.is_set():
                    time.sleep(0.1)
                    continue

                if self.sock is None or self.sock.fileno() == -1:
                    try:
                        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        self.sock.settimeout(self.cfg.SOCKET_TIMEOUT)
                        self.sock.connect((self.cfg.IP, self.cfg.PORT))
                        self.logger.info(f"🔌 Connected to adapter {self.cfg.IP}:{self.cfg.PORT}")

                        # 🔥 КРИТИЧЕСКИ ВАЖНО: При новом TCP-подключении сбрасываем packet_id в 0
                        self.state.last_down_packet_id = 0
                        self.state.reconnect_backoff = 1.0
                        self.state.buffer_free_slots = 5
                        self.state.executing_group_id = 0
                        self.buffer.clear()

                    except Exception as e:
                        self.logger.error(f"Adapter connection failed: {e}. Retry in {self.state.reconnect_backoff:.1f}s")
                        time.sleep(self.state.reconnect_backoff)
                        self.state.reconnect_backoff = min(self.state.reconnect_backoff * 2, self.cfg.RECONNECT_BACKOFF_MAX)
                        continue

                while not self.stop_event.is_set() and task_executor.check_robot_safety(self.robot):
                    if self.pause_event.is_set():
                        time.sleep(0.1)
                        continue

                    pose, joints = task_executor.get_robot_pose(self.robot)
                    self.state.grip_status = 1.0 if self.robot.io.digital.get_output(self.cfg.GRIPPER_CLOSED_DO) else 0.0

                    uplink = self._build_uplink(joints, pose)
                    if self.state.pending_command:
                        self.state.pending_command = None
                    if self.sock:
                        self.sock.sendall(uplink.encode("utf-8"))

                    raw_down, self.buffer = self._receive_framed()
                    parsed_downlink = self._parse_downlink(raw_down) if raw_down else None

                    if parsed_downlink is not None:
                        self.state.last_down_packet_id = parsed_downlink.packet_id

                    # Теперь работаем с задачей, если она есть
                    task = parsed_downlink
                    if task:
                        self.state.last_down_packet_id = task.packet_id
                        self.state.buffer_free_slots = max(self.state.buffer_free_slots, 1)
                        success, cycle_complete = task_executor.execute_task(self.robot, task, self.cfg)
                        self.state.executing_group_id = -task.task_group_id
                        self._handle_state_machine(task, cycle_complete)

                    self._send_state_to_gui()
                    time.sleep(self.cfg.HEARTBEAT_INTERVAL)

        except Exception as e:
            self.logger.critical(f"Engine crashed: {e}", exc_info=True)
        finally:
            self._cleanup()
            self.logger.info("🛑 Engine stopped.")

    def _cleanup(self):
        """Корректное закрытие соединения с адаптером (по аналогии с driver.py)."""
        # 🔹 Закрываем сокет — ПРОСТО close(), БЕЗ shutdown() (как в driver.py!)
        if self.sock:
            try:
                self.sock.close()
            except:
                pass
            self.sock = None

        #  Пауза на корректное завершение (как в driver.py: time.sleep(2))
        time.sleep(0.5)

        # 🔹 Останавливаем робота
        if self.robot:
            try:
                task_executor.emergency_stop(self.robot)
            except:
                pass

    def _build_uplink(self, joints, pose):
        payload = [float(self.state.last_down_packet_id), 0.0]
        payload += [1.0, 5.0, float(self.state.robot_status), float(self.state.buffer_free_slots),
                    self.state.grip_status, 0.0, float(self.state.executing_group_id)]
        payload += [2.0, float(len(joints))] + [float(j) for j in joints]
        payload += [3.0, 6.0, pose[0], pose[1], pose[2], pose[3], pose[4], pose[5]]
        cmd = self.state.pending_command if self.state.pending_command else [0.0] * 11
        cmd_safe = ([float(x) for x in cmd] + [0.0] * 11)[:11]
        payload += [4.0, 11.0] + cmd_safe
        payload = (payload + [0.0] * self.cfg.PACKET_FLOAT_COUNT)[:self.cfg.PACKET_FLOAT_COUNT]
        return f"{self.cfg.PREFIX}{self.cfg.SEPARATOR.join(f'{v:.6g}' for v in payload)}{self.cfg.SUFFIX}"

    def _receive_framed(self):
        if self.sock is None:
            return None, self.buffer
        suffix_bytes = self.cfg.SUFFIX.encode()
        self.sock.settimeout(self.cfg.READ_TIMEOUT)
        while suffix_bytes not in self.buffer:
            if self.stop_event.is_set():
                return None, self.buffer
            try:
                chunk = self.sock.recv(1024)
                if not chunk:
                    self.logger.info(" Adapter closed connection gracefully")
                    return None, self.buffer
                self.buffer.extend(chunk)
                if len(self.buffer) > 4096:
                    self.buffer[:] = self.buffer[-1024:]
            except socket.timeout:
                return None, self.buffer
            except ConnectionResetError:
                self.logger.info("🔄 Adapter reset connection")
                return None, self.buffer
            except OSError as e:
                if self.stop_event.is_set():
                    return None, self.buffer
                self.logger.warning(f"️ Socket error: {e}")
                return None, self.buffer

        end_idx = self.buffer.find(suffix_bytes) + len(suffix_bytes)
        packet = self.buffer[:end_idx].decode("utf-8", errors="ignore")
        del self.buffer[:end_idx]
        return packet, self.buffer

    def _parse_downlink(self, raw):
        if not raw:
            return None
        clean = raw.replace(self.cfg.PREFIX, " ").replace(self.cfg.SUFFIX, " ").strip()
        try:
            data = [float(x) for x in clean.split(self.cfg.SEPARATOR) if x.strip()]
        except ValueError:
            return None
        if len(data) < 9:
            return None

        packet_id = int(round(data[0]))
        task_group_id = int(round(data[7]))
        task_type = int(round(data[8]))
        remain_tasks = int(round(data[6]))

        if task_group_id == 0 or task_type == 0:
            return Task(packet_id=packet_id, task_group_id=0, task_type=0, payload=[], remain_tasks=remain_tasks)

        payload_start = 10 if task_type in (16, 17, 18) else 9
        return Task(
            packet_id=packet_id,
            task_group_id=task_group_id,
            task_type=task_type,
            payload=data[payload_start:],
            remain_tasks=remain_tasks
        )

    def _handle_state_machine(self, task: Task, cycle_complete: bool):
        if self.state.mode == ActiveMode.CALIBRATING:
            if self.state.calib_phase == CalibPhase.RESET:
                self.state.pending_command = [-1.0] + [0.0] * 10
                self.state.calib_phase = CalibPhase.SEND_10
                self.logger.info(" Calib Phase 0: Sent reset (-1)")
            elif self.state.calib_phase == CalibPhase.SEND_10:
                self.state.pending_command = [10.0] + [0.0] * 10
                self.state.calib_phase = CalibPhase.PROCESS
                self.logger.info("🔄 Calib Phase 1: Sent calibrate command (10)")
            elif self.state.calib_phase == CalibPhase.PROCESS:
                if cycle_complete:
                    self.state.pending_command = [10.0] + [0.0] * 10
                    self.logger.info("✅ Calib step complete. Sent confirmation (10)")
                if task.task_type == 15:
                    self.state.calib_phase = CalibPhase.DONE
                    self.state.mode = ActiveMode.IDLE
                    self.logger.info("✅ Calibration complete. Ready.")

        elif self.state.mode == ActiveMode.PICKING:
            if self.state.bp_phase == BpPhase.RESET:
                self.state.pending_command = [-1.0] + [0.0] * 10
                self.state.bp_phase = BpPhase.SEND_20
                self.logger.info("🔄 Phase 0: Sent reset (-1)")
            elif self.state.bp_phase == BpPhase.SEND_20:
                self.state.pending_command = [20.0, 1.0, 1.0, 9.0] + [0.0] * 7
                self.state.bp_phase = BpPhase.WAIT_15
                self.logger.info("🔄 Phase 1: Sent sleep (20)")
            elif self.state.bp_phase == BpPhase.WAIT_15:
                if task.task_type == 15 or self.state.first_iteration:
                    self.state.first_iteration = False
                    self.state.pending_command = [
                        30.0, float(config.ACTIVE_PRODUCT.product_id),
                        float(int(config.ACTIVE_PRODUCT.rescan)),
                        float(config.ACTIVE_PRODUCT.config),
                        float(int(config.ACTIVE_PRODUCT.path_planning)),
                        float(int(config.ACTIVE_PRODUCT.dump_back_bin)),
                        float(int(config.ACTIVE_PRODUCT.start_highest_zone))
                    ] + [0.0] * 4
                    self.state.bp_phase = BpPhase.PROCESS
                    self.logger.info("✅ Phase 2: Received 15. Sent pick (30)")
            elif self.state.bp_phase == BpPhase.PROCESS:
                if task.task_type == 15:
                    task_payload = task.payload
                    self.logger.info(f" FinalTask received. Payload: {task_payload}")

                    if self.state.on_pick_success or self.state.on_pick_failure:
                        try:
                            if task_payload and len(task_payload) >= 3 and task_payload[1] == 0.0 and task_payload[2] == 1.0:
                                self.logger.info("✅ Pick successful. Calling on_pick_success()...")
                                if self.state.on_pick_success:
                                    self.state.on_pick_success(self.robot, self.cfg, self.state, task_payload)
                            else:
                                self.logger.info("⚠️ Pick failed. Calling on_pick_failure()...")
                                if self.state.on_pick_failure:
                                    self.state.on_pick_failure(self.robot, self.cfg, self.state, task_payload)
                        except Exception as e:
                            self.logger.error(f"❌ Error in user_logic: {e}", exc_info=True)

                    self.state.bp_phase = BpPhase.WAIT_15
                    self.state.first_iteration = True
                    self.logger.info("✅ Phase 3: Cycle complete. Ready for next trigger.")


if __name__ == "__main__":
    root = tk.Tk()
    app = ScapeGUI(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    
    try:
        root.mainloop()
    except KeyboardInterrupt:
        # Ctrl+C в терминале — корректное завершение
        print("\n⚠️ Прервано пользователем (Ctrl+C). Завершение работы...")
        app.on_closing()
    except Exception as e:
        # Другие ошибки — тоже пытаемся корректно завершиться
        print(f"\n❌ Непредвиденная ошибка: {e}")
        try:
            app.on_closing()
        except:
            pass

