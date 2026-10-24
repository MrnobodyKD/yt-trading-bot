import json
import os

print('=== TRADE STORE ===')
if os.path.exists('data/trades.json'):
    with open('data/trades.json', 'r') as f:
        data = json.load(f)
        open_trades = data.get('open_trades', {})
        closed_trades = data.get('closed_trades', {})
        print('Open trades: ' + str(len(open_trades)))
        print('Closed trades: ' + str(len(closed_trades)))
        print()
        
        if open_trades:
            for tid, trade in open_trades.items():
                symbol = trade.get('symbol', 'Unknown')
                side = trade.get('side', 'Unknown')
                entry = trade.get('entry_price', 'Unknown')
                sl = trade.get('stop_loss', 'None')
                tp = trade.get('take_profit', 'None')
                status = trade.get('status', 'Unknown')
                mt5_ticket = trade.get('mt5_ticket', 'None')
                
                print('Trade: ' + tid)
                print('  Symbol: ' + str(symbol))
                print('  Side: ' + str(side))
                print('  Entry: ' + str(entry))
                print('  SL: ' + str(sl))
                print('  TP: ' + str(tp))
                print('  Status: ' + str(status))
                print('  MT5 Ticket: ' + str(mt5_ticket))
                print()

print('=== RECENT BRAIN DECISIONS ===')
if os.path.exists('logs/brain_decisions.jsonl'):
    with open('logs/brain_decisions.jsonl', 'r') as f:
        lines = f.readlines()
        print('Total decisions: ' + str(len(lines)))
        print()
        print('Last 5 decisions:')
        for line in lines[-5:]:
            print(line.strip())
            print()
else:
    print('No decisions logged yet')

print('=== COMPRESSED MEMORY ===')
from brain import Brain
brain = Brain()
print(brain.compressed.to_prompt_text())
print()
print('Trader state: ' + brain.compressed.trader_state)
print()
print('Key events:')
for event in brain.compressed.last_events:
    print('  ' + event)
