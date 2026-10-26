Spectrum Visualizer
===================
IF YOU HAVE RUSSIAN OR OTHER keyb, To open the settings, switch your keyboard layout to English — otherwise, it won't open

Windows audio spectrum visualizer with a small terminal-based settings menu.

Requirements
------------
Python 3 with Tkinter, plus NumPy and SoundCard:

    py -m pip install numpy soundcard

Run
---
Double-click `spectrum.pyw` to open the visualizer without a console window.
System audio is captured by default. Use `py spectrum.pyw --mic` for a microphone.

Settings
--------
Press Alt+K in the visualizer. Choose a setting with the arrow keys and press
Enter. The bar and background color options open a palette with 23 presets and
a custom HEX option. Adjust Gradient strength to control how much lighter the
top of each bar becomes. Settings are saved in the user's AppData folder.

The project is a single Python application file; no photo assets or Pillow
dependency are needed.
