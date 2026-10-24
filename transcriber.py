import os
import time
from pathlib import Path
from datetime import datetime
from collections import deque
from openai import OpenAI
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

class Transcriber:
    def __init__(self, audio_dir="audio_chunks"):
        self.audio_dir = Path(audio_dir)
        self.audio_dir.mkdir(exist_ok=True)
        
        # Initialize OpenAI client with custom base URL
        self.client = OpenAI(
            api_key=os.getenv('OPENAI_API_KEY'),
            base_url=os.getenv('OPENAI_BASE_URL', 'https://api.openai.com/v1')
        )
        
        # Rolling transcript buffer (keeps last 3 minutes)
        self.transcript_buffer = deque(maxlen=30)  # ~30 chunks of 8 seconds = 4 minutes
        self.full_transcript = []
        
        # Track processed files
        self.processed_files = set()
        
    def transcribe_file(self, audio_file):
        """Transcribe a single audio file using Whisper API"""
        try:
            with open(audio_file, 'rb') as f:
                response = self.client.audio.transcriptions.create(
                    model='whisper-1',
                    file=f,
                    language='en',  # Can change or remove for auto-detect
                    response_format='text'
                )
            
            # If response is a string
            if isinstance(response, str):
                return response.strip()
            # If response is an object
            elif hasattr(response, 'text'):
                return response.text.strip()
            else:
                return str(response).strip()
                
        except Exception as e:
            print(f"❌ Transcription failed for {audio_file.name}: {e}")
            return ""
    
    def process_new_chunks(self):
        """Process any new audio chunks that haven't been transcribed yet"""
        audio_files = sorted(self.audio_dir.glob("chunk_*.wav"))
        new_transcripts = []
        
        for audio_file in audio_files:
            if audio_file.name not in self.processed_files:
                print(f"🔄 Transcribing {audio_file.name}...")
                
                transcript = self.transcribe_file(audio_file)
                
                if transcript:
                    timestamp = datetime.now().strftime("%H:%M:%S")
                    entry = f"[{timestamp}] {transcript}"
                    
                    # Add to rolling buffer
                    self.transcript_buffer.append(entry)
                    self.full_transcript.append(entry)
                    new_transcripts.append(entry)
                    
                    print(f"   📝 {transcript}")
                
                # Mark as processed
                self.processed_files.add(audio_file.name)
                
                # Small delay to avoid rate limits
                time.sleep(0.5)
        
        return new_transcripts
    
    def get_rolling_transcript(self):
        """Get the current rolling transcript (last 3-4 minutes)"""
        return "\n".join(self.transcript_buffer)
    
    def get_full_transcript(self):
        """Get the complete transcript history"""
        return "\n".join(self.full_transcript)
    
    def continuous_transcribe(self, check_interval=10, duration_minutes=5):
        """Continuously check for new audio chunks and transcribe them"""
        print(f"🎯 Starting continuous transcription...")
        print(f"   Checking for new chunks every {check_interval} seconds")
        print(f"   Will run for {duration_minutes} minutes")
        print(f"   Press Ctrl+C to stop\n")
        
        start_time = time.time()
        end_time = start_time + (duration_minutes * 60)
        
        try:
            while time.time() < end_time:
                new_transcripts = self.process_new_chunks()
                
                if new_transcripts:
                    print(f"\n📊 Rolling transcript (last 3 min):")
                    print("=" * 50)
                    print(self.get_rolling_transcript())
                    print("=" * 50)
                    print()
                
                # Wait before checking again
                time.sleep(check_interval)
                
        except KeyboardInterrupt:
            print("\n\n⏹️  Transcription stopped by user")
        finally:
            # Save full transcript
            transcript_file = Path("logs") / f"transcript_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            transcript_file.parent.mkdir(exist_ok=True)
            transcript_file.write_text(self.get_full_transcript())
            print(f"\n💾 Full transcript saved to: {transcript_file}")
            print(f"📊 Total segments transcribed: {len(self.processed_files)}")

# Main execution
if __name__ == "__main__":
    # Create transcriber instance
    transcriber = Transcriber()
    
    # Test mode 1: Transcribe existing chunks
    print("🧪 Testing with existing audio chunks...\n")
    transcriber.process_new_chunks()
    
    # Show what we got
    if transcriber.processed_files:
        print("\n✅ Transcription successful!")
        print("\n📝 Full transcript:")
        print("=" * 50)
        print(transcriber.get_full_transcript())
        print("=" * 50)
    else:
        print("❌ No audio chunks found or transcription failed")