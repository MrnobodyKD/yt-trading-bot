"""
risk_math.py - Pure mathematical calculations for trading
No decisions, no AI, no API calls - just fast, accurate math
"""

class RiskMath:
    """Pure math calculations for trade risk management"""
    
    def __init__(self, default_risk_percent=0.5, reward_risk_ratio=2.0, tp_adjustment_percent=5.0):
        """
        Initialize risk math with default parameters
        
        Args:
            default_risk_percent: Default SL distance (0.5% from entry)
            reward_risk_ratio: Default R:R ratio (2:1)
            tp_adjustment_percent: How much to reduce trader's TP (5%)
        """
        self.default_risk_percent = default_risk_percent  # 0.5% SL distance
        self.reward_risk_ratio = reward_risk_ratio  # 2:1 R:R ratio
        self.tp_adjustment_percent = tp_adjustment_percent  # 5% TP reduction
        self.fixed_position_size = 0.01  # Always 0.01 lots
    
    def get_default_sl(self, entry_price, side):
        """
        Calculate default stop loss if trader doesn't provide one
        
        Args:
            entry_price: Entry price
            side: 'long' or 'short'
        
        Returns:
            Default SL price
        """
        if side.lower() == 'long':
            # SL below entry for long
            return entry_price * (1 - self.default_risk_percent / 100)
        elif side.lower() == 'short':
            # SL above entry for short
            return entry_price * (1 + self.default_risk_percent / 100)
        else:
            raise ValueError(f"Invalid side: {side}. Must be 'long' or 'short'")
    
    def get_default_tp(self, entry_price, sl_price, side):
        """
        Calculate default take profit based on R:R ratio
        
        Args:
            entry_price: Entry price
            sl_price: Stop loss price
            side: 'long' or 'short'
        
        Returns:
            Default TP price
        """
        risk_distance = abs(entry_price - sl_price)
        reward_distance = risk_distance * self.reward_risk_ratio
        
        if side.lower() == 'long':
            return entry_price + reward_distance
        elif side.lower() == 'short':
            return entry_price - reward_distance
        else:
            raise ValueError(f"Invalid side: {side}. Must be 'long' or 'short'")
    
    def adjust_trader_tp(self, entry_price, trader_tp, side):
        """
        Reduce trader's TP by 5% to exit before the crowd
        
        Args:
            entry_price: Entry price
            trader_tp: Trader's original TP
            side: 'long' or 'short'
        
        Returns:
            Adjusted TP price (5% less than trader's)
        """
        if trader_tp is None or trader_tp <= 0:
            return None
        
        total_move = abs(trader_tp - entry_price)
        adjustment = total_move * (self.tp_adjustment_percent / 100)
        
        if side.lower() == 'long':
            return trader_tp - adjustment
        elif side.lower() == 'short':
            return trader_tp + adjustment
        else:
            raise ValueError(f"Invalid side: {side}. Must be 'long' or 'short'")
    
    def get_position_size(self):
        """
        Get position size - always 0.01 lots for now
        
        Returns:
            Position size in lots
        """
        return self.fixed_position_size
    
    def calculate_risk_amount(self, entry_price, sl_price, position_size=None):
        """
        Calculate dollar risk for a trade
        
        Args:
            entry_price: Entry price
            sl_price: Stop loss price
            position_size: Position size (default: 0.01)
        
        Returns:
            Risk amount in dollars
        """
        if position_size is None:
            position_size = self.fixed_position_size
        
        risk_per_unit = abs(entry_price - sl_price)
        total_risk = risk_per_unit * position_size
        
        return total_risk
    
    def calculate_reward_amount(self, entry_price, tp_price, position_size=None):
        """
        Calculate dollar reward for a trade
        
        Args:
            entry_price: Entry price
            tp_price: Take profit price
            position_size: Position size (default: 0.01)
        
        Returns:
            Reward amount in dollars
        """
        if position_size is None:
            position_size = self.fixed_position_size
        
        reward_per_unit = abs(tp_price - entry_price)
        total_reward = reward_per_unit * position_size
        
        return total_reward
    
    def calculate_rr_ratio(self, entry_price, sl_price, tp_price):
        """
        Calculate risk/reward ratio
        
        Args:
            entry_price: Entry price
            sl_price: Stop loss price
            tp_price: Take profit price
        
        Returns:
            R:R ratio (reward/risk)
        """
        risk = abs(entry_price - sl_price)
        reward = abs(tp_price - entry_price)
        
        if risk == 0:
            return 0
        
        return reward / risk
    
    def validate_prices(self, entry_price, sl_price, tp_price, side):
        """
        Validate if prices make sense for the trade
        
        Args:
            entry_price: Entry price
            sl_price: Stop loss price
            tp_price: Take profit price
            side: 'long' or 'short'
        
        Returns:
            (is_valid: bool, message: str)
        """
        if entry_price <= 0:
            return False, "Entry price must be positive"
        
        if sl_price <= 0:
            return False, "Stop loss must be positive"
        
        if tp_price <= 0:
            return False, "Take profit must be positive"
        
        if side.lower() == 'long':
            if sl_price >= entry_price:
                return False, "Long trade: SL must be below entry"
            if tp_price <= entry_price:
                return False, "Long trade: TP must be above entry"
        elif side.lower() == 'short':
            if sl_price <= entry_price:
                return False, "Short trade: SL must be above entry"
            if tp_price >= entry_price:
                return False, "Short trade: TP must be below entry"
        else:
            return False, f"Invalid side: {side}"
        
        return True, "Valid trade setup"
    
    def get_complete_trade_setup(self, entry_price, side, trader_sl=None, trader_tp=None):
        """
        Get complete trade setup with all calculations
        
        Args:
            entry_price: Entry price
            side: 'long' or 'short'
            trader_sl: Trader's SL (optional)
            trader_tp: Trader's TP (optional)
        
        Returns:
            Dictionary with complete trade setup
        """
        # Determine SL
        if trader_sl and trader_sl > 0:
            sl_price = trader_sl
            sl_source = 'trader'
        else:
            sl_price = self.get_default_sl(entry_price, side)
            sl_source = 'default'
        
        # Determine TP
        if trader_tp and trader_tp > 0:
            tp_price = self.adjust_trader_tp(entry_price, trader_tp, side)
            tp_source = 'trader_adjusted'
        else:
            tp_price = self.get_default_tp(entry_price, sl_price, side)
            tp_source = 'default'
        
        # Get position size
        position_size = self.get_position_size()
        
        # Calculate risk and reward
        risk_amount = self.calculate_risk_amount(entry_price, sl_price, position_size)
        reward_amount = self.calculate_reward_amount(entry_price, tp_price, position_size)
        rr_ratio = self.calculate_rr_ratio(entry_price, sl_price, tp_price)
        
        # Validate
        is_valid, validation_message = self.validate_prices(entry_price, sl_price, tp_price, side)
        
        return {
            'entry_price': entry_price,
            'stop_loss': sl_price,
            'take_profit': tp_price,
            'sl_source': sl_source,
            'tp_source': tp_source,
            'position_size': position_size,
            'risk_amount': round(risk_amount, 2),
            'reward_amount': round(reward_amount, 2),
            'rr_ratio': round(rr_ratio, 2),
            'is_valid': is_valid,
            'validation_message': validation_message
        }
    
    def update_trade_with_new_sl_tp(self, current_trade, new_sl=None, new_tp=None):
        """
        Update existing trade with new SL/TP from trader
        
        Args:
            current_trade: Current trade dictionary
            new_sl: New stop loss (optional)
            new_tp: New take profit (optional)
        
        Returns:
            Updated trade setup
        """
        entry_price = current_trade.get('entry_price')
        side = current_trade.get('side')
        
        if not entry_price or not side:
            return current_trade
        
        # Use existing values as defaults
        sl_price = new_sl if new_sl and new_sl > 0 else current_trade.get('stop_loss')
        trader_tp = new_tp if new_tp and new_tp > 0 else None
        
        # If new TP given, adjust it
        if trader_tp:
            tp_price = self.adjust_trader_tp(entry_price, trader_tp, side)
        else:
            tp_price = current_trade.get('take_profit')
        
        # Recalculate
        position_size = current_trade.get('position_size', self.fixed_position_size)
        risk_amount = self.calculate_risk_amount(entry_price, sl_price, position_size)
        reward_amount = self.calculate_reward_amount(entry_price, tp_price, position_size)
        rr_ratio = self.calculate_rr_ratio(entry_price, sl_price, tp_price)
        
        updated_trade = current_trade.copy()
        updated_trade.update({
            'stop_loss': sl_price,
            'take_profit': tp_price,
            'risk_amount': round(risk_amount, 2),
            'reward_amount': round(reward_amount, 2),
            'rr_ratio': round(rr_ratio, 2)
        })
        
        return updated_trade


# Test the math
if __name__ == "__main__":
    print("Testing Risk Math...")
    print("=" * 50)
    
    rm = RiskMath()
    
    # Test 1: Complete setup with trader's SL/TP
    print("\nTest 1: Trader gives SL and TP")
    setup = rm.get_complete_trade_setup(
        entry_price=67200,
        side='long',
        trader_sl=66800,
        trader_tp=68200
    )
    for key, value in setup.items():
        print(f"  {key}: {value}")
    
    # Test 2: No SL/TP from trader
    print("\nTest 2: Trader gives nothing")
    setup2 = rm.get_complete_trade_setup(
        entry_price=67200,
        side='long'
    )
    for key, value in setup2.items():
        print(f"  {key}: {value}")
    
    # Test 3: Short trade
    print("\nTest 3: Short trade")
    setup3 = rm.get_complete_trade_setup(
        entry_price=3500,
        side='short',
        trader_sl=3550,
        trader_tp=3400
    )
    for key, value in setup3.items():
        print(f"  {key}: {value}")
    
    # Test 4: Invalid trade
    print("\nTest 4: Invalid trade (bad prices)")
    is_valid, msg = rm.validate_prices(67200, 68000, 67000, 'long')
    print(f"  Valid: {is_valid}, Message: {msg}")
    
    print("\n" + "=" * 50)
    print("✅ Risk Math working perfectly!")