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
        
        self._build_ui()
        self._start_pose_update_loop()
    
    def set_robot(self, robot):
        """Устанавливает ссылку на робот (вызывается после подключения)."""
        self.robot = robot
        self._log(f"🔗 JoggingPanel: robot connected")
    
    def _build_ui(self):
        main_frame = ttk.Frame(self)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # === ЛЕВАЯ КОЛОНКА: Сочленения ===
        joint_frame = ttk.LabelFrame(main_frame, text="Угловое перемещение", padding=10)
        joint_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        
        ttk.Label(joint_frame, text="Положение сочленения", 
                  font=("Segoe UI", 9, "bold")).pack(pady=(0, 10))
        
        joint_names = [
            "Основание (J1)", "Плечо (J2)", "Локоть (J3)",
            "Запястье 1 (J4)", "Запястье 2 (J5)", "Запястье 3 (J6)"
        ]
        
        for i, name in enumerate(joint_names):
            row = ttk.Frame(joint_frame)
            row.pack(fill=tk.X, pady=2)

            btn_minus = ttk.Button(row, text="−", width=3)
            btn_minus.pack(side=tk.LEFT, padx=(0, 5))
            btn_minus.bind("<ButtonPress-1>", lambda e, idx=i: self._start_joint_jog(idx, "-"))
            btn_minus.bind("<ButtonRelease-1>", lambda e, idx=i: self._stop_joint_jog(idx))
            btn_minus.bind("<Leave>", lambda e, idx=i: self._stop_joint_jog(idx))

            lbl = ttk.Label(row, text=name, width=18, anchor=tk.CENTER)
            lbl.pack(side=tk.LEFT, expand=True)

            btn_plus = ttk.Button(row, text="+", width=3)
            btn_plus.pack(side=tk.RIGHT, padx=(5, 0))
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
        trans_frame = ttk.LabelFrame(motion_frame, text="Перемещение", padding=10)
        trans_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))
        self._build_3x3_grid(trans_frame, "X", "Y", "Z", 
                             self._start_linear_jog, self._stop_linear_jog)
        
        # Вращение (сетка 3x3)
        rot_frame = ttk.LabelFrame(motion_frame, text="Вращение", padding=10)
        rot_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        self._build_3x3_grid(rot_frame, "Rx", "Ry", "Rz", 
                             self._start_linear_jog, self._stop_linear_jog)
        
        # Нижняя часть: Управление
        control_frame = ttk.Frame(right_frame)
        control_frame.pack(fill=tk.X)
        
        # Свободный привод
        self.btn_free_drive = ttk.Button(control_frame, text="Свободный привод", 
                                         command=self._toggle_free_drive)
        self.btn_free_drive.pack(fill=tk.X, pady=2)

        # Схват (зажать/отпустить)
        grip_frame = ttk.LabelFrame(control_frame, text="Схват", padding=5)
        grip_frame.pack(fill=tk.X, pady=5)

        self.btn_grip_close = ttk.Button(grip_frame, text=" Зажать схват",
                                         command=self._grip_close)
        self.btn_grip_close.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))

        self.btn_grip_open = ttk.Button(grip_frame, text=" Отпустить схват",
                                        command=self._grip_open)
        self.btn_grip_open.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(4, 0))
        
        # Слайдер скорости
        speed_frame = ttk.Frame(control_frame)
        speed_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(speed_frame, text="Скорость:").pack(side=tk.LEFT)
        self.speed_slider = ttk.Scale(speed_frame, from_=0, to=100, variable=self.speed_var, 
                               orient=tk.HORIZONTAL, command=self._on_speed_change)
        self.speed_slider.config(length=200)
        self.speed_slider.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        self.speed_lbl = ttk.Label(speed_frame, text="70%", width=10)
        self.speed_lbl.pack(side=tk.RIGHT)
        
        # Переключатели
        options_frame = ttk.Frame(control_frame)
        options_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(options_frame, text="Система координат:").grid(row=0, column=0, sticky=tk.W)
        coord_combo = ttk.Combobox(options_frame, textvariable=self.coord_system_var, 
                                    values=["base", "tcp"], state="readonly", width=10)
        coord_combo.grid(row=0, column=1, padx=5)
        coord_combo.bind("<<ComboboxSelected>>", self._on_coord_system_change)
        
        ttk.Label(options_frame, text="Единицы:").grid(row=1, column=0, sticky=tk.W)
        units_combo = ttk.Combobox(options_frame, textvariable=self.units_var,
                                    values=["deg", "rad"], state="readonly", width=10)
        units_combo.grid(row=1, column=1, padx=5, pady=2)
        
        # Кнопка подтверждения точки
        self.btn_teach = ttk.Button(control_frame, text=" Захватить позу", 
                                     command=self._on_teach_point)
        self.btn_teach.pack(fill=tk.X, pady=2)
        
        # Отображение текущей позы
        self.pose_lbl = ttk.Label(control_frame, text="TCP: —", font=("Consolas", 9))
        self.pose_lbl.pack(fill=tk.X, pady=2)
        
        self.joints_lbl = ttk.Label(control_frame, text="Joints: —", font=("Consolas", 9))
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
    
        # Настраиваем сетку 3 колонки, 3 строки
        for i in range(3):
            grid_frame.columnconfigure(i, weight=1)
            grid_frame.rowconfigure(i, weight=1)
    
        # Ряд 0: [axis_extra+, axis_v+, empty]
        btn = ttk.Button(grid_frame, text=f"{axis_extra}+", width=3)
        btn.grid(row=0, column=0, padx=2, pady=2, sticky="nsew")
        btn.bind("<ButtonPress-1>", lambda e: start_cb(axis_extra, "+"))
        btn.bind("<ButtonRelease-1>", lambda e: stop_cb(axis_extra))
        btn.bind("<Leave>", lambda e: stop_cb(axis_extra))
    
        btn = ttk.Button(grid_frame, text=f"{axis_v}+", width=3)
        btn.grid(row=0, column=1, padx=2, pady=2, sticky="nsew")
        btn.bind("<ButtonPress-1>", lambda e: start_cb(axis_v, "+"))
        btn.bind("<ButtonRelease-1>", lambda e: stop_cb(axis_v))
        btn.bind("<Leave>", lambda e: stop_cb(axis_v))
    
        # Ряд 1: [axis_h-, empty, axis_h+]
        btn = ttk.Button(grid_frame, text=f"{axis_h}-", width=3)
        btn.grid(row=1, column=0, padx=2, pady=2, sticky="nsew")
        btn.bind("<ButtonPress-1>", lambda e: start_cb(axis_h, "-"))
        btn.bind("<ButtonRelease-1>", lambda e: stop_cb(axis_h))
        btn.bind("<Leave>", lambda e: stop_cb(axis_h))
    
        btn = ttk.Button(grid_frame, text=f"{axis_h}+", width=3)
        btn.grid(row=1, column=2, padx=2, pady=2, sticky="nsew")
        btn.bind("<ButtonPress-1>", lambda e: start_cb(axis_h, "+"))
        btn.bind("<ButtonRelease-1>", lambda e: stop_cb(axis_h))
        btn.bind("<Leave>", lambda e: stop_cb(axis_h))
    
        # Ряд 2: [axis_extra-, axis_v-, empty]
        btn = ttk.Button(grid_frame, text=f"{axis_extra}-", width=3)
        btn.grid(row=2, column=0, padx=2, pady=2, sticky="nsew")
        btn.bind("<ButtonPress-1>", lambda e: start_cb(axis_extra, "-"))
        btn.bind("<ButtonRelease-1>", lambda e: stop_cb(axis_extra))
        btn.bind("<Leave>", lambda e: stop_cb(axis_extra))
    
        btn = ttk.Button(grid_frame, text=f"{axis_v}-", width=3)
        btn.grid(row=2, column=1, padx=2, pady=2, sticky="nsew")
        btn.bind("<ButtonPress-1>", lambda e: start_cb(axis_v, "-"))
        btn.bind("<ButtonRelease-1>", lambda e: stop_cb(axis_v))
        btn.bind("<Leave>", lambda e: stop_cb(axis_v))
    
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
            self.btn_free_drive.config(text="Свободный привод")
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
            self.btn_free_drive.config(text="🔒 Свободный привод (активен)")
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
                self.btn_free_drive.config(text="Свободный привод")
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