#!/usr/bin/env python3
"""
RealSense D415 Dataset Recorder — v6.8 (Working Folder Selection)
- Выбор папки через текстовое поле + кнопка "Обзор" (работает в VS Code)
- Экспозиция округляется до 10, Усиление до 1, WB до 10
- FPS: 6 | 15 | 30 | Разрешения: 640x480 | 848x480 | 1280x720
"""
import tkinter as tk
from tkinter import ttk, messagebox
import threading
import queue
import time
import json
import shutil
import subprocess
import pyrealsense2 as rs
from datetime import datetime
from pathlib import Path

try:
    import psutil
except ImportError:
    print("❌ Требуется psutil: pip install psutil")
    exit(1)

class RealSenseRecorderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("RealSense D415 Dataset Recorder")
        self.root.geometry("700x640")
        self.root.resizable(False, False)

        self.recording = False
        self.stop_event = threading.Event()
        self.msg_queue = queue.Queue()
        
        # Базовая папка по умолчанию
        self.base_dir = Path("recordings")
        self.base_dir.mkdir(exist_ok=True)
        self.output_dir = self.base_dir

        self.segment_idx = 0
        self.segment_start = 0.0
        self.pipe = rs.pipeline()
        self.cam_info = {"name": "Не найдена", "sn": "-", "fw": "-"}
        self.calib_data = None

        # Настройки освещения
        self.var_exposure = tk.IntVar(value=200)
        self.var_gain = tk.IntVar(value=32)
        self.var_wb = tk.IntVar(value=4600)
        
        # Путь к папке (для текстового поля)
        self.var_path = tk.StringVar(value=str(self.base_dir))

        self._sys_stats_ts = 0
        self._last_io_time = time.time()
        io_counters = psutil.disk_io_counters()
        self._last_io_bytes = io_counters.write_bytes if io_counters else 0
        psutil.cpu_percent(interval=None)

        self._build_ui()
        self._init_camera()
        self._start_polling()

    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}

        # === 1. Камера ===
        info_frame = ttk.LabelFrame(self.root, text="📷 Камера")
        info_frame.pack(fill="x", **pad)
        self.lbl_cam = ttk.Label(info_frame, text="Инициализация...")
        self.lbl_cam.pack(anchor="w", **pad)

        # === 2. Выбор папки (текстовое поле + кнопка) ===
        dir_frame = ttk.LabelFrame(self.root, text="💾 Папка сохранений")
        dir_frame.pack(fill="x", **pad)
        
        ttk.Label(dir_frame, text="Путь:").grid(row=0, column=0, sticky="w", **pad)
        path_entry = ttk.Entry(dir_frame, textvariable=self.var_path, width=60)
        path_entry.grid(row=0, column=1, sticky="ew", padx=5, pady=5)
        
        btn_browse = ttk.Button(dir_frame, text="📁 Обзор...", command=self._browse_folder)
        btn_browse.grid(row=0, column=2, padx=5)
        
        btn_apply_path = ttk.Button(dir_frame, text="✅ Применить путь", command=self._apply_path)
        btn_apply_path.grid(row=0, column=3, padx=5)
        
        dir_frame.grid_columnconfigure(1, weight=1)

        # === 3. Настройки записи ===
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
        self.cmb_fps.current(1)
        self.cmb_fps.grid(row=0, column=3, sticky="w", padx=5)

        ttk.Label(settings_frame, text="Поток:").grid(row=0, column=4, sticky="w", **pad)
        ttk.Label(settings_frame, text="RGB8 + Z16", foreground="green").grid(row=0, column=5, sticky="w")

        # 💡 Ползунки яркости
        light_frame = ttk.LabelFrame(settings_frame, text="💡 Яркость/Цвет")
        light_frame.grid(row=1, column=0, columnspan=6, sticky="ew", pady=5)

        ttk.Label(light_frame, text="Экспозиция:").grid(row=0, column=0, sticky="w", padx=5)
        tk.Scale(light_frame, from_=50, to=1000, variable=self.var_exposure, orient="horizontal", 
                 length=160, resolution=10).grid(row=0, column=1, padx=2)
        ttk.Label(light_frame, textvariable=self.var_exposure, width=4).grid(row=0, column=2)

        ttk.Label(light_frame, text="Усиление:").grid(row=0, column=3, sticky="w", padx=5)
        tk.Scale(light_frame, from_=0, to=128, variable=self.var_gain, orient="horizontal", 
                 length=100, resolution=1).grid(row=0, column=4, padx=2)
        ttk.Label(light_frame, textvariable=self.var_gain, width=4).grid(row=0, column=5)

        ttk.Label(light_frame, text="WB:").grid(row=1, column=0, sticky="w", padx=5)
        tk.Scale(light_frame, from_=2800, to=7000, variable=self.var_wb, orient="horizontal", 
                 length=160, resolution=10).grid(row=1, column=1, padx=2)
        ttk.Label(light_frame, textvariable=self.var_wb, width=5).grid(row=1, column=2)

        btn_apply = ttk.Button(light_frame, text="Применить", command=self._apply_lighting_settings)
        btn_apply.grid(row=1, column=3, padx=2)
        
        btn_reset = ttk.Button(light_frame, text="🔄 Сброс", command=self._reset_lighting_settings)
        btn_reset.grid(row=1, column=4, padx=2)

        # === 4. Управление ===
        ctrl_frame = ttk.Frame(self.root)
        ctrl_frame.pack(fill="x", **pad)
        self.btn_toggle = ttk.Button(ctrl_frame, text="🟢 Начать запись", command=self._toggle_recording)
        self.btn_toggle.pack(side="left", **pad)

        # === 5. Статус & Система ===
        status_frame = ttk.LabelFrame(self.root, text="📊 Статус")
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

        ttk.Label(status_frame, text="Диск:").grid(row=3, column=0, sticky="w", **pad)
        self.lbl_disk_free = ttk.Label(status_frame, text="Вычисление...")
        self.lbl_disk_free.grid(row=3, column=1, sticky="w", **pad)

        sys_frame = ttk.Frame(status_frame)
        sys_frame.grid(row=4, column=0, columnspan=2, sticky="ew", **pad)
        
        ttk.Label(sys_frame, text="CPU:").pack(side="left")
        self.lbl_cpu = ttk.Label(sys_frame, text="0%", width=5)
        self.lbl_cpu.pack(side="left", padx=2)
        ttk.Label(sys_frame, text="| RAM:").pack(side="left", padx=(15,0))
        self.lbl_ram = ttk.Label(sys_frame, text="0/0 GB", width=12)
        self.lbl_ram.pack(side="left", padx=2)
        ttk.Label(sys_frame, text="| Запись:").pack(side="left", padx=(15,0))
        self.lbl_disk_write = ttk.Label(sys_frame, text="0 MB/s", width=8)
        self.lbl_disk_write.pack(side="left", padx=2)

        self.progress = ttk.Progressbar(status_frame, orient="horizontal", length=620, mode="determinate")
        self.progress.grid(row=5, column=0, columnspan=5, sticky="ew", **pad)

        # === 6. Лог ===
        log_frame = ttk.LabelFrame(self.root, text="📜 Лог")
        log_frame.pack(fill="both", expand=True, **pad)
        self.log_text = tk.Text(log_frame, height=5, state="disabled", font=("Consolas", 9))
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
        cpu = psutil.cpu_percent(interval=None)
        self.lbl_cpu.config(text=f"{cpu}%", foreground="red" if cpu>85 else "black")
        mem = psutil.virtual_memory()
        self.lbl_ram.config(text=f"{mem.used/1e9:.1f}/{mem.total/1e9:.1f} GB", foreground="red" if mem.percent>90 else "black")
        try: free = shutil.disk_usage(self.base_dir).free / 1e9
        except: free = 0.0
        self.lbl_disk_free.config(text=f"{free:.1f} ГБ", foreground="red" if free<0.5 else "orange" if free<2.0 else "black")
        if self.recording and free < 0.5:
            self.stop_event.set()
            self._log("⚠️ МАЛО МЕСТА! Авто-стоп...")
        io = psutil.disk_io_counters()
        if io:
            dt = time.time() - self._last_io_time
            if dt > 0.8:
                speed = max(0.0, (io.write_bytes - self._last_io_bytes)/dt/1e6)
                self.lbl_disk_write.config(text=f"{speed:.1f} MB/s", foreground="green" if self.recording and speed>0.5 else "gray")
                self._last_io_bytes, self._last_io_time = io.write_bytes, time.time()

    def _browse_folder(self):
        """Открывает проводник Windows для выбора папки (через subprocess)"""
        try:
            # Используем PowerShell для открытия диалога выбора папки
            ps_script = '''
                Add-Type -AssemblyName System.Windows.Forms
                $folderBrowser = New-Object System.Windows.Forms.FolderBrowserDialog
                $folderBrowser.Description = "Выберите папку для сохранений"
                $folderBrowser.SelectedPath = "{}"
                $result = $folderBrowser.ShowDialog()
                if ($result -eq [System.Windows.Forms.DialogResult]::OK) {{
                    Write-Output $folderBrowser.SelectedPath
                }}
            '''.format(str(self.base_dir))
            
            result = subprocess.run(
                ["powershell", "-Command", ps_script],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0 and result.stdout.strip():
                folder = result.stdout.strip()
                self.var_path.set(folder)
                self._log(f"📁 Выбрана папка: {folder}")
        except Exception as e:
            self._log(f"⚠️ Ошибка выбора: {e}")
            self._log("💡 Введите путь вручную в текстовое поле")

    def _apply_path(self):
        """Применяет путь из текстового поля"""
        try:
            new_path = Path(self.var_path.get())
            new_path.mkdir(parents=True, exist_ok=True)
            self.base_dir = new_path / "recordings"
            self.base_dir.mkdir(parents=True, exist_ok=True)
            self._log(f"✅ Путь применён: {self.base_dir}")
        except Exception as e:
            self._log(f"❌ Ошибка: {e}")
            messagebox.showerror("Ошибка пути", f"Не удалось создать папку: {e}")

    def _apply_lighting_settings(self):
        """Применяет настройки камеры"""
        try:
            ctx = rs.context()
            dev = ctx.query_devices()[0]
            for sensor in dev.sensors:
                if not any(p.stream_type() == rs.stream.color for p in sensor.get_stream_profiles()):
                    continue

                sensor.set_option(rs.option.enable_auto_exposure, False)
                sensor.set_option(rs.option.enable_auto_white_balance, False)

                exp = round(max(50, min(1000, self.var_exposure.get())) / 10) * 10
                gain = max(0, min(128, self.var_gain.get()))
                wb = round(max(2800, min(7000, self.var_wb.get())) / 10) * 10

                sensor.set_option(rs.option.exposure, exp)
                sensor.set_option(rs.option.gain, gain)
                sensor.set_option(rs.option.white_balance, wb)

                self._log(f"✅ Применено: Exp={exp}, Gain={gain}, WB={wb}K")
                return
        except Exception as e:
            self._log(f"⚠️ Ошибка: {e}")

    def _reset_lighting_settings(self):
        self.var_exposure.set(200)
        self.var_gain.set(32)
        self.var_wb.set(4600)
        self._apply_lighting_settings()
        self._log("🔄 Сброшено")

    def _init_camera(self):
        self._log("🔍 Поиск камеры...")
        try:
            ctx = rs.context()
            dev = ctx.query_devices()[0]
            self.cam_info = {
                "name": dev.get_info(rs.camera_info.name),
                "sn": dev.get_info(rs.camera_info.serial_number),
                "fw": dev.get_info(rs.camera_info.firmware_version)
            }
            self.lbl_cam.config(text=f"✅ {self.cam_info['name']} | SN: {self.cam_info['sn']}")
            
            tmp = rs.pipeline()
            cfg = rs.config()
            cfg.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)
            cfg.enable_stream(rs.stream.color, 640, 480, rs.format.rgb8, 30)
            p = tmp.start(cfg)
            
            dp = p.get_stream(rs.stream.depth).as_video_stream_profile()
            cp = p.get_stream(rs.stream.color).as_video_stream_profile()
            self.calib_data = {
                "device": self.cam_info["name"], "serial": self.cam_info["sn"], "firmware": self.cam_info["fw"],
                "lighting": {"exposure": 200, "gain": 32, "white_balance": 4600},
                "depth_intrinsics": {"fx": dp.intrinsics.fx, "fy": dp.intrinsics.fy, "ppx": dp.intrinsics.ppx, "ppy": dp.intrinsics.ppy, "model": str(dp.intrinsics.model), "coeffs": list(dp.intrinsics.coeffs)},
                "color_intrinsics": {"fx": cp.intrinsics.fx, "fy": cp.intrinsics.fy, "ppx": cp.intrinsics.ppx, "ppy": cp.intrinsics.ppy, "model": str(cp.intrinsics.model), "coeffs": list(cp.intrinsics.coeffs)},
                "extrinsics": {"rot": list(dp.get_extrinsics_to(cp).rotation), "trans": list(dp.get_extrinsics_to(cp).translation)}
            }
            tmp.stop()
            self._log("✅ Калибровка считана.")
        except Exception as e:
            self._log(f"❌ Ошибка: {e}")
            self.btn_toggle.config(state="disabled")

    def _toggle_recording(self):
        if not self.recording: self._start_recording()
        else: self._stop_recording()

    def _lock_settings(self, lock):
        st = "disabled" if lock else "normal"
        self.cmb_res.config(state=st)
        self.cmb_fps.config(state=st)
        for child in self.root.winfo_children():
            if isinstance(child, ttk.LabelFrame) and "Яркость" in child.cget("text"):
                for w in child.winfo_children():
                    w.config(state=st)

    def _start_recording(self):
        try: free = shutil.disk_usage(self.base_dir).free / 1e9
        except: free = 0.0
        if free < 0.5: self._log("⚠️ <500 МБ свободно"); return

        session = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        self.output_dir = self.base_dir / session
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.segment_idx = 0
        
        if self.calib_data:
            self.calib_data["lighting"] = {
                "exposure": self.var_exposure.get(),
                "gain": self.var_gain.get(),
                "white_balance": self.var_wb.get()
            }
            with open(self.output_dir / "calibration.json", "w", encoding="utf-8") as f:
                json.dump(self.calib_data, f, indent=2, ensure_ascii=False)
                
        self.lbl_session.config(text=session)
        self._log(f"📁 Сессия: {session}")
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
        w, h = map(int, self.cmb_res.get().split('x'))
        fps = int(self.cmb_fps.get())
        
        while not self.stop_event.is_set():
            seg = f"seq_{self.segment_idx:03d}.db3"
            fp = self.output_dir / seg
            try:
                cfg = rs.config()
                cfg.enable_record_to_file(str(fp))
                cfg.enable_stream(rs.stream.depth, w, h, rs.format.z16, fps)
                cfg.enable_stream(rs.stream.color, w, h, rs.format.rgb8, fps)
                self.pipe.start(cfg)
                
                dev = self.pipe.get_active_profile().get_device()
                for sensor in dev.sensors:
                    if any(p.stream_type() == rs.stream.color for p in sensor.get_stream_profiles()):
                        sensor.set_option(rs.option.enable_auto_exposure, False)
                        sensor.set_option(rs.option.enable_auto_white_balance, False)
                        
                        exp = round(max(50, min(1000, self.var_exposure.get())) / 10) * 10
                        gain = max(0, min(128, self.var_gain.get()))
                        wb = round(max(2800, min(7000, self.var_wb.get())) / 10) * 10
                        
                        sensor.set_option(rs.option.exposure, exp)
                        sensor.set_option(rs.option.gain, gain)
                        sensor.set_option(rs.option.white_balance, wb)
                        break
                
                self.segment_start = time.time()
                self.msg_queue.put(f"💾 {seg} | {w}x{h}@{fps}FPS | Exp:{exp} Gain:{gain} WB:{wb}K")
                
                while not self.stop_event.is_set() and (time.time()-self.segment_start)<60:
                    self.pipe.wait_for_frames(timeout_ms=2000)
                    time.sleep(0.02)
                    
                self.pipe.stop()
                self.msg_queue.put(f"✅ {seg}: {time.time()-self.segment_start:.1f}с")
                self.segment_idx += 1
                self.lbl_seg.config(text=f"seq_{self.segment_idx:03d}.db3")
            except RuntimeError as e:
                self.msg_queue.put(f"⚠️ {e}")
                if "Couldn't resolve requests" in str(e): self.msg_queue.put("💡 USB 3.0 / снизьте FPS")
                break
            except Exception as e:
                self.msg_queue.put(f"❌ {e}")
                break
        self.recording = False
        self.root.after(0, lambda: self._lock_settings(False))

    def _start_polling(self):
        self._poll_queue()
        self._update_ui_loop()

if __name__ == "__main__":
    root = tk.Tk()
    app = RealSenseRecorderApp(root)
    root.mainloop()