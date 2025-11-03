import serial
import serial.tools.list_ports
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, StringVar
import math
import time
import csv
import matplotlib
import xml.etree.ElementTree as ET
from xml.dom import minidom

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

        self.serial_thread = None
        self.serial_stop = threading.Event()
        self.serial_conn = None
        self.writer_lock = threading.Lock()

        self.dose_history = []
        self.spectrum = []
        self.collect_spectrum = False
        self.gamma_manual_get = False

        self.timed_acquire_active = False
        self.timed_acquire_abort = threading.Event()
        self.timed_acquire_thread = None
        self.timed_value = 0

        self.gamma_auto_running = False
        self.gamma_auto_thread = None

        self.cs137_roi = (650, 675)  # keV
        self.cs137_efficiency = 0.10  # 10% (per your note, set proper value if available)
        self.cs137_gamma_abundance = 0.85  # 85% gamma emission probability (branching ratio)
        self.cs137_results_var = StringVar(value="")

        self._setup_ui()
        self._populate_com_ports()

    def _setup_ui(self):
        frame_top = tk.Frame(self.master); frame_top.pack(padx=8, pady=5, anchor='w')
        tk.Label(frame_top, textvariable=self.status_var, font=('TkDefaultFont', 10, 'bold')).grid(row=0, column=0, sticky='w', pady=1)
        tk.Label(frame_top, textvariable=self.dose_var, font=('TkDefaultFont', 10, 'bold')).grid(row=1, column=0, sticky='w', pady=1)
        tk.Label(frame_top, textvariable=self.avg_var, font=('TkDefaultFont', 10, 'bold')).grid(row=2, column=0, sticky='w', pady=1)

        frame_port = tk.Frame(self.master); frame_port.pack(padx=8, pady=0, anchor='w')
        tk.Label(frame_port, text="Serial Port:").pack(side='left')
        self.combobox_ports = ttk.Combobox(frame_port, width=15, state='readonly')
        self.combobox_ports.pack(side='left', padx=4)
        tk.Button(frame_port, text="Refresh", command=self._populate_com_ports).pack(side='left', padx=4)
        self.button_connect = tk.Button(frame_port, text="Connect", command=self.connect_serial)
        self.button_connect.pack(side='left', padx=4)
        self.button_disconnect = tk.Button(frame_port, text="Disconnect", command=self.disconnect_serial, state='disabled')
        self.button_disconnect.pack(side='left', padx=4)

        frame_dose = tk.Frame(self.master); frame_dose.pack(padx=8, pady=2, anchor='w')
        tk.Button(frame_dose, text="Download Dose Rate CSV", command=self.export_dose_csv).pack(side='left')
        # tk.Button(frame_dose, text="Download Dose Rate N42", command=self.export_dose_n42).pack(side='left')
        tk.Button(frame_dose, text="Clear Dose Data", command=self.clear_dose_history).pack(side='left', padx=6)

        frame_gamma = tk.Frame(self.master); frame_gamma.pack(padx=8, pady=6, anchor='w')
        self.button_gamma = tk.Button(frame_gamma, text="Download Gamma Spec (CSV)", command=self.manual_get_gamma)
        self.button_gamma.pack(side='left')
        self.button_gamma_n42 = tk.Button(frame_gamma, text="Export Spectrum N42", command=self.export_gamma_n42)
        self.button_gamma_n42.pack(side='left', padx=2)        
        self.button_clear_gamma = tk.Button(frame_gamma, text="Clear Gamma Spectrum", command=self.clear_spectrum)
        self.button_clear_gamma.pack(side='left', padx=4)
        self.button_auto_gamma = tk.Button(frame_gamma, text="Start Auto Gamma View", command=self.toggle_auto_gamma)
        self.button_auto_gamma.pack(side='left', padx=4)

        self.checkvar_energy_csv = tk.IntVar(value=1)
        tk.Checkbutton(frame_gamma, text="CSV includes Energy (checked)", variable=self.checkvar_energy_csv).pack(side='left', padx=16)

        self.frame_time = tk.Frame(self.master)
        self.frame_time.pack(padx=8, pady=4, anchor='w')
        tk.Label(self.frame_time, text="Timed Count (minutes):").pack(side='left')
        self.entry_time = tk.Entry(self.frame_time, width=7)
        self.entry_time.insert(0, "10")
        self.entry_time.pack(side='left')
        self.button_start_time = tk.Button(self.frame_time, text="Start Timed Count", command=self.start_timed_count)
        self.button_start_time.pack(side='left', padx=3)
        self.button_stop_time = tk.Button(self.frame_time, text="Stop Timed Count", command=self.abort_timed_count, state='disabled')
        self.button_stop_time.pack(side='left', padx=3)
        self.label_timer = tk.Label(self.frame_time, text="")
        self.label_timer.pack(side='left', padx=5)

        frame_cmd = tk.Frame(self.master); frame_cmd.pack(padx=8, pady=3, anchor='w')
        self.entry_command = tk.Entry(frame_cmd, width=30)
        self.entry_command.pack(side='left')
        tk.Button(frame_cmd, text="Send Data", command=self.send_custom_command).pack(side='left', padx=3)

        frame_plot = tk.Frame(self.master); frame_plot.pack(fill='x', padx=12, pady=8, expand=True)
        self.fig = Figure(figsize=(7, 5), dpi=90)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_xlabel("Channel"); self.ax.set_ylabel("Count"); self.ax.set_title("Gamma Spectrum")
        self.canvas = FigureCanvasTkAgg(self.fig, master=frame_plot)
        self.canvas.get_tk_widget().pack(fill='x', expand=True)
        self._draw_blank_spectrum()

        frame_roi = tk.Frame(self.master)
        frame_roi.pack(fill='x', padx=12, pady=4)
        lbl = tk.Label(frame_roi, text="Cs-137 ROI (gamma 661.7 keV):", font=('TkDefaultFont', 10, 'bold'))
        lbl.pack(side='left')
        self.label_cs137_counts = tk.Label(frame_roi, textvariable=self.cs137_results_var, fg='blue')
        self.label_cs137_counts.pack(side='left', padx=8)
        tk.Button(frame_roi, text="Show ROI on Plot", command=self.show_cs137_roi_overlay).pack(side='left')

    def _populate_com_ports(self):
        ports = [port.device for port in serial.tools.list_ports.comports()]
        self.combobox_ports['values'] = ports
        if ports:
            self.combobox_ports.current(0)
        else:
            self.combobox_ports.set('')

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
        self.abort_timed_count()
        self.stop_auto_gamma(force=True)
        self.serial_stop.set()
        if self.serial_conn:
            try: self.serial_conn.close()
            except: pass
        self.serial_conn = None
        self.status_var.set("Status: Disconnected")
        self.button_connect.config(state='normal')
        self.button_disconnect.config(state='disabled')

    # Enforce mutual exclusion of gamma operations
    def _disable_during_operation(self, spectrum=True, auto=True, timed=True):
        if spectrum:
            self.button_gamma.config(state='disabled')
            self.button_gamma_n42.config(state='disabled')
        if auto:
            self.button_auto_gamma.config(state='disabled')
        if timed:
            self.button_start_time.config(state='disabled')
            self.entry_time.config(state='disabled')

    def _enable_normal_ops(self):
        self.button_gamma.config(state='normal')
        self.button_gamma_n42.config(state='normal')
        self.button_auto_gamma.config(state='normal')
        self.button_start_time.config(state='normal')
        self.entry_time.config(state='normal')

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
                        if line == "Comp":
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
                                # Handle spectrum ending:
                                if self.timed_acquire_active:
                                    self.master.after(0, self.timed_count_completed)
                                elif self.gamma_auto_running:
                                    # Just update plot, nothing to do.
                                    pass
                                elif self.gamma_manual_get:
                                    self.master.after(0, self.manual_gamma_completed)
                                else:
                                    # Shouldn't happen, just show exported spectrum
                                    pass
                                expecting_spectrum = False
                        elif expecting_spectrum and not line:
                            continue
                        elif not expecting_spectrum and ',' not in line:
                            try:
                                dose = float(line)
                                self._handle_dose(dose)
                            except ValueError:
                                continue
                curr = time.time()
                if self.serial_conn and curr - last_dose_time >= 1.0:
                    self._serial_write(b"D"); last_dose_time = curr
                time.sleep(0.05)
        except Exception as e:
            self.status_var.set(f"Status: Serial error: {str(e)}")
            self.disconnect_serial()

    def _serial_write(self, data):
        if not self.serial_conn:
            return
        try:
            with self.writer_lock:
                self.serial_conn.write(data)
        except Exception as e:
            self.status_var.set("Status: Serial error: " + str(e))
            self.disconnect_serial()

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
        if not filename: return
        with open(filename, 'w', newline='') as f:
            w = csv.writer(f)
            w.writerow(["Time", "Dose Rate"])
            for row in self.dose_history:
                w.writerow([row['time'], row['dose']])
        messagebox.showinfo("Dose Rate Data", f"CSV saved: {filename}")

    # def export_dose_n42(self):
    #     if not self.dose_history:
    #         messagebox.showinfo("Dose Rate Data", "No dose data to export.")
    #         return
    #     filename = filedialog.asksaveasfilename(defaultextension='.n42',
    #                                     filetypes=[("N42 XML Files", "*.n42")],
    #                                     title="Save Dose Rate N42")
    #     if not filename: return

    #     root = ET.Element('RadiologicalMeasurements', {'xmlns':"http://physics.nist.gov/N42/2006/N42"})
    #     for row in self.dose_history:
    #         meas = ET.SubElement(root, "Measurement")
    #         ET.SubElement(meas, "Time").text = row['time']
    #         ET.SubElement(meas, "DoseRate").text = str(row['dose'])

    #     tree = ET.ElementTree(root)
    #     tree.write(filename, encoding="utf-8", xml_declaration=True)
    #     messagebox.showinfo("Dose Rate Data", f"N42 XML saved: {filename}")

    #========== SPECTRUM ACQUISITION MUTEX LOGIC ==========

    def manual_get_gamma(self):
        # Cancel all other operations!
        if self.gamma_auto_running:
            self.stop_auto_gamma(force=True)
        if self.timed_acquire_active:
            self.abort_timed_count(forced=True)
        self.gamma_manual_get = True
        self.spectrum = []
        self._disable_during_operation(spectrum=True, auto=True, timed=True)
        self.status_var.set("Status: Waiting for spectrum...")
        self._serial_write(b'G')

    def manual_gamma_completed(self):
        self.gamma_manual_get = False
        self.status_var.set("Status: Manual spectrum collected, saving CSV...")
        self.export_gamma_csv()
        self.export_gamma_n42()
        self._enable_normal_ops()
        self.button_clear_gamma.config(state='normal')

    def clear_spectrum(self):
        if not self.serial_conn:
            messagebox.showinfo("Not Connected", "Connect to the device first.")
            return
        self._serial_write(b'W')
        self.spectrum = []
        self._draw_blank_spectrum()
        self.status_var.set("Status: Clear command sent (W)")

    #========== ROI LOGIC ==========
    # --- New: Cs-137 ROI analysis ---
    def _analyze_cs137_roi(self):
        # If not enough data/energy calibration, don't proceed
        if not self.spectrum or len(self.spectrum) == 0:
            self.cs137_results_var.set("")
            return
        # Extract energies from spectrum
        energies = [energy for count, energy in self.spectrum]
        counts = [count for count, energy in self.spectrum]
        # Find indices in ROI
        roi_min, roi_max = self.cs137_roi
        roi_indices = [i for i, e in enumerate(energies) if roi_min <= e <= roi_max]
        if not roi_indices:
            self.cs137_results_var.set("No channels in ROI.")
            return
        # Integrate counts in ROI
        net_counts = sum([counts[i] for i in roi_indices])
        # Live time: For now, 1s unless you track otherwise
        # (Use actual acquisition time if available)
        live_time = float(self.entry_time.get())
        # Efficiency & gamma abundance
        eff = self.cs137_efficiency
        br = self.cs137_gamma_abundance

        # Activity in Bq (disintegrations/sec)
        activity_bq = net_counts / (live_time * eff * br) if eff * br != 0 else 0
        # Convert Bq to Ci (1 Ci = 3.7e10 Bq)
        activity_ci = activity_bq / 3.7e10

        # Auto-scale the Ci output to μCi, mCi, Ci
        if activity_ci < 1e-3:
            activity_scaled = activity_ci * 1e6
            activity_unit = 'μCi'
        elif activity_ci < 1:
            activity_scaled = activity_ci * 1e3
            activity_unit = 'mCi'
        else:
            activity_scaled = activity_ci
            activity_unit = 'Ci'

        # Format output
        self.cs137_results_var.set(
            f'Cs-137 ROI {roi_min}-{roi_max} keV: '
            f'Counts: {int(net_counts)}, '
            f'Activity: {activity_bq:.1f} Bq, '
            f'{activity_scaled:.3g} {activity_unit}'
        )
        
    # --- Optional: Show/hide ROI overlay on the spectrum plot ---
    def show_cs137_roi_overlay(self):
        if not self.spectrum:
            return
        energies = [energy for count, energy in self.spectrum]
        counts = [count for count, energy in self.spectrum]
        roi_min, roi_max = self.cs137_roi
        roi_indices = [i for i, e in enumerate(energies) if roi_min <= e <= roi_max]
        if roi_indices:
            start = roi_indices[0]
            end = roi_indices[-1]
            self.ax.bar(range(start, end+1), [counts[i] for i in range(start, end+1)],
                        color='orange', alpha=0.4, width=1.0, label='Cs-137 ROI')
            self.ax.legend()
            self.canvas.draw()


    #========== AUTO GAMMA MUTEX LOGIC ==========
    def toggle_auto_gamma(self):
        if not self.serial_conn:
            messagebox.showinfo("Not Connected", "Connect to the device first.")
            return
        if self.gamma_auto_running:
            self.stop_auto_gamma(force=False)
        else:
            # Cancel other conflicts first:
            if self.timed_acquire_active:
                self.abort_timed_count(forced=True)
            if self.gamma_manual_get:
                self.status_var.set("Status: Interrupted manual spectrum for auto gamma start.")
                self.gamma_manual_get = False
            self.gamma_auto_running = True
            self.button_auto_gamma.config(text="Stop Auto Gamma View")
            self._disable_during_operation(spectrum=True, auto=False, timed=True)
            self.button_stop_time.config(state='disabled')
            self.status_var.set("Status: Auto Gamma started.")
            self._start_auto_gamma()

    def stop_auto_gamma(self, force=False):
        self.gamma_auto_running = False
        self.button_auto_gamma.config(text="Start Auto Gamma View")
        self._enable_normal_ops()
        if not self.timed_acquire_active:
            self.button_stop_time.config(state='disabled')
        if not force:
            self.status_var.set("Status: Auto Gamma stopped.")

    def _start_auto_gamma(self):
        def auto_loop():
            while self.gamma_auto_running and self.serial_conn:
                self._serial_write(b'G')
                for _ in range(50):
                    if not self.gamma_auto_running: break
                    time.sleep(0.1)
        self.gamma_auto_thread = threading.Thread(target=auto_loop, daemon=True)
        self.gamma_auto_thread.start()
    #========== TIMED COUNT MUTEX LOGIC ==========

    def start_timed_count(self):
        if not self.serial_conn:
            messagebox.showinfo("Not Connected", "Connect to the device first.")
            return
        if self.gamma_auto_running:
            self.stop_auto_gamma(force=True)
        if self.gamma_manual_get:
            self.gamma_manual_get = False

        try:
            minutes = float(self.entry_time.get())
            if minutes <= 0: raise ValueError
        except:
            messagebox.showerror("Invalid Input", "Enter a positive number for minutes.")
            return
        self.timed_value = minutes
        self.timed_acquire_active = True
        self.timed_acquire_abort.clear()
        self._disable_during_operation(spectrum=True, auto=True, timed=True)
        self.button_stop_time.config(state='normal')
        self.label_timer.config(text=f"Time remaining: {int(minutes):02d}:00")
        self.status_var.set(f"Status: Timed count running for {minutes} min...")
        self.timed_acquire_thread = threading.Thread(target=self.timed_count_thread, args=(minutes,), daemon=True)
        self.timed_acquire_thread.start()

    def timed_count_thread(self, minutes):
        total_secs = int(minutes*60)
        for t in range(total_secs,0,-1):
            if self.timed_acquire_abort.is_set():
                self.master.after(0, self.label_timer.config, {"text": " "})
                self.master.after(0, self.status_var.set, "Status: Abort: acquiring spectrum...")
                self._serial_write(b'G')
                return
            self.master.after(0, self.label_timer.config, {"text":f"Time remaining: {t//60:02d}:{t%60:02d}"})
            time.sleep(1)
        self.master.after(0, self.label_timer.config, {"text": " "})
        if not self.timed_acquire_abort.is_set():
            self.master.after(0, self.status_var.set, "Status: Timed count finished, acquiring spectrum...")
            self._serial_write(b'G')

    def abort_timed_count(self, forced=False):
        if self.timed_acquire_active:
            self.timed_acquire_abort.set()
            if not forced:
                self.label_timer.config(text=" ")
                self.status_var.set("Status: Stopping timed count, please wait for gamma spectrum...")
                self.button_stop_time.config(state='disabled') # The real UI reset is in timed_count_completed

    def timed_count_completed(self):
        self.timed_acquire_active = False
        self.timed_acquire_abort.clear()
        self.label_timer.config(text=" ")
        self.button_stop_time.config(state='disabled')
        self.status_var.set("Status: Timed spectrum collected, saving CSV...")
        self.export_gamma_csv()
        self.export_gamma_n42()
        self._enable_normal_ops()

    #========== END MUTEX LOGIC ==========

    def export_gamma_csv(self):
        if not self.spectrum:
            messagebox.showinfo("No Data", "No gamma spectrum data available.")
            return
        include_energy = self.checkvar_energy_csv.get() == 1
        filename = filedialog.asksaveasfilename(defaultextension='.csv',
                            filetypes=[("CSV Files", "*.csv")],
                            title="Save Gamma Spectrum CSV")
        if not filename: return
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

    def export_gamma_n42(self):
        if not self.spectrum:
            messagebox.showinfo("No Data", "No gamma spectrum data available.")
            return

        filename = filedialog.asksaveasfilename(defaultextension='.n42',
                                                filetypes=[("N42 XML Files", "*.n42")],
                                                title="Save Gamma Spectrum N42")
        if not filename: return

        spectrum_counts = [int(count) for count, energy in self.spectrum]
        energies = [float(energy) for count, energy in self.spectrum]
        n_channels = len(spectrum_counts)

        ns = "http://physics.nist.gov/N42/2006/N42"
        ET.register_namespace('', ns)

        root = ET.Element('RadiologicalInstrumentData', {'xmlns': ns})

        # Header/Instrument info
        group = ET.SubElement(root, "MeasurementGroup")
        measurement = ET.SubElement(group, "Measurement")
        spectrum = ET.SubElement(measurement, "Spectrum")

        instrument = ET.SubElement(spectrum, "InstrumentInformation")
        mn = ET.SubElement(instrument, "Manufacturer")
        mn.text = "AlphaHound"
        md = ET.SubElement(instrument, "Model")
        md.text = "ALPHAHOUND"
        sn = ET.SubElement(instrument, "SerialNumber")
        sn.text = "001"

        # EnergyCalibration: full array
        energy_cal = ET.SubElement(spectrum, "EnergyCalibration")
        cal_fit = ET.SubElement(energy_cal, "CalibrationEquation")
        cal_fit.text = "List"
        channel_energies = ET.SubElement(energy_cal, "ChannelEnergies")
        channel_energies.text = " ".join(f"{e:.5f}" for e in energies)

        # ChannelData
        channeldata = ET.SubElement(spectrum, "ChannelData", NumberOfChannels=str(n_channels))
        channeldata.text = " ".join(str(int(c)) for c in spectrum_counts)

        # Optional meta
        spectrum_time = ET.SubElement(spectrum, "LiveTime")
        spectrum_time.text = "1.0"
        sptype = ET.SubElement(spectrum, "SpectrumType")
        sptype.text = "PHA"

        xmlstr = minidom.parseString(ET.tostring(root)).toprettyxml(indent="  ")
        with open(filename, "w", encoding="utf-8") as f:
            f.write(xmlstr)
        messagebox.showinfo("Export", f"N42 XML saved: {filename}")

    def _draw_spectrum(self):
        self.ax.cla()
        self.ax.set_xlabel("Channel")
        self.ax.set_ylabel("Count")
        self.ax.set_title("Gamma Spectrum")
        if self.spectrum:
            channels = list(range(len(self.spectrum)))
            counts = [count for count,energy in self.spectrum]
            self.ax.bar(channels, counts, color='black', alpha=0.6, width=1.0)
            # After plotting, do ROI calculation for Cs-137 if spectrum present
            self._analyze_cs137_roi()
        self.canvas.draw()

    def _draw_blank_spectrum(self):
        self.ax.cla()
        self.ax.set_xlabel("Channel")
        self.ax.set_ylabel("Count")
        self.ax.set_title("Gamma Spectrum")
        self.canvas.draw()

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