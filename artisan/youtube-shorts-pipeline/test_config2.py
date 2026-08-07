import sys
sys.path.insert(0, 'src')
from config import config
print(f"Background mode: {config.background_mode}")
print(f"Caption style: {config.caption_style}")
print(f"Video preset: {config.video_preset}")
print(f"Video CRF: {config.video_crf}")