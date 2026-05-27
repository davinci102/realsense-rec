#!/usr/bin/env python3
"""
RealSense D415 Dataset Recorder — GUI Mini-App (v4 FIXED)
- Мониторинг CPU, RAM, скорости записи на диск, свободного места
- Исправлена ошибка с psutil.disk_io_counters (namedtuple immutability)
- тест
"""
import tkinter as tk
from tkinter import ttk, messagebox
import threading
import queue
import time
import json
import shutil
import pyrealsense2 as rs
from datetime import datetime
from pathlib import Path

try:
    import psutil
except ImportError:
    print("❌ Требуется библиотека psutil. Установите: pip install psutil")
    exit(1)

class RealSenseRecorderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("RealSense D415 Dataset Recorder")
        self.root.geometry("640x580")
        self.root.resizable(False, False)

        # Состояние
        self.recording = False
        self.stop_event = threading.Event()
        self.msg_queue = queue.Queue()
        
        self.base_dir = Path("recordings")
        self.base_dir.mkdir(exist_ok=True)
        self.output_dir = self.base_dir

        self.segment_idx = 0
        self.segment_start = 0.0
        self.pipe = rs.pipeline()
        self.cam_info = {"name": "Не найдена", "sn": "-", "fw": "-"}
        self.calib_data = None

        # Системные метрики
        self._sys_stats_ts = 0
        self._last_io_time = time.time()
        
        # 🔑 Храним только число байтов, а не весь namedtuple
        io_counters = psutil.disk_io_counters()
        self._last_io_bytes = io_counters.write_bytes if io_counters else 0
        
        # Первый вызов cpu_percent всегда 0.0, "разогреваем" его
        psutil.cpu_percent(interval=None)

        self._build_ui()
        self._init_camera()
        self._start_polling()

    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}

        # === 1. Инфо о камере ===
        info_frame = ttk.LabelFrame(self.root, text="📷 Камера")
        info_frame.pack(fill="x", **pad)
        self.lbl_cam = ttk.Label(info_frame, text="Инициализация...")
        self.lbl_cam.pack(anchor="w", **pad)

        # === 2. Настройки ===
        settings_frame = ttk.LabelFrame(self.root, text="⚙️ Параметры записи")
        settings_frame.pack(fill="x", **pad)

        ttk.Label(settings_frame, text="Разрешение:").grid(row=0, column=0, sticky="w", **pad)
        self.cmb_res = ttk.Combobox(settings_frame, values=["640x480", "848x480", "1280x720"], 
                                    state="readonly", width=10)
        self.cmb_res.current(0)
        self.cmb_res.grid(row=0, column=1, sticky="w", padx=5)

        ttk.Label(settings_frame, text="FPS:").grid(row=0, column=2, sticky="w", **pad)
        self.cmb_fps = ttk.Combobox(settings_frame, values=["6", "15", "30"], 
                                    state="readonly", width=5)
        self.cmb_fps.current(2)
        self.cmb_fps.grid(row=0, column=3, sticky="w", padx=5)

        self.var_color = tk.BooleanVar(value=True)
        chk_color = ttk.Checkbutton(settings_frame, text="🎨 Цветной поток", variable=self.var_color)
        chk_color.grid(row=0, column=4, sticky="w", padx=10)

        # === 3. Управление ===
        ctrl_frame = ttk.Frame(self.root)
        ctrl_frame.pack(fill="x", **pad)
        self.btn_toggle = ttk.Button(ctrl_frame, text="🟢 Начать запись", command=self._toggle_recording)
        self.btn_toggle.pack(side="left", **pad)

        # === 4. Статус и Метрики ===
        status_frame = ttk.LabelFrame(self.root, text="📊 Статус & Система")
        status_frame.pack(fill="x", **pad)

        ttk.Label(status_frame, text="Сессия:").grid(row=0, column=0, sticky="w", **pad)
        self.lbl_session = ttk.Label(status_frame, text="Не начата")
        self.lbl_session.grid(row=0, column=1, sticky="w", **pad)

        ttk.Label(status_frame, text="Сегмент:").grid(row=1, column=0, sticky="w", **pad)
        self.lbl_seg = ttk.Label(status_frame, text="seq_000.db3")
        self.lbl_seg.grid(row=1, column=1, sticky="w", **pad)

        ttk.Label(status_frame, text="Время:").grid(row=2, column=0, sticky="w", **pad)
        self.lbl_timer = ttk.Label(status_frame, text="00:00 / 60с")
        self.lbl_timer.grid(row=2, column=1, sticky="w", **pad)

        ttk.Label(status_frame, text="💾 Диск:").grid(row=3, column=0, sticky="w", **pad)
        self.lbl_disk_free = ttk.Label(status_frame, text="Вычисление...")
        self.lbl_disk_free.grid(row=3, column=1, sticky="w", **pad)

        # Системные метрики в строку
        sys_frame = ttk.Frame(status_frame)
        sys_frame.grid(row=4, column=0, columnspan=2, sticky="ew", **pad)
        
        ttk.Label(sys_frame, text="🖥 CPU:").pack(side="left")
        self.lbl_cpu = ttk.Label(sys_frame, text="0%", width=5)
        self.lbl_cpu.pack(side="left", padx=2)

        ttk.Label(sys_frame, text="| 💾 RAM:").pack(side="left", padx=(15,0))
        self.lbl_ram = ttk.Label(sys_frame, text="0 GB / 0 GB", width=16)
        self.lbl_ram.pack(side="left", padx=2)

        ttk.Label(sys_frame, text="| 📥 Запись:").pack(side="left", padx=(15,0))
        self.lbl_disk_write = ttk.Label(sys_frame, text="0 MB/s", width=8)
        self.lbl_disk_write.pack(side="left", padx=2)

        self.progress = ttk.Progressbar(status_frame, orient="horizontal", length=560, mode="determinate")
        self.progress.grid(row=5, column=0, columnspan=5, sticky="ew", **pad)

        # === 5. Лог ===
        log_frame = ttk.LabelFrame(self.root, text="📜 Лог действий")
        log_frame.pack(fill="both", expand=True, **pad)
        self.log_text = tk.Text(log_frame, height=7, state="disabled", font=("Consolas", 9))
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.log_text.pack(side="left", fill="both", expand=True)

    def _log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self.msg_queue.put(f"[{ts}] {msg}")

    def _poll_queue(self):
        while not self.msg_queue.empty():
            msg = self.msg_queue.get()
            self.log_text.config(state="normal")
            self.log_text.insert("end", msg + "\n")
            self.log_text.see("end")
            self.log_text.config(state="disabled")
        self.root.after(50, self._poll_queue)

    def _update_ui_loop(self):
        now = time.time()
        
        if self.recording:
            elapsed = time.time() - self.segment_start
            mins, secs = divmod(int(elapsed), 60)
            self.lbl_timer.config(text=f"{mins:02d}:{secs:02d} / 60с")
            self.progress["value"] = (elapsed / 60.0) * 100

        if now - self._sys_stats_ts >= 1.0:
            self._sys_stats_ts = now
            self._update_system_stats()

        self.root.after(100, self._update_ui_loop)

    def _update_system_stats(self):
        # CPU
        cpu_pct = psutil.cpu_percent(interval=None)
        self.lbl_cpu.config(text=f"{cpu_pct}%", 
                            foreground="red" if cpu_pct > 85 else "black")

        # RAM
        mem = psutil.virtual_memory()
        used_gb = mem.used / (1024**3)
        total_gb = mem.total / (1024**3)
        self.lbl_ram.config(text=f"{used_gb:.1f} / {total_gb:.1f} GB",
                            foreground="red" if mem.percent > 90 else "black")

        # Свободное место
        try:
            free_gb = shutil.disk_usage(self.base_dir).free / (1024**3)
        except Exception:
            free_gb = 0.0
        self.lbl_disk_free.config(text=f"{free_gb:.2f} ГБ свободно",
                                  foreground="red" if free_gb < 0.5 else "orange" if free_gb < 2.0 else "black")
        if self.recording and free_gb < 0.5:
            self.stop_event.set()
            self._log("⚠️ КРИТИЧЕСКИ МАЛО МЕСТА! Авто-остановка...")

        # 🔑 Скорость записи (используем только числовое значение байтов)
        current_io = psutil.disk_io_counters()
        if current_io is not None:
            dt = time.time() - self._last_io_time
            if dt > 0.8:
                write_delta = current_io.write_bytes - self._last_io_bytes
                write_speed = (write_delta / dt) / (1024**2)  # MB/s
                # Защита от отрицательных значений при сбросе счётчиков ОС
                write_speed = max(0.0, write_speed)
                
                self.lbl_disk_write.config(text=f"{write_speed:.1f} MB/s",
                                           foreground="green" if self.recording and write_speed > 0.5 else "gray")
                
                self._last_io_bytes = current_io.write_bytes
                self._last_io_time = time.time()

    def _init_camera(self):
        self._log("🔍 Поиск камеры...")
        try:
            ctx = rs.context()
            devices = ctx.query_devices()
            if not devices:
                raise RuntimeError("Камера не подключена или занята")
            
            dev = devices[0]
            self.cam_info = {
                "name": dev.get_info(rs.camera_info.name),
                "sn": dev.get_info(rs.camera_info.serial_number),
                "fw": dev.get_info(rs.camera_info.firmware_version)
            }
            self.lbl_cam.config(text=f"✅ {self.cam_info['name']} | SN: {self.cam_info['sn']}")
            
            tmp_pipe = rs.pipeline()
            tmp_cfg = rs.config()
            tmp_cfg.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)
            tmp_cfg.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 30)
            tmp_profile = tmp_pipe.start(tmp_cfg)
            
            d_p = tmp_profile.get_stream(rs.stream.depth).as_video_stream_profile()
            c_p = tmp_profile.get_stream(rs.stream.color).as_video_stream_profile()
            
            self.calib_data = {
                "device": self.cam_info["name"], "serial": self.cam_info["sn"],
                "depth_intrinsics": {"fx": d_p.intrinsics.fx, "fy": d_p.intrinsics.fy,
                                     "ppx": d_p.intrinsics.ppx, "ppy": d_p.intrinsics.ppy,
                                     "model": str(d_p.intrinsics.model), "coeffs": list(d_p.intrinsics.coeffs)},
                "color_intrinsics": {"fx": c_p.intrinsics.fx, "fy": c_p.intrinsics.fy,
                                     "ppx": c_p.intrinsics.ppx, "ppy": c_p.intrinsics.ppy,
                                     "model": str(c_p.intrinsics.model), "coeffs": list(c_p.intrinsics.coeffs)},
                "extrinsics": {"rot": list(d_p.get_extrinsics_to(c_p).rotation),
                               "trans": list(d_p.get_extrinsics_to(c_p).translation)}
            }
            tmp_pipe.stop()
            self._log("✅ Калибровка считана.")
            
        except Exception as e:
            self._log(f"❌ Ошибка инициализации: {e}")
            messagebox.showerror("Ошибка камеры", str(e))
            self.btn_toggle.config(state="disabled")

    def _toggle_recording(self):
        if not self.recording:
            self._start_recording()
        else:
            self._stop_recording()

    def _lock_settings(self, lock):
        state = "disabled" if lock else "readonly"
        self.cmb_res.config(state=state)
        self.cmb_fps.config(state=state)
        for child in self.root.winfo_children():
            if isinstance(child, ttk.Checkbutton) and "Цветной поток" in child.cget("text"):
                child.config(state="disabled" if lock else "normal")

    def _get_current_settings(self):
        w, h = map(int, self.cmb_res.get().split('x'))
        return {"width": w, "height": h, "fps": int(self.cmb_fps.get()), "color": self.var_color.get()}

    def _start_recording(self):
        try:
            free_gb = shutil.disk_usage(self.base_dir).free / (1024**3)
        except:
            free_gb = 0.0
        if free_gb < 0.5:
            self._log("⚠️ Недостаточно места (<500 МБ). Запись невозможна.")
            return

        session_name = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        self.output_dir = self.base_dir / session_name
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.segment_idx = 0
        
        if self.calib_data:
            with open(self.output_dir / "calibration.json", "w", encoding="utf-8") as f:
                json.dump(self.calib_data, f, indent=2, ensure_ascii=False)
                
        self.lbl_session.config(text=session_name)
        self._log(f"📁 Сессия: {session_name}")
        
        self._lock_settings(True)
        self.recording = True
        self.stop_event.clear()
        self.btn_toggle.config(text="🔴 Остановить")
        self._log("▶ Запись начата")
        threading.Thread(target=self._record_worker, daemon=True).start()

    def _stop_recording(self):
        self.recording = False
        self.stop_event.set()
        self._lock_settings(False)
        self.btn_toggle.config(text="🟢 Начать запись")
        self.progress["value"] = 0
        self.lbl_timer.config(text="00:00 / 60с")
        self.lbl_session.config(text="Не начата")
        self._log("⏹ Сеанс завершён.")

    def _record_worker(self):
        settings = self._get_current_settings()
        segment_name = f"seq_{self.segment_idx:03d}.db3"
        filepath = self.output_dir / segment_name
        
        while not self.stop_event.is_set():
            try:
                cfg = rs.config()
                cfg.enable_record_to_file(str(filepath))
                cfg.enable_stream(rs.stream.depth, settings["width"], settings["height"], 
                                  rs.format.z16, settings["fps"])
                if settings["color"]:
                    cfg.enable_stream(rs.stream.color, settings["width"], settings["height"], 
                                      rs.format.bgr8, settings["fps"])
                
                self.pipe.start(cfg)
                self.segment_start = time.time()
                self.msg_queue.put(f"💾 {segment_name} | {settings['width']}x{settings['height']}@{settings['fps']}FPS")
                
                while not self.stop_event.is_set() and (time.time() - self.segment_start) < 60:
                    frames = self.pipe.wait_for_frames(timeout_ms=2000)
                    time.sleep(0.02)
                
                self.pipe.stop()
                duration = time.time() - self.segment_start
                self.msg_queue.put(f"✅ Сегмент: {duration:.1f}с")
                
                self.segment_idx += 1
                segment_name = f"seq_{self.segment_idx:03d}.db3"
                filepath = self.output_dir / segment_name
                self.lbl_seg.config(text=segment_name)
                
            except RuntimeError as e:
                self.msg_queue.put(f"⚠️ Ошибка: {e}")
                if "Couldn't resolve requests" in str(e):
                    self.msg_queue.put("💡 Проверьте USB 3.0 или снизьте FPS/разрешение.")
                break
            except Exception as e:
                self.msg_queue.put(f"❌ Критическая ошибка: {e}")
                break
                
        self.recording = False
        self.root.after(0, lambda: (self._lock_settings(False), self.btn_toggle.config(text="🟢 Начать запись")))

    def _start_polling(self):
        self._poll_queue()
        self._update_ui_loop()

if __name__ == "__main__":
    root = tk.Tk()
    app = RealSenseRecorderApp(root)
    root.mainloop()