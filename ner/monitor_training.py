"""
Monitor training progress by checking the log file.
"""

import time
import os
import re

LOG_FILE = "training_improved.log"

print("="*80)
print("MONITORING TRAINING PROGRESS")
print("="*80)

last_position = 0
last_step = 0

while True:
    try:
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, 'r') as f:
                f.seek(last_position)
                new_lines = f.readlines()
                last_position = f.tell()
                
                for line in new_lines:
                    # Look for progress indicators
                    if "%" in line and "/11600" in line:
                        # Extract step number
                        match = re.search(r'(\d+)/11600', line)
                        if match:
                            step = int(match.group(1))
                            if step > last_step:
                                last_step = step
                                percent = (step / 11600) * 100
                                print(f"Progress: {step:5d}/11,600 ({percent:5.2f}%) - {time.strftime('%H:%M:%S')}")
                    
                    # Look for epoch completion
                    if "Saving model checkpoint" in line or "epoch" in line.lower():
                        print(f"📊 {line.strip()}")
                    
                    # Look for F1 scores
                    if "f1" in line.lower() or "precision" in line.lower() or "recall" in line.lower():
                        print(f"📈 {line.strip()}")
                    
                    # Look for completion
                    if "TRAINING COMPLETE" in line or "SUCCESS" in line:
                        print("\n" + "="*80)
                        print("✅ TRAINING COMPLETE!")
                        print("="*80)
                        exit(0)
                    
                    # Look for errors
                    if "failed" in line.lower() or "error" in line.lower():
                        print(f"❌ {line.strip()}")
        
        time.sleep(60)  # Check every minute
        
    except KeyboardInterrupt:
        print("\nMonitoring stopped by user")
        break
    except Exception as e:
        print(f"Error: {e}")
        time.sleep(60)

