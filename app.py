import os
import sys
import time
import json
import threading
import queue
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, send_from_directory, session
from flask_cors import CORS

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from stream_capture import StreamCapture, capture_screenshot
from transcriber import Transcriber
from brain import Brain
from executor import TradeExecutor
from trade_store import TradeStore

# Load environment
load_dotenv()

DASHBOARD_KEY = os.getenv("DASHBOARD_KEY", "palantir123")
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"
SCREENSHOT_DIR = BASE_DIR / "screenshots"
ACTIVITY_LIMIT = 20

flask_app = Flask(__name__)
flask_app.secret_key = os.getenv("FLASK_SECRET_KEY", DASHBOARD_KEY)
CORS(flask_app)

dashboard_bot = None
dashboard_lock = threading.Lock()
rate_buckets = defaultdict(deque)
login_attempts = defaultdict(list)


def _safe_read_json(path, fallback):
    try:
        path = Path(path)
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return fallback


def _safe_write_json(path, payload, limit=None):
    try:
        path = Path(path)
        path.parent.mkdir(exist_ok=True)
        if limit and isinstance(payload, list):
            payload = payload[-limit:]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    except Exception:
        pass


def _append_json_log(filename, entry, limit=500):
    path = DATA_DIR / filename
    entries = _safe_read_json(path, [])
    if not isinstance(entries, list):
        entries = []
    entries.append(entry)
    _safe_write_json(path, entries, limit=limit)


def _tail_lines(path, limit=50):
    try:
        path = Path(path)
        if not path.exists():
            return []
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return [line.strip() for line in f.readlines()[-limit:] if line.strip()]
    except Exception:
        return []


def _get_trade_data():
    return _safe_read_json(DATA_DIR / "trades.json", {
        "open_trades": {},
        "closed_trades": {},
        "trade_counter": 0,
        "last_updated": None,
    })


def _as_trade_list(value):
    if isinstance(value, dict):
        return list(value.values())
    if isinstance(value, list):
        return value
    return []


def _pnl_value(trade):
    for key in ("pnl", "current_profit", "profit"):
        value = trade.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return 0.0


def _trade_summary():
    data = _get_trade_data()
    open_trades = _as_trade_list(data.get("open_trades", {}))
    closed_trades = _as_trade_list(data.get("closed_trades", {}))
    closed_pnl = sum(_pnl_value(t) for t in closed_trades)
    open_pnl = sum(_pnl_value(t) for t in open_trades)
    wins = [t for t in closed_trades if _pnl_value(t) > 0]
    losses = [t for t in closed_trades if _pnl_value(t) < 0]
    gross_win = sum(_pnl_value(t) for t in wins)
    gross_loss = abs(sum(_pnl_value(t) for t in losses))
    return {
        "open_trades": len(open_trades),
        "closed_trades": len(closed_trades),
        "total_trades": len(open_trades) + len(closed_trades),
        "open_pnl": round(open_pnl, 2),
        "closed_pnl": round(closed_pnl, 2),
        "total_pnl": round(open_pnl + closed_pnl, 2),
        "win_rate": round((len(wins) / len(closed_trades) * 100), 2) if closed_trades else 0,
        "average_win": round(gross_win / len(wins), 2) if wins else 0,
        "average_loss": round(gross_loss / len(losses), 2) if losses else 0,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else (round(gross_win, 2) if gross_win else 0),
        "last_updated": data.get("last_updated"),
    }


def _pnl_series():
    closed = sorted(
        _as_trade_list(_get_trade_data().get("closed_trades", {})),
        key=lambda t: t.get("closed_at") or t.get("opened_at") or "",
    )
    running = 0.0
    points = []
    for trade in closed:
        running += _pnl_value(trade)
        points.append({
            "label": (trade.get("closed_at") or trade.get("opened_at") or "")[:10] or trade.get("trade_id", "Trade"),
            "trade_id": trade.get("trade_id"),
            "pnl": round(_pnl_value(trade), 2),
            "cumulative": round(running, 2),
        })
    return points


def _mask_secret(value):
    if not value:
        return ""
    value = str(value)
    if len(value) <= 4:
        return "****"
    return ("*" * max(4, len(value) - 4)) + value[-4:]


def _dashboard_authenticated():
    supplied = request.headers.get("X-Dashboard-Key") or request.args.get("key")
    return session.get("palantir_auth") is True or supplied == DASHBOARD_KEY


@flask_app.before_request
def _guard_dashboard():
    if request.endpoint in ("index", "api_login", "health"):
        return None

    ip = request.remote_addr or "local"
    now = time.time()
    bucket = rate_buckets[ip]
    while bucket and now - bucket[0] > 1:
        bucket.popleft()
    bucket.append(now)
    if len(bucket) > 10:
        return jsonify({"ok": False, "error": "rate_limited"}), 429

    if request.path.startswith("/api/") or request.path.startswith("/screenshots/"):
        if not _dashboard_authenticated():
            return jsonify({"ok": False, "error": "unauthorized"}), 401
    return None


def _get_bot():
    global dashboard_bot
    with dashboard_lock:
        if dashboard_bot is None:
            dashboard_bot = TradingBotApp(os.getenv("YOUTUBE_URL"))
        return dashboard_bot


def _bot_snapshot():
    bot = dashboard_bot
    stream = getattr(bot, "stream_capture", None) if bot else None
    brain_obj = getattr(bot, "brain", None) if bot else None
    started_at = getattr(bot, "started_at", None) if bot else None
    started_ts = getattr(bot, "started_ts", None) if bot else None
    return {
        "running": bool(getattr(bot, "is_running", False)),
        "chunk_count": getattr(bot, "chunk_count", 0) if bot else 0,
        "started_at": started_at,
        "uptime_seconds": int(time.time() - started_ts) if started_ts else 0,
        "youtube_url": getattr(bot, "youtube_url", os.getenv("YOUTUBE_URL", "")) if bot else os.getenv("YOUTUBE_URL", ""),
        "stream_url": getattr(stream, "stream_url", None) if stream else None,
        "brain": brain_obj.get_status() if brain_obj else {},
    }


def _activity_feed(limit=ACTIVITY_LIMIT):
    lines = _tail_lines(LOG_DIR / "app.log", limit)
    return [{"time": line[1:9] if line.startswith("[") else "", "message": line} for line in lines[-limit:]][::-1]

class TradingBotApp:
    def __init__(self, youtube_url=None):
        self.youtube_url = youtube_url or os.getenv('YOUTUBE_URL')
        
        # Initialize components
        print('🚀 Initializing Trading Bot...')
        print('=' * 50)
        
        self.stream_capture = StreamCapture(self.youtube_url)
        self.transcriber = Transcriber()
        self.brain = Brain()
        self.executor = TradeExecutor()
        self.trade_store = TradeStore()
        
        # Queues
        self.audio_queue = queue.Queue(maxsize=5)
        self.transcript_queue = queue.Queue()
        
        # Threads
        self.capture_thread = None
        self.transcribe_thread = None
        self.brain_thread = None
        
        # State
        self.is_running = False
        self.chunk_count = 0
        self.started_at = None
        self.started_ts = None
        self.processed_files = set()
        self.last_transcript_text = None
        self.last_transcript_time = 0
        self.recent_transcripts = []
        
        # Logging
        self.log_dir = Path('logs')
        self.log_dir.mkdir(exist_ok=True)
        self.app_log = self.log_dir / 'app.log'
        
    def log(self, message):
        """Log message"""
        timestamp = datetime.now().strftime('%H:%M:%S')
        log_msg = f'[{timestamp}] {message}'
        print(log_msg)
        
        try:
            with open(self.app_log, 'a') as f:
                f.write(log_msg + '\n')
        except:
            pass
    
    def capture_worker(self):
        """Thread 1: Capture audio chunks"""
        self.log('🎙️ Audio capture started')
        
        while self.is_running:
            try:
                self.chunk_count += 1
                chunk_file = self.stream_capture.capture_audio_chunk()
                
                if chunk_file:
                    self.audio_queue.put(chunk_file, timeout=5)
                    self.log(f'Captured chunk {self.chunk_count}')
                    
            except queue.Full:
                self.log('⚠️ Audio queue full - dropping oldest')
                try:
                    old_chunk = self.audio_queue.get_nowait()
                    Path(old_chunk).unlink()
                except:
                    pass
            except Exception as e:
                self.log(f'❌ Capture error: {e}')
                time.sleep(1)
    
    def transcribe_worker(self):
        """Thread 2: Transcribe audio chunks"""
        self.log('🔄 Transcription started')
        
        while self.is_running:
            try:
                audio_file = self.audio_queue.get(timeout=10)
                audio_path = Path(audio_file)
                try:
                    file_key = str(audio_path.resolve())
                except Exception:
                    file_key = str(audio_path)

                if file_key in self.processed_files:
                    self.log(f'Skipping duplicate audio file: {audio_path.name}')
                    try:
                        audio_path.unlink(missing_ok=True)
                    except Exception:
                        pass
                    continue

                self.processed_files.add(file_key)
                if len(self.processed_files) > 50:
                    self.processed_files.clear()
                    self.log('Cleared processed audio file cache')
                
                # Transcribe
                transcript = self.transcriber.transcribe_file(audio_file)
                
                if transcript:
                    normalized_transcript = " ".join(transcript.split()).strip().lower()
                    now = time.time()
                    if (
                        normalized_transcript
                        and normalized_transcript == self.last_transcript_text
                        and now - self.last_transcript_time <= 5
                    ):
                        self.log('Skipping duplicate transcript within 5 seconds')
                    else:
                        self.last_transcript_text = normalized_transcript
                        self.last_transcript_time = now
                        self.transcript_queue.put(transcript)
                        _append_json_log("transcripts.json", {
                            "timestamp": datetime.now().isoformat(),
                            "text": transcript,
                        })
                        self.log(f'📝 {transcript[:60]}...')
                
                # Delete audio file
                audio_path.unlink(missing_ok=True)
                
            except queue.Empty:
                continue
            except Exception as e:
                self.log(f'❌ Transcription error: {e}')
    
    def brain_worker(self):
        """Thread 3: Process transcripts with brain"""
        self.log('🧠 Brain processing started')
        
        while self.is_running:
            try:
                transcript = self.transcript_queue.get(timeout=10)
                normalized_transcript = " ".join(transcript.split()).strip().lower()
                if normalized_transcript in self.recent_transcripts:
                    self.log('Skipping duplicate transcript in brain worker')
                    continue
                
                # Process with brain
                result = self.brain.process_transcript_chunk(
                    transcript, 
                    self.stream_capture.stream_url
                )
                if normalized_transcript:
                    self.recent_transcripts.append(normalized_transcript)
                    self.recent_transcripts = self.recent_transcripts[-10:]
                
                if result and result.get('action_taken'):
                    _append_json_log("brain_decisions.json", result)
                    self.log(f'Brain action: {result["action_taken"]}')
                
            except queue.Empty:
                # Check stream health
                status = self.brain.handle_possible_disconnect()
                if status != 'ok':
                    self.log(f'⚠️ Stream status: {status}')
                continue
            except Exception as e:
                self.log(f'❌ Brain error: {e}')
    
    def start(self):
        """Start the trading bot"""
        print()
        print('🚀 STARTING TRADING BOT')
        print('=' * 50)
        
        # Get stream URL first
        if not self.stream_capture.get_stream_url():
            print('❌ Failed to get stream URL')
            return
        
        self.is_running = True
        self.started_at = datetime.now().isoformat()
        self.started_ts = time.time()
        
        # Start threads
        self.capture_thread = threading.Thread(target=self.capture_worker, daemon=True)
        self.transcribe_thread = threading.Thread(target=self.transcribe_worker, daemon=True)
        self.brain_thread = threading.Thread(target=self.brain_worker, daemon=True)
        
        self.capture_thread.start()
        self.transcribe_thread.start()
        self.brain_thread.start()
        
        self.log('✅ All systems running')
        self.log('   Press Ctrl+C to stop')
        print()
        
        try:
            # Main loop - monitor everything
            while self.is_running:
                time.sleep(10)
                
                # Show status every minute
                if self.chunk_count % 6 == 0 and self.chunk_count > 0:
                    status = self.brain.get_status()
                    self.log(f'📊 Status: {status}')
                    
        except KeyboardInterrupt:
            print()
            self.log('⏹️ Shutting down...')
        finally:
            self.stop()

    def start_background(self, youtube_url=None):
        """Start the trading bot workers without blocking the Flask server."""
        if self.is_running:
            return True

        if youtube_url:
            self.youtube_url = youtube_url
            self.stream_capture = StreamCapture(youtube_url)

        if not self.stream_capture.get_stream_url():
            self.log("Failed to get stream URL")
            return False

        self.is_running = True
        self.started_at = datetime.now().isoformat()
        self.started_ts = time.time()
        self.capture_thread = threading.Thread(target=self.capture_worker, daemon=True)
        self.transcribe_thread = threading.Thread(target=self.transcribe_worker, daemon=True)
        self.brain_thread = threading.Thread(target=self.brain_worker, daemon=True)
        self.capture_thread.start()
        self.transcribe_thread.start()
        self.brain_thread.start()
        self.log("All systems running from dashboard")
        return True

    def stop(self, shutdown_components=True):
        """Stop everything"""
        self.is_running = False
        
        if self.capture_thread:
            self.capture_thread.join(timeout=5)
        if self.transcribe_thread:
            self.transcribe_thread.join(timeout=5)
        if self.brain_thread:
            self.brain_thread.join(timeout=5)
        
        # Cleanup
        self.stream_capture.cleanup_old_chunks(keep_last=0)
        
        if shutdown_components:
            # Handle open trades if stream ended
            result = self.brain.handle_stream_end()
            self.log(f'Stream end handling: {result}')
            
            self.trade_store.shutdown()
            self.executor.shutdown()
        
        self.log('✅ Shutdown complete')
        print()
        print('📊 Final Summary:')
        print(f'   Chunks captured: {self.chunk_count}')
        print(f'   Open trades: {len(self.trade_store.get_open_trades())}')
        print(f'   Closed trades: {len(self.trade_store.get_closed_trades())}')


@flask_app.route("/")
def index():
    return render_template("index.html")


@flask_app.route("/health")
def health():
    return jsonify({"ok": True, "time": datetime.now().isoformat()})


@flask_app.route("/api/login", methods=["POST"])
def api_login():
    ip = request.remote_addr or "local"
    now = time.time()
    attempts = login_attempts[ip]
    login_attempts[ip] = [attempt for attempt in attempts if now - attempt < 60]
    if len(login_attempts[ip]) >= 5:
        return jsonify({"ok": False, "error": "too_many_login_attempts"}), 429

    key = (request.get_json(silent=True) or {}).get("key", "")
    if key == DASHBOARD_KEY:
        login_attempts.pop(ip, None)
        session["palantir_auth"] = True
        return jsonify({"ok": True})
    login_attempts[ip].append(now)
    return jsonify({"ok": False, "error": "invalid_key"}), 401


@flask_app.route("/api/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"ok": True})


@flask_app.route("/api/status")
def api_status():
    payload = _bot_snapshot()
    payload["stats"] = _trade_summary()
    return jsonify(payload)


@flask_app.route("/api/stream/info")
def api_stream_info():
    return jsonify(_bot_snapshot())


@flask_app.route("/api/stream/start", methods=["GET", "POST"])
def api_stream_start():
    global dashboard_bot
    url = request.args.get("url")
    if request.is_json:
        url = ((request.get_json(silent=True) or {}).get("url", url) or "").strip()
    url = (url or os.getenv("YOUTUBE_URL", "")).strip()

    with dashboard_lock:
        if dashboard_bot and dashboard_bot.is_running:
            dashboard_bot.log("Dashboard start requested - stopping existing stream first")
            dashboard_bot.stop(shutdown_components=False)

        dashboard_bot = TradingBotApp(url)
        ok = dashboard_bot.start_background()
        if not ok:
            dashboard_bot.log("Dashboard start request failed")

    return jsonify({"ok": ok, "stream": _bot_snapshot()}), 200 if ok else 400


@flask_app.route("/api/stream/stop", methods=["GET", "POST"])
def api_stream_stop():
    global dashboard_bot
    bot = dashboard_bot
    if bot:
        bot.log("Dashboard stop requested")
        bot.stop(shutdown_components=False)
        bot.started_at = None
        bot.started_ts = None
    return jsonify({"ok": True, "stream": _bot_snapshot()})


@flask_app.route("/api/stream/change", methods=["POST"])
def api_stream_change():
    global dashboard_bot
    payload = request.get_json(silent=True) or {}
    url = (payload.get("url") or "").strip()
    if not url:
        return jsonify({"ok": False, "error": "missing_url"}), 400

    with dashboard_lock:
        if dashboard_bot:
            dashboard_bot.log(f"Dashboard stream change requested: {url}")
            dashboard_bot.stop(shutdown_components=False)
        dashboard_bot = TradingBotApp(url)
        ok = dashboard_bot.start_background()
        if not ok:
            dashboard_bot.log("Dashboard stream change failed")

    return jsonify({"ok": ok, "stream": _bot_snapshot()}), 200 if ok else 400


@flask_app.route("/api/stats/quick")
def api_stats_quick():
    return jsonify(_trade_summary())


@flask_app.route("/api/trades")
def api_trades():
    data = _get_trade_data()
    open_trades = _as_trade_list(data.get("open_trades", {}))
    closed_trades = _as_trade_list(data.get("closed_trades", {}))
    return jsonify({"open": open_trades, "closed": closed_trades, "all": open_trades + closed_trades})


@flask_app.route("/api/trades/open")
def api_trades_open():
    return jsonify(_as_trade_list(_get_trade_data().get("open_trades", {})))


@flask_app.route("/api/trades/closed")
def api_trades_closed():
    return jsonify(_as_trade_list(_get_trade_data().get("closed_trades", {})))


@flask_app.route("/api/trades/pnl")
def api_trades_pnl():
    summary = _trade_summary()
    summary["series"] = _pnl_series()
    return jsonify(summary)


@flask_app.route("/api/analytics/costs")
def api_analytics_costs():
    transcripts = _safe_read_json(DATA_DIR / "transcripts.json", [])
    decisions = _safe_read_json(DATA_DIR / "brain_decisions.json", [])
    screenshots = list(SCREENSHOT_DIR.glob("*")) if SCREENSHOT_DIR.exists() else []
    counts = {
        "whisper": len(transcripts),
        "gpt-4o-mini": len(decisions),
        "gpt-4o": sum(1 for d in decisions if d.get("validation") or d.get("vision_data")),
        "vision": len(screenshots),
    }
    estimates = {
        "whisper": counts["whisper"] * 0.006,
        "gpt-4o-mini": counts["gpt-4o-mini"] * 0.001,
        "gpt-4o": counts["gpt-4o"] * 0.01,
        "vision": counts["vision"] * 0.005,
    }
    total = round(sum(estimates.values()), 4)
    uptime_hours = max((_bot_snapshot().get("uptime_seconds", 0) / 3600), 1 / 60)
    return jsonify({
        "counts": counts,
        "estimated_costs": {k: round(v, 4) for k, v in estimates.items()},
        "total_estimated": total,
        "cost_per_hour": round(total / uptime_hours, 4),
    })


@flask_app.route("/api/analytics/requests")
def api_analytics_requests():
    costs = api_analytics_costs().get_json()
    return jsonify(costs.get("counts", {}))


@flask_app.route("/api/analytics/transcripts")
def api_analytics_transcripts():
    entries = _safe_read_json(DATA_DIR / "transcripts.json", [])
    if not entries:
        entries = [{"timestamp": "", "text": line} for line in _tail_lines(LOG_DIR / "app.log", 50) if "📝" in line]
    return jsonify(entries[-50:][::-1])


@flask_app.route("/api/analytics/vision")
def api_analytics_vision():
    files = []
    if SCREENSHOT_DIR.exists():
        for path in sorted(SCREENSHOT_DIR.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)[:40]:
            if path.is_file():
                files.append({
                    "name": path.name,
                    "url": f"/screenshots/{path.name}",
                    "size": path.stat().st_size,
                    "updated_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
                })
    return jsonify(files)


@flask_app.route("/screenshots/<path:filename>")
def dashboard_screenshot(filename):
    return send_from_directory(SCREENSHOT_DIR, filename)


@flask_app.route("/api/analytics/decisions")
def api_analytics_decisions():
    return jsonify(_safe_read_json(DATA_DIR / "brain_decisions.json", [])[-50:][::-1])


@flask_app.route("/api/memory/compressed")
def api_memory_compressed():
    bot = dashboard_bot
    memory = getattr(getattr(bot, "brain", None), "compressed", None)
    if not memory:
        return jsonify({
            "running": False,
            "trader_state": "offline",
            "last_events": [],
            "pending": None,
            "trader_notes": [],
            "market_notes": [],
            "setup_notes": [],
        })
    return jsonify({
        "running": bool(getattr(bot, "is_running", False)),
        "trader_state": memory.trader_state,
        "last_events": memory.last_events,
        "pending": memory.pending,
        "trader_notes": memory.trader_notes,
        "market_notes": memory.market_notes,
        "setup_notes": memory.setup_notes,
    })


@flask_app.route("/api/memory/context")
def api_memory_context():
    bot = dashboard_bot
    short_term = getattr(getattr(bot, "brain", None), "short_term", None)
    chunks = []
    if short_term:
        short_term._trim()
        chunks = [{"time": t.isoformat(), "text": text} for t, text in short_term.chunks]
    return jsonify({"chunks": chunks, "text": " ".join(c["text"] for c in chunks)})


@flask_app.route("/api/activity/feed")
def api_activity_feed():
    return jsonify(_activity_feed())


@flask_app.route("/api/settings")
def api_settings():
    bot = dashboard_bot
    return jsonify({
        "dashboard_key": _mask_secret(DASHBOARD_KEY),
        "openai_api_key": _mask_secret(os.getenv("OPENAI_API_KEY")),
        "openai_base_url": os.getenv("OPENAI_BASE_URL", ""),
        "youtube_url": getattr(bot, "youtube_url", os.getenv("YOUTUBE_URL", "")) if bot else os.getenv("YOUTUBE_URL", ""),
        "confidence_threshold": getattr(__import__("brain"), "CONFIDENCE_THRESHOLD", 0.70),
        "fixed_lot_size": getattr(__import__("brain"), "FIXED_LOT_SIZE", 0.01),
        "chunk_duration": os.getenv("CHUNK_DURATION", "10"),
        "mt5_connected": bool(getattr(getattr(bot, "trade_store", None), "mt5_connected", False)) if bot else False,
    })


@flask_app.route("/api/settings/update", methods=["POST"])
def api_settings_update():
    payload = request.get_json(silent=True) or {}
    import brain as brain_module

    if "confidence_threshold" in payload:
        brain_module.CONFIDENCE_THRESHOLD = float(payload["confidence_threshold"])
    if "fixed_lot_size" in payload:
        brain_module.FIXED_LOT_SIZE = float(payload["fixed_lot_size"])
    if "youtube_url" in payload:
        os.environ["YOUTUBE_URL"] = str(payload["youtube_url"]).strip()
        if dashboard_bot and not dashboard_bot.is_running:
            dashboard_bot.youtube_url = os.environ["YOUTUBE_URL"]
            dashboard_bot.stream_capture = StreamCapture(dashboard_bot.youtube_url)
    if "chunk_duration" in payload:
        os.environ["CHUNK_DURATION"] = str(payload["chunk_duration"])

    return jsonify({"ok": True, "settings": api_settings().get_json()})


# Main execution
if __name__ == '__main__':
    flask_app.run(host='127.0.0.1', port=int(os.getenv("PORT", "5000")), debug=False)
