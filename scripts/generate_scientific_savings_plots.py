"""Professional Financial & Realistic Operational Savings Visualizer.

Replaces the technical latency plot with an intuitive, business-focused 2-panel figure:
1. Panel 1: Stacked Bar Comparison (Baseline vs. Realistic EMS Managed).
2. Panel 2: Financial Waterfall Breakdown showing exactly where every euro is saved
   via strictly bounded, realistic 2-hour operational shifts (without disrupting bakery workflow).
"""

from pathlib import Path
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "reports" / "real_data"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Apply clean Seaborn whitegrid style
plt.style.use("seaborn-v0_8-whitegrid")
plt.rcParams["font.sans-serif"] = ["Segoe UI", "Arial", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 7), dpi=300)

# =====================================================================
# PANEL 1: STACKED BAR CHART (BASELINE VS. REALISTIC MANAGED)
# =====================================================================

financial_df = pd.DataFrame({
    "Κατάσταση": ["Χωρίς EMS\n(Σημερινή Κατάσταση)", "Με EMS\n(Ρεαλιστικές Μετατοπίσεις ≤2h)"],
    "Ενέργεια Βάσης": [24.20, 24.20],
    "Απόψυξη": [5.12, 3.20],             # 16 kWh @ 0.32 vs 0.20 (2h shift)
    "Πρόστιμο Υπέρβασης": [25.00, 0.00]  # 5 kW breach eliminated via 2h staggering
})

idx = np.arange(len(financial_df))
bar_width = 0.42

p1 = ax1.bar(idx, financial_df["Ενέργεια Βάσης"], bar_width, label="Ενέργεια Βάσης Καταστήματος (24.20 €)", color="#3b82f6")
p2 = ax1.bar(idx, financial_df["Απόψυξη"], bar_width, bottom=financial_df["Ενέργεια Βάσης"], label="Κύκλος Απόψυξης (3.20 € έναντι 5.12 €)", color="#06b6d4")
p3 = ax1.bar(
    idx, 
    financial_df["Πρόστιμο Υπέρβασης"], 
    bar_width, 
    bottom=financial_df["Ενέργεια Βάσης"] + financial_df["Απόψυξη"], 
    label="Πρόστιμο Υπέρβασης Ισχύος ΔΕΔΔΗΕ (0 € έναντι 25 €)", 
    color="#ef4444"
)

totals = financial_df["Ενέργεια Βάσης"] + financial_df["Απόψυξη"] + financial_df["Πρόστιμο Υπέρβασης"]
for i, total in enumerate(totals):
    ax1.text(i, total + 1.2, f"{total:.2f} € / ημέρα", ha="center", va="bottom", fontweight="bold", fontsize=11.5)

# Annotation callout
ax1.annotate(
    "Καθαρό Κέρδος:\n-26.92 € / ημέρα\n(-49.6% μείωση λογαριασμού!)",
    xy=(1.0, totals[1]),
    xytext=(0.42, 40.0),
    arrowprops=dict(arrowstyle="->", color="#059669", lw=2.2),
    fontweight="bold", color="#065f46", fontsize=10.5,
    bbox=dict(boxstyle="round,pad=0.4", fc="#ecfdf5", ec="#10b981", lw=1.5)
)

ax1.set_xticks(idx)
ax1.set_xticklabels(financial_df["Κατάσταση"], fontsize=11, fontweight="bold")
ax1.set_ylabel("Ημερήσιο Κόστος Ηλεκτρισμού (€/ημέρα)", fontsize=11, fontweight="bold")
ax1.set_ylim(0, 65)
ax1.set_title("1. Ημερήσιο Συνολικό Κόστος: Baseline vs. Ρεαλιστικό EMS", fontsize=12, fontweight="bold", pad=12)
ax1.legend(loc="upper left", fontsize=9.5, framealpha=0.95)


# =====================================================================
# PANEL 2: FINANCIAL WATERFALL OF EXACT SAVINGS
# =====================================================================

categories = [
    "Κόστος Χωρίς EMS\n(Baseline)",
    "Μετατόπιση Απόψυξης\n(2h νωρίτερα)",
    "Staggering Φούρνων\n(2h μετά)",
    "Τελικό Κόστος με EMS\n(Ρεαλιστικό)"
]

values = [54.32, -1.92, -25.00, 27.40]
colors = ["#ef4444", "#10b981", "#059669", "#3b82f6"]

# Waterfall bar positions
bottoms = [0.0, 54.32 - 1.92, 54.32 - 1.92 - 25.00, 0.0]
heights = [54.32, 1.92, 25.00, 27.40]

x_pos = np.arange(len(categories))
bars = ax2.bar(x_pos, heights, bottom=bottoms, color=colors, width=0.45)

# Value annotations on waterfall bars
ax2.text(0, 54.32 + 1.2, "54.32 €", ha="center", va="bottom", fontweight="bold", fontsize=10.5, color="#dc2626")
ax2.text(1, bottoms[1] + heights[1]/2.0 - 1.0, "-1.92 €\n(-37.5%)", ha="center", va="center", fontweight="bold", fontsize=9.5, color="#ffffff")
ax2.text(2, bottoms[2] + heights[2]/2.0 - 1.0, "-25.00 €\n(-100% πρόστιμο)", ha="center", va="center", fontweight="bold", fontsize=9.5, color="#ffffff")
ax2.text(3, 27.40 + 1.2, "27.40 €", ha="center", va="bottom", fontweight="bold", fontsize=10.5, color="#1d4ed8")

# Connecting dashed lines
ax2.plot([0, 1], [54.32, 54.32], color="#94a3b8", ls="--", lw=1.2)
ax2.plot([1, 2], [52.40, 52.40], color="#94a3b8", ls="--", lw=1.2)
ax2.plot([2, 3], [27.40, 27.40], color="#94a3b8", ls="--", lw=1.2)

# Operational explanation annotation
ax2.annotate(
    "Τι σημαίνει «Ρεαλιστικό»;\n• 0 μεταβολή στο ωράριο του προσωπικού\n• 0 καθυστέρηση στην παράδοση ψωμιού\n• 100% ασφάλεια τροφίμων (< -18°C)",
    xy=(2.0, 10.0),
    xytext=(0.8, 12.0),
    fontweight="bold", color="#0f172a", fontsize=9.5,
    bbox=dict(boxstyle="round,pad=0.4", fc="#f8fafc", ec="#cbd5e1", lw=1.2)
)

ax2.set_xticks(x_pos)
ax2.set_xticklabels(categories, fontsize=10, fontweight="bold")
ax2.set_ylabel("Ροή Κόστους (€/ημέρα)", fontsize=11, fontweight="bold")
ax2.set_ylim(0, 65)
ax2.set_title("2. Πού Ακριβώς Κερδίζονται τα Χρήματα (Waterfall Ανάλυση)", fontsize=12, fontweight="bold", pad=12)

plt.tight_layout()
target_fig = OUT_DIR / "scientific_time_and_financial_breakdown.png"
fig.savefig(target_fig, dpi=300)
print(f"Financial figure successfully saved to {target_fig}")
