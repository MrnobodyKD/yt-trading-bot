import yt_dlp
import subprocess
import os
import time
import threading
import queue
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI
import json

# Load environment variables
load_dotenv()

class RealTimeTradingBot:
    def __init__(self, youtube_url=None):
        self.youtube_url = youtube_url or os.getenv('YOUTUBE_URL')
        self.stream_url = None
        self.chunk_duration = 8  # 8 seconds per chunk
        self.output_dir = Path("audio_chunks")
        self.output_dir.mkdir(exist_ok=True)
        self.screenshot_dir = Path("screenshots")
        self.screenshot_dir.mkdir(exist_ok=True)
        
        # Queues for coordination
        self.audio_queue = queue.Queue(maxsize=2)  # Max 2 chunks waiting
        self.transcript_queue = queue.Queue()  # Transcribed text ready for processing
        
        # Thread control
        self.is_running = False
        self.capture_thread = None
        self.transcribe_thread = None
        
        # Counters
        self.chunk_count = 0
        self.transcript_count = 0
        
        # Initialize OpenAI client
        self.client = OpenAI(
            api_key=os.getenv('OPENAI_API_KEY'),
            base_url=os.getenv('OPENAI_BASE_URL', 'https://api.openai.com/v1')
        )
        
        # Rolling transcript buffer (last 3 minutes)
        self.transcript_buffer = []
        self.max_buffer_size = 25  # ~25 chunks * 8 sec = 200 seconds
        
    def get_stream_url(self):
        """Get the actual stream URL from YouTube"""
        print("🔍 Fetching stream URL...")
        
        ydl_opts = {
            'format': 'bestaudio/best',
            'quiet': True,
            'no_warnings': True,
            'live_from_start': False,
        }
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(self.youtube_url, download=False)
                self.stream_url = info['url']
                title = info.get('title', 'Unknown Stream')
                print(f"✅ Connected to: {title}")
                return True
        except Exception as e:
            print(f"❌ Failed to get stream URL: {e}")
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
                return info['url']
        except:
            return None
    
    def capture_audio_worker(self):
        """Thread 1: Continuously capture audio chunks"""
        print("🎙️  Audio capture thread started")
        
        while self.is_running:
            try:
                self.chunk_count += 1
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                output_file = self.output_dir / f"chunk_{self.chunk_count:04d}_{timestamp}.wav"
                
                command = [
                    'ffmpeg',
                    '-i', self.stream_url,
                    '-t', str(self.chunk_duration),
                    '-ac', '1',
                    '-ar', '16000',
                    '-f', 'wav',
                    '-y',
                    str(output_file)
                ]
                
                start_time = time.time()
                subprocess.run(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=self.chunk_duration + 5
                )
                
                if output_file.exists() and output_file.stat().st_size > 0:
                    # Put in queue for transcription
                    self.audio_queue.put(output_file, timeout=5)
                    print(f"🎵 Captured chunk {self.chunk_count:04d} ({time.time()-start_time:.1f}s)")
                else:
                    print(f"⚠️  Chunk {self.chunk_count:04d} empty")
                    
            except queue.Full:
                print("⚠️  Audio queue full - transcription is lagging!")
                # Drop oldest chunk to catch up
                try:
                    old_chunk = self.audio_queue.get_nowait()
                    old_chunk.unlink()  # Delete old chunk
                    print(f"🗑️  Dropped old chunk to catch up")
                except:
                    pass
                    
            except Exception as e:
                print(f"❌ Capture error: {e}")
                time.sleep(1)  # Small delay before retry
    
    def transcribe_worker(self):
        """Thread 2: Transcribe chunks as soon as they're available"""
        print("🔄 Transcription thread started")
        
        while self.is_running:
            try:
                # Get chunk from queue (wait max 10 seconds)
                audio_file = self.audio_queue.get(timeout=10)
                
                # Transcribe immediately
                start_time = time.time()
                transcript = self.transcribe_file(audio_file)
                
                if transcript:
                    self.transcript_count += 1
                    timestamp = datetime.now().strftime("%H:%M:%S")
                    
                    # Add to rolling buffer
                    entry = f"[{timestamp}] {transcript}"
                    self.transcript_buffer.append(entry)
                    
                    # Keep buffer size limited
                    if len(self.transcript_buffer) > self.max_buffer_size:
                        self.transcript_buffer.pop(0)
                    
                    # Put in transcript queue for processing
                    self.transcript_queue.put({
                        'timestamp': timestamp,
                        'text': transcript,
                        'chunk_file': audio_file.name
                    })
                    
                    latency = time.time() - start_time
                    print(f"📝 {transcript[:80]}... ({latency:.1f}s)")
                else:
                    print(f"⚠️  Empty transcript for {audio_file.name}")
                
                # Delete processed audio file to save space
                audio_file.unlink()
                
            except queue.Empty:
                # No chunks waiting, just wait
                continue
            except Exception as e:
                print(f"❌ Transcription error: {e}")
    
    def transcribe_file(self, audio_file, max_retries=2):
        """Transcribe a single audio file with retry logic"""
        for attempt in range(max_retries):
            try:
                with open(audio_file, 'rb') as f:
                    response = self.client.audio.transcriptions.create(
                        model='whisper-1',
                        file=f,
                        language='en',
                        response_format='text'
                    )
                
                # Parse response
                if isinstance(response, str):
                    # Try to parse JSON if it looks like JSON
                    if response.startswith('{'):
                        try:
                            data = json.loads(response)
                            return data.get('text', response).strip()
                        except:
                            return response.strip()
                    return response.strip()
                elif hasattr(response, 'text'):
                    return response.text.strip()
                else:
                    return str(response).strip()
                    
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(1)
                else:
                    return ""
        return ""
    
    def capture_screenshot(self):
        """Capture screenshot on demand"""
        try:
            video_url = self.get_video_url()
            if not video_url:
                return None
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            screenshot_file = self.screenshot_dir / f"screenshot_{timestamp}.jpg"
            
            command = [
                'ffmpeg',
                '-i', video_url,
                '-frames:v', '1',
                '-q:v', '2',
                '-y',
                str(screenshot_file)
            ]
            
            subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
            
            if screenshot_file.exists():
                print(f"📸 Screenshot captured: {screenshot_file.name}")
                return screenshot_file
            return None
            
        except Exception as e:
            print(f"❌ Screenshot error: {e}")
            return None
    
    def start(self, duration_minutes=30):
        """Start the real-time bot"""
        if not self.get_stream_url():
            return
        
        print(f"\n🚀 Starting REAL-TIME trading bot...")
        print(f"   Audio chunks: {self.chunk_duration} seconds")
        print(f"   Transcription: Immediate")
        print(f"   Screenshot: On-demand")
        print(f"   Press Ctrl+C to stop\n")
        
        self.is_running = True
        
        # Start threads
        self.capture_thread = threading.Thread(target=self.capture_audio_worker)
        self.transcribe_thread = threading.Thread(target=self.transcribe_worker)
        
        self.capture_thread.start()
        self.transcribe_thread.start()
        
        try:
            # Main loop - process transcripts
            start_time = time.time()
            end_time = start_time + (duration_minutes * 60)
            
            while self.is_running and time.time() < end_time:
                try:
                    # Check for new transcripts
                    transcript_data = self.transcript_queue.get(timeout=1)
                    
                    # HERE IS WHERE WE'LL ADD BRAIN.PY LOGIC LATER
                    # For now, just display rolling transcript
                    print(f"\n📊 Rolling Transcript (last 3 min):")
                    print("=" * 60)
                    for line in self.transcript_buffer[-5:]:  # Show last 5 entries
                        print(line)
                    print("=" * 60)
                    
                except queue.Empty:
                    # No new transcripts, just wait
                    continue
                    
        except KeyboardInterrupt:
            print("\n\n⏹️  Stopping bot...")
        finally:
            self.is_running = False
            self.capture_thread.join(timeout=5)
            self.transcribe_thread.join(timeout=5)
            print(f"\n📊 Final stats:")
            print(f"   Chunks captured: {self.chunk_count}")
            print(f"   Chunks transcribed: {self.transcript_count}")
            print(f"   Transcript buffer size: {len(self.transcript_buffer)}")
    
    def cleanup(self):
        """Clean up old audio files"""
        for file in self.output_dir.glob("chunk_*.wav"):
            file.unlink()
        print("🧹 Cleaned up audio chunks")

# Main execution
if __name__ == "__main__":
    # Get YouTube URL
    youtube_url = os.getenv('YOUTUBE_URL')
    
    if not youtube_url or 'YOUR_LIVE_STREAM_ID' in youtube_url:
        youtube_url = input("Enter YouTube live stream URL: ").strip()
    
    if youtube_url:
        bot = RealTimeTradingBot(youtube_url)
        bot.start(duration_minutes=30)  # Run for 30 minutes
    else:
        print("❌ No YouTube URL provided")