"""
vision.py - Complete Trading Chart Analysis
Uses OpenAI-compatible API (aicredits.in) for vision
Includes SL/TP zone estimation from Long/Short position tool
"""

import os
import json
import base64
import time
from datetime import datetime
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# Initialize OpenAI client with aicredits.in
client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL", "https://aicredits.in/v1")
)

# Vision model - GPT-4o for best chart reading
VISION_MODEL = "gpt-4o"

# System prompt for complete trade analysis
SYSTEM_PROMPT = """You are an expert trading analyst viewing a screenshot from a live crypto trading stream.

Your job is to extract COMPLETE trading information from what you see.

Analyze and extract:

1. BASIC TRADE DATA:
   - Symbol/pair (BTCUSD, ETHUSD, XAUUSD, etc.)
   - Entry price (the middle line of Long/Short tool)
   - Stop loss level (from red zone)
   - Take profit level (from green zone)
   - Trade direction (buy/sell)

2. CHART ANALYSIS:
   - Timeframe (1m, 5m, 15m, 1h, etc.)
   - Current trend (bullish/bearish/sideways)
   - Market structure (higher highs, lower lows, etc.)
   - Chart pattern visible

3. SETUP IDENTIFICATION:
   - Setup type (order block, FVG, breakout, support/resistance, etc.)
   - What basis the setup is forming on
   - Confirmation signals visible

4. TRADE QUALITY ASSESSMENT:
   - Score 0-10
   - Is this a good setup?
   - Strengths of the trade
   - Weaknesses/risks
   - Risk/reward ratio if calculable

5. MARKET CONTEXT:
   - Market condition (trending, ranging, volatile)
   - Volume indication
   - Key levels nearby

6. SCREENSHOT QUALITY:
   - Is the chart actually visible?
   - Is it clear enough to read?
   - Confidence in your extraction

SPECIAL SL/TP ZONE READING (CRITICAL):
Traders use the Long/Short position tool showing:
- 🟩 Green rectangle/zone ABOVE entry = Take Profit (TP)
- 🟥 Red rectangle/zone BELOW entry = Stop Loss (SL)
- Middle line = Entry price

For each colored zone, you MUST:
1. Look at the y-axis price labels
2. Identify which two grid lines/price levels the zone is between
3. Determine if the zone edge is closer to upper or lower price
4. Estimate the exact price value
5. Use midpoint if zone is evenly between two lines
6. If the zone spans multiple grid lines, use the zone's center

Example:
- Entry at 4042.92
- Red SL zone between 4010 and 4020, zone edge closer to 4012
  → estimated SL = 4012
- Green TP zone between 4070 and 4090, zone edge closer to 4085
  → estimated TP = 4085

ALWAYS provide for each zone:
- between_lower: lower boundary price
- between_upper: upper boundary price
- closer_to: "lower" | "upper" | "middle"
- estimated_value: your best estimate
- confidence: 0.0-1.0

IMPORTANT:
- Be precise with all numbers
- If something is not visible or unclear, set to null
- Provide honest assessment - don't fake data
- If no chart is visible (camera on trader's face), set chart_visible to false
- Focus on the MAIN chart if multiple are visible
- Read everything from ONE screenshot - no zooming needed

Return ONLY valid JSON with this structure:
{
  "basic_data": {
    "symbol": "string or null",
    "entry": number or null,
    "sl": number or null,
    "tp": number or null,
    "side": "buy" | "sell" | null,
    "exit_price": number or null,
    "sl_estimation": {
      "between_lower": number or null,
      "between_upper": number or null,
      "closer_to": "lower" | "upper" | "middle" | null,
      "estimated_value": number or null,
      "confidence": 0.0-1.0
    },
    "tp_estimation": {
      "between_lower": number or null,
      "between_upper": number or null,
      "closer_to": "lower" | "upper" | "middle" | null,
      "estimated_value": number or null,
      "confidence": 0.0-1.0
    }
  },
  "chart_analysis": {
    "timeframe": "string or null",
    "trend": "bullish" | "bearish" | "sideways" | null,
    "structure": "string or null",
    "pattern": "string or null",
    "chart_visible": true | false,
    "is_clear": true | false
  },
  "setup": {
    "type": "string or null",
    "basis": "string or null",
    "confirmation": "string or null",
    "setup_quality": "high" | "medium" | "low" | null
  },
  "trade_quality": {
    "score": 0.0-10.0,
    "is_good_setup": true | false,
    "confidence": 0.0-1.0,
    "strengths": ["string"],
    "weaknesses": ["string"],
    "risk_reward_ratio": number or null
  },
  "market_context": {
    "condition": "trending" | "ranging" | "volatile" | null,
    "volume": "increasing" | "decreasing" | "average" | null,
    "key_levels_nearby": ["string"],
    "liquidity_areas": "string or null"
  }
}
"""


def extract_trade_data(screenshot_path):
    """
    Main function - extract complete trade data from screenshot
    
    Args:
        screenshot_path: Path to screenshot file
    
    Returns:
        Dictionary with complete trade analysis
    """
    try:
        # Check if file exists
        if not os.path.exists(screenshot_path):
            print(f"❌ Screenshot not found: {screenshot_path}")
            return _empty_data()
        
        # Check file size
        file_size = os.path.getsize(screenshot_path)
        if file_size < 1000:  # Less than 1KB = probably blank
            print(f"❌ Screenshot too small: {file_size} bytes")
            return _empty_data()
        
        # Encode image
        image_data = _encode_image(screenshot_path)
        if not image_data:
            return _empty_data()
        
        # Call Vision API
        response = _call_vision_api(image_data)
        
        # Parse response
        data = _parse_response(response)
        
        # Validate and clean data
        data = _validate_data(data)
        
        return data
        
    except Exception as e:
        print(f"❌ Vision extraction failed: {e}")
        return _empty_data()


def extract_sl_tp_only(screenshot_path):
    """
    Specifically extract only SL/TP levels (for updates)
    
    Args:
        screenshot_path: Path to screenshot
    
    Returns:
        Dictionary with sl and tp only
    """
    data = extract_trade_data(screenshot_path)
    basic = data.get('basic_data', {})
    
    return {
        'sl': basic.get('sl'),
        'tp': basic.get('tp'),
        'symbol': basic.get('symbol'),
        'sl_estimation': basic.get('sl_estimation'),
        'tp_estimation': basic.get('tp_estimation'),
        'confidence': data.get('trade_quality', {}).get('confidence', 0)
    }


def extract_exit_price(screenshot_path):
    """
    Specifically extract current price for exit
    
    Args:
        screenshot_path: Path to screenshot
    
    Returns:
        Dictionary with exit price
    """
    data = extract_trade_data(screenshot_path)
    basic = data.get('basic_data', {})
    
    return {
        'exit_price': basic.get('entry') or basic.get('exit_price'),
        'symbol': basic.get('symbol'),
        'confidence': data.get('trade_quality', {}).get('confidence', 0)
    }


def _encode_image(image_path):
    """Convert image to base64"""
    try:
        with open(image_path, 'rb') as f:
            image_data = base64.b64encode(f.read()).decode()
        
        # Determine media type
        if image_path.endswith('.png'):
            media_type = 'image/png'
        elif image_path.endswith('.jpg') or image_path.endswith('.jpeg'):
            media_type = 'image/jpeg'
        else:
            media_type = 'image/png'  # Default
        
        return {
            'data': image_data,
            'media_type': media_type
        }
        
    except Exception as e:
        print(f"❌ Image encoding failed: {e}")
        return None


def _call_vision_api(image_data):
    """Call OpenAI Vision API"""
    try:
        # Create data URL
        data_url = f"data:{image_data['media_type']};base64,{image_data['data']}"
        
        response = client.chat.completions.create(
            model=VISION_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": data_url
                            }
                        },
                        {
                            "type": "text",
                            "text": "Analyze this trading chart screenshot. Pay special attention to the Long/Short position tool showing entry, SL zone, and TP zone. Estimate SL/TP values from the colored zones."
                        }
                    ]
                }
            ],
            max_tokens=1000,
            temperature=0.1
        )
        
        return response.choices[0].message.content.strip()
        
    except Exception as e:
        print(f"❌ Vision API call failed: {e}")
        return None


def _parse_response(response_text):
    """Parse JSON from API response"""
    if not response_text:
        return _empty_data()
    
    # Clean markdown fences if present
    if response_text.startswith('```'):
        response_text = response_text.strip('`')
        if response_text.startswith('json'):
            response_text = response_text[4:]
    
    try:
        return json.loads(response_text)
    except json.JSONDecodeError as e:
        print(f"❌ JSON parsing failed: {e}")
        print(f"Raw response: {response_text[:200]}")
        return _empty_data()


def _validate_data(data):
    """Validate and clean extracted data"""
    
    # Ensure all required keys exist
    required_structure = {
        'basic_data': {},
        'chart_analysis': {},
        'setup': {},
        'trade_quality': {},
        'market_context': {}
    }
    
    for key, default in required_structure.items():
        if key not in data:
            data[key] = default
    
    # Ensure basic_data has all fields
    basic_defaults = {
        'symbol': None,
        'entry': None,
        'sl': None,
        'tp': None,
        'side': None,
        'exit_price': None,
        'sl_estimation': {},
        'tp_estimation': {}
    }
    for key, default in basic_defaults.items():
        if key not in data['basic_data']:
            data['basic_data'][key] = default
    
    # Ensure estimation fields have defaults
    estimation_defaults = {
        'between_lower': None,
        'between_upper': None,
        'closer_to': None,
        'estimated_value': None,
        'confidence': 0
    }
    
    for est_key in ['sl_estimation', 'tp_estimation']:
        if not data['basic_data'].get(est_key):
            data['basic_data'][est_key] = {}
        for key, default in estimation_defaults.items():
            if key not in data['basic_data'][est_key]:
                data['basic_data'][est_key][key] = default
    
    # Use estimated values if sl/tp is None but estimation exists
    if not data['basic_data'].get('sl') and data['basic_data'].get('sl_estimation', {}).get('estimated_value'):
        data['basic_data']['sl'] = data['basic_data']['sl_estimation']['estimated_value']
    
    if not data['basic_data'].get('tp') and data['basic_data'].get('tp_estimation', {}).get('estimated_value'):
        data['basic_data']['tp'] = data['basic_data']['tp_estimation']['estimated_value']
    
    # Ensure chart_analysis has all fields
    chart_defaults = {
        'timeframe': None,
        'trend': None,
        'structure': None,
        'pattern': None,
        'chart_visible': False,
        'is_clear': False
    }
    for key, default in chart_defaults.items():
        if key not in data['chart_analysis']:
            data['chart_analysis'][key] = default
    
    # Normalize side
    side = data['basic_data'].get('side')
    if side in ['buy', 'long', 'BUY', 'Long']:
        data['basic_data']['side'] = 'buy'
    elif side in ['sell', 'short', 'SELL', 'Short']:
        data['basic_data']['side'] = 'sell'
    
    # Normalize symbol
    symbol = data['basic_data'].get('symbol')
    if symbol:
        symbol = symbol.upper().replace('/', '').replace('-', '')
        data['basic_data']['symbol'] = symbol
    
    # Ensure setup has defaults
    setup_defaults = {
        'type': None,
        'basis': None,
        'confirmation': None,
        'setup_quality': None
    }
    for key, default in setup_defaults.items():
        if key not in data['setup']:
            data['setup'][key] = default
    
    # Ensure trade_quality has defaults
    quality_defaults = {
        'score': 0,
        'is_good_setup': False,
        'confidence': 0,
        'strengths': [],
        'weaknesses': [],
        'risk_reward_ratio': None
    }
    for key, default in quality_defaults.items():
        if key not in data['trade_quality']:
            data['trade_quality'][key] = default
    
    # Ensure market_context has defaults
    market_defaults = {
        'condition': None,
        'volume': None,
        'key_levels_nearby': [],
        'liquidity_areas': None
    }
    for key, default in market_defaults.items():
        if key not in data['market_context']:
            data['market_context'][key] = default
    
    return data


def _empty_data():
    """Return empty data structure on failure"""
    return {
        'basic_data': {
            'symbol': None,
            'entry': None,
            'sl': None,
            'tp': None,
            'side': None,
            'exit_price': None,
            'sl_estimation': {
                'between_lower': None,
                'between_upper': None,
                'closer_to': None,
                'estimated_value': None,
                'confidence': 0
            },
            'tp_estimation': {
                'between_lower': None,
                'between_upper': None,
                'closer_to': None,
                'estimated_value': None,
                'confidence': 0
            }
        },
        'chart_analysis': {
            'timeframe': None,
            'trend': None,
            'structure': None,
            'pattern': None,
            'chart_visible': False,
            'is_clear': False
        },
        'setup': {
            'type': None,
            'basis': None,
            'confirmation': None,
            'setup_quality': None
        },
        'trade_quality': {
            'score': 0,
            'is_good_setup': False,
            'confidence': 0,
            'strengths': [],
            'weaknesses': [],
            'risk_reward_ratio': None
        },
        'market_context': {
            'condition': None,
            'volume': None,
            'key_levels_nearby': [],
            'liquidity_areas': None
        }
    }


# Test function
if __name__ == "__main__":
    print("👁️ Vision Module Test")
    print("=" * 50)
    
    # Test with specific screenshot
    test_path = r"C:\codes\yt trading bot\screenshots\Screenshot 2026-09-05 121431.png"
    
    if os.path.exists(test_path):
        print(f"📸 Testing with: {test_path}")
        print()
        
        data = extract_trade_data(test_path)
        
        print("📊 Extracted Data:")
        print(json.dumps(data, indent=2))
        
        basic = data['basic_data']
        print(f"\n✅ Symbol: {basic['symbol']}")
        print(f"✅ Entry: {basic['entry']}")
        print(f"✅ SL: {basic['sl']} (confidence: {basic.get('sl_estimation', {}).get('confidence', 0)})")
        print(f"✅ TP: {basic['tp']} (confidence: {basic.get('tp_estimation', {}).get('confidence', 0)})")
        print(f"✅ Chart Visible: {data['chart_analysis']['chart_visible']}")
        print(f"✅ Confidence: {data['trade_quality']['confidence']}")
    else:
        print(f"❌ Test screenshot not found: {test_path}")
        print("   Available screenshots:")
        if os.path.exists("screenshots"):
            for f in os.listdir("screenshots"):
                print(f"   - {f}")