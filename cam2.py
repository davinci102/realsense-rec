#!/usr/bin/env python3
"""
Logitech C920 Parallel Dataset Recorder — v1.4 (Fix Stop Button)
- Мгновенный выход из цикла записи при нажатии "Стоп"
- Предпросмотр: фиксированный размер (400x300)
- Настройки: прокручиваемая область
- FPS: 6 | 15 | 30 | 60
"""
import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageTk
import threading
import queue
import time
import json
import csv
import shutil
import subprocess
import cv2
import numpy as np
from datetime import datetime
from pathlib import Path

try:
    import psutil
except ImportError:
    print("❌ Требуется psutil: pip install psutil")
    exit(1)

class WebcamRecorderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Logitech C920 Dataset Recorder")
        self.root.geometry("900x600")
        self.root.minsize(800, 500)

        self.recording = False
        self.stop_event = threading.Event()
        self.msg_queue = queue.Queue()
        
        self.base_dir = Path("webcam_recordings")
        self.base_dir.mkdir(exist_ok=True)
        self.output_dir = self.base_dir

        self.cap = None
        self.cam_idx = 0
        self.segment_start = 0.0
        self.frame_idx = 0
        
        self.var_res = tk.StringVar(value="1280x720")
        self.var_fps = tk.IntVar(value=30)
        self.var_exposure = tk.IntVar(value=-6)
        self.var_gain = tk.IntVar(value=0)
        self.var_auto_wb = tk.BooleanVar(value=True)
        self.var_wb_temp = tk.IntVar(value=4600)
        
        self.var_path = tk.StringVar(value=str(self.base_dir))
        self.preview_label = None
        self.preview_after_id = None

        self._sys_stats_ts = 0
        self._last_io_time = time.time()
        io_counters = psutil.disk_io_counters()
        self._last_io_bytes = io_counters.write_bytes if io_counters else 0
        psutil.cpu_percent(interval=None)

        self._build_ui()
        self._init_camera()
        self._start_polling()
        self._start_preview()

    def _build_ui(self):
        main_pane = tk.PanedWindow(self.root, orient=tk.HORIZONTAL, sashrelief=tk.RAISED, bg="#ccc")
        main_pane.pack(fill=tk.BOTH, expand=True)

        left_frame = tk.Frame(main_pane, bg="#222")
        main_pane.add(left_frame, width=450, minsize=300)

        ttk.Label(left_frame, text=" Предпросмотр (Live)", foreground="white", background="#222", font=("Arial", 10, "bold")).pack(pady=5)
        self.preview_container = tk.Frame(left_frame, bg="#000")
        self.preview_container.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        self.preview_label = ttk.Label(self.preview_container, background="#000")
        self.preview_label.pack(fill=tk.BOTH, expand=True)

        info_frame = tk.Frame(left_frame, bg="#222")
        info_frame.pack(fill="x", pady=5)
        self.lbl_cam = ttk.Label(info_frame, text="Инициализация...", foreground="white", background="#222")
        self.lbl_cam.pack(anchor="w", padx=10)

        right_frame = tk.Frame(main_pane)
        main_pane.add(right_frame, width=400)

        canvas = tk.Canvas(right_frame)
        scrollbar = ttk.Scrollbar(right_frame, orient="vertical", command=canvas.yview)
        self.scrollable_frame = ttk.Frame(canvas)
        self.scrollable_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        pad = {"padx": 10, "pady": 5}

        dir_frame = ttk.LabelFrame(self.scrollable_frame, text="💾 Папка сохранений")
        dir_frame.pack(fill="x", **pad)
        ttk.Label(dir_frame, text="Путь:").grid(row=0, column=0, sticky="w")
        ttk.Entry(dir_frame, textvariable=self.var_path, width=30).grid(row=0, column=1, sticky="ew", padx=5, pady=5)
        ttk.Button(dir_frame, text="", command=self._browse_folder).grid(row=0, column=2, padx=2)
        ttk.Button(dir_frame, text="✅", command=self._apply_path).grid(row=0, column=3, padx=2)
        dir_frame.grid_columnconfigure(1, weight=1)

        settings_frame = ttk.LabelFrame(self.scrollable_frame, text="⚙️ Параметры")
        settings_frame.pack(fill="x", **pad)
        ttk.Label(settings_frame, text="Разрешение:").grid(row=0, column=0, sticky="w")
        ttk.Combobox(settings_frame, textvariable=self.var_res, values=["640x480", "1280x720", "1920x1080"], state="readonly", width=12).grid(row=0, column=1, sticky="w", padx=5, pady=5)
        ttk.Label(settings_frame, text="FPS:").grid(row=1, column=0, sticky="w")
        ttk.Combobox(settings_frame, values=["6", "15", "30", "60"], textvariable=self.var_fps, state="readonly", width=8).grid(row=1, column=1, sticky="w", padx=5, pady=5)

        light_frame = ttk.LabelFrame(self.scrollable_frame, text="💡 Яркость/Цвет")
        light_frame.pack(fill="x", **pad)
        ttk.Label(light_frame, text="Экспозиция:").grid(row=0, column=0, sticky="w")
        tk.Scale(light_frame, from_=-10, to=-1, variable=self.var_exposure, orient="horizontal", length=140, resolution=1).grid(row=0, column=1, padx=2)
        ttk.Label(light_frame, textvariable=self.var_exposure, width=3).grid(row=0, column=2)
        ttk.Label(light_frame, text="Усиление:").grid(row=1, column=0, sticky="w")
        tk.Scale(light_frame, from_=0, to=255, variable=self.var_gain, orient="horizontal", length=140, resolution=1).grid(row=1, column=1, padx=2)
        ttk.Label(light_frame, textvariable=self.var_gain, width=3).grid(row=1, column=2)
        ttk.Checkbutton(light_frame, text="Авто ББ", variable=self.var_auto_wb).grid(row=2, column=0, sticky="w", pady=5)
        ttk.Label(light_frame, text="Темп. ББ:").grid(row=3, column=0, sticky="w")
        tk.Scale(light_frame, from_=2800, to=7000, variable=self.var_wb_temp, orient="horizontal", length=140, resolution=10).grid(row=3, column=1, padx=2)
        ttk.Label(light_frame, textvariable=self.var_wb_temp, width=5).grid(row=3, column=2)
        ttk.Button(light_frame, text="Применить настройки", command=self._apply_camera_settings).grid(row=4, column=0, columnspan=3, pady=10, ipadx=10)
        ttk.Button(light_frame, text="🔄 Сброс", command=self._reset_camera_settings).grid(row=5, column=0, columnspan=3)

        ctrl_frame = ttk.LabelFrame(self.scrollable_frame, text=" Запись")
        ctrl_frame.pack(fill="x", **pad)
        self.btn_toggle = ttk.Button(ctrl_frame, text=" Начать запись", command=self._toggle_recording)
        self.btn_toggle.pack(fill="x", **pad)

        status_frame = ttk.LabelFrame(self.scrollable_frame, text=" Статус")
        status_frame.pack(fill="x", **pad)
        ttk.Label(status_frame, text="Сессия:").grid(row=0, column=0, sticky="w")
        self.lbl_session = ttk.Label(status_frame, text="Не начата")
        self.lbl_session.grid(row=0, column=1, sticky="w")
        ttk.Label(status_frame, text="Кадры:").grid(row=1, column=0, sticky="w")
        self.lbl_frames = ttk.Label(status_frame, text="0")
        self.lbl_frames.grid(row=1, column=1, sticky="w")
        ttk.Label(status_frame, text="Время:").grid(row=2, column=0, sticky="w")
        self.lbl_timer = ttk.Label(status_frame, text="00:00")
        self.lbl_timer.grid(row=2, column=1, sticky="w")
        ttk.Label(status_frame, text="Диск:").grid(row=3, column=0, sticky="w")
        self.lbl_disk_free = ttk.Label(status_frame, text="Вычисление...")
        self.lbl_disk_free.grid(row=3, column=1, sticky="w")

        log_frame = ttk.LabelFrame(self.scrollable_frame, text=" Лог")
        log_frame.pack(fill="x", **pad)
        self.log_text = tk.Text(log_frame, height=4, state="disabled", font=("Consolas", 8))
        scrollbar_log = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar_log.set)
        scrollbar_log.pack(side="right", fill="y")
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
            self.lbl_timer.config(text=f"{mins:02d}:{secs:02d}")
            self.lbl_frames.config(text=str(self.frame_idx))
        if now - self._sys_stats_ts >= 1.0:
            self._sys_stats_ts = now
            self._update_system_stats()
        self.root.after(100, self._update_ui_loop)

    def _update_system_stats(self):
        mem = psutil.virtual_memory()
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
                self._last_io_bytes, self._last_io_time = io.write_bytes, time.time()

    def _browse_folder(self):
        try:
            self.root.update_idletasks()
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
            result = subprocess.run(["powershell", "-Command", ps_script], capture_output=True, text=True, timeout=30)
            if result.returncode == 0 and result.stdout.strip():
                self.var_path.set(result.stdout.strip())
                self._log(f"📁 Выбрана папка: {result.stdout.strip()}")
        except Exception as e:
            self._log(f"⚠️ Ошибка выбора: {e}")

    def _apply_path(self):
        try:
            new_path = Path(self.var_path.get())
            new_path.mkdir(parents=True, exist_ok=True)
            self.base_dir = new_path / "webcam_recordings"
            self.base_dir.mkdir(parents=True, exist_ok=True)
            self._log(f"✅ Путь применён: {self.base_dir}")
        except Exception as e:
            self._log(f"❌ Ошибка: {e}")

    def _init_camera(self):
        self._log("🔍 Поиск камеры...")
        try:
            self.cap = cv2.VideoCapture(self.cam_idx)
            if not self.cap.isOpened():
                raise RuntimeError("Камера не найдена или занята")
            self._apply_camera_settings(silent=True)
            w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            fps = int(self.cap.get(cv2.CAP_PROP_FPS))
            self.lbl_cam.config(text=f"✅ Logitech C920 | {w}x{h} @ {fps}FPS")
            self._log(f"✅ Камера инициализирована")
        except Exception as e:
            self._log(f"❌ Ошибка: {e}")
            self.btn_toggle.config(state="disabled")

    def _apply_camera_settings(self, silent=False):
        if self.cap is None or not self.cap.isOpened(): return
        try:
            w, h = map(int, self.var_res.get().split('x'))
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
            self.cap.set(cv2.CAP_PROP_FPS, self.var_fps.get())
            self.cap.set(cv2.CAP_PROP_EXPOSURE, float(self.var_exposure.get()))
            self.cap.set(cv2.CAP_PROP_GAIN, float(self.var_gain.get()))
            self.cap.set(cv2.CAP_PROP_AUTO_WB, 1 if self.var_auto_wb.get() else 0)
            if not self.var_auto_wb.get():
                try: self.cap.set(cv2.CAP_PROP_WB_TEMPERATURE, float(self.var_wb_temp.get()))
                except: pass
            if not silent: self._log("✅ Настройки применены")
        except Exception as e:
            if not silent: self._log(f"⚠️ Ошибка: {e}")

    def _reset_camera_settings(self):
        self.var_exposure.set(-6)
        self.var_gain.set(0)
        self.var_auto_wb.set(True)
        self.var_wb_temp.set(4600)
        self._apply_camera_settings()
        self._log("🔄 Сброшено")

    def _toggle_recording(self):
        if not self.recording: self._start_recording()
        else: self._stop_recording()

    def _lock_ui(self, lock):
        st = "disabled" if lock else "normal"
        for child in self.scrollable_frame.winfo_children():
            if isinstance(child, ttk.LabelFrame):
                for w in child.winfo_children():
                    # Не трогаем саму кнопку стоп/старт, чтобы она не зависла в disabled
                    if w == self.btn_toggle: continue
                    if isinstance(w, (ttk.Button, ttk.Combobox, tk.Scale, ttk.Checkbutton)):
                        w.config(state=st)

    def _start_recording(self):
        try: free = shutil.disk_usage(self.base_dir).free / 1e9
        except: free = 0.0
        if free < 0.5: self._log("⚠️ <500 МБ свободно"); return

        session = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        self.output_dir = self.base_dir / session
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / "frames").mkdir(exist_ok=True)
        self.segment_start = time.time()
        self.frame_idx = 0
        
        calib = {
            "device": "Logitech HD Pro C920",
            "resolution": self.var_res.get(),
            "fps": self.var_fps.get(),
            "exposure": self.var_exposure.get(),
            "gain": self.var_gain.get(),
            "auto_white_balance": self.var_auto_wb.get(),
            "wb_temperature": self.var_wb_temp.get()
        }
        with open(self.output_dir / "calibration.json", "w", encoding="utf-8") as f:
            json.dump(calib, f, indent=2, ensure_ascii=False)
                
        self.lbl_session.config(text=session)
        self._log(f"📁 Сессия: {session}")
        self._lock_ui(True)
        self.recording = True
        self.stop_event.clear()
        self.btn_toggle.config(text="🔴 Остановить", state="normal")
        self._log("▶ Запись начата")
        threading.Thread(target=self._record_worker, daemon=True).start()

    def _stop_recording(self):
        self.recording = False
        self.stop_event.set()
        self._lock_ui(False)
        self.btn_toggle.config(text="🟢 Начать запись", state="normal")
        self.lbl_timer.config(text="00:00")
        self.lbl_session.config(text="Не начата")
        self._log(f"⏹ Завершено. Кадров: {self.frame_idx}")
        self.root.update_idletasks() # 🔑 Принудительно обновляем UI

    def _record_worker(self):
        timestamps = []
        jpeg_quality = 95
        fps = max(1, self.var_fps.get())
        frame_interval = 1.0 / fps
        last_frame_time = time.time()

        while not self.stop_event.is_set():
            ret, frame = self.cap.read()
            if not ret or self.stop_event.is_set():
                break

            now = time.time()
            wait = frame_interval - (now - last_frame_time)
            if wait > 0:
                #  Проверяем флаг остановки каждые 5мс вместо блокирующего sleep
                sleep_end = time.time() + wait
                while time.time() < sleep_end:
                    if self.stop_event.is_set(): break
                    time.sleep(0.005)
            if self.stop_event.is_set(): break

            last_frame_time = time.time()
            ts_ns = time.time_ns()
            frame_path = self.output_dir / "frames" / f"rgb_{self.frame_idx:06d}.jpg"
            cv2.imwrite(str(frame_path), frame, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
            timestamps.append([self.frame_idx, ts_ns / 1e9])
            self.frame_idx += 1

            if self.frame_idx % 50 == 0:
                try:
                    if shutil.disk_usage(self.base_dir).free < 0.5e9:
                        self.stop_event.set()
                        self._log("⚠️ МАЛО МЕСТА! Авто-стоп...")
                        break
                except: pass
                
        with open(self.output_dir / "timestamps.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["frame_idx", "timestamp_utc_seconds"])
            writer.writerows(timestamps)
            
        self.recording = False
        self.root.after(0, lambda: self._stop_recording()) # 🔑 Безопасный возврат в главный поток

    def _start_preview(self):
        self._update_preview()

    def _update_preview(self):
        if self.cap is not None and self.cap.isOpened() and self.preview_label is not None:
            ret, frame = self.cap.read()
            if ret:
                max_w, max_h = 400, 300
                h, w = frame.shape[:2]
                scale = min(max_w / w, max_h / h)
                new_w, new_h = int(w * scale), int(h * scale)
                frame_resized = cv2.resize(frame, (new_w, new_h))
                frame_rgb = cv2.cvtColor(frame_resized, cv2.COLOR_BGR2RGB)
                img_pil = Image.fromarray(frame_rgb)
                img_tk = ImageTk.PhotoImage(image=img_pil)
                self.preview_label.config(image=img_tk)
                self.preview_label.image = img_tk 
        if not self.stop_event.is_set():
            self.preview_after_id = self.root.after(100, self._update_preview)

    def _stop_preview(self):
        if self.preview_after_id:
            self.root.after_cancel(self.preview_after_id)
            self.preview_after_id = None

    def _start_polling(self):
        self._poll_queue()
        self._update_ui_loop()

    def __del__(self):
        self._stop_preview()
        if self.cap:
            self.cap.release()

if __name__ == "__main__":
    root = tk.Tk()
    app = WebcamRecorderApp(root)
    try:
        root.mainloop()
    finally:
        app._stop_preview()
        if app.cap:
            app.cap.release()