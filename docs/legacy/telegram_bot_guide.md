# Telegram Alert Bot Setup & Greek Operations Guide

**System:** Proactive Behind-the-Meter Energy Management System (EMS)  
**Channel:** Telegram Bot API (Official, Free, High-Reliability)  
**Target Language:** Greek (Ελληνικά) with Business Context  

---

## 1. Why Telegram for Greek Commercial Enterprises?

1. **Zero Recurring API Costs:** Unlike WhatsApp Business or Viber commercial messaging which charge per outbound message or monthly partner tiers, the Telegram Bot API is **100% free with unlimited proactive alerts**.
2. **Instant Delivery:** Sub-second push notification latency ensures owners can react within the 30-minute high-tariff peak window.
3. **Rich Text & Formatting:** Supports bold headers, emojis, code tags, and inline buttons for clear comprehension on mobile devices.
4. **Group Support:** Can alert multiple stakeholders simultaneously (e.g. business owner, head baker, maintenance technician).

---

## 2. Step-by-Step Bot Creation (BotFather)

### Step 1: Create the Bot
1. Open Telegram on your phone or desktop.
2. Search for `@BotFather` (verified official bot with blue badge).
3. Send `/newbot`.
4. Choose a friendly name: e.g. `Athens Bakery EMS Bot`.
5. Choose a unique username ending in `bot`: e.g. `AthensBakery_ems_bot`.
6. BotFather will reply with your secret **HTTP API Token**:
   ```
   7123456789:AAFlkjw98234-example-token-here
   ```
7. Copy this token into your `.env` file:
   ```ini
   TELEGRAM_BOT_TOKEN=7123456789:AAFlkjw98234-example-token-here
   ```

### Step 2: Configure Bot Commands Menu
Send `/setcommands` to `@BotFather`, select your bot, and paste:
```text
status - Στιγμιαία ισχύς, τάσεις και κόστος (€/h)
cost_today - Συνολική ενέργεια kWh & έξοδα ημέρας (€)
tariff - Ενεργό συμβόλαιο, χρώμα και πρόγραμμα αιχμής
settings - Ρυθμισμένα όρια kW και χρόνοι ειδοποίησης
help - Οδηγός χρήσης και επεξήγηση εντολών
```

---

## 3. Discovering Your Telegram Chat ID

To deliver proactive alerts, the system needs the destination Chat ID (individual or group):

### Method A: Individual User Chat ID
1. Search for `@userinfobot` on Telegram and send `/start`.
2. It will reply with your numeric User ID (e.g., `999111222`).
3. Add it to your `.env` file:
   ```ini
   TELEGRAM_DEFAULT_CHAT_ID=999111222
   ```

### Method B: Business Staff Group Chat ID
1. Create a new Telegram group (e.g. `Αρτοποιείο - Ενεργειακός Έλεγχος`).
2. Add your bot to the group.
3. Send any message in the group (e.g. `test`).
4. In your browser or curl, run:
   ```bash
   curl https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates
   ```
5. Look for `"chat":{"id":-1001234567890,...}`. Group IDs always start with `-` or `-100`.
6. Set `TELEGRAM_DEFAULT_CHAT_ID=-1001234567890`.

---

## 4. Message Templates & Real-Life Alert Examples

### 4.1 Proactive Peak Breach Warning (Critical Alert)
Dispatched when load breaches the configured threshold during high-tariff windows after the 3-sample debounce filter passes:

```
[ΠΡΟΣΟΧΗ: ΥΠΕΡΒΑΣΗ ΟΡΙΟΥ ΣΕ ΖΩΝΗ ΑΙΧΜΗΣ]

Εγκατάσταση: Bakery Central Athens
Συνολικό Φορτίο: 32.5 kW (Όριο: 22.0 kW)
Υπέρβαση: +10.5 kW

Ζώνη Αιχμής: 14:00 - 17:00 (Τιμολόγιο Γ22 Πράσινο)
Τρέχουσα Χρέωση: 0.2450 €/kWh
Εκτιμώμενη Επιπλέον Επιβάρυνση Σήμερα: +1.68 €

Προτεινόμενη Ενέργεια:
Μεταφέρετε το ψήσιμο παρτίδας στη ζώνη μειωμένης χρέωσης ή σβήστε προσωρινά 1 φούρνο.
```

---

### 4.2 Sudden Escalation Alert
Triggered if the load jumps $\ge 25\%$ higher even during an active 30-minute cooldown window:

```
[ΚΛΙΜΑΚΩΣΗ ΥΠΕΡΒΑΣΗΣ ΦΟΡΤΙΟΥ]

Εγκατάσταση: Bakery Central Athens
Προηγούμενο Φορτίο: 24.0 kW
Νέο Φορτίο: 36.2 kW (+50.8% άλμα)
Συμφωνημένη Ισχύς: 35.0 kVA

Κίνδυνος υπέρβασης συμφωνημένης ισχύος και ενεργοποίησης προστίμου ισχύος ΔΕΔΔΗΕ.
```

---

### 4.3 Normalization Recovery Alert
Dispatched once load drops below the 10% hysteresis threshold ($0.90 \times 22.0 = 19.8\text{ kW}$):

```
[ΟΜΑΛΟΠΟΙΗΣΗ ΚΑΤΑΝΑΛΩΣΗΣ]

Εγκατάσταση: Bakery Central Athens
Τρέχον Φορτίο: 18.2 kW (Εντός ορίων <= 19.8 kW)
Κατάσταση: Κανονική Λειτουργία

Η κατανάλωση επανήλθε σε ασφαλή επίπεδα. Το σύστημα επανατέθηκε σε επιτήρηση.
```

---

## 5. Interactive Commands Directory

### `/status`
```text
ΚΑΤΑΣΤΑΣΗ ΕΓΚΑΤΑΣΤΑΣΗΣ: Bakery Central Athens
----------------------------------------
Φάση L1: 230.2 V | 26.4 A | 5.95 kW (cos φ: 0.98)
Φάση L2: 229.8 V | 25.8 A | 5.82 kW (cos φ: 0.98)
Φάση L3: 231.0 V | 27.1 A | 6.13 kW (cos φ: 0.98)

Σύνολο Ενεργού Ισχύος: 17.90 kW
Σύνολο Φαινομενικής: 18.27 kVA
Συντελεστής Ισχύος Συστήματος: 0.98

Στιγμιαίο Κόστος: 4.39 €/ώρα
Ζώνη: Αιχμή (14:00 - 17:00) | Όριο: 22.0 kW
Κατάσταση: Εντός Ορίου
```

### `/cost_today`
```text
ΕΝΕΡΓΕΙΑΚΟΣ ΑΠΟΛΟΓΙΣΜΟΣ ΗΜΕΡΑΣ (14/09/2026)
----------------------------------------
Εγκατάσταση: Bakery Central Athens
Συνολική Ενέργεια: 240.50 kWh
Συνολικό Κόστος: 48.60 €

- Ζώνη Αιχμής: 85.20 kWh (21.40 €)
- Ζώνη Μειωμένης: 155.30 kWh (27.20 €)

Μέση Πραγματική Τιμή: 0.202 €/kWh
Πρόστιμο Υπέρβασης Ισχύος: 0.00 €
Πρόστιμο Cos φ: 0.00 € (Cos φ = 0.98 >= 0.85)
```

### `/tariff`
```text
ΠΡΟΦΙΛ ΤΙΜΟΛΟΓΙΟΥ
----------------------------------------
Εγκατάσταση: Bakery Central Athens
Συμβόλαιο: Γ22 (Χαμηλή Τάση, Εμπορικό Διπλής Χρέωσης)
Χρώμα: Πράσινο (Ειδικό Τιμολόγιο Ν. 5068/2023)
Συμφωνημένη Ισχύς: 35.0 kVA

Ωράριο Αιχμής (Θερινό): 14:00 - 17:00 (Δευτέρα - Παρασκευή)
Ωράριο Μειωμένου: 23:00 - 07:00 (Καθημερινά) & Σαββατοκύριακα

Βασική Τιμή Προμήθειας: 0.1450 €/kWh
Μηχανισμός Διακύμανσης: +0.0370 €/kWh
Ρυθμιζόμενες Χρεώσεις: 0.0630 €/kWh
Συνολική Εκτιμώμενη Χρέωση: 0.2450 €/kWh + 6% ΦΠΑ
```

---

## 7. Unified Multi-Channel Alerting: Viber Bot Setup

In addition to Telegram, the Behind-the-Meter EMS supports **Viber Bot** alerting for Greek business owners who prefer receiving operational notifications directly on Viber.

### Step 1: Create a Viber Bot
1. Open the [Viber Partners Portal](https://partners.viber.com/).
2. Create a new Bot account with your business details.
3. Obtain your **Viber Bot Authentication Token**.
4. Configure in your `.env` file:
   ```ini
   VIBER_BOT_TOKEN=4f8b9...-your-viber-token
   VIBER_WEBHOOK_URL=https://your-domain.gr/api/v1/viber/webhook
   ```

### Step 2: Configure Notification Target (Telegram / Viber / Both)
Each commercial facility can configure its alerting channel preference:
- **`telegram`**: Sends alerts exclusively to the configured Telegram chat.
- **`viber`**: Sends clean, plain-text alerts to the Viber receiver ID.
- **`both`**: Simultaneously dispatches proactive alerts to both Telegram and Viber.

This can be configured dynamically either via the **Web Dashboard** (`http://localhost:8000/dashboard`) or via the REST API:
```bash
curl -X POST http://localhost:8000/api/v1/dashboard/config/bakery-central-athens \
  -H "Content-Type: application/json" \
  -d '{
    "notification_channel": "both",
    "chat_id": 999111222,
    "viber_receiver_id": "vb_usr_bakery_123"
  }'
```

### Step 3: Interactive Commands on Viber
The Viber Webhook handles the exact same conversational Greek commands as Telegram (`/status`, `/cost_today`, `/tariff`, `/settings`, `/help`) with HTML tags automatically stripped for clean display on Viber mobile clients.
