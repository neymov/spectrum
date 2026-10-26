"""A CAVA-inspired audio visualizer with a configurable desktop window."""

from __future__ import annotations

import argparse
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import tkinter as tk
from pathlib import Path

try:
    import numpy as np
    import soundcard as sc
except ImportError as exc:
    error = f"Could not load Python dependencies.\n\n{exc}\n\nInstall them with:\npy -m pip install numpy soundcard"
    if os.name == "nt":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, error, "Spectrum Visualizer — Startup Error", 0x10)
    else:
        print(error)
    raise SystemExit(1) from exc


CONFIG_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "SpectrumVisualizer"
CONFIG_PATH = CONFIG_DIR / "config.json"
DEFAULTS = {
    "window_width": 1150,
    "window_height": 550,
    "bars": 67,
    "bar_width": 11,
    "gap": 4,
    "height_scale": 0.95,
    "sensitivity": 0.6,
    "smoothness": 0.1,
    "bar_color": "#ff0006",
    "background_color": "#090b12",
    "gradient_strength": 0.28,
    "mirror": True,
    "fps": 30,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CAVA-inspired desktop audio visualizer")
    parser.add_argument("--device", help="substring of an audio device name")
    parser.add_argument("--mic", action="store_true", help="use microphone instead of system audio")
    parser.add_argument("--list-devices", action="store_true", help="show available audio devices")
    parser.add_argument("--settings", action="store_true", help="open terminal settings menu")
    return parser.parse_args()


def load_config() -> dict:
    config = DEFAULTS.copy()
    try:
        saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        config.update({key: saved[key] for key in DEFAULTS if key in saved})
    except (OSError, ValueError, TypeError):
        pass
    return config


def save_config(config: dict) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")
    except OSError:
        pass


def pick_device(name: str | None, microphone: bool):
    devices = sc.all_microphones(include_loopback=not microphone)
    if not microphone:
        devices = [device for device in devices if getattr(device, "isloopback", False)]
    if name:
        devices = [device for device in devices if name.casefold() in device.name.casefold()]
    if not devices:
        raise RuntimeError("No audio input was found. Try --mic or --list-devices.")
    return devices[0]


def spectrum(samples: np.ndarray, count: int) -> np.ndarray:
    mono = samples.mean(axis=1) if samples.ndim > 1 else samples
    mono = mono * np.hanning(len(mono))
    power = np.abs(np.fft.rfft(mono))
    edges = np.geomspace(1, max(2, len(power) - 1), count + 1).astype(int)
    values = np.zeros(count)
    for index in range(count):
        band = power[edges[index] : max(edges[index] + 1, edges[index + 1])]
        if band.size:
            values[index] = np.sqrt(np.mean(band * band))
    return np.log1p(values * 20)


def rgb(hex_color: str) -> tuple[int, int, int]:
    value = hex_color.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


def show_startup_error(message: str) -> None:
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, "Spectrum Visualizer — Startup Error", 0x10)
            return
        except (AttributeError, OSError):
            pass
    print(message)


class Visualizer:
    def __init__(self, device, microphone: bool):
        self.device = device
        self.microphone = microphone
        self.config = load_config()
        self.config_mtime = self.config_file_mtime()
        self.root = tk.Tk()
        self.root.title("Spectrum Visualizer")
        self.root.geometry(f"{int(self.config['window_width'])}x{int(self.config['window_height'])}")
        self.root.minsize(500, 300)
        self.root.configure(bg=self.config["background_color"])
        self.canvas = tk.Canvas(self.root, highlightthickness=0, bg=self.config["background_color"])
        self.canvas.pack(fill="both", expand=True)
        self.level_queue: queue.Queue = queue.Queue(maxsize=1)
        self.smoothed = np.zeros(max(4, int(self.config["bars"])))
        self.last_draw = 0.0
        self.root.bind_all("<Alt-k>", self.open_settings)
        self.root.bind_all("<Alt-K>", self.open_settings)
        self.root.bind_all("<Escape>", lambda _event: self.close())
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        threading.Thread(target=self.audio_loop, daemon=True).start()
        self.tick()

    def audio_loop(self):
        try:
            channels = 1 if self.microphone else 2
            with self.device.recorder(samplerate=44100, channels=channels, blocksize=2048) as recorder:
                while True:
                    samples = recorder.record(numframes=2048)
                    values = spectrum(samples, max(4, int(self.config["bars"])))
                    try:
                        self.level_queue.put_nowait(values)
                    except queue.Full:
                        try:
                            self.level_queue.get_nowait()
                        except queue.Empty:
                            pass
                        self.level_queue.put_nowait(values)
        except Exception:
            return

    def open_settings(self, _event=None):
        try:
            creation_flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
            executable = Path(sys.executable)
            if executable.name.lower() == "pythonw.exe":
                console_python = executable.with_name("python.exe")
                if console_python.exists():
                    executable = console_python
                elif shutil.which("py"):
                    executable = Path(shutil.which("py"))
            command = [str(executable), str(Path(__file__).resolve()), "--settings"]
            if executable.name.lower() == "py.exe":
                command = [str(executable), "-3", str(Path(__file__).resolve()), "--settings"]
            subprocess.Popen(command,
                             creationflags=creation_flags)
        except OSError:
            pass
        return "break"

    @staticmethod
    def config_file_mtime():
        try:
            return CONFIG_PATH.stat().st_mtime_ns
        except OSError:
            return None

    def reload_external_config(self):
        mtime = self.config_file_mtime()
        if mtime is None or mtime == self.config_mtime:
            return
        self.config_mtime = mtime
        self.config = load_config()
        self.root.configure(bg=self.config["background_color"])
        self.canvas.configure(bg=self.config["background_color"])
        self.root.geometry(f"{int(self.config['window_width'])}x{int(self.config['window_height'])}")
        

    def draw(self):
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        if width < 10 or height < 10:
            return
        self.canvas.delete("bars")
        self.canvas.configure(bg=self.config["background_color"])

        count = max(4, int(self.config["bars"]))
        available_height = max(10, int((height - 32) * float(self.config["height_scale"])))
        requested_bar_width = max(2, int(self.config["bar_width"]))
        gap = max(0, int(self.config["gap"]))
        # Keep the configured number of bars and distribute them across the
        # available width, so maximizing/resizing never leaves a tiny cluster.
        stride = width / count
        max_bar = max(1, min(requested_bar_width, int(stride) - gap))
        center_mode = bool(self.config["mirror"])
        if center_mode:
            if count % 2:
                count -= 1
            half = (count + 1) // 2
            sample = np.interp(np.linspace(0, len(self.smoothed) - 1, half),
                               np.arange(len(self.smoothed)), self.smoothed)
            values = np.concatenate((sample[::-1], sample))[:count]
        else:
            values = np.interp(np.linspace(0, len(self.smoothed) - 1, count),
                               np.arange(len(self.smoothed)), self.smoothed)
        color = rgb(self.config["bar_color"])
        gradient_strength = float(self.config["gradient_strength"])
        base_y = height - 20
        for index, value in enumerate(values):
            x_center = (index + 0.5) * stride
            x1 = round(x_center - max_bar / 2)
            x2 = round(x_center + max_bar / 2)
            bar_height = min(available_height, max(1, round(float(value) * available_height)))
            y1 = base_y - bar_height
            steps = min(6, max(1, bar_height // 6))
            for step in range(steps):
                # Selected color at the bottom, gently mixed with white at the top.
                lighten = gradient_strength * (1 - (step + 0.5) / steps)
                shade = tuple(round(channel + (255 - channel) * lighten) for channel in color)
                fill = "#%02x%02x%02x" % shade
                top = y1 + step * bar_height / steps
                bottom = y1 + (step + 1) * bar_height / steps
                self.canvas.create_rectangle(x1, top, x2, bottom + 1, fill=fill,
                                             outline="", tags="bars")

    def tick(self):
        self.reload_external_config()
        try:
            while True:
                values = self.level_queue.get_nowait()
                if len(values) != len(self.smoothed):
                    self.smoothed = np.zeros_like(values)
                sensitivity = float(self.config["sensitivity"])
                normalized = np.clip(values * sensitivity / 5.0, 0, 1)
                smooth = float(self.config["smoothness"])
                self.smoothed = self.smoothed * smooth + normalized * (1 - smooth)
        except queue.Empty:
            pass
        now = time.monotonic()
        frame_rate = min(int(self.config["fps"]), 30)
        if now - self.last_draw >= 1 / max(10, frame_rate):
            self.draw()
            self.last_draw = now
        self.root.after(15, self.tick)

    def close(self):
        self.root.destroy()

    def run(self):
        self.root.mainloop()


SETTING_ROWS = [
    ("Window width (px)", "window_width", "number", 500, 3840, 50),
    ("Window height (px)", "window_height", "number", 300, 2160, 50),
    ("Number of bars", "bars", "number", 8, 256, 4),
    ("Bar thickness (px)", "bar_width", "number", 1, 60, 1),
    ("Gap between bars (px)", "gap", "number", 0, 50, 1),
    ("Visualizer height (window fraction)", "height_scale", "number", 0.2, 1.0, 0.05),
    ("Sensitivity", "sensitivity", "number", 0.2, 4.0, 0.1),
    ("Smoothing", "smoothness", "number", 0.0, 0.95, 0.05),
    ("Frame rate", "fps", "number", 10, 60, 1),
    ("Bar color", "bar_color", "color"),
    ("Background color", "background_color", "color"),
    ("Gradient strength", "gradient_strength", "number", 0.0, 0.65, 0.05),
    ("Mirror bass in the center", "mirror", "toggle"),
    ("Quit", None, "exit"),
]

COLOR_PALETTE = [
    ("Crimson", "#e11d48"), ("Red", "#ff3030"), ("Coral", "#ff6b5e"),
    ("Orange", "#ff7a18"), ("Amber", "#ffb020"), ("Gold", "#ffd43b"),
    ("Lime", "#a3e635"), ("Green", "#22c55e"), ("Emerald", "#10b981"),
    ("Teal", "#14b8a6"), ("Cyan", "#06d6e0"), ("Ice blue", "#7dd3fc"),
    ("Sky blue", "#38bdf8"), ("Blue", "#3b82f6"), ("Indigo", "#6366f1"),
    ("Violet", "#8b5cf6"), ("Purple", "#a855f7"), ("Magenta", "#d946ef"),
    ("Pink", "#ec4899"), ("Rose", "#fb7185"), ("White", "#f8fafc"),
    ("Silver", "#aab4c8"), ("Slate", "#64748b"), ("Custom HEX", None),
]


def setting_value(config, row):
    label, key, kind, *_ = row
    if kind == "toggle":
        return "ON" if config[key] else "OFF"
    if key == "height_scale":
        return f"{round(float(config[key]) * 100)}%"
    return str(config[key])


def choose_color(current: str, msvcrt) -> str | None:
    choices = [color for _name, color in COLOR_PALETTE]
    if current in choices:
        selected = choices.index(current)
    else:
        current_rgb = rgb(current)
        selected = min(
            range(len(COLOR_PALETTE) - 1),
            key=lambda index: sum((a - b) ** 2 for a, b in zip(current_rgb, rgb(choices[index]))),
        )
    while True:
        os.system("cls")
        print("CHOOSE COLOR\n")
        for index, (name, color) in enumerate(COLOR_PALETTE):
            value = color if color else "Enter a custom HEX value"
            print(("> " if index == selected else "  ") + f"{name:<18} {value}")
        key = msvcrt.getwch()
        if key in ("\x00", "\xe0"):
            arrow = msvcrt.getwch()
            if arrow == "H":
                selected = (selected - 1) % len(COLOR_PALETTE)
            elif arrow == "P":
                selected = (selected + 1) % len(COLOR_PALETTE)
        elif key in ("\x1b", "q", "Q"):
            return None
        elif key == "\r":
            name, color = COLOR_PALETTE[selected]
            if color:
                return color
            value = input("HEX color (#RRGGBB): ").strip()
            if re.fullmatch(r"#[0-9a-fA-F]{6}", value):
                return value.lower()


def settings_menu() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        import msvcrt
    except (AttributeError, ImportError):
        print("The terminal settings menu is available on Windows.")
        return 1

    config = load_config()
    selected = 0
    def draw_menu():
        os.system("cls")
        print("SPECTRUM CONFIG\n")
        for index, row in enumerate(SETTING_ROWS):
            label, key, kind, *_ = row
            value = setting_value(config, row) if key else ""
            print(("> " if index == selected else "  ") + f"{label:<47} {value}")

    def edit(row, direction=None):
        label, key, kind, *limits = row
        if kind == "toggle":
            config[key] = not config[key]
        elif kind == "number":
            low, high, step = limits
            if direction is None:
                raw = input(f"{label} [{config[key]}]: ").strip()
                if not raw:
                    return
                try:
                    value = float(raw)
                except ValueError:
                    return
            else:
                value = float(config[key]) + direction * step
            value = min(high, max(low, value))
            config[key] = int(round(value)) if step == 1 else round(value, 2)
        elif kind == "color":
            value = choose_color(config[key], msvcrt)
            if value is None:
                return
            config[key] = value
        save_config(config)

    draw_menu()
    while True:
        key_press = msvcrt.getwch()
        if key_press in ("\x00", "\xe0"):
            arrow = msvcrt.getwch()
            if arrow == "H":
                selected = (selected - 1) % len(SETTING_ROWS)
            elif arrow == "P":
                selected = (selected + 1) % len(SETTING_ROWS)
            elif arrow in ("K", "M") and SETTING_ROWS[selected][2] == "number":
                edit(SETTING_ROWS[selected], -1 if arrow == "K" else 1)
            draw_menu()
            continue
        if key_press in ("\x1b", "q", "Q"):
            return 0
        row = SETTING_ROWS[selected]
        if key_press in ("\r", " "):
            if row[2] == "exit":
                return 0
            edit(row)
            draw_menu()


def main() -> int:
    args = parse_args()
    if args.settings:
        return settings_menu()
    if args.list_devices:
        print("System audio:")
        for device in sc.all_microphones(include_loopback=True):
            if getattr(device, "isloopback", False):
                print(f"  {device.name}")
        print("Microphones:")
        for device in sc.all_microphones(include_loopback=False):
            print(f"  {device.name}")
        return 0
    try:
        device = pick_device(args.device, args.mic)
    except Exception as exc:
        show_startup_error(f"Could not find an audio capture device.\n\n{exc}")
        return 1
    try:
        app = Visualizer(device, args.mic)
        app.run()
    except Exception:
        show_startup_error(traceback.format_exc())
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
