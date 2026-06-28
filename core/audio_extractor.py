import os
from utils.video_utils import get_video_duration, extract_audio


class AudioExtractor:
    def get_audio_duration(self, audio_path):
        """Get duration of audio file."""
        return get_video_duration(audio_path)
    
    def extract_audio_from_video(self, video_path, output_path):
        """Extract audio from video file."""
        return extract_audio(video_path, output_path)
    
    def get_audio_from_folder(self, folder_path):
        """Get list of audio files from folder."""
        audio_exts = (".mp3", ".wav", ".aac", ".flac", ".ogg")
        return [
            os.path.join(folder_path, f)
            for f in os.listdir(folder_path)
            if f.lower().endswith(audio_exts)
        ]
