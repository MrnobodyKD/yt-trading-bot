import json
import os
import time
import threading
from datetime import datetime
from pathlib import Path
import MetaTrader5 as mt5

class TradeStore:
    def __init__(self, data_dir='data', auto_sync_interval=45):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(exist_ok=True)
        
        # File paths
        self.trades_file = self.data_dir / 'trades.json'
        self.sync_log_file = self.data_dir / 'mt5_sync_log.json'
        self.history_file = self.data_dir / 'trade_history.json'
        
        # In-memory storage
        self.open_trades = {}
        self.closed_trades = {}
        self.trade_history = []
        self.trade_counter = 0
        
        # MT5 connection
        self.mt5_connected = False
        self.auto_sync_interval = auto_sync_interval
        self.sync_thread = None
        self.is_syncing = False
        self.stop_sync = False
        
        # Thread safety
        self.lock = threading.Lock()
        
        # Load existing data
        self.load_from_file()
        
        # Connect to MT5
        self.connect_mt5()
        
        # Start auto-sync
        self.start_auto_sync()
    
    def connect_mt5(self):
        """Connect to MT5 terminal"""
        try:
            if not mt5.initialize():
                print("❌ MT5 initialization failed. Make sure MT5 is running.")
                self.mt5_connected = False
                return False
            
            self.mt5_connected = True
            account_info = mt5.account_info()
            if account_info:
                print(f"✅ MT5 Connected: Account {account_info.login}")
                print(f"   Balance: ${account_info.balance:.2f}")
                print(f"   Equity: ${account_info.equity:.2f}")
            return True
        except Exception as e:
            print(f"❌ MT5 connection error: {e}")
            self.mt5_connected = False
            return False
    
    def load_from_file(self):
        """Load trades from JSON file"""
        try:
            if self.trades_file.exists():
                with open(self.trades_file, 'r') as f:
                    data = json.load(f)
                    self.open_trades = data.get('open_trades', {})
                    self.closed_trades = data.get('closed_trades', {})
                    self.trade_counter = data.get('trade_counter', 0)
                    print(f"✅ Loaded {len(self.open_trades)} open trades from file")
            
            if self.history_file.exists():
                with open(self.history_file, 'r') as f:
                    self.trade_history = json.load(f)
                    print(f"✅ Loaded {len(self.trade_history)} trades from history")
        except Exception as e:
            print(f"⚠️ Error loading trades: {e}")
    
    def save_to_file(self):
        """Save current state to JSON file"""
        try:
            data = {
                'open_trades': self.open_trades,
                'closed_trades': self.closed_trades,
                'trade_counter': self.trade_counter,
                'last_updated': datetime.now().isoformat()
            }
            with open(self.trades_file, 'w') as f:
                json.dump(data, f, indent=2)
            
            with open(self.history_file, 'w') as f:
                json.dump(self.trade_history, f, indent=2)
        except Exception as e:
            print(f"❌ Error saving: {e}")
    
    def start_auto_sync(self):
        """Start background thread for auto-sync"""
        if self.mt5_connected:
            self.stop_sync = False
            self.sync_thread = threading.Thread(target=self._auto_sync_worker, daemon=True)
            self.sync_thread.start()
            print(f"🔄 Auto-sync started (every {self.auto_sync_interval} seconds)")
    
    def _auto_sync_worker(self):
        """Background worker for auto-sync"""
        while not self.stop_sync:
            time.sleep(self.auto_sync_interval)
            if not self.stop_sync:
                try:
                    self.sync_with_mt5()
                except Exception as e:
                    print(f"❌ Auto-sync error: {e}")
    
    def stop_auto_sync(self):
        """Stop auto-sync thread"""
        self.stop_sync = True
        if self.sync_thread:
            self.sync_thread.join(timeout=5)
        print("🛑 Auto-sync stopped")
    
    def get_mt5_positions(self):
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
                        'type': pos.type,  # 0=buy, 1=sell
                        'volume': pos.volume,
                        'price_open': pos.price_open,
                        'price_current': pos.price_current,
                        'sl': pos.sl,
                        'tp': pos.tp,
                        'profit': pos.profit,
                        'swap': pos.swap,
                        'comment': pos.comment,
                        'time': pos.time
                    })
        except Exception as e:
            print(f"❌ Error getting MT5 positions: {e}")
        
        return positions
    
    def get_mt5_history(self, days=7):
        """Get trade history from MT5"""
        if not self.mt5_connected:
            return []
        
        history = []
        try:
            from datetime import timedelta
            from_time = datetime.now() - timedelta(days=days)
            
            deals = mt5.history_deals_get(from_time, datetime.now())
            if deals:
                for deal in deals:
                    history.append({
                        'ticket': deal.ticket,
                        'position_id': deal.position_id,
                        'symbol': deal.symbol,
                        'type': deal.type,  # 0=buy, 1=sell
                        'volume': deal.volume,
                        'price': deal.price,
                        'profit': deal.profit,
                        'time': deal.time,
                        'entry': deal.entry,  # 0=entry, 1=exit
                        'reason': deal.reason
                    })
        except Exception as e:
            print(f"❌ Error getting MT5 history: {e}")
        
        return history
    
    def sync_with_mt5(self):
        """Full sync with MT5 - positions, prices, SL/TP, and history"""
        if not self.mt5_connected:
            print("⚠️ MT5 not connected")
            return None
        
        if self.is_syncing:
            return None
        
        self.is_syncing = True
        
        try:
            sync_log = {
                'timestamp': datetime.now().isoformat(),
                'mt5_open_positions': 0,
                'store_open_trades': len(self.open_trades),
                'discrepancies': [],
                'updates': []
            }
            
            # Get current MT5 positions
            mt5_positions = self.get_mt5_positions()
            sync_log['mt5_open_positions'] = len(mt5_positions)
            mt5_open_tickets = {pos['ticket'] for pos in mt5_positions}
            mt5_positions_by_ticket = {pos['ticket']: pos for pos in mt5_positions}
            
            # CHECK 1: Store says open, but MT5 says closed
            for trade_id, trade in list(self.open_trades.items()):
                mt5_ticket = trade.get('mt5_ticket')
                if mt5_ticket and mt5_ticket not in mt5_open_tickets:
                    print(f"🔄 DISCREPANCY: {trade_id} open in store but closed in MT5")
                    sync_log['discrepancies'].append({
                        'type': 'store_open_mt5_closed',
                        'trade_id': trade_id,
                        'symbol': trade['symbol'],
                        'mt5_ticket': mt5_ticket
                    })
                    # Close in store
                    self.close_trade(trade_id, reason='mt5_closed_auto')
            
            # CHECK 2: MT5 has position, store doesn't know
            for ticket, pos in mt5_positions_by_ticket.items():
                if not self._find_trade_by_mt5_ticket(ticket):
                    print(f"🔄 DISCREPANCY: MT5 has {pos['symbol']} (ticket {ticket}) but store doesn't know")
                    sync_log['discrepancies'].append({
                        'type': 'mt5_open_store_unknown',
                        'mt5_ticket': ticket,
                        'symbol': pos['symbol']
                    })
                    # Add to store
                    self._add_mt5_trade_to_store(pos)
            
            # CHECK 3: Update prices, SL/TP for matching trades
            for trade_id, trade in list(self.open_trades.items()):
                mt5_ticket = trade.get('mt5_ticket')
                if mt5_ticket and mt5_ticket in mt5_positions_by_ticket:
                    mt5_pos = mt5_positions_by_ticket[mt5_ticket]
                    
                    # Check if updates needed
                    updates = {}
                    
                    if trade.get('current_price') != mt5_pos['price_current']:
                        updates['current_price'] = mt5_pos['price_current']
                        updates['current_profit'] = mt5_pos['profit']
                    
                    if trade.get('stop_loss') != mt5_pos['sl'] and mt5_pos['sl'] > 0:
                        print(f"🔄 UPDATING: {trade_id} SL changed from {trade['stop_loss']} to {mt5_pos['sl']}")
                        updates['stop_loss'] = mt5_pos['sl']
                        updates['sl'] = mt5_pos['sl']
                        sync_log['updates'].append({
                            'trade_id': trade_id,
                            'field': 'stop_loss',
                            'old': trade['stop_loss'],
                            'new': mt5_pos['sl']
                        })
                    
                    if trade.get('take_profit') != mt5_pos['tp'] and mt5_pos['tp'] > 0:
                        print(f"🔄 UPDATING: {trade_id} TP changed from {trade['take_profit']} to {mt5_pos['tp']}")
                        updates['take_profit'] = mt5_pos['tp']
                        updates['tp'] = mt5_pos['tp']
                        sync_log['updates'].append({
                            'trade_id': trade_id,
                            'field': 'take_profit',
                            'old': trade['take_profit'],
                            'new': mt5_pos['tp']
                        })
                    
                    if trade.get('position_size') != mt5_pos['volume']:
                        updates['position_size'] = mt5_pos['volume']
                        updates['lot_size'] = mt5_pos['volume']
                        sync_log['updates'].append({
                            'trade_id': trade_id,
                            'field': 'position_size',
                            'old': trade.get('position_size'),
                            'new': mt5_pos['volume']
                        })
                    
                    if updates:
                        trade.update(updates)
                        trade['last_synced'] = datetime.now().isoformat()
            
            # CHECK 4: Check MT5 history for closed trades we missed
            mt5_history = self.get_mt5_history(days=1)
            for deal in mt5_history:
                if deal['entry'] == 1:  # Exit deal
                    # Check if we have this as open
                    matching_trade = self._find_trade_by_position_id(deal['position_id'])
                    if matching_trade and matching_trade['status'] == 'open':
                        print(f"🔄 DISCREPANCY: {matching_trade['trade_id']} closed in MT5 history")
                        sync_log['discrepancies'].append({
                            'type': 'closed_in_mt5_history',
                            'trade_id': matching_trade['trade_id'],
                            'position_id': deal['position_id'],
                            'exit_price': deal['price'],
                            'profit': deal['profit']
                        })
                        # Close in store with MT5 data
                        self.close_trade(
                            matching_trade['trade_id'], 
                            exit_price=deal['price'], 
                            reason='mt5_history_closed'
                        )
            
            # Save sync log
            self._save_sync_log(sync_log)
            self.save_to_file()
            
            # Print summary
            if sync_log['discrepancies'] or sync_log['updates']:
                print(f"📊 Sync: {len(sync_log['discrepancies'])} discrepancies, {len(sync_log['updates'])} updates")
            
            return sync_log
            
        except Exception as e:
            print(f"❌ Sync error: {e}")
            return None
        finally:
            self.is_syncing = False
    
    def _find_trade_by_mt5_ticket(self, ticket):
        """Find trade by MT5 ticket number"""
        for trade in self.open_trades.values():
            if trade.get('mt5_ticket') == ticket:
                return trade
        return None
    
    def _find_trade_by_position_id(self, position_id):
        """Find trade by position ID"""
        for trade in self.open_trades.values():
            if trade.get('position_id') == position_id:
                return trade
        for trade in self.closed_trades.values():
            if trade.get('position_id') == position_id:
                return trade
        return None
    
    def _add_mt5_trade_to_store(self, mt5_position):
        """Add MT5 trade to store"""
        side = 'long' if mt5_position['type'] == 0 else 'short'
        
        self.trade_counter += 1
        trade_id = f"T{self.trade_counter:04d}"
        
        trade = {
            'trade_id': trade_id,
            'symbol': mt5_position['symbol'],
            'side': side,
            'entry_price': mt5_position['price_open'],
            'entry': mt5_position['price_open'],
            'stop_loss': mt5_position['sl'],
            'sl': mt5_position['sl'],
            'take_profit': mt5_position['tp'],
            'tp': mt5_position['tp'],
            'your_tp': None,
            'his_sl': mt5_position['sl'],
            'his_tp': mt5_position['tp'],
            'position_size': mt5_position['volume'],
            'lot_size': mt5_position['volume'],
            'status': 'open',
            'source': 'mt5',
            'mt5_ticket': mt5_position['ticket'],
            'position_id': mt5_position['ticket'],
            'current_price': mt5_position['price_current'],
            'current_profit': mt5_position['profit'],
            'opened_at': datetime.now().isoformat(),
            'closed_at': None,
            'exit_price': None,
            'pnl': None,
            'trailing_enabled': False,
            'last_synced': datetime.now().isoformat(),
            'notes': ['Auto-added from MT5 sync']
        }
        
        self.open_trades[trade_id] = trade
        self.trade_history.append(trade.copy())
        self.save_to_file()
        
        print(f"✅ Added MT5 trade: {trade_id} - {side.upper()} {mt5_position['symbol']} @ {mt5_position['price_open']}")
        return trade
    
    def create_trade(self, symbol, side, entry=None, entry_price=None, 
                     stop_loss=None, take_profit=None, his_sl=None, his_tp=None,
                     your_tp=None, lot_size=0.01, position_size=None, 
                     source='stream', mt5_ticket=None):
        """Create a new trade in store - accepts multiple parameter names"""
        
        # Handle different parameter names from brain.py
        final_entry = entry if entry is not None else entry_price
        final_sl = stop_loss if stop_loss is not None else his_sl
        final_tp = take_profit if take_profit is not None else your_tp if your_tp else his_tp
        final_size = position_size if position_size is not None else lot_size
        
        self.trade_counter += 1
        trade_id = f"T{self.trade_counter:04d}"
        
        trade = {
            'trade_id': trade_id,
            'symbol': symbol,
            'side': side,
            'entry_price': final_entry,
            'entry': final_entry,  # Alias
            'stop_loss': final_sl,
            'sl': final_sl,  # Alias
            'take_profit': final_tp,
            'tp': final_tp,  # Alias
            'his_sl': his_sl,  # Trader's original SL
            'his_tp': his_tp,  # Trader's original TP
            'your_tp': your_tp,  # Your adjusted TP
            'position_size': final_size,
            'lot_size': final_size,  # Alias
            'status': 'open',
            'source': source,
            'mt5_ticket': mt5_ticket,
            'position_id': mt5_ticket,
            'opened_at': datetime.now().isoformat(),
            'closed_at': None,
            'exit_price': None,
            'pnl': None,
            'current_price': final_entry,
            'current_profit': 0,
            'trailing_enabled': False,
            'last_synced': datetime.now().isoformat(),
            'notes': []
        }
        
        self.open_trades[trade_id] = trade
        self.trade_history.append(trade.copy())
        self.save_to_file()
        
        print(f"✅ Trade created: {trade_id} - {side.upper()} {symbol} @ {final_entry}")
        return trade
    
    def close_trade(self, trade_id, exit_price=None, reason='manual'):
        """Close a trade in store"""
        trade = None
        
        if trade_id in self.open_trades:
            trade = self.open_trades.pop(trade_id)
        else:
            # Try to find by symbol
            for tid, t in self.open_trades.items():
                if t['symbol'] == trade_id:
                    trade = self.open_trades.pop(tid)
                    trade_id = tid
                    break
        
        if trade:
            trade['status'] = 'closed'
            trade['closed_at'] = datetime.now().isoformat()
            trade['exit_price'] = exit_price
            trade['close_reason'] = reason
            
            # Calculate P&L
            if trade.get('entry_price') and exit_price:
                if trade['side'] == 'long':
                    trade['pnl'] = exit_price - trade['entry_price']
                else:
                    trade['pnl'] = trade['entry_price'] - exit_price
            
            self.closed_trades[trade_id] = trade
            self.save_to_file()
            
            print(f"✅ Trade closed: {trade_id} - Exit @ {exit_price}, PnL: {trade.get('pnl', 'N/A')}")
            return trade
        return None
    
    def partial_close(self, trade_id, exit_price=None, fraction=0.5):
        """Close part of a position"""
        trade = self.get_trade(trade_id)
        if not trade:
            print(f"⚠️ Trade {trade_id} not found for partial close")
            return None
        
        # Reduce position size by fraction
        if trade.get('position_size'):
            original_size = trade['position_size']
            close_size = original_size * fraction
            remaining_size = original_size * (1 - fraction)
            
            trade['position_size'] = remaining_size
            trade['lot_size'] = remaining_size  # Update alias
            
            trade['last_partial_close'] = {
                'time': datetime.now().isoformat(),
                'fraction': fraction,
                'exit_price': exit_price,
                'closed_size': close_size
            }
            
            # If position too small, close fully
            if remaining_size < 0.005:
                return self.close_trade(trade_id, exit_price, reason='partial_close_remaining')
            
            # Add note
            if 'notes' not in trade:
                trade['notes'] = []
            trade['notes'].append(f"Partial close: {fraction*100}% @ {exit_price}")
            
            self.save_to_file()
            print(f"✅ Partial close: {trade_id} - {fraction*100}% @ {exit_price}")
            return trade
        
        return None
    
    def set_trailing_flag(self, trade_id, enabled=True, distance=None):
        """Enable/disable trailing stop"""
        trade = self.get_trade(trade_id)
        if not trade:
            print(f"⚠️ Trade {trade_id} not found for trailing")
            return None
        
        trade['trailing_enabled'] = enabled
        if distance:
            trade['trailing_distance'] = distance
        
        if 'notes' not in trade:
            trade['notes'] = []
        trade['notes'].append(f"Trailing {'enabled' if enabled else 'disabled'}")
        
        self.save_to_file()
        print(f"✅ Trailing {'enabled' if enabled else 'disabled'}: {trade_id}")
        return trade
    
    def update_trade(self, trade_id, sl=None, tp=None, **updates):
        """Update trade details - accepts sl/tp or other fields"""
        
        # Find trade
        trade = None
        if trade_id in self.open_trades:
            trade = self.open_trades[trade_id]
        elif trade_id in self.closed_trades:
            trade = self.closed_trades[trade_id]
        else:
            print(f"⚠️ Trade {trade_id} not found for update")
            return None
        
        # Handle sl/tp specifically
        if sl is not None:
            trade['stop_loss'] = sl
            trade['sl'] = sl
            updates['stop_loss'] = sl
            updates['sl'] = sl
        
        if tp is not None:
            trade['take_profit'] = tp
            trade['tp'] = tp
            updates['take_profit'] = tp
            updates['tp'] = tp
        
        # Apply other updates
        trade.update(updates)
        trade['last_updated'] = datetime.now().isoformat()
        
        self.save_to_file()
        print(f"✅ Trade updated: {trade_id}")
        return trade
    
    def get_open_trades(self):
        """Get all open trades"""
        return list(self.open_trades.values())
    
    def get_open_trades_by_symbol(self, symbol):
        """Get open trades for specific symbol"""
        return [t for t in self.open_trades.values() if t.get('symbol') == symbol and t.get('status') == 'open']
    
    def get_closed_trades(self):
        """Get all closed trades"""
        return list(self.closed_trades.values())
    
    def get_trade(self, trade_id):
        """Get specific trade"""
        if trade_id in self.open_trades:
            return self.open_trades[trade_id]
        elif trade_id in self.closed_trades:
            return self.closed_trades[trade_id]
        return None
    
    def get_trades_by_symbol(self, symbol):
        """Get all trades for a symbol"""
        trades = []
        for trade in self.open_trades.values():
            if trade['symbol'] == symbol:
                trades.append(trade)
        for trade in self.closed_trades.values():
            if trade['symbol'] == symbol:
                trades.append(trade)
        return trades
    
    def get_sync_status(self):
        """Get current status"""
        return {
            'open_trades': len(self.open_trades),
            'closed_trades': len(self.closed_trades),
            'total_history': len(self.trade_history),
            'mt5_connected': self.mt5_connected,
            'auto_sync_interval': self.auto_sync_interval,
            'is_syncing': self.is_syncing
        }
    
    def _save_sync_log(self, sync_log):
        """Save sync log to file"""
        try:
            existing = []
            if self.sync_log_file.exists():
                with open(self.sync_log_file, 'r') as f:
                    existing = json.load(f)
            
            existing.append(sync_log)
            if len(existing) > 500:
                existing = existing[-500:]
            
            with open(self.sync_log_file, 'w') as f:
                json.dump(existing, f, indent=2)
        except Exception as e:
            print(f"❌ Error saving sync log: {e}")
    
    def get_sync_log(self, limit=10):
        """Get recent sync logs"""
        try:
            if self.sync_log_file.exists():
                with open(self.sync_log_file, 'r') as f:
                    logs = json.load(f)
                    return logs[-limit:]
        except:
            pass
        return []
    
    def shutdown(self):
        """Clean shutdown"""
        self.stop_auto_sync()
        self.save_to_file()
        if self.mt5_connected:
            mt5.shutdown()
            print("✅ MT5 disconnected")
        print("✅ Trade store shutdown complete")


# Global instance
trade_store = TradeStore()

# Main execution
if __name__ == "__main__":
    print("🚀 Starting Trade Store with MT5 Auto-Sync")
    print("=" * 50)
    
    # Create store with 45-second auto-sync
    store = TradeStore(auto_sync_interval=45)
    
    print("\n📊 Initial Status:")
    status = store.get_sync_status()
    print(f"   Open trades: {status['open_trades']}")
    print(f"   Closed trades: {status['closed_trades']}")
    print(f"   MT5 connected: {status['mt5_connected']}")
    print(f"   Auto-sync: every {status['auto_sync_interval']} seconds")
    
    try:
        # Keep running to show auto-sync working
        print("\n🔄 Auto-sync is running in background...")
        print("   Press Ctrl+C to stop\n")
        
        while True:
            time.sleep(10)
            status = store.get_sync_status()
            if status['open_trades'] > 0:
                print(f"📊 Open trades: {status['open_trades']} | Syncing: {status['is_syncing']}")
            
    except KeyboardInterrupt:
        print("\n\n⏹️  Shutting down...")
        store.shutdown()
# Standalone functions for brain.py compatibility
_global_store = None

def get_global_store():
    global _global_store
    if _global_store is None:
        _global_store = TradeStore()
    return _global_store

def get_open_trades():
    return get_global_store().get_open_trades()

def get_closed_trades():
    return get_global_store().get_closed_trades()

def create_trade(symbol, side, entry=None, entry_price=None, 
                 stop_loss=None, take_profit=None, his_sl=None, his_tp=None,
                 your_tp=None, lot_size=0.01, position_size=None, 
                 source='stream', mt5_ticket=None):
    return get_global_store().create_trade(
        symbol=symbol, side=side, entry=entry, entry_price=entry_price,
        stop_loss=stop_loss, take_profit=take_profit, his_sl=his_sl, his_tp=his_tp,
        your_tp=your_tp, lot_size=lot_size, position_size=position_size,
        source=source, mt5_ticket=mt5_ticket
    )

def close_trade(trade_id, exit_price=None, reason='manual'):
    return get_global_store().close_trade(trade_id, exit_price, reason)

def update_trade(trade_id, sl=None, tp=None, **updates):
    return get_global_store().update_trade(trade_id, sl=sl, tp=tp, **updates)

def partial_close(trade_id, exit_price=None, fraction=0.5):
    return get_global_store().partial_close(trade_id, exit_price, fraction)

def set_trailing_flag(trade_id, enabled=True, distance=None):
    return get_global_store().set_trailing_flag(trade_id, enabled, distance)
