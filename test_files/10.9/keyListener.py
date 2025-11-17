# async_app.py
import asyncio

from pynput import keyboard  # 需要安装：pip install pynput


class KeyListener:
    def __init__(self):
        self.p_pressed = False
        self.listener = None
        
    def on_press(self, key):
        try:
            if key == keyboard.KeyCode.from_char('p'):
                self.p_pressed = True
        except AttributeError:
            pass
            
    def start(self):
        self.listener = keyboard.Listener(on_press=self.on_press)
        self.listener.start()
        
    def stop(self):
        if self.listener:
            self.listener.stop()
