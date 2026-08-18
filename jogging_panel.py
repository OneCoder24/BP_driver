"""
JoggingPanel — виджет ручного управления роботом (джойстик).
Встраивается в driver_gui.py как часть правой панели.
"""
import tkinter as tk
from tkinter import ttk
import logging
import threading
from typing import Optional, Callable

from config import set_gripper_state

logger = logging.getLogger("RoboProSCAPE.JoggingPanel")


class SpeedSlider(tk.Canvas):
    """Кастомный «толстый» слайдер скорости на Canvas.

    Заменяет тонкий ttk.Scale: широкий закруглённый трек с заливкой
    текущего значения и крупным бегунком. Значение синхронизируется
    с переменной (DoubleVar) и возвращается через command(value).
    """

    TRACK_MARGIN = 10          # отступ трека от краёв, px
    TRACK_HEIGHT = 22          # высота трека (толстый), px
    THUMB_PAD = 8              # запас под бегунок

    def __init__(self, master, variable, command=None, height=48, **kw):
        kw.setdefault("bd", 0)
        kw.setdefault("highlightthickness", 0)
        super().__init__(master, height=height, **kw)
        self._var = variable
        self._command = command
        self._value = float(variable.get())
        self._apply_frame_background()
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self._var.trace_add("write", self._on_var_changed)
        self._draw()

    def _apply_frame_background(self):
        try:
            bg = ttk.Style().lookup("TFrame", "background") or "#f0f0f0"
        except Exception:
            bg = "#f0f0f0"
        self.configure(bg=bg)

    def _on_var_changed(self, *args):
        self._value = float(self._var.get())
        self._draw()

    def _on_press(self, event):
        self._set_value(self._event_to_value(event))

    def _on_drag(self, event):
        self._set_value(self._event_to_value(event))

    def _event_to_value(self, event):
        w = max(self.winfo_width(), 2 * self.TRACK_MARGIN + 10)
        x0 = self.TRACK_MARGIN
        x1 = w - self.TRACK_MARGIN
        v = (event.x - x0) / max((x1 - x0), 1) * 100.0
        return max(0.0, min(100.0, v))

    def _set_value(self, value):
        value = round(value, 1)
        self._var.set(value)             # триггерит перерисовку
        if self._command:
            self._command(f"{value}")

    def _round_rect(self, x1, y1, x2, y2, r, **kw):
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
               x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
               x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return self.create_polygon(pts, smooth=True, **kw)

    def _draw(self):
        self.delete("all")
        w = max(self.winfo_width(), 2 * self.TRACK_MARGIN + 10)
        h = max(self.winfo_height(), 24)
        x0 = self.TRACK_MARGIN
        x1 = w - self.TRACK_MARGIN
        y = h // 2
        t = self.TRACK_HEIGHT // 2
        v = max(0.0, min(100.0, self._value))
        fx = x0 + (x1 - x0) * v / 100.0

        # Трек
        self._round_rect(x0, y - t, x1, y + t, 12,
                         fill="#D4DCE8", outline="#AEBACB", width=1)
        # Заливка текущего значения
        if fx > x0 + 4:
            self._round_rect(x0, y - t, fx, y + t, 12,
                             fill="#4C8DF6", outline="")
        # Метки 0 / 100
        self.create_text(x0, h - 5, text="0", anchor="sw",
                         fill="#8A97AC", font=("Segoe UI", 7))
        self.create_text(x1, h - 5, text="100%", anchor="se",
                         fill="#8A97AC", font=("Segoe UI", 7))

        # Бегунок
        r = t + self.THUMB_PAD
        fx = max(x0 + r, min(x1 - r, fx))
        self.create_oval(fx - r, y - r, fx + r, y + r,
                         fill="#FFFFFF", outline="#2563EB", width=2)
        self.create_oval(fx - 4, y - 4, fx + 4, y + 4,
                         fill="#4C8DF6", outline="")


class JoggingPanel(ttk.Frame):
    JOG_INTERVAL_MS = 10  # 100 Гц
    
    def __init__(self, parent, robot=None, log_queue=None, 
                 on_teach_point: Optional[Callable] = None, *args, **kwargs):
        super().__init__(parent, *args, **kwargs)
        self.robot = robot
        self.log_queue = log_queue
        self.on_teach_point = on_teach_point
        
        self.active_jog = {}
        self.jog_after_id = None
        self.free_drive_active = False
        self.free_drive_after_id = None
        
        self.speed_var = tk.DoubleVar(value=70)
        self.coord_system_var = tk.StringVar(value="base")
        self.units_var = tk.StringVar(value="deg")
        
        self._setup_styles()
        self._build_ui()
        self._start_pose_update_loop()
    
    def set_robot(self, robot):
        """Устанавливает ссылку на робот (вызывается после подключения)."""
        self.robot = robot
        self._log(f"🔗 JoggingPanel: robot connected")
    
    def _setup_styles(self):
        """Настраивает единый набор стилей для стандартизированного вида."""
        style = ttk.Style()
        self._style = style

        font_bold = ("Segoe UI", 10, "bold")

        # --- Базовые кнопки джоггинга ---
        style.configure("Jog.TButton",
                        font=font_bold, padding=(14, 9),
                        background="#E8EDF5", foreground="#1F2A44",
                        bordercolor="#AFBCCE", lightcolor="#F7F9FC",
                        darkcolor="#C6D2E2", focuscolor="#3B82F6", relief="flat")
        style.map("Jog.TButton",
                  background=[("disabled", "#E6EAF0"), ("pressed", "#C6D2E2"),
                              ("active", "#D9E3F0")],
                  foreground=[("disabled", "#9AA5B5")],
                  bordercolor=[("disabled", "#C9D2DE"), ("active", "#93A5C0")])

        # Минус — тёплый красноватый оттенок
        style.configure("JogMinus.TButton",
                        font=font_bold, padding=(14, 9),
                        background="#FBE4DE", foreground="#8E3B24",
                        bordercolor="#E8B0A0", lightcolor="#FDF2EF",
                        darkcolor="#EBC0B2", focuscolor="#F97316", relief="flat")
        style.map("JogMinus.TButton",
                  background=[("pressed", "#F3C6B8"), ("active", "#F8D6CC")],
                  bordercolor=[("active", "#E19A87")])

        # Плюс — холодный зелёный оттенок
        style.configure("JogPlus.TButton",
                        font=font_bold, padding=(14, 9),
                        background="#DFF2E3", foreground="#1E6B35",
                        bordercolor="#A7D6B3", lightcolor="#F2FBF4",
                        darkcolor="#B2DCBD", focuscolor="#22C55E", relief="flat")
        style.map("JogPlus.TButton",
                  background=[("pressed", "#C2E5CB"), ("active", "#CDEBd5")],
                  bordercolor=[("active", "#8FC9A0")])

        # Свободный привод — янтарный (вкл/выкл)
        style.configure("Free.TButton",
                        font=font_bold, padding=(14, 9),
                        background="#FDF0C8", foreground="#8A5A00",
                        bordercolor="#E7CD8C", lightcolor="#FFFBE8",
                        darkcolor="#E8D6A4", focuscolor="#F59E0B", relief="flat")
        style.map("Free.TButton",
                  background=[("pressed", "#F3E2A8"), ("active", "#FBEBC0")])

        style.configure("FreeActive.TButton",
                        font=font_bold, padding=(14, 9),
                        background="#F59E0B", foreground="#FFFFFF",
                        bordercolor="#C77E05", lightcolor="#FCCA6B",
                        darkcolor="#C07704", relief="flat")
        style.map("FreeActive.TButton",
                  background=[("pressed", "#D9850A"), ("active", "#F7A92E")])

        # Схват: Зажать — насыщенный зелёный, Отпустить — оранжевый
        style.configure("GripClose.TButton",
                        font=font_bold, padding=(14, 10),
                        background="#1FA34B", foreground="#FFFFFF",
                        bordercolor="#157C38", lightcolor="#5BCB7F",
                        darkcolor="#157C38", relief="flat")
        style.map("GripClose.TButton",
                  background=[("pressed", "#16843C"), ("active", "#2BB55A")])

        style.configure("GripOpen.TButton",
                        font=font_bold, padding=(14, 10),
                        background="#ED7B2E", foreground="#FFFFFF",
                        bordercolor="#C25F18", lightcolor="#F5A367",
                        darkcolor="#B85917", relief="flat")
        style.map("GripOpen.TButton",
                  background=[("pressed", "#D9681F"), ("active", "#F08A42")])

        # Захват позы — фиолетовый
        style.configure("Teach.TButton",
                        font=font_bold, padding=(14, 9),
                        background="#7C3AED", foreground="#FFFFFF",
                        bordercolor="#6329C5", lightcolor="#A78BFA",
                        darkcolor="#5E26B8", relief="flat")
        style.map("Teach.TButton",
                  background=[("pressed", "#6A2ED0"), ("active", "#8B5CF6")])

        # LabelFrame — единая рамка с подписью
        style.configure("Jog.TLabelframe",
                        bordercolor="#AFBCCE", relief="flat")
        style.configure("Jog.TLabelframe.Label",
                        font=font_bold, foreground="#33415C")

        # Combobox
        style.configure("Jog.TCombobox",
                        font=("Segoe UI", 10), fieldbackground="#FFFFFF",
                        background="#E8EDF5", foreground="#1F2A44",
                        arrowcolor="#33415C", bordercolor="#AFBCCE",
                        lightcolor="#FFFFFF", darkcolor="#B9C4D4", padding=(6, 5))
        style.map("Jog.TCombobox",
                  fieldbackground=[("readonly", "#FFFFFF")],
                  bordercolor=[("focus", "#3B82F6")])
    
    def _build_ui(self):
        main_frame = ttk.Frame(self)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # === ЛЕВАЯ КОЛОНКА: Сочленения ===
        joint_frame = ttk.LabelFrame(main_frame, text="Угловое перемещение",
                                     padding=10, style="Jog.TLabelframe")
        joint_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        
        ttk.Label(joint_frame, text="Положение сочленения",
                  font=("Segoe UI", 9, "bold"),
                  foreground="#33415C").pack(pady=(0, 10))
        
        joint_names = [
            "Основание (J1)", "Плечо (J2)", "Локоть (J3)",
            "Запястье 1 (J4)", "Запястье 2 (J5)", "Запястье 3 (J6)"
        ]
        
        for i, name in enumerate(joint_names):
            row = ttk.Frame(joint_frame)
            row.pack(fill=tk.X, pady=3)

            btn_minus = ttk.Button(row, text="−", width=4, style="JogMinus.TButton")
            btn_minus.pack(side=tk.LEFT, padx=(0, 5), ipady=1)
            btn_minus.bind("<ButtonPress-1>", lambda e, idx=i: self._start_joint_jog(idx, "-"))
            btn_minus.bind("<ButtonRelease-1>", lambda e, idx=i: self._stop_joint_jog(idx))
            btn_minus.bind("<Leave>", lambda e, idx=i: self._stop_joint_jog(idx))

            lbl = ttk.Label(row, text=name, width=18, anchor=tk.CENTER,
                            font=("Segoe UI", 9), foreground="#243047")
            lbl.pack(side=tk.LEFT, expand=True)

            btn_plus = ttk.Button(row, text="+", width=4, style="JogPlus.TButton")
            btn_plus.pack(side=tk.RIGHT, padx=(5, 0), ipady=1)
            btn_plus.bind("<ButtonPress-1>", lambda e, idx=i: self._start_joint_jog(idx, "+"))
            btn_plus.bind("<ButtonRelease-1>", lambda e, idx=i: self._stop_joint_jog(idx))
            btn_plus.bind("<Leave>", lambda e, idx=i: self._stop_joint_jog(idx))
        
        # === ПРАВАЯ КОЛОНКА: Перемещение + Вращение + Управление ===
        right_frame = ttk.Frame(main_frame)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        
        # Верхняя часть: Перемещение и Вращение (сетка 3x3)
        motion_frame = ttk.Frame(right_frame)
        motion_frame.pack(fill=tk.X, pady=(0, 10))
        
        # Перемещение (сетка 3x3)
        trans_frame = ttk.LabelFrame(motion_frame, text="Перемещение",
                                     padding=10, style="Jog.TLabelframe")
        trans_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))
        self._build_3x3_grid(trans_frame, "X", "Y", "Z", 
                             self._start_linear_jog, self._stop_linear_jog)
        
        # Вращение (сетка 3x3)
        rot_frame = ttk.LabelFrame(motion_frame, text="Вращение",
                                   padding=10, style="Jog.TLabelframe")
        rot_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        self._build_3x3_grid(rot_frame, "Rx", "Ry", "Rz", 
                             self._start_linear_jog, self._stop_linear_jog)
        
        # Нижняя часть: Управление
        control_frame = ttk.Frame(right_frame)
        control_frame.pack(fill=tk.X)
        
        # Свободный привод
        self.btn_free_drive = ttk.Button(control_frame, text="Свободный привод",
                                         command=self._toggle_free_drive,
                                         style="Free.TButton")
        self.btn_free_drive.pack(fill=tk.X, pady=2)

        # Схват (зажать/отпустить)
        grip_frame = ttk.LabelFrame(control_frame, text="Схват", padding=5,
                                    style="Jog.TLabelframe")
        grip_frame.pack(fill=tk.X, pady=5)

        self.btn_grip_close = ttk.Button(grip_frame, text="  Зажать схват",
                                         command=self._grip_close,
                                         style="GripClose.TButton")
        self.btn_grip_close.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))

        self.btn_grip_open = ttk.Button(grip_frame, text="  Отпустить схват",
                                        command=self._grip_open,
                                        style="GripOpen.TButton")
        self.btn_grip_open.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(4, 0))
        
        # Слайдер скорости
        speed_frame = ttk.Frame(control_frame)
        speed_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(speed_frame, text="Скорость:", font=("Segoe UI", 9, "bold"),
                  foreground="#33415C").pack(side=tk.LEFT, padx=(0, 2))
        self.speed_slider = SpeedSlider(speed_frame, variable=self.speed_var,
                                        command=self._on_speed_change, height=48)
        self.speed_slider.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        self.speed_lbl = ttk.Label(speed_frame, text="70%", width=10,
                                   font=("Segoe UI", 9, "bold"), foreground="#33415C")
        self.speed_lbl.pack(side=tk.RIGHT)
        
        # Переключатели
        options_frame = ttk.Frame(control_frame)
        options_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(options_frame, text="Система координат:",
                  foreground="#33415C").grid(row=0, column=0, sticky=tk.W)
        coord_combo = ttk.Combobox(options_frame, textvariable=self.coord_system_var,
                                    values=["base", "tcp"], state="readonly", width=12,
                                    style="Jog.TCombobox")
        coord_combo.grid(row=0, column=1, padx=6, pady=1)
        coord_combo.bind("<<ComboboxSelected>>", self._on_coord_system_change)
        
        ttk.Label(options_frame, text="Единицы:",
                  foreground="#33415C").grid(row=1, column=0, sticky=tk.W)
        units_combo = ttk.Combobox(options_frame, textvariable=self.units_var,
                                    values=["deg", "rad"], state="readonly", width=12,
                                    style="Jog.TCombobox")
        units_combo.grid(row=1, column=1, padx=6, pady=2)
        
        # Кнопка подтверждения точки
        self.btn_teach = ttk.Button(control_frame, text="  Захватить позу",
                                     command=self._on_teach_point, style="Teach.TButton")
        self.btn_teach.pack(fill=tk.X, pady=2, ipady=1)
        
        # Отображение текущей позы
        self.pose_lbl = ttk.Label(control_frame, text="TCP: —", font=("Consolas", 9),
                                  foreground="#243047")
        self.pose_lbl.pack(fill=tk.X, pady=2)
        
        self.joints_lbl = ttk.Label(control_frame, text="Joints: —", font=("Consolas", 9),
                                    foreground="#243047")
        self.joints_lbl.pack(fill=tk.X, pady=2)
    
    def _build_3x3_grid(self, parent, axis_h, axis_v, axis_extra, start_cb, stop_cb):
        """
        Строит сетку 3x3 для кнопок перемещения/вращения.
        Матрица (для перемещения X/Y/Z):
            [Z+, Y+, empty]
            [X-, empty, X+]
            [Z-, Y-, empty]
        Матрица (для вращения Rx/Ry/Rz):
            [Rz+, Ry+, empty]
            [Rx-, empty, Rx+]
            [Rz-, Ry-, empty]
        """
        grid_frame = ttk.Frame(parent)
        grid_frame.pack(fill=tk.BOTH, expand=True)
    
        # Настраиваем сетку 3 колонки, 3 строки — ячейки равных размеров
        for i in range(3):
            grid_frame.columnconfigure(i, weight=1, uniform="jog3")
            grid_frame.rowconfigure(i, weight=1, uniform="jog3")
    
        def make_btn(text, row, col, axis, direction):
            """Создаёт широкую цветную кнопку в ячейке сетки."""
            style = "JogPlus.TButton" if direction == "+" else "JogMinus.TButton"
            btn = ttk.Button(grid_frame, text=text, style=style)
            btn.grid(row=row, column=col, padx=3, pady=3, sticky="nsew")
            btn.bind("<ButtonPress-1>", lambda e, a=axis, d=direction: start_cb(a, d))
            btn.bind("<ButtonRelease-1>", lambda e, a=axis: stop_cb(a))
            btn.bind("<Leave>", lambda e, a=axis: stop_cb(a))
    
        # Ряд 0: [axis_extra+, axis_v+, empty]
        make_btn(f"{axis_extra}+", 0, 0, axis_extra, "+")
        make_btn(f"{axis_v}+", 0, 1, axis_v, "+")
    
        # Ряд 1: [axis_h-, empty, axis_h+]
        make_btn(f"{axis_h}-", 1, 0, axis_h, "-")
        make_btn(f"{axis_h}+", 1, 2, axis_h, "+")
    
        # Ряд 2: [axis_extra-, axis_v-, empty]
        make_btn(f"{axis_extra}-", 2, 0, axis_extra, "-")
        make_btn(f"{axis_v}-", 2, 1, axis_v, "-")
    
    # ==========================================
    # ЛОГИКА ДЖОГГИНГА
    # ==========================================
    def _start_linear_jog(self, axis, direction):
        if not self.robot:
            self._log("⚠️ Robot not connected!")
            return
        key = f"linear_{axis}"
        self.active_jog[key] = direction
        self._log(f" Jog {axis}{direction}")
        if self.jog_after_id is None:
            self._jog_loop()
    
    def _stop_linear_jog(self, axis):
        key = f"linear_{axis}"
        self.active_jog.pop(key, None)
    
    def _start_joint_jog(self, joint_index, direction):
        if not self.robot:
            self._log("⚠️ Robot not connected!")
            return
        key = f"joint_{joint_index}"
        self.active_jog[key] = direction
        self._log(f"🎮 Jog J{joint_index+1}{direction}")
        if self.jog_after_id is None:
            self._jog_loop()
    
    def _stop_joint_jog(self, joint_index):
        key = f"joint_{joint_index}"
        self.active_jog.pop(key, None)
    
    def _jog_loop(self):
        """Цикл джоггинга — вызывается каждые 10 мс (100 Гц)."""
        if not self.active_jog and not self.free_drive_active:
            # 🔹 Явная остановка при завершении джоггинга
            if self.robot:
                try:
                    self.robot.motion.mode.set("hold")
                except:
                    pass
            self.jog_after_id = None
            return

        speed = self.speed_var.get() / 100.0
        if self.robot:
            try:
                # Устанавливаем скорость
                self.robot.motion.scale_setup.set(velocity=speed, acceleration=speed)

                # Выполняем активные джоги
                for key, direction in list(self.active_jog.items()):
                    if key.startswith("linear_"):
                        axis = key[7:]  # "X", "Y", "Z", "Rx", "Ry", "Rz"
                        self.robot.motion.linear.jog_once(axis, direction)
                    elif key.startswith("joint_"):
                        joint_idx = int(key[6:])
                        self.robot.motion.joint.jog_once(joint_idx, direction)
            except Exception as e:
                logger.error(f"Jog error: {e}")
                self.active_jog.clear()

        self.jog_after_id = self.after(self.JOG_INTERVAL_MS, self._jog_loop)
    
    # ==========================================
    # СВОБОДНЫЙ ПРИВОД
    # ==========================================
    def _toggle_free_drive(self):
        if self.free_drive_active:
            # Выключаем
            self.free_drive_active = False
            self.btn_free_drive.config(text="Свободный привод", style="Free.TButton")
            if self.robot:
                try:
                    self.robot.motion.free_drive(False)
                except:
                    pass
            if self.free_drive_after_id:
                self.after_cancel(self.free_drive_after_id)
                self.free_drive_after_id = None
            self._log("🔓 Свободный привод выключен")
        else:
            # Включаем
            self.free_drive_active = True
            self.btn_free_drive.config(text="🔒 Свободный привод (активен)", style="FreeActive.TButton")
            self._free_drive_loop()
            self._log(" Свободный привод включён")
    
    def _free_drive_loop(self):
        if not self.free_drive_active:
            return
        if self.robot:
            try:
                self.robot.motion.free_drive(True)
            except Exception as e:
                logger.error(f"Free drive error: {e}")
                self.free_drive_active = False
                self.btn_free_drive.config(text="Свободный привод", style="Free.TButton")
                return
        self.free_drive_after_id = self.after(self.JOG_INTERVAL_MS, self._free_drive_loop)

    # ==========================================
    # СХВАТ
    # ==========================================
    def _grip_close(self):
        """Зажать схват (set_gripper_state(robot, True))."""
        self._grip(True)

    def _grip_open(self):
        """Отпустить схват (set_gripper_state(robot, False))."""
        self._grip(False)

    def _grip(self, close: bool):
        if not self.robot:
            self._log("⚠️ Робот не подключён — схват недоступен.")
            return
        # Выполняем в отдельном потоке, чтобы не блокировать GUI на GRIPPER_WAIT_SEC
        threading.Thread(target=self._grip_worker, args=(close,), daemon=True).start()

    def _grip_worker(self, close: bool):
        try:
            set_gripper_state(self.robot, close)
            self._log("🤏 Схват " + ("зажат" if close else "отпущен"))
        except Exception as e:
            logger.error(f"Gripper error: {e}")
            self._log(f"❌ Ошибка схвата: {e}")
    
    # ==========================================
    # НАСТРОЙКИ
    # ==========================================
    def _on_speed_change(self, value):
        speed = float(value) / 100.0
        self.speed_lbl.config(text=f"{int(float(value))}%")
        if self.robot:
            try:
                self.robot.motion.scale_setup.set(velocity=speed, acceleration=speed)
            except:
                pass
    
    def _on_coord_system_change(self, event=None):
        if self.robot:
            try:
                cs = self.coord_system_var.get()
                self.robot.motion.linear.set_jog_param_in_tcp(cs)
                self._log(f"🌐 Система координат: {cs.upper()}")
            except Exception as e:
                logger.error(f"Coord system error: {e}")
    
    # ==========================================
    # ЗАХВАТ ПОЗЫ
    # ==========================================
    def _on_teach_point(self):
        if not self.robot or not self.on_teach_point:
            return
        try:
            pose = self.robot.motion.linear.get_actual_position(orientation_units="deg")
            joints = self.robot.motion.joint.get_actual_position(units="deg")
            self.on_teach_point(pose, joints)
            self._log(f"📍 Pose captured: X={pose[0]*1000:.1f} Y={pose[1]*1000:.1f} Z={pose[2]*1000:.1f}")
        except Exception as e:
            logger.error(f"Teach point error: {e}")
    
    # ==========================================
    # ОТОБРАЖЕНИЕ ПОЗЫ
    # ==========================================
    def _start_pose_update_loop(self):
        """Обновляет отображение текущей позы каждые 200 мс."""
        self._update_pose_display()
        self.after(200, self._start_pose_update_loop)
    
    def _update_pose_display(self):
        if self.robot:
            try:
                pose = self.robot.motion.linear.get_actual_position(orientation_units="deg")
                joints = self.robot.motion.joint.get_actual_position(units="deg")
                self.pose_lbl.config(
                    text=f"TCP: X={pose[0]*1000:.1f}  Y={pose[1]*1000:.1f}  Z={pose[2]*1000:.1f} mm"
                )
                self.joints_lbl.config(
                    text=f"Joints: {', '.join(f'{j:.1f}' for j in joints)}°"
                )
            except:
                pass
    
    def _log(self, msg):
        if self.log_queue:
            self.log_queue.put_nowait(msg)