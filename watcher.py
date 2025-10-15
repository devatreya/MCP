import time
import os
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import shutil

FOLDER = "generated_scripts/"
DEST_FILE = "auto_runner/fusion_auto_run.py"

class Handler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory or not event.src_path.endswith(".py"):
            return
        print(f"New script detected: {event.src_path}")
        shutil.copy(event.src_path, DEST_FILE)
        print(f"Updated {DEST_FILE}")

if __name__ == "__main__":
    os.makedirs(FOLDER, exist_ok=True)
    observer = Observer()
    observer.schedule(Handler(), path=FOLDER, recursive=False)
    observer.start()
    print("Watching for new scripts...")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer
