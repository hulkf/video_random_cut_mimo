import sys
sys.path.insert(0, '.')
from utils.video_utils import get_video_duration
from gui.config import load_config
import os

config = load_config()
vm = config.get('video_mix', {})
output_folder = vm.get('output_folder', '')
video_folder = vm.get('video_folder', '')

print('Output folder:', output_folder)
print('Video folder:', video_folder)

if os.path.exists(output_folder):
    files = [f for f in os.listdir(output_folder) if f.endswith('.mp4')]
    print('\nOutput files:')
    for f in files:
        path = os.path.join(output_folder, f)
        dur = get_video_duration(path)
        size = os.path.getsize(path)
        print('  %s: %.2fs, %.1fMB' % (f[:50], dur, size/1024/1024))

if os.path.exists(video_folder):
    files = [f for f in os.listdir(video_folder) if f.endswith('.mp4')]
    print('\nBase video files:')
    for f in files[:3]:
        path = os.path.join(video_folder, f)
        dur = get_video_duration(path)
        print('  %s: %.2fs' % (f[:50], dur))
