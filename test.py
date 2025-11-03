import serial
import serial.tools.list_ports
import time
import sys
import re

def pick_serial_port():
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        print("No serial ports found. Make sure your device is connected and turned on.")
        sys.exit(1)
    print("Available serial ports:")
    for i, port in enumerate(ports):
        print(f"  [{i+1}] {port.device} ({port.description})")
    while True:
        sel = input("Select port number: ").strip()
        try:
            idx = int(sel)-1
            assert 0 <= idx < len(ports)
            return ports[idx].device
        except Exception:
            print("Invalid selection.")

def try_command(ser, cmd, timeout=1.5, tag=""):
    """Send the command and print all output returned until timeout."""
    # Always encode as ASCII, add newline if not present
    if isinstance(cmd, str): cmd = cmd.encode('utf-8')
    if not cmd.endswith(b'\n'):
        cmd += b'\n'
    print(f"\n> SENDING: {cmd.strip().decode()} {f'({tag})' if tag else ''}")
    ser.reset_input_buffer()
    ser.write(cmd)
    t0 = time.time()
    first_line = True
    buffer = b""
    results = []
    while time.time() - t0 < timeout:
        if ser.in_waiting:
            data = ser.read(ser.in_waiting)
            buffer += data
            while b'\n' in buffer:
                line, buffer = buffer.split(b'\n', 1)
                try:
                    text = line.decode(errors="ignore").strip()
                except:
                    text = str(line).strip()
                if first_line: first_line = False
                results.append(text)
                # Print with keyword/color highlighting!
                if "Comp" in text:
                    print("  [COMP]       " + text)
                elif "," in text and re.match(r"\d+([.,]\d*)?,", text):
                    print("  [SPECTRUM?]  " + text)
                elif re.match(r"^\d+\.?\d*$", text):
                    print("  [NUMERIC]    " + text)
                else:
                    print("  [REPLY]      " + text)
        time.sleep(0.05)
    return results

def main():
    port = pick_serial_port()
    baud = 9600
    ser = serial.Serial(port, baud, timeout=0.3)
    print(f"\nOpened serial port {port} at {baud} baud.\n")
    logfile = f"alphahound_serial_probe_{int(time.time())}.txt"

    # List of likely/test commands
    test_cmds = [
        "G",         # Gamma spectrum
        "A",         # Alpha spectrum (if any)
        "B",         # Beta spectrum (if any)
        "D",         # Dose rate / gamma rate
        "DA",        # Dose alpha / alpha CPM/CPS
        "DB",        # Dose beta  / beta CPM/CPS
        "RA",        # Some devices: rate alpha
        "RB",        # ... rate beta
        "GA",
        "GB",
        "SpecA",     # Spec alpha/beta (rare)
        "SpecB",
        "COUNT",     # Some devices: total counts
        "ALL",       # Some: all measurements?
        "?"          # Query command (rare but just in case)
    ]
    print("\nType a command to send to the device, or use one of the following test commands:")
    print("  " + "  ".join(test_cmds))
    print("Type 'AUTO' to probe all of them in sequence.")
    print("Type 'QUIT' to exit.")

    all_log = []

    while True:
        inp = input("> Command: ").strip()
        if not inp: continue
        if inp.upper() == "QUIT": break
        if inp.upper() == "AUTO":
            print("\n=== AUTO-PROBING ALL COMMON COMMANDS ===\n")
            all_auto_res = []
            for test in test_cmds:
                responses = try_command(ser, test, timeout=2.5, tag="AUTO")
                all_auto_res.append((test, responses))
                print("-"*50)
            print("\nDONE with AUTO. Saved output to logfile.")
            with open(logfile, "a", encoding="utf-8") as f:
                tmark = time.strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"\n-----[AUTO PROBE {tmark}]-----\n")
                for cmd, responses in all_auto_res:
                    f.write(f"\nCMD: {cmd}\n")
                    for r in responses:
                        f.write("   " + r + "\n")
            continue

        print(f"\n--- Sending '{inp}' ---")
        responses = try_command(ser, inp, timeout=2.5)
        tmark = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(logfile, "a", encoding="utf-8") as f:
            f.write(f"\n[{tmark}] CMD: {inp}\n")
            for r in responses:
                f.write("   " + r + "\n")

    ser.close()
    print("Closed serial port.")

if __name__ == "__main__":
    main()