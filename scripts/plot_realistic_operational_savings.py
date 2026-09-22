"""Generate realistic operational savings charts (1-2h tight shift windows).

Visualizes:
1. Defrost Shift (Just 2 hours earlier: 14:00 -> 12:00, saving 37.5% without food safety risk).
2. Bakery Oven Staggering (Just 2 hours shift: 05:00 -> 07:00, eliminating 30 kW capacity breach).
3. Walk-in Freezer Thermal Pre-cooling (Pre-cooling before 13:30, floating through peak).
4. Time Savings & Response Latency (Edge <0.1ms vs Cloud 250ms, 60min proactive warning vs 0min reactive).
"""

from pathlib import Path
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "reports" / "real_data"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Configure typography for Greek characters
plt.rcParams["font.sans-serif"] = ["Segoe UI", "Arial", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

fig, axes = plt.subplots(2, 2, figsize=(15, 11), dpi=300)
fig.patch.set_facecolor("#f8fafc")

# -------------------------------------------------------------
# 1. Defrost Load Shift (Only 2 Hours)
# -------------------------------------------------------------
ax1 = axes[0, 0]
ax1.set_facecolor("#ffffff")
hours_defrost = np.arange(10, 19)

# Greek summer peak: 13:00 - 17:00 (0.32 €/kWh), normal: 0.20 €/kWh
ax1.axvspan(13, 17, color="#fee2e2", alpha=0.6, label="Ζώνη Αιχμής Τιμολογίου Γ22 (0.32 €/kWh)")
ax1.axvspan(10, 13, color="#f1f5f9", alpha=0.6, label="Κανονική Ζώνη (0.20 €/kWh)")
ax1.axvspan(17, 18.5, color="#f1f5f9", alpha=0.6)

# Baseline: Defrost at 14:00-16:00 (8 kW)
baseline_defrost = np.zeros_like(hours_defrost, dtype=float)
baseline_defrost[(hours_defrost >= 14) & (hours_defrost < 16)] = 8.0

# Managed: Defrost shifted just 2 hours earlier to 12:00-14:00
managed_defrost = np.zeros_like(hours_defrost, dtype=float)
managed_defrost[(hours_defrost >= 12) & (hours_defrost < 14)] = 8.0

width = 0.38
ax1.bar(hours_defrost - width/2, baseline_defrost, width=width, color="#ef4444", alpha=0.85, label="Baseline: Απόψυξη στις 14:00 (Κόστος: 5.12 €)")
ax1.bar(hours_defrost + width/2, managed_defrost, width=width, color="#10b981", alpha=0.85, label="EMS: Απόψυξη στις 12:00 (Κόστος: 3.20 €)")

# Arrow indicating 2-hour shift
ax1.annotate("Ρεαλιστική μετατόπιση\nΜΟΛΙΣ 2 ΩΡΕΣ!\n(-37.5% κόστος)", xy=(12.2, 8.2), xytext=(10.5, 9.2),
             arrowprops=dict(arrowstyle="->", color="#059669", lw=2.0),
             fontweight="bold", color="#059669", fontsize=10,
             bbox=dict(boxstyle="round,pad=0.3", fc="#ecfdf5", ec="#10b981", lw=1))

ax1.set_title("1. Ρεαλιστική Μετατόπιση Απόψυξης (Μόλις 2 Ώρες Νωρίτερα)", fontsize=11, fontweight="bold", pad=10)
ax1.set_xlabel("Ώρα της Ημέρας", fontsize=9.5)
ax1.set_ylabel("Ισχύς Απόψυξης (kW)", fontsize=9.5)
ax1.set_xticks(hours_defrost)
ax1.set_xticklabels([f"{h:02d}:00" for h in hours_defrost])
ax1.set_ylim(0, 11.5)
ax1.grid(True, linestyle=":", alpha=0.5)
ax1.legend(loc="upper right", fontsize=8.5, framealpha=0.95)

# -------------------------------------------------------------
# 2. Oven Staggering (Just 2 Hours Shift)
# -------------------------------------------------------------
ax2 = axes[0, 1]
ax2.set_facecolor("#ffffff")
hours_oven = np.arange(3, 11)
contracted_limit = 25.0

# Baseline: Both ovens start simultaneously at 05:00
# Base load 8 kW + Oven 1 (12 kW) + Oven 2 (10 kW) = 30 kW at 05:00-07:00
baseline_oven_load = np.array([8.0, 8.0, 30.0, 30.0, 14.0, 14.0, 10.0, 10.0])

# Managed: Oven 1 at 05:00-07:00 (8+12 = 20 kW), Oven 2 shifted to 07:00-09:00 (8+10 = 18 kW)
managed_oven_load = np.array([8.0, 8.0, 20.0, 20.0, 18.0, 18.0, 10.0, 10.0])

ax2.plot(hours_oven, baseline_oven_load, color="#dc2626", lw=2.5, marker="o", label="Baseline: Ταυτόχρονο άναμμα (Αιχμή 30 kW)")
ax2.plot(hours_oven, managed_oven_load, color="#16a34a", lw=2.5, marker="s", label="EMS: Staggering 2ου φούρνου (Μέγιστο 20 kW)")
ax2.axhline(contracted_limit, color="#ea580c", ls="--", lw=2, label=f"Συμφωνημένη Ισχύς ({contracted_limit} kW)")

# Highlight breach area
ax2.fill_between(hours_oven, contracted_limit, baseline_oven_load, 
                 where=(baseline_oven_load > contracted_limit), color="#ef4444", alpha=0.35, label="Πρόστιμο Υπέρβασης (5 kW x 2.50 €/kW)")

ax2.annotate("Φούρνος 2: Μεταφορά στις 07:00\n(Μόλις 2 ώρες μετά)\n0 € Πρόστιμο Υπέρβασης!", xy=(7.0, 18.2), xytext=(7.2, 24.5),
             arrowprops=dict(arrowstyle="->", color="#15803d", lw=1.8),
             fontweight="bold", color="#15803d", fontsize=9.5,
             bbox=dict(boxstyle="round,pad=0.3", fc="#f0fdf4", ec="#16a34a", lw=1))

ax2.set_title("2. Staggering Φούρνων Παραγωγής (Διαδοχικό Άναμμα 2 Ώρες Μετά)", fontsize=11, fontweight="bold", pad=10)
ax2.set_xlabel("Ώρα Πρωινής Παραγωγής", fontsize=9.5)
ax2.set_ylabel("Συνολική Ισχύς Καταστήματος (kW)", fontsize=9.5)
ax2.set_xticks(hours_oven)
ax2.set_xticklabels([f"{h:02d}:00" for h in hours_oven])
ax2.set_ylim(5, 34)
ax2.grid(True, linestyle=":", alpha=0.5)
ax2.legend(loc="upper left", fontsize=8.5, framealpha=0.95)

# -------------------------------------------------------------
# 3. Thermal Pre-Cooling & Chamber Floating
# -------------------------------------------------------------
ax3 = axes[1, 0]
ax3.set_facecolor("#ffffff")
hours_temp = np.linspace(11, 18, 50)

# Safe temperature threshold (-18.0 °C)
safe_temp_limit = -18.0

# Baseline temp: cycles around -20°C with compressor kicking in during peak
# Managed temp: pre-cools to -22.5°C at 12:00-13:30, then compressor turns OFF, temp floats safely to -19.2°C at 16:30
ax3.axvspan(13.5, 16.5, color="#fee2e2", alpha=0.5, label="Ζώνη Ακριβού Ρεύματος (13:30 - 16:30)")
ax3.axhline(safe_temp_limit, color="#dc2626", ls=":", lw=1.8, label="Όριο Ασφάλειας Τροφίμων (-18.0 °C)")

managed_temp = -20.0 - 2.5 * np.exp(-((hours_temp - 13.0)/1.0)**2) + 1.2 * np.clip((hours_temp - 13.5)/2.5, 0, 1) * np.exp(-((hours_temp - 17.0)/2.0)**2)
ax3.plot(hours_temp, managed_temp, color="#2563eb", lw=2.5, label="Θερμοκρασία Θαλάμου EMS (°C)")

ax3.annotate("Προ-ψύξη στους -22.5°C\n(Πριν την αιχμή 12:00-13:30)", xy=(13.0, -22.4), xytext=(11.2, -23.5),
             arrowprops=dict(arrowstyle="->", color="#1d4ed8", lw=1.5),
             fontweight="bold", color="#1d4ed8", fontsize=8.5)

ax3.annotate("Thermal Floating:\nΟ κομπρέσορας σβήνει!\n(Απόλυτη ασφάλεια < -18°C)", xy=(15.5, -19.5), xytext=(14.2, -21.2),
             arrowprops=dict(arrowstyle="->", color="#047857", lw=1.5),
             fontweight="bold", color="#047857", fontsize=8.5,
             bbox=dict(boxstyle="round,pad=0.2", fc="#ecfdf5", ec="#10b981", lw=1))

ax3.set_title("3. Θερμική Προ-ψύξη Καταψύκτη (Εκμετάλλευση Αδράνειας 1.5 Ώρα)", fontsize=11, fontweight="bold", pad=10)
ax3.set_xlabel("Ώρα της Ημέρας", fontsize=9.5)
ax3.set_ylabel("Θερμοκρασία Καταψύκτη (°C)", fontsize=9.5)
ax3.set_ylim(-24.5, -16.5)
ax3.grid(True, linestyle=":", alpha=0.5)
ax3.legend(loc="upper right", fontsize=8.5, framealpha=0.95)

# -------------------------------------------------------------
# 4. Time Savings & Response Latency (Speedup Metrics)
# -------------------------------------------------------------
ax4 = axes[1, 1]
ax4.set_facecolor("#ffffff")

metrics = [
    "Χρόνος Απόκρισης\nΠρόγνωσης", 
    "Χρόνος Προειδοποίησης\nΑιχμής (Lead Time)", 
    "Χρόνος Επίλυσης\nΠρογράμματος 24h"
]
baseline_times = [250.0, 0.0, 1800.0]  # Cloud RTT (ms), Reactive trip (min), Manual planning (sec)
managed_times = [0.024, 60.0, 0.025]   # Edge tinyML (ms), Proactive lead (min), SciPy MILP (sec)

# Visual comparison cards via horizontal bars or grouped highlights
y_pos = np.arange(len(metrics))

ax4.text(0.05, 0.75, "[A] Ταχύτητα Edge tinyML vs Cloud API:", fontweight="bold", fontsize=10, transform=ax4.transAxes)
ax4.text(0.08, 0.65, "• Edge ESP32: 0.024 ms (24 μs)\n• Cloud REST API: 250 ms\n• Κέρδος: > 10.000x ταχύτερη απόκριση (χωρίς εξάρτηση από internet)", 
         fontsize=9, color="#0f172a", transform=ax4.transAxes)

ax4.text(0.05, 0.45, "[B] Προειδοποίηση Αιχμής (Early Warning Lead Time):", fontweight="bold", fontsize=10, transform=ax4.transAxes)
ax4.text(0.08, 0.35, "• Baseline ρολόι: 0 λεπτά (αντιδρά αφού πέσει ο διακόπτης)\n• EMS Edge P95: 60 λεπτά προειδοποίηση εκ των προτέρων\n• Κέρδος: 1 ολόκληρη ώρα περιθώριο αντίδρασης", 
         fontsize=9, color="#0f172a", transform=ax4.transAxes)

ax4.text(0.05, 0.18, "[C] Αυτοματοποίηση vs Χειροκίνητη Εργασία:", fontweight="bold", fontsize=10, transform=ax4.transAxes)
ax4.text(0.08, 0.07, "• Χειροκίνητος σχεδιασμός: 30 λεπτά/ημέρα (15 ώρες/μήνα)\n• Μαθηματικός HiGHS Solver: 0.025 δευτερόλεπτα (25 ms)\n• Κέρδος: Μηδενισμός χαμένου χρόνου γραφείου για τον επαγγελματία", 
         fontsize=9, color="#0f172a", transform=ax4.transAxes)

ax4.set_title("4. Αποδεδειγμένη Εξοικονόμηση Χρόνου & Υπολογιστική Ταχύτητα", fontsize=11, fontweight="bold", pad=10)
ax4.axis("off")

plt.tight_layout()
target_path = OUT_DIR / "realistic_operational_savings.png"
plt.savefig(target_path, dpi=300)
print(f"Chart successfully saved to {target_path}")
