import serial
import serial.tools.list_ports
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import time
import csv
import matplotlib
import sys

matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

class GammaInterface:
    def __init__(self, master):
        self.master = master
        master.title("AlphaHound Interface (Python)")
        self.status_var = tk.StringVar(value="Status: Not Connected")
        self.dose_var = tk.StringVar(value="Dose Rate: N/A uRem")
        self.avg_var = tk.StringVar(value="Dose Average: N/A uRem")
        self.gamma_auto_running = False
        self.serial_thread = None
        self.serial_stop = threading.Event()
        self.serial_conn = None
        self.writer_lock = threading.Lock()

        # Dose log (list of dict: {'time': ?, 'dose': ?})
        self.dose_history = []
        # Spectrum log (list of count,energy)
        self.spectrum = []
        self.collect_spectrum = False

        self._setup_ui()
        self._populate_com_ports()

    # ------ UI LAYOUT -------
    def _setup_ui(self):
        # ------ Status frame ------
        frame_top = tk.Frame(self.master)
        frame_top.pack(padx=8, pady=5, anchor='w')

        tk.Label(frame_top, textvariable=self.status_var, font=('TkDefaultFont', 10, 'bold')).grid(row=0, column=0, sticky='w', pady=1)
        tk.Label(frame_top, textvariable=self.dose_var, font=('TkDefaultFont', 10, 'bold')).grid(row=1, column=0, sticky='w', pady=1)
        tk.Label(frame_top, textvariable=self.avg_var, font=('TkDefaultFont', 10, 'bold')).grid(row=2, column=0, sticky='w', pady=1)

        # ------ Serial config frame ------
        frame_port = tk.Frame(self.master)
        frame_port.pack(padx=8, pady=0, anchor='w')
        tk.Label(frame_port, text="Serial Port:").pack(side='left')
        self.combobox_ports = ttk.Combobox(frame_port, width=15, state='readonly')
        self.combobox_ports.pack(side='left', padx=4)
        tk.Button(frame_port, text="Refresh", command=self._populate_com_ports).pack(side='left', padx=4)
        self.button_connect = tk.Button(frame_port, text="Connect", command=self.connect_serial)
        self.button_connect.pack(side='left', padx=4)
        self.button_disconnect = tk.Button(frame_port, text="Disconnect", command=self.disconnect_serial, state='disabled')
        self.button_disconnect.pack(side='left', padx=4)

        # ------ Dose data controls ------
        frame_dose = tk.Frame(self.master)
        frame_dose.pack(padx=8, pady=2, anchor='w')
        tk.Button(frame_dose, text="Download Dose Rate CSV", command=self.export_dose_csv).pack(side='left')
        tk.Button(frame_dose, text="Clear Dose Data", command=self.clear_dose_history).pack(side='left', padx=6)

        # ------ Gamma spectrum controls ------
        frame_gamma = tk.Frame(self.master)
        frame_gamma.pack(padx=8, pady=6, anchor='w')

        self.button_gamma = tk.Button(frame_gamma, text="Download Gamma Spec", command=self.manual_get_gamma)
        self.button_gamma.pack(side='left')
        self.button_clear_gamma = tk.Button(frame_gamma, text="Clear Gamma Spectrum", command=self.clear_spectrum)
        self.button_clear_gamma.pack(side='left', padx=4)
        self.button_auto_gamma = tk.Button(frame_gamma, text="Start Auto Gamma View", command=self.toggle_auto_gamma)
        self.button_auto_gamma.pack(side='left', padx=4)

        self.checkvar_energy_csv = tk.IntVar(value=1)
        tk.Checkbutton(frame_gamma, text="CSV includes Energy (checked)", variable=self.checkvar_energy_csv).pack(side='left', padx=16)

        # ------ User command entry ------
        frame_cmd = tk.Frame(self.master)
        frame_cmd.pack(padx=8, pady=3, anchor='w')
        self.entry_command = tk.Entry(frame_cmd, width=30)
        self.entry_command.pack(side='left')
        tk.Button(frame_cmd, text="Send Data", command=self.send_custom_command).pack(side='left', padx=3)

        # ------ Gamma plot ------
        frame_plot = tk.Frame(self.master)
        frame_plot.pack(fill='x', padx=12, pady=8, expand=True)
        self.fig = Figure(figsize=(7, 3), dpi=90)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_xlabel("Channel")
        self.ax.set_ylabel("Count")
        self.ax.set_title("Gamma Spectrum")
        self.canvas = FigureCanvasTkAgg(self.fig, master=frame_plot)
        self.canvas.get_tk_widget().pack(fill='x', expand=True)
        self._draw_blank_spectrum()

    # ------ Serial port population ------
    def _populate_com_ports(self):
        ports = [port.device for port in serial.tools.list_ports.comports()]
        self.combobox_ports['values'] = ports
        if ports:
            self.combobox_ports.current(0)
        else:
            self.combobox_ports.set('')

    # ------ Connect / Disconnect ------
    def connect_serial(self):
        port = self.combobox_ports.get()
        if not port:
            messagebox.showwarning("No Port", "Please select a serial port.")
            return
        try:
            self.serial_conn = serial.Serial(port, 9600, timeout=0.2)
            self.status_var.set("Status: Connected to " + port)
            self.button_connect.config(state='disabled')
            self.button_disconnect.config(state='normal')
            self.serial_stop.clear()
            self.serial_thread = threading.Thread(target=self.serial_worker, daemon=True)
            self.serial_thread.start()
        except Exception as e:
            self.status_var.set(f"Status: Connection Failed ({e})")
            self.serial_conn = None

    def disconnect_serial(self):
        self.serial_stop.set()
        if self.serial_conn:
            try:
                self.serial_conn.close()
            except: pass
        self.serial_conn = None
        self.status_var.set("Status: Disconnected")
        self.button_connect.config(state='normal')
        self.button_disconnect.config(state='disabled')

    # ------ Serial thread: reads lines, responds to commands ------
    def serial_worker(self):
        buffer = b''
        spectrum_tmp = []
        expecting_spectrum = False
        try:
            last_dose_time = 0
            while not self.serial_stop.is_set():
                if self.serial_conn.in_waiting:
                    data = self.serial_conn.read(self.serial_conn.in_waiting)
                    buffer += data
                    while b'\n' in buffer:
                        line, buffer = buffer.split(b'\n', 1)
                        line = line.decode(errors='ignore').strip()
                        if line == "Comp":  # Start of spectrum collection
                            spectrum_tmp = []
                            expecting_spectrum = True
                            self.status_var.set("Status: Spectrum collecting...")
                        elif expecting_spectrum and ',' in line:
                            try:
                                count, energy = line.split(',', 1)
                                count = float(count)
                                energy = float(energy)
                                spectrum_tmp.append((count, energy))
                            except ValueError:
                                continue
                            if len(spectrum_tmp) >= 1024:
                                self.spectrum = spectrum_tmp.copy()
                                self._draw_spectrum()
                                if self.gamma_auto_running or not self.gamma_manual_get:
                                    # In auto or manual (no CSV): no export
                                    pass
                                else:
                                    self.export_gamma_csv()
                                expecting_spectrum = False
                        elif expecting_spectrum and not line:
                            continue
                        elif not expecting_spectrum and ',' not in line:
                            try:
                                dose = float(line)
                                self._handle_dose(dose)
                            except ValueError:
                                continue
                # Trigger periodic dose polling by sending 'D'
                curr = time.time()
                if self.serial_conn and curr - last_dose_time >= 1.0:
                    self._serial_write(b"D")
                    last_dose_time = curr
                time.sleep(0.05)
        except Exception as e:
            self.status_var.set(f"Status: Serial error: {str(e)}")
            self.disconnect_serial()

    # ------ Send over serial (thread safe) ------
    def _serial_write(self, data):
        if not self.serial_conn:
            return
        try:
            with self.writer_lock:
                self.serial_conn.write(data)
        except Exception as e:
            self.status_var.set("Status: Serial error: " + str(e))
            self.disconnect_serial()

    # ------ Dose handling ------
    def _handle_dose(self, dose):
        self.dose_var.set(f"Dose Rate: {dose} uRem")
        entry = {'time': time.strftime("%Y-%m-%d %H:%M:%S"), 'dose': dose}
        self.dose_history.append(entry)
        avg = sum([e['dose'] for e in self.dose_history]) / len(self.dose_history)
        self.avg_var.set(f"Dose Average: {avg:.2f} uRem")

    def clear_dose_history(self):
        self.dose_history = []
        self.avg_var.set("Dose Average: N/A uRem")
        self.dose_var.set("Dose Rate: N/A uRem")
        messagebox.showinfo("Dose Rate Data", "Dose rate data cleared.")

    def export_dose_csv(self):
        if not self.dose_history:
            messagebox.showinfo("Dose Rate Data", "No dose data to export.")
            return
        filename = filedialog.asksaveasfilename(defaultextension='.csv',
                            filetypes=[("CSV Files", "*.csv")],
                            title="Save Dose Rate CSV")
        if not filename:
            return
        with open(filename, 'w', newline='') as f:
            w = csv.writer(f)
            w.writerow(["Time", "Dose Rate"])
            for row in self.dose_history:
                w.writerow([row['time'], row['dose']])
        messagebox.showinfo("Dose Rate Data", f"CSV saved: {filename}")

    # ------ Gamma spectrum (manual, auto view, clear, CSV) ------
    def manual_get_gamma(self):
        if not self.serial_conn:
            messagebox.showinfo("Not Connected", "Connect to the device first.")
            return
        self.gamma_manual_get = True # for worker logic
        self.spectrum = []
        self.collect_spectrum = True
        # Send G command
        self._serial_write(b'G')
        # Wait up to 5s for spectrum to fill (handled in reader)
        self.status_var.set("Status: Waiting for spectrum...")
        # CSV is made ONLY if user triggers with button, not auto
        # CSV export will be from serial_worker after spectrum complete.

    def clear_spectrum(self):
        if not self.serial_conn:
            messagebox.showinfo("Not Connected", "Connect to the device first.")
            return
        self._serial_write(b'W')
        self.spectrum = []
        self._draw_blank_spectrum()
        self.status_var.set("Status: Clear command sent (W)")

    def toggle_auto_gamma(self):
        if not self.serial_conn:
            messagebox.showinfo("Not Connected", "Connect to the device first.")
            return
        if not self.gamma_auto_running:
            self.gamma_auto_running = True
            self.button_auto_gamma.config(text="Stop Auto Gamma View")
            self.status_var.set("Status: Auto Gamma started.")
            self._start_auto_gamma()
        else:
            self.gamma_auto_running = False
            self.button_auto_gamma.config(text="Start Auto Gamma View")
            self.status_var.set("Status: Auto Gamma stopped.")

    def _start_auto_gamma(self):
        def auto_loop():
            while self.gamma_auto_running and self.serial_conn:
                self._serial_write(b'G')
                for _ in range(50): # 0.1s x 50 = 5s
                    if not self.gamma_auto_running: break
                    time.sleep(0.1)
        t = threading.Thread(target=auto_loop, daemon=True)
        t.start()

    def export_gamma_csv(self):
        if not self.spectrum:
            messagebox.showinfo("No Data", "No gamma spectrum data available.")
            return
        include_energy = self.checkvar_energy_csv.get() == 1
        filename = filedialog.asksaveasfilename(defaultextension='.csv',
                            filetypes=[("CSV Files", "*.csv")],
                            title="Save Gamma Spectrum CSV")
        if not filename:
            return
        with open(filename, 'w', newline='') as f:
            w = csv.writer(f)
            if include_energy:
                w.writerow(["Data", "Energy"])
            else:
                w.writerow(["Data"])
            for count, energy in self.spectrum:
                if include_energy:
                    w.writerow([count, energy])
                else:
                    w.writerow([count])
        messagebox.showinfo("Export", f"CSV saved: {filename}")

    # ------- Draw/blank spectrum -------
    def _draw_spectrum(self):
        self.ax.cla()
        self.ax.set_xlabel("Channel")
        self.ax.set_ylabel("Count")
        self.ax.set_title("Gamma Spectrum")
        if self.spectrum:
            channels = list(range(len(self.spectrum)))
            counts = [count for count,energy in self.spectrum]
            self.ax.bar(channels, counts, color='black', alpha=0.6, width=1.0)
        self.canvas.draw()

    def _draw_blank_spectrum(self):
        self.ax.cla()
        self.ax.set_xlabel("Channel")
        self.ax.set_ylabel("Count")
        self.ax.set_title("Gamma Spectrum")
        self.canvas.draw()

    # ------ User command ------
    def send_custom_command(self):
        if not self.serial_conn:
            messagebox.showinfo("Not Connected", "Connect to the device first.")
            return
        text = self.entry_command.get().strip()
        if not text:
            return
        if text.startswith("C"):
            toks = text[1:].split(",")
            if len(toks) == 4:
                toks = [self._normalize_number(t) for t in toks]
                text = "C" + ",".join(toks)
        self._serial_write(text.encode('utf-8'))
        self.entry_command.delete(0, tk.END)

    def _normalize_number(self, token):
        # Convert "1e-03" to "0.001" style if possible
        try:
            num = float(token)
            if "e" in token.lower():
                coef, exp = token.lower().split('e')
                places = abs(int(exp)) + (len(coef.split(".")[1]) if '.' in coef else 0)
                return f"{num:.{places}f}"
            else:
                return str(num)
        except:
            return token

    def on_closing(self):
        self.disconnect_serial()
        self.master.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = GammaInterface(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()