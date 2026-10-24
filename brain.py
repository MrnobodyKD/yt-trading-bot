import os
import json
import time
from datetime import datetime, timedelta
from openai import OpenAI
from dotenv import load_dotenv

import trade_store
import vision
import risk_math
from executor import TradeExecutor

load_dotenv()

# OpenAI client with aicredits.in
client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL", "https://aicredits.in/v1")
)

# Use cheaper model for brain analysis
MODEL = "gpt-4o-mini"  # Cheap and fast
SMART_MODEL = "gpt-4o"  # For high urgency decisions
CONFIDENCE_THRESHOLD = 0.70
FIXED_LOT_SIZE = 0.01

NERVOUS_WORDS = ["nervous", "not sure", "careful", "risky", "might be wrong", "unsure", "worried"]
CONFIDENT_WORDS = ["perfect", "great", "definitely", "strong", "amazing", "clean setup", "confident"]
BREAKEVEN_WORDS = ["breakeven", "break even", "move sl to entry", "risk free"]
TRAILING_WORDS = ["trail", "trailing", "lock in profit", "lock profit"]
PARTIAL_WORDS = ["take half", "50%", "partial", "scale out", "trim"]
ALREADY_IN_PHRASES = [
    'already in', 'entered earlier', 'been in since', 'from before',
    'i am in', "i'm in", 'holding since', 'my position from',
    'since morning', 'since yesterday', 'previous entry', 'old position',
    'already have', 'already long', 'already short', 'already holding'
]


def normalize_side(side):
    """Convert 'buy'/'sell' to 'long'/'short'"""
    if side in ['buy', 'long']:
        return 'long'
    elif side in ['sell', 'short']:
        return 'short'
    return side


def flatten_vision_data(vision_data):
    """Convert nested vision data to flat structure"""
    if not vision_data:
        return {}
    
    if 'basic_data' not in vision_data:
        return vision_data
    
    basic = vision_data.get('basic_data', {})
    trade_quality = vision_data.get('trade_quality', {})
    chart_analysis = vision_data.get('chart_analysis', {})
    setup = vision_data.get('setup', {})
    
    return {
        'symbol': basic.get('symbol'),
        'entry': basic.get('entry'),
        'sl': basic.get('sl'),
        'tp': basic.get('tp'),
        'side': basic.get('side'),
        'exit_price': basic.get('exit_price'),
        'confidence': trade_quality.get('confidence', 0),
        'trade_score': trade_quality.get('score', 0),
        'is_good_setup': trade_quality.get('is_good_setup', False),
        'chart_visible': chart_analysis.get('chart_visible', False),
        'timeframe': chart_analysis.get('timeframe'),
        'trend': chart_analysis.get('trend'),
        'setup_type': setup.get('type'),
        'setup_basis': setup.get('basis'),
        'trade_strengths': trade_quality.get('strengths', []),
        'trade_weaknesses': trade_quality.get('weaknesses', []),
        'risk_reward': trade_quality.get('risk_reward_ratio'),
        'market_condition': vision_data.get('market_context', {}).get('condition')
    }


class ShortTermMemory:
    """Rolling buffer of transcript chunks"""
    def __init__(self, window_minutes=5):
        self.window_minutes = window_minutes
        self.chunks = []

    def add(self, text):
        self.chunks.append((datetime.now(), text))
        self._trim()

    def _trim(self):
        cutoff = datetime.now() - timedelta(minutes=self.window_minutes)
        self.chunks = [(t, txt) for t, txt in self.chunks if t >= cutoff]

    def get_context_text(self):
        self._trim()
        return " ".join(txt for _, txt in self.chunks)


class CompressedMemory:
    """Smart memory that stores useful context and notes"""
    def __init__(self):
        self.trader_state = "neutral, no strong bias yet"
        self.last_events = []
        self.pending = None
        self.trader_notes = []  # Trader's plans
        self.market_notes = []  # Market observations
        self.setup_notes = []   # Setup details

    def add_event(self, event_text):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.last_events.append(f"{timestamp} - {event_text}")
        self.last_events = self.last_events[-5:]

    def add_note(self, note_type, note_text):
        """Add structured note to memory"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        note = f"[{timestamp}] {note_text}"
        
        if note_type == "plan":
            self.trader_notes.append(note)
            self.trader_notes = self.trader_notes[-3:]
        elif note_type == "market":
            self.market_notes.append(note)
            self.market_notes = self.market_notes[-3:]
        elif note_type == "setup":
            self.setup_notes.append(note)
            self.setup_notes = self.setup_notes[-3:]

    def update_state(self, new_state):
        self.trader_state = new_state

    def set_pending(self, pending_text):
        self.pending = pending_text

    def clear_pending(self):
        self.pending = None

    def to_prompt_text(self):
        events = "\n".join(self.last_events) if self.last_events else "None yet"
        pending = self.pending if self.pending else "None"
        plans = "\n".join(self.trader_notes) if self.trader_notes else "None"
        markets = "\n".join(self.market_notes) if self.market_notes else "None"
        setups = "\n".join(self.setup_notes) if self.setup_notes else "None"
        
        return (
            f"Trader state: {self.trader_state}\n"
            f"Recent events:\n{events}\n"
            f"Trader plans:\n{plans}\n"
            f"Market notes:\n{markets}\n"
            f"Setup notes:\n{setups}\n"
            f"Pending: {pending}"
        )


SYSTEM_PROMPT = """You are a trading signal analyst watching a live-streamed trader's speech transcript.

Your job is to:
1. Detect trade signals (entry, exit, modify)
2. Take smart notes about trader's plans and market views

IMPORTANT: Detect if trader is ALREADY in a position vs entering NEW position:
- "I'm already in BTC" = ALREADY IN POSITION (do NOT enter)
- "I entered BTC earlier" = OLD TRADE (do NOT enter)
- "I've been in this since morning" = OLD POSITION (do NOT enter)
- "I'm entering NOW" = NEW ENTRY (enter)
- "Taking fresh entry" = NEW ENTRY (enter)

Classify each moment into:
- "entry" - trader taking trade RIGHT NOW
- "exit" - trader closing position RIGHT NOW
- "partial_exit" - closing part of position
- "modify" - changing SL/TP
- "breakeven" - moving SL to entry
- "trailing" - enabling trailing stop
- "watch" - analysis or discussion

ALSO DETECT AND NOTE (include in reasoning):
- Future plans: "I'll enter on pullback", "Waiting for retest"
- Market views: "Gold looks bullish", "BTC might dump"
- Setup details: "Entry at support", "Order block at 67000"

Be conservative: casual analysis is "watch", not "entry".

Respond ONLY with valid JSON:
{
  "signal": "entry" | "exit" | "partial_exit" | "modify" | "breakeven" | "trailing" | "watch",
  "urgency": "high" | "medium" | "low",
  "confidence": 0.0-1.0,
  "symbol": "string or null",
  "needs_screenshot": true | false,
  "reasoning": "one short sentence",
  "note_type": "plan" | "market" | "setup" | null,
  "note_text": "brief note if any useful info detected"
}
"""


def think(transcript_context, compressed_memory, open_trades):
    """Smart model selection - cheap first, escalate if urgency"""
    open_trades_text = json.dumps(open_trades, indent=2) if open_trades else "None"

    user_message = f"""RECENT TRANSCRIPT (last ~5 min):
{transcript_context}

MEMORY:
{compressed_memory.to_prompt_text()}

OPEN TRADES:
{open_trades_text}

Analyze the most recent statement in context. Return your JSON decision."""

    try:
        # STEP 1: Use cheap model first
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message}
            ],
            max_tokens=250,
            temperature=0.1
        )
        
        raw_text = response.choices[0].message.content.strip()
        decision = _safe_json_parse(raw_text)
        
        # STEP 2: Check if escalation needed
        signal = decision.get('signal', 'watch')
        urgency = decision.get('urgency', 'low')
        confidence = decision.get('confidence', 0.0)
        
        needs_escalation = (
            urgency == 'high' or
            (signal in ['entry', 'exit', 'modify'] and confidence < 0.85) or
            (signal == 'watch' and urgency == 'medium' and confidence > 0.5)
        )
        
        if needs_escalation:
            print(f"🧠 Escalating to GPT-4o: {signal}, {urgency}, conf {confidence}")
            
            response2 = client.chat.completions.create(
                model=SMART_MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_message}
                ],
                max_tokens=300,
                temperature=0.1
            )
            
            raw_text2 = response2.choices[0].message.content.strip()
            smart_decision = _safe_json_parse(raw_text2)
            
            if smart_decision.get('confidence', 0) > confidence:
                return smart_decision
            else:
                return decision
        else:
            return decision
            
    except Exception as e:
        print(f"❌ Think API error: {e}")
        return _safe_json_parse("")


def _safe_json_parse(text):
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except:
        return {
            "signal": "watch",
            "urgency": "low",
            "confidence": 0.0,
            "symbol": None,
            "needs_screenshot": False,
            "reasoning": "Failed to parse AI response",
            "note_type": None,
            "note_text": None
        }


VALIDATION_PROMPT = """You are validating a trade signal by cross-checking transcript against vision data.

Check:
1. Does the symbol match?
2. Is the entry price reasonable?
3. Does this align with trader's intent?
4. Is this a NEW entry or was trader already in position?

Respond ONLY with valid JSON:
{
  "valid": true | false,
  "confidence": 0.0-1.0,
  "reasoning": "one short sentence"
}
"""


def validate_trade(transcript_snippet, vision_data, decision_signal):
    """Validate trade - use GPT-4o for critical validation"""
    user_message = f"""TRANSCRIPT:
{transcript_snippet}

VISION DATA:
{json.dumps(vision_data, indent=2)}

SIGNAL TYPE: {decision_signal}

Validate this and return JSON."""

    try:
        # Use SMART_MODEL for validation (critical decision)
        response = client.chat.completions.create(
            model=SMART_MODEL,
            messages=[
                {"role": "system", "content": VALIDATION_PROMPT},
                {"role": "user", "content": user_message}
            ],
            max_tokens=100,
            temperature=0.1
        )
        
        raw_text = response.choices[0].message.content.strip()
        return _safe_json_parse_validation(raw_text)
    except Exception as e:
        print(f"❌ Validation error with GPT-4o, falling back to mini")
        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": VALIDATION_PROMPT},
                    {"role": "user", "content": user_message}
                ],
                max_tokens=100,
                temperature=0.1
            )
            raw_text = response.choices[0].message.content.strip()
            return _safe_json_parse_validation(raw_text)
        except:
            return {"valid": False, "confidence": 0.0, "reasoning": "API error"}


def _safe_json_parse_validation(text):
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except:
        return {"valid": False, "confidence": 0.0, "reasoning": "Parse error"}


class Brain:
    def __init__(self):
        self.short_term = ShortTermMemory(window_minutes=5)
        self.compressed = CompressedMemory()
        self.last_audio_time = datetime.now()
        self.executor = TradeExecutor()

    def mark_audio_received(self):
        self.last_audio_time = datetime.now()

    def seconds_since_last_audio(self):
        return (datetime.now() - self.last_audio_time).total_seconds()

    def handle_possible_disconnect(self, silence_threshold_sec=30):
        silence = self.seconds_since_last_audio()
        if silence < silence_threshold_sec:
            return "ok"
        open_trades = trade_store.get_open_trades()
        if not open_trades:
            return "silent_no_open_trades"
        return f"silent_{int(silence)}s_WITH_open_trades_{len(open_trades)}"

    def handle_stream_end(self):
        open_trades = trade_store.get_open_trades()
        if not open_trades:
            return "no_open_trades"
        results = []
        for trade in open_trades:
            pnl = trade.get('pnl', 0)
            if pnl > 30:
                trade_store.set_trailing_flag(trade['trade_id'], True)
                results.append(f"{trade['symbol']}: trailing_enabled")
            elif pnl > 0:
                trade_store.update_trade(trade['trade_id'], sl=trade['entry_price'])
                results.append(f"{trade['symbol']}: breakeven_set")
            else:
                results.append(f"{trade['symbol']}: keeping_sl")
        self.compressed.add_event("Stream ended - handled open trades")
        return "; ".join(results)

    def get_status(self):
        """Get brain status"""
        return {
            'context_entries': len(self.short_term.chunks),
            'key_events': len(self.compressed.last_events),
            'active_trades': len(trade_store.get_open_trades()),
            'last_audio': self.last_audio_time.strftime('%H:%M:%S'),
            'trader_mood': self.compressed.trader_state,
            'trader_notes': len(self.compressed.trader_notes),
            'market_notes': len(self.compressed.market_notes),
            'setup_notes': len(self.compressed.setup_notes)
        }

    def _apply_emotion_adjustment(self, text, decision):
        text_lower = text.lower()
        if any(w in text_lower for w in NERVOUS_WORDS):
            self.compressed.update_state("nervous / uncertain")
            decision["confidence"] = round(decision.get("confidence", 0.0) * 0.8, 3)
        elif any(w in text_lower for w in CONFIDENT_WORDS):
            self.compressed.update_state("confident")
            decision["confidence"] = round(min(decision.get("confidence", 0.0) * 1.1, 1.0), 3)
        return decision

    def _check_already_in_position(self, text):
        """Check if trader mentions already being in a position"""
        text_lower = text.lower()
        for phrase in ALREADY_IN_PHRASES:
            if phrase in text_lower:
                return True
        return False

    def process_transcript_chunk(self, text, video_stream_url):
        self.mark_audio_received()
        self.short_term.add(text)
        context = self.short_term.get_context_text()
        open_trades = trade_store.get_open_trades()

        decision = think(context, self.compressed, open_trades)
        decision = self._apply_emotion_adjustment(text, decision)
        
        # Save notes from AI analysis
        note_type = decision.get('note_type')
        note_text = decision.get('note_text')
        if note_type and note_text:
            self.compressed.add_note(note_type, note_text)
            print(f"📝 Note saved [{note_type}]: {note_text[:50]}")

        # Check if trader mentions already being in a position
        if self._check_already_in_position(text):
            if decision.get('signal') == 'entry':
                decision['signal'] = 'watch'
                decision['reasoning'] = 'Trader already in position - not a new entry'
                self.compressed.add_event("Trader mentioned existing position - tracking only")
                print("📝 Noted: Trader already has position - won't enter")

        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "transcript_chunk": text,
            "decision": decision,
            "action_taken": None
        }

        signal = decision.get("signal", "watch")
        confidence = decision.get("confidence", 0.0)

        if signal == "watch":
            if confidence >= 0.5:
                self.compressed.update_state(decision.get("reasoning", self.compressed.trader_state))
            log_entry["action_taken"] = "memory_updated"
            return log_entry

        if confidence < CONFIDENCE_THRESHOLD:
            log_entry["action_taken"] = "skipped_low_confidence"
            return log_entry

        vision_data = {}
        if decision.get("needs_screenshot", True):
            vision_data = self._capture_and_read(video_stream_url, retry_for_sltp=(signal == "entry"))
            if vision_data is None:
                log_entry["action_taken"] = "screenshot_failed"
                return log_entry

            log_entry["trade_quality"] = {
                "score": vision_data.get('trade_score', 0),
                "is_good_setup": vision_data.get('is_good_setup', False),
                "setup_type": vision_data.get('setup_type'),
                "trend": vision_data.get('trend')
            }

            validation = validate_trade(context, vision_data, signal)
            log_entry["vision_data"] = vision_data
            log_entry["validation"] = validation

            if not validation.get("valid") or validation.get("confidence", 0.0) < CONFIDENCE_THRESHOLD:
                log_entry["action_taken"] = "rejected_validation"
                self._cleanup_screenshot()
                return log_entry

        handlers = {
            "entry": self._handle_entry,
            "exit": self._handle_exit,
            "partial_exit": self._handle_partial_exit,
            "modify": self._handle_modify,
            "breakeven": self._handle_breakeven,
            "trailing": self._handle_trailing,
        }
        handler = handlers.get(signal)
        result = handler(decision, vision_data) if handler else "unknown_signal"

        self._cleanup_screenshot()
        log_entry["action_taken"] = result
        return log_entry

    def _capture_and_read(self, video_stream_url, retry_for_sltp=False):
        try:
            from stream_capture import capture_screenshot
            path = "temp_screenshot.png"
            capture_screenshot(video_stream_url, path)
            raw_data = vision.extract_trade_data(path)
            data = flatten_vision_data(raw_data)
        except Exception:
            return None

        if retry_for_sltp and (not data.get("sl") or not data.get("tp")):
            time.sleep(5)
            try:
                capture_screenshot(video_stream_url, path)
                raw_data2 = vision.extract_trade_data(path)
                data2 = flatten_vision_data(raw_data2)
                for k, v in data2.items():
                    if not data.get(k) and v:
                        data[k] = v
            except Exception:
                pass

        return data

    def _cleanup_screenshot(self, path="temp_screenshot.png"):
        try:
            if os.path.exists(path):
                os.remove(path)
        except:
            pass

    def _find_open_trade(self, symbol, open_trades, prefer="most_recent"):
        matches = [t for t in open_trades if t.get("symbol") == symbol and t.get("status") == "open"]
        if not matches:
            return None
        if prefer == "most_recent":
            return sorted(matches, key=lambda t: t.get("opened_at", ""))[-1]
        return matches[0]

    def _handle_entry(self, decision, vision_data):
        symbol = vision_data.get("symbol") or decision.get("symbol")
        entry = vision_data.get("entry")
        his_sl = vision_data.get("sl")
        his_tp = vision_data.get("tp")
        side = normalize_side(vision_data.get("side", "buy"))

        if not symbol or not entry:
            return "entry_rejected_missing_data"

        # Check if trader was already in this position
        transcript_context = self.short_term.get_context_text().lower()
        already_in_indicators = [
            'already in', 'entered earlier', 'been in', 'from before',
            'since morning', 'since yesterday', 'holding from',
            'previous entry', 'old position', 'already have'
        ]
        
        if any(indicator in transcript_context for indicator in already_in_indicators):
            print("⚠️ Trader was already in position - NOT taking new entry")
            self.compressed.add_event(f"Trader already in {symbol} - skipped")
            return "entry_skipped_already_in_position"

        if vision_data.get('chart_visible') == False:
            return "entry_rejected_chart_not_visible"
        
        trade_score = vision_data.get('trade_score', 0)
        if trade_score and trade_score < 5.0:
            return f"entry_rejected_low_quality_score_{trade_score}"
        
        if vision_data.get('is_good_setup') == False:
            return "entry_rejected_not_good_setup"

        open_trades = trade_store.get_open_trades()
        for t in open_trades:
            if t.get("symbol") == symbol and t.get("status") == "open":
                return f"entry_skipped_already_open_{symbol}"

        your_tp = risk_math.calculate_your_tp(entry, his_tp, side) if his_tp else None
        lot_size = FIXED_LOT_SIZE

        trade_id = trade_store.create_trade(
            symbol=symbol,
            side=side,
            entry=entry,
            his_sl=his_sl,
            his_tp=his_tp,
            your_tp=your_tp,
            lot_size=lot_size
        )

        mt5_ticket = self.executor.execute_entry(
            symbol=symbol,
            side=side,
            sl=his_sl,
            tp=your_tp if your_tp else his_tp,
            lot_size=lot_size
        )

        if mt5_ticket:
            trade_store.update_trade(trade_id, mt5_ticket=mt5_ticket, position_id=mt5_ticket)
            print(f"✅ MT5 execution successful: Ticket {mt5_ticket}")
        else:
            print(f"⚠️ MT5 execution failed for {trade_id}")

        setup_type = vision_data.get('setup_type')
        if setup_type:
            self.compressed.add_event(f"Setup: {setup_type}")

        self.compressed.add_event(f"Entered {symbol} {side} @ {entry}")
        self.compressed.clear_pending()

        return f"trade_created_{trade_id}"

    def _handle_exit(self, decision, vision_data):
        symbol = vision_data.get("symbol") or decision.get("symbol")
        exit_price = vision_data.get("exit_price") or vision_data.get("entry")
        open_trades = trade_store.get_open_trades()

        trade = self._find_open_trade(symbol, open_trades)
        if not trade:
            # We don't have this trade - just note it
            self.compressed.add_event(f"Trader exited {symbol} (we had no position)")
            return f"exit_no_matching_position_{symbol}"

        if trade.get('mt5_ticket'):
            self.executor.execute_exit(trade['mt5_ticket'])

        if not exit_price:
            exit_price = trade.get('current_price') or trade.get('entry_price')

        confidence = vision_data.get('confidence', 0)
        if confidence and confidence < 0.3:
            print(f"⚠️ Low vision confidence for exit: {confidence}")
        
        trade_store.close_trade(trade["trade_id"], exit_price)
        self.compressed.add_event(f"Exited {symbol} @ {exit_price}")
        return f"trade_closed_{trade['trade_id']}"

    def _handle_partial_exit(self, decision, vision_data):
        symbol = vision_data.get("symbol") or decision.get("symbol")
        exit_price = vision_data.get("exit_price") or vision_data.get("entry")
        open_trades = trade_store.get_open_trades()

        trade = self._find_open_trade(symbol, open_trades)
        if not trade:
            self.compressed.add_event(f"Trader partial exit {symbol} (we had no position)")
            return f"partial_exit_no_position_{symbol}"

        if trade.get('mt5_ticket'):
            self.executor.execute_partial_exit(trade['mt5_ticket'], fraction=0.5)

        if not exit_price:
            exit_price = trade.get('current_price') or trade.get('entry_price')

        trade_store.partial_close(trade["trade_id"], exit_price, fraction=0.5)
        self.compressed.add_event(f"Partial exit {symbol} @ {exit_price} (50%)")
        return f"trade_partial_closed_{trade['trade_id']}"

    def _handle_modify(self, decision, vision_data):
        symbol = vision_data.get("symbol") or decision.get("symbol")
        new_sl = vision_data.get("sl")
        new_tp = vision_data.get("tp")
        open_trades = trade_store.get_open_trades()

        if not new_sl and not new_tp:
            return "modify_no_new_values"

        trade = self._find_open_trade(symbol, open_trades)
        if not trade:
            return f"modify_no_matching_open_trade_{symbol}"

        updates = {}
        if new_sl:
            updates['sl'] = new_sl
        if new_tp:
            side = trade.get('side', 'long')
            entry_price = trade.get('entry_price', trade.get('entry'))
            adjusted_tp = risk_math.calculate_your_tp(entry_price, new_tp, side)
            updates['tp'] = adjusted_tp if adjusted_tp else new_tp

        if updates:
            trade_store.update_trade(trade["trade_id"], **updates)
            
            if trade.get('mt5_ticket'):
                self.executor.execute_modify(
                    trade['mt5_ticket'],
                    new_sl=updates.get('sl'),
                    new_tp=updates.get('tp')
                )

        self.compressed.add_event(f"Modified {symbol} SL/TP")
        return f"trade_modified_{trade['trade_id']}"

    def _handle_breakeven(self, decision, vision_data):
        symbol = vision_data.get("symbol") or decision.get("symbol")
        open_trades = trade_store.get_open_trades()

        trade = self._find_open_trade(symbol, open_trades)
        if not trade:
            return f"breakeven_no_matching_open_trade_{symbol}"

        entry_price = trade.get('entry_price', trade.get('entry'))
        trade_store.update_trade(trade["trade_id"], sl=entry_price)
        
        if trade.get('mt5_ticket'):
            self.executor.execute_breakeven(trade['mt5_ticket'])
        
        self.compressed.add_event(f"Moved {symbol} SL to breakeven")
        return f"trade_breakeven_{trade['trade_id']}"

    def _handle_trailing(self, decision, vision_data):
        symbol = vision_data.get("symbol") or decision.get("symbol")
        new_sl = vision_data.get("sl")
        open_trades = trade_store.get_open_trades()

        trade = self._find_open_trade(symbol, open_trades)
        if not trade:
            return f"trailing_no_matching_open_trade_{symbol}"

        if new_sl:
            trade_store.update_trade(trade["trade_id"], sl=new_sl)
            
            if trade.get('mt5_ticket'):
                self.executor.execute_modify(trade['mt5_ticket'], new_sl=new_sl)
        
        trade_store.set_trailing_flag(trade["trade_id"], True)
        self.compressed.add_event(f"Trailing enabled on {symbol}")
        return f"trade_trailing_{trade['trade_id']}"