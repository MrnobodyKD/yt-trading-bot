import yt_dlp
import subprocess
import os
import time
import threading
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

class StreamCapture:
    def __init__(self, youtube_url=None):
        self.youtube_url = youtube_url or os.getenv('YOUTUBE_URL')
        self.stream_url = None
        self.video_url = None
        self.chunk_duration = 8  # seconds per chunk
        self.output_dir = Path("audio_chunks")
        self.output_dir.mkdir(exist_ok=True)
        self.is_running = False
        self.chunk_count = 0
        self.last_url_refresh = 0  # Track when URL was last fetched
        self.url_refresh_interval = 180  # Refresh every 3 minutes
        
    def get_stream_url(self):
        """Get the actual stream URL from YouTube"""
        print("🔍 Fetching stream URL...")
        
        ydl_opts = {
            'format': 'bestaudio/best',
            'quiet': True,
            'no_warnings': True,
            'live_from_start': False,
            'extract_flat': False,
        }
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(self.youtube_url, download=False)
                
                if 'url' in info:
                    self.stream_url = info['url']
                elif 'formats' in info and len(info['formats']) > 0:
                    # Get best audio format
                    audio_formats = [f for f in info['formats'] if f.get('acodec') != 'none']
                    if audio_formats:
                        self.stream_url = audio_formats[-1]['url']
                    else:
                        self.stream_url = info['formats'][-1]['url']
                
                title = info.get('title', 'Unknown Stream')
                print(f"✅ Connected to: {title}")
                print(f"   Channel: {info.get('channel', 'Unknown')}")
                print(f"   Live: {info.get('is_live', False)}")
                
                # Also get video URL for screenshots
                self.get_video_url()
                
                # Track when we refreshed
                self.last_url_refresh = time.time()
                
                return True
                
        except Exception as e:
            print(f"❌ Failed to get stream URL: {e}")
            print("   Make sure the YouTube URL is correct and the stream is live.")
            return False
    
    def get_video_url(self):
        """Get video stream URL for screenshots"""
        ydl_opts = {
            'format': 'bestvideo[ext=mp4]/best[ext=mp4]/best',
            'quiet': True,
            'no_warnings': True,
        }
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(self.youtube_url, download=False)
                if 'url' in info:
                    self.video_url = info['url']
                elif 'formats' in info and len(info['formats']) > 0:
                    # Get best video format
                    video_formats = [f for f in info['formats'] if f.get('vcodec') != 'none']
                    if video_formats:
                        self.video_url = video_formats[-1]['url']
                    else:
                        self.video_url = self.stream_url  # Fallback to audio URL
                return self.video_url
        except:
            self.video_url = self.stream_url  # Fallback to audio URL
            return self.video_url
    
    def _refresh_url_if_needed(self):
        """Refresh stream URL if it's been too long (YouTube URLs expire)"""
        if time.time() - self.last_url_refresh > self.url_refresh_interval:
            print("🔄 Stream URL may be expired, refreshing...")
            return self.get_stream_url()
        return True
    
    def capture_audio_chunk(self):
        """Capture a single audio chunk using ffmpeg (HLS-optimized)"""
        # Refresh URL if needed to avoid expiration
        self._refresh_url_if_needed()
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = self.output_dir / f"chunk_{self.chunk_count:04d}_{timestamp}.wav"
        
        # HLS-optimized command with proper flags for live streams
        command = [
            'ffmpeg',
            '-i', self.stream_url,
            '-t', str(self.chunk_duration),
            '-ac', '1',           # Mono audio
            '-ar', '16000',       # 16kHz sample rate (good for speech)
            '-f', 'wav',
            '-y',                # Overwrite output file
            # HLS SPECIFIC FLAGS for live streams:
            '-live_start_index', '-1',                          # Start near live edge
            '-allowed_extensions', 'ALL',                       # Allow all segment types
            '-protocol_whitelist', 'file,http,https,tcp,tls,crypto',
            '-fflags', 'nobuffer',                              # Don't buffer
            '-flags', 'low_delay',                              # Low latency mode
            '-analyzeduration', '10000000',                     # 10 sec analyze
            '-probesize', '10000000',                           # 10 sec probe
            str(output_file)
        ]
        
        try:
            result = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.chunk_duration + 30   # More buffer for HLS
            )
            
            if output_file.exists() and output_file.stat().st_size > 0:
                size_kb = output_file.stat().st_size / 1024
                print(f"✅ Chunk {self.chunk_count:04d}: {size_kb:.1f} KB captured")
                return str(output_file)  # Return string path
            else:
                print(f"⚠️  Chunk {self.chunk_count:04d}: No audio captured")
                # Try refreshing URL on failure
                if self.chunk_count < 3:
                    print("🔄 Attempting URL refresh...")
                    self.get_stream_url()
                return None
                
        except subprocess.TimeoutExpired:
            print(f"⚠️  Chunk {self.chunk_count:04d}: Timeout - refreshing URL")
            # Refresh URL on timeout
            self.get_stream_url()
            return None
        except Exception as e:
            print(f"❌ Chunk {self.chunk_count:04d} failed: {e}")
            return None
    
    def capture_screenshot(self, output_path="temp_screenshot.png"):
        """Capture a screenshot from the stream"""
        # Refresh URL if needed
        self._refresh_url_if_needed()
        
        # If output_path is a directory, create filename
        if output_path.endswith('/') or output_path.endswith('\\') or Path(output_path).is_dir():
            screenshot_dir = Path(output_path)
            screenshot_dir.mkdir(exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            screenshot_file = screenshot_dir / f"screenshot_{timestamp}.jpg"
        else:
            # Use exact file path provided
            screenshot_file = Path(output_path)
            screenshot_file.parent.mkdir(exist_ok=True, parents=True)
        
        # Use video URL if available, otherwise use stream URL
        url_to_use = self.video_url if self.video_url else self.stream_url
        
        if not url_to_use:
            print("❌ No stream URL available. Call get_stream_url() first.")
            return None
        
        command = [
            'ffmpeg',
            '-i', url_to_use,
            '-frames:v', '1',
            '-q:v', '2',
            '-y',
            # HLS flags for screenshots too
            '-protocol_whitelist', 'file,http,https,tcp,tls,crypto',
            str(screenshot_file)
        ]
        
        try:
            subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
            if screenshot_file.exists() and screenshot_file.stat().st_size > 0:
                print(f"📸 Screenshot saved: {screenshot_file.name}")
                return str(screenshot_file)  # Return string path
            else:
                print(f"⚠️  Screenshot failed: No image captured")
                return None
        except Exception as e:
            print(f"❌ Screenshot failed: {e}")
            return None
    
    def start_continuous_capture(self, duration_minutes=2):
        """Start capturing audio chunks continuously"""
        if not self.get_stream_url():
            return
        
        print(f"\n🎙️  Starting audio capture...")
        print(f"   Chunk duration: {self.chunk_duration} seconds")
        print(f"   Output directory: {self.output_dir}")
        print(f"   Press Ctrl+C to stop\n")
        
        self.is_running = True
        start_time = time.time()
        end_time = start_time + (duration_minutes * 60)
        
        try:
            while self.is_running and time.time() < end_time:
                self.chunk_count += 1
                chunk_file = self.capture_audio_chunk()
                
                # Small delay between chunks
                time.sleep(0.5)
                
        except KeyboardInterrupt:
            print("\n\n⏹️  Capture stopped by user")
        finally:
            self.is_running = False
            total_chunks = len(list(self.output_dir.glob("chunk_*.wav")))
            print(f"\n📊 Summary: {total_chunks} chunks captured")
            print(f"   Total audio: {total_chunks * self.chunk_duration} seconds")
    
    def cleanup_old_chunks(self, keep_last=10):
        """Delete old chunks, keeping only the most recent ones"""
        chunks = sorted(self.output_dir.glob("chunk_*.wav"))
        if len(chunks) > keep_last:
            for chunk in chunks[:-keep_last]:
                chunk.unlink()
                print(f"🗑️  Deleted old chunk: {chunk.name}")


# ============================================================
# STANDALONE FUNCTION FOR BRAIN.PY
# ============================================================

def capture_screenshot(video_stream_url, output_path="temp_screenshot.png"):
    """
    Standalone function to capture screenshot
    Used by brain.py - takes stream URL and output path
    """
    from pathlib import Path
    
    Path(output_path).parent.mkdir(exist_ok=True, parents=True)
    
    command = [
        'ffmpeg',
        '-i', video_stream_url,
        '-frames:v', '1',
        '-q:v', '2',
        '-y',
        '-protocol_whitelist', 'file,http,https,tcp,tls,crypto',
        output_path
    ]
    
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15
        )
        
        if Path(output_path).exists() and Path(output_path).stat().st_size > 0:
            return True
        return False
        
    except Exception as e:
        print(f"❌ Screenshot failed: {e}")
        return False


# Main execution
if __name__ == "__main__":
    # Get YouTube URL from .env or user input
    youtube_url = os.getenv('YOUTUBE_URL')
    
    if not youtube_url or 'YOUR_LIVE_STREAM_ID' in youtube_url:
        youtube_url = input("Enter YouTube live stream URL: ").strip()
    
    if youtube_url:
        # Create capture instance
        capture = StreamCapture(youtube_url)
        
        # Get stream URL first
        if capture.get_stream_url():
            print("\n🎙️ Testing audio capture...")
            # Test single chunk first
            test_file = capture.capture_audio_chunk()
            if test_file:
                print(f"✅ Single chunk test PASSED: {test_file}")
                
                # Test screenshot
                print("\n📸 Testing screenshot capture...")
                screenshot = capture.capture_screenshot("test_screenshot.png")
                if screenshot:
                    print(f"✅ Screenshot test PASSED: {screenshot}")
                
                # Now do continuous capture
                print("\n🎙️ Starting continuous capture...")
                capture.start_continuous_capture(duration_minutes=2)
            else:
                print("❌ Single chunk test FAILED")
        else:
            print("❌ Failed to get stream URL")
    else:
        print("❌ No YouTube URL provided. Add it to .env file.")