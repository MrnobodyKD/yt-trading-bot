"""
executor.py - DUMB Trade Executor
No thinking, no validation, no decisions
Just executes what brain.py tells it to do
"""

import MetaTrader5 as mt5
from datetime import datetime
import json
from pathlib import Path

class TradeExecutor:
    def __init__(self, magic_number=123456):
        self.mt5_connected = False
        self.magic_number = magic_number
        self.log_dir = Path('logs')
        self.log_dir.mkdir(exist_ok=True)
        self.execution_log = self.log_dir / 'executions.jsonl'
        
        # Connect to MT5
        self.connect_mt5()
    
    def connect_mt5(self):
        """Connect to MT5 terminal"""
        try:
            if not mt5.initialize():
                print("❌ Executor: MT5 initialization failed")
                self.mt5_connected = False
                return False
            
            self.mt5_connected = True
            account_info = mt5.account_info()
            if account_info:
                print(f"✅ Executor connected: Account {account_info.login}")
                print(f"   Balance: ${account_info.balance:.2f}")
            return True
            
        except Exception as e:
            print(f"❌ Executor connection error: {e}")
            self.mt5_connected = False
            return False
    
    def execute_entry(self, symbol, side, sl=None, tp=None, lot_size=0.01, comment="YouTube Bot Entry"):
        """
        Execute entry order
        Brain said ENTER - just do it
        """
        if not self.mt5_connected:
            self._log_execution('entry', symbol, 'failed', 'MT5 not connected')
            return None
        
        try:
            # Get current price
            tick = mt5.symbol_info_tick(symbol)
            if not tick:
                self._log_execution('entry', symbol, 'failed', f'No tick for {symbol}')
                return None
            
            # Determine order type and price
            if side in ['long', 'buy']:
                order_type = mt5.ORDER_TYPE_BUY
                price = tick.ask
            elif side in ['short', 'sell']:
                order_type = mt5.ORDER_TYPE_SELL
                price = tick.bid
            else:
                self._log_execution('entry', symbol, 'failed', f'Invalid side: {side}')
                return None
            
            # Prepare order request
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": lot_size,
                "type": order_type,
                "price": price,
                "sl": sl if sl else 0,
                "tp": tp if tp else 0,
                "deviation": 20,
                "magic": self.magic_number,
                "comment": comment,
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            
            # Send order
            result = mt5.order_send(request)
            
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                print(f"✅ EXECUTED ENTRY: {side.upper()} {symbol} @ {price}")
                print(f"   Ticket: {result.order}")
                print(f"   Volume: {lot_size}")
                print(f"   SL: {sl if sl else 'None'}")
                print(f"   TP: {tp if tp else 'None'}")
                
                self._log_execution('entry', symbol, 'success', 
                                   f'Ticket: {result.order}, Price: {price}, Side: {side}')
                return result.order
            else:
                error_msg = self._get_error_message(result.retcode)
                print(f"❌ Entry failed: {error_msg}")
                self._log_execution('entry', symbol, 'failed', error_msg)
                return None
                
        except Exception as e:
            print(f"❌ Entry execution error: {e}")
            self._log_execution('entry', symbol, 'failed', str(e))
            return None
    
    def execute_exit(self, mt5_ticket, lot_size=None, comment="YouTube Bot Exit"):
        """
        Execute exit order
        Brain said EXIT - just do it
        """
        if not self.mt5_connected:
            self._log_execution('exit', str(mt5_ticket), 'failed', 'MT5 not connected')
            return False
        
        try:
            # Get position
            position = mt5.positions_get(ticket=mt5_ticket)
            if not position:
                print(f"⚠️ Position {mt5_ticket} not found")
                self._log_execution('exit', str(mt5_ticket), 'failed', 'Position not found')
                return False
            
            position = position[0]
            
            # Determine close order type and price
            if position.type == mt5.ORDER_TYPE_BUY:
                order_type = mt5.ORDER_TYPE_SELL
                price = mt5.symbol_info_tick(position.symbol).bid
            else:
                order_type = mt5.ORDER_TYPE_BUY
                price = mt5.symbol_info_tick(position.symbol).ask
            
            # Determine volume to close
            close_volume = lot_size if lot_size else position.volume
            
            # Prepare close request
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": position.symbol,
                "volume": close_volume,
                "type": order_type,
                "position": mt5_ticket,
                "price": price,
                "deviation": 20,
                "magic": self.magic_number,
                "comment": comment,
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            
            # Send order
            result = mt5.order_send(request)
            
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                print(f"✅ EXECUTED EXIT: {position.symbol} @ {price}")
                print(f"   Ticket: {mt5_ticket}")
                print(f"   Volume: {close_volume}")
                print(f"   Profit: {position.profit}")
                
                self._log_execution('exit', position.symbol, 'success', 
                                   f'Ticket: {mt5_ticket}, Price: {price}, Profit: {position.profit}')
                return True
            else:
                error_msg = self._get_error_message(result.retcode)
                print(f"❌ Exit failed: {error_msg}")
                self._log_execution('exit', position.symbol, 'failed', error_msg)
                return False
                
        except Exception as e:
            print(f"❌ Exit execution error: {e}")
            self._log_execution('exit', str(mt5_ticket), 'failed', str(e))
            return False
    
    def execute_partial_exit(self, mt5_ticket, fraction=0.5, comment="YouTube Bot Partial Exit"):
        """
        Execute partial exit (close part of position)
        Brain said PARTIAL EXIT - just do it
        """
        if not self.mt5_connected:
            return False
        
        try:
            # Get position
            position = mt5.positions_get(ticket=mt5_ticket)
            if not position:
                print(f"⚠️ Position {mt5_ticket} not found")
                return False
            
            position = position[0]
            
            # Calculate partial volume
            close_volume = round(position.volume * fraction, 2)
            
            if close_volume < 0.01:
                print(f"⚠️ Partial volume too small: {close_volume}")
                return False
            
            print(f"🔄 Partial exit: {close_volume} of {position.volume} lots")
            
            # Execute partial close
            return self.execute_exit(mt5_ticket, lot_size=close_volume, comment=comment)
            
        except Exception as e:
            print(f"❌ Partial exit error: {e}")
            return False
    
    def execute_modify(self, mt5_ticket, new_sl=None, new_tp=None):
        """
        Modify SL/TP on existing position
        Brain said MODIFY - just do it
        """
        if not self.mt5_connected:
            self._log_execution('modify', str(mt5_ticket), 'failed', 'MT5 not connected')
            return False
        
        try:
            # Get position
            position = mt5.positions_get(ticket=mt5_ticket)
            if not position:
                print(f"⚠️ Position {mt5_ticket} not found")
                return False
            
            position = position[0]
            
            # Prepare modify request
            request = {
                "action": mt5.TRADE_ACTION_SLTP,
                "symbol": position.symbol,
                "position": mt5_ticket,
                "sl": new_sl if new_sl else position.sl,
                "tp": new_tp if new_tp else position.tp,
            }
            
            # Send modify request
            result = mt5.order_send(request)
            
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                print(f"✅ EXECUTED MODIFY: {position.symbol}")
                print(f"   Ticket: {mt5_ticket}")
                print(f"   New SL: {new_sl if new_sl else position.sl}")
                print(f"   New TP: {new_tp if new_tp else position.tp}")
                
                self._log_execution('modify', position.symbol, 'success', 
                                   f'Ticket: {mt5_ticket}, SL: {new_sl}, TP: {new_tp}')
                return True
            else:
                error_msg = self._get_error_message(result.retcode)
                print(f"❌ Modify failed: {error_msg}")
                self._log_execution('modify', position.symbol, 'failed', error_msg)
                return False
                
        except Exception as e:
            print(f"❌ Modify execution error: {e}")
            self._log_execution('modify', str(mt5_ticket), 'failed', str(e))
            return False
    
    def execute_breakeven(self, mt5_ticket):
        """
        Move SL to entry price (breakeven)
        Brain said BREAKEVEN - just do it
        """
        try:
            # Get position
            position = mt5.positions_get(ticket=mt5_ticket)
            if not position:
                return False
            
            position = position[0]
            
            # Set SL to entry price
            return self.execute_modify(mt5_ticket, new_sl=position.price_open)
            
        except Exception as e:
            print(f"❌ Breakeven error: {e}")
            return False
    
    def _get_error_message(self, retcode):
        """Get error message from MT5 return code"""
        error_codes = {
            mt5.TRADE_RETCODE_REQUOTE: "Requote",
            mt5.TRADE_RETCODE_REJECT: "Request rejected",
            mt5.TRADE_RETCODE_TRADE_DISABLED: "Trading disabled",
            mt5.TRADE_RETCODE_INVALID_FILL: "Invalid fill",
            mt5.TRADE_RETCODE_NO_MONEY: "Not enough money",
            mt5.TRADE_RETCODE_INVALID_VOLUME: "Invalid volume",
            mt5.TRADE_RETCODE_INVALID_PRICE: "Invalid price",
            mt5.TRADE_RETCODE_INVALID_STOPS: "Invalid stops",
            mt5.TRADE_RETCODE_MARKET_CLOSED: "Market closed",
            mt5.TRADE_RETCODE_TIMEOUT: "Timeout",
            mt5.TRADE_RETCODE_PRICE_CHANGED: "Price changed",
            mt5.TRADE_RETCODE_PRICE_OFF: "Price off",
            mt5.TRADE_RETCODE_TOO_MANY_REQUESTS: "Too many requests",
            mt5.TRADE_RETCODE_NO_CHANGES: "No changes",
        }
        return error_codes.get(retcode, f"Unknown error: {retcode}")
    
    def _log_execution(self, action, symbol, status, details):
        """Log execution to file"""
        try:
            log_entry = {
                'timestamp': datetime.now().isoformat(),
                'action': action,
                'symbol': symbol,
                'status': status,
                'details': details
            }
            with open(self.execution_log, 'a') as f:
                f.write(json.dumps(log_entry) + '\n')
        except:
            pass
    
    def get_open_positions(self):
        """Get all open positions from MT5"""
        if not self.mt5_connected:
            return []
        
        positions = []
        try:
            mt5_positions = mt5.positions_get()
            if mt5_positions:
                for pos in mt5_positions:
                    positions.append({
                        'ticket': pos.ticket,
                        'symbol': pos.symbol,
                        'type': 'long' if pos.type == 0 else 'short',
                        'volume': pos.volume,
                        'price_open': pos.price_open,
                        'price_current': pos.price_current,
                        'sl': pos.sl,
                        'tp': pos.tp,
                        'profit': pos.profit
                    })
        except Exception as e:
            print(f"❌ Get positions error: {e}")
        
        return positions
    
    def shutdown(self):
        """Disconnect from MT5"""
        if self.mt5_connected:
            mt5.shutdown()
            print("✅ Executor disconnected from MT5")


# Test function
if __name__ == "__main__":
    print("🎯 Trade Executor Test")
    print("=" * 50)
    
    executor = TradeExecutor()
    
    if executor.mt5_connected:
        print("\n✅ Executor ready")
        print("   Waiting for brain.py commands...")
        print("   This file doesn't trade by itself.")
        print("   Brain.py will call these functions:")
        print("   - execute_entry()")
        print("   - execute_exit()")
        print("   - execute_partial_exit()")
        print("   - execute_modify()")
        print("   - execute_breakeven()")
        
        # Show current positions
        positions = executor.get_open_positions()
        if positions:
            print(f"\n📊 Current MT5 positions: {len(positions)}")
            for pos in positions:
                print(f"   {pos['symbol']} {pos['type']} @ {pos['price_open']}")
        else:
            print("\n📊 No open positions in MT5")
    
    executor.shutdown()