/*
 * ============================================================
 *  FarmCam ESP32 — WiFi Hardware Bridge  v2.2
 *  Hardware: ESP32 DevKit V1 + SIM800L EVB
 *            + 1x Relay Module + Active Buzzer
 * ============================================================
 *
 *  ARCHITECTURE (v2 — WiFi over USB Serial)
 *  ─────────────────────────────────────────
 *  The ESP32 joins the same WiFi hotspot (FarmCam / farmcam1)
 *  that the RPi5 is connected to.  RPi5 sends commands via HTTP
 *  POST to this ESP32's local IP.  No USB cable needed between
 *  RPi5 and ESP32 in production — both are on the same LAN.
 *
 *  RPi5 discovers the ESP32 at:  http://<ESP32_IP>:<ESP32_PORT>/cmd
 *  The ESP32 IP is printed to Serial on boot and sent via SMS.
 *
 *  v2.2 CHANGE: added incoming-SMS detection. When someone texts
 *  the number inside the SIM800L, the message is now parsed and
 *  printed to the Serial Monitor as a formatted block. Nothing
 *  from v2.1 was removed.
 *
 * ============================================================
 *  PIN CONNECTION TABLE
 * ============================================================
 *
 *  ┌────────────────────┬──────────────────────────────────────┐
 *  │ ESP32 Pin          │ Connected To                         │
 *  ├────────────────────┼──────────────────────────────────────┤
 *  │ GPIO17 (TX2/U2TXD) │ SIM800L EVB  RXD                     │
 *  │ GPIO16 (RX2/U2RXD) │ SIM800L EVB  TXD                     │
 *  │ GPIO26             │ Buzzer (+) positive leg              │
 *  │ GPIO27             │ Relay IN1 → Single Pump (Pesticide)  │
 *  │ 5V  (VIN)          │ Relay Board VCC                      │
 *  │ GND                │ Relay Board GND + Buzzer (−)         │
 *  └────────────────────┴──────────────────────────────────────┘
 *
 *  SIM800L EVB — separate 5V/2A PSU, shared GND with ESP32.
 *
 *  PUMP / RELAY WIRING:
 *  COM → 12V PSU (+)  |  NO → Pump (+)  |  Pump (−) → PSU (−)
 *
 *  POWER SUMMARY:
 *  - ESP32      : USB or dedicated 5V supply
 *  - SIM800L EVB: Separate 5V / 2A PSU (peaks ~2A on SMS send)
 *  - Relay Board: ESP32 5V (VIN) pin
 *  - Pump       : 12V / 6A via relay COM → NO
 *  - COMMON GND : Tie all GNDs (ESP32, SIM800L, PSU, pump)
 *
 * ============================================================
 *  HTTP COMMANDS  (POST /cmd  body: text/plain)
 * ============================================================
 *  BUZZ_ALERT          → Pattern 1 – Pest alert  (fast triple beep)
 *  BUZZ_FUNGUS         → Pattern 2 – Fungus alert (slow two-pulse)
 *  BUZZ_CRITICAL       → Pattern 3 – Critical / HIGH (SOS tone)
 *  BUZZ_OFF            → Silence buzzer immediately
 *  PUMP_ON             → Activate relay (start spray)
 *  PUMP_OFF            → Deactivate relay (stop spray)
 *  SMS:<text>          → Send SMS via SIM800L
 *  STATUS              → Returns JSON status
 *
 *  RESPONSE:  "OK" on success, "ERR:<msg>" on failure
 * ============================================================
 *  Farmer receives SMS at +639916425163. SIM800L SIM is +639693141995.
 * ============================================================
 */

#include <WiFi.h>
#include <WebServer.h>

// ─── WiFi Credentials ────────────────────────────────────────
// Must match the hotspot that RPi5 also connects to
const char* WIFI_SSID = "FarmCam";
const char* WIFI_PASS = "farmcam1";

// ─── Farmer SMS Number ────────────────────────────────────────
const char* FARMER_PHONE = "639916425163";   // farmer handset, no '+' (SIM800L)flj
const char* SIM800L_OWN  = "639693141995";   // SIM inside SIM800L (sender)

// ─── Network ─────────────────────────────────────────────────
#define ESP32_HTTP_PORT   80      // HTTP server port (RPi5 posts here)

// ─── Pin Definitions ─────────────────────────────────────────
#define RELAY_PIN      27   // Relay IN → Single Pump (Pesticide/Fungicide)
#define BUZZER_PIN     26   // Active buzzer
#define SIM_RX_PIN     16   // UART2 RX ← SIM800L TXD
#define SIM_TX_PIN     17   // UART2 TX → SIM800L RXD

// ─── Hardware Config ─────────────────────────────────────────
#define RELAY_ACTIVE_LOW  true   // Standard blue relay board: LOW = ON
#define USB_BAUD          115200 // Serial monitor baud
#define SIM_BAUD          9600   // SIM800L default

// ─── Buzzer Pattern Timing (ms) ──────────────────────────────
// Pattern 1: PEST    — fast triple beep (urgent, short)
// Pattern 2: FUNGUS  — slow double pulse (warning, moderate)
// Pattern 3: CRITICAL— SOS morse (3short+3long+3short, severe)
#define BUZZ_SHORT_MS  150
#define BUZZ_LONG_MS   500
#define BUZZ_GAP_MS    120
#define BUZZ_PAUSE_MS  300

// ─── Globals ─────────────────────────────────────────────────
HardwareSerial sim800l(2);
WebServer      server(ESP32_HTTP_PORT);

bool pumpActive    = false;
bool sim800lReady  = false;
String simLastError = "Not initialized";
String smsLastResult = "none";
unsigned long simRetryAt = 0;
bool simDiagnosticsPending = false;
bool manualBuzz = false;
unsigned long pumpOffAt = 0;   // auto-stop pesticide / safety watchdog
int  pendingBuzz   = 0;      // 1=pest 2=fungus 3=critical 4=spray tone

// ─── Incoming SMS parsing (v2.2 addition) ─────────────────────
String simIncomingBuffer = "";
bool expectingSmsBody = false;
String pendingIncomingSender = "";

// ─── SMS Queue (prevents dropped/overwritten SMS) ─────────────
#define SMS_QUEUE_SIZE 6
String smsQueue[SMS_QUEUE_SIZE];
int smsHead = 0;
int smsTail = 0;
int smsCount = 0;

bool queueSMS(String msg) {
  if (msg.length() == 0 || smsCount >= SMS_QUEUE_SIZE) return false;
  if (smsCount < SMS_QUEUE_SIZE) {
    smsQueue[smsTail] = msg;
    smsTail = (smsTail + 1) % SMS_QUEUE_SIZE;
    smsCount++;
    Serial.print("[SMS QUEUE] Enqueued: "); Serial.println(msg);
  }
  return true;
}

// ─────────────────────────────────────────────────────────────
//  RELAY HELPERS
// ─────────────────────────────────────────────────────────────
void relayON()  { digitalWrite(RELAY_PIN, RELAY_ACTIVE_LOW ? LOW  : HIGH); }
void relayOFF() { digitalWrite(RELAY_PIN, RELAY_ACTIVE_LOW ? HIGH : LOW);  }

// Run this during ALL modem/buzzer waits, not just at the top of loop().
void servicePumpWatchdog() {
  if (pumpActive && (long)(millis() - pumpOffAt) >= 0) {
    relayOFF();
    pumpActive = false;
    pumpOffAt = 0;
    if (manualBuzz) digitalWrite(BUZZER_PIN, LOW);
    manualBuzz = false;
    Serial.println("[PUMP] auto-OFF (safety timeout)");
  }
}

void serviceHardware() {
  servicePumpWatchdog();
  server.handleClient();
  servicePumpWatchdog();
}

// ─────────────────────────────────────────────────────────────
//  BUZZER PATTERNS
//  All patterns are blocking but short (<5s), run in-place.
//  Non-blocking async version can be added if needed.
// ─────────────────────────────────────────────────────────────
void _yieldHttp(unsigned long ms) {
  unsigned long t0 = millis();
  while (millis() - t0 < ms) {
    serviceHardware();
    delay(5);
  }
}

void _buzz(int onMs, int offMs) {
  digitalWrite(BUZZER_PIN, HIGH);
  _yieldHttp(onMs);
  digitalWrite(BUZZER_PIN, manualBuzz ? HIGH : LOW);
  _yieldHttp(offMs);
}

// Pattern 1 — Pest alert: fast triple beep ×2 (BIP BIP BIP)
void buzzerPest() {
  Serial.println("[BUZZ] Pattern 1: Pest alert");
  for (int rep = 0; rep < 2; rep++) {
    for (int i = 0; i < 3; i++) _buzz(BUZZ_SHORT_MS, BUZZ_GAP_MS);
    _yieldHttp(BUZZ_PAUSE_MS);
  }
}

// Pattern 2 — Fungus alert: slow double pulse ×3 (BEEP——BEEP)
void buzzerFungus() {
  Serial.println("[BUZZ] Pattern 2: Fungus alert");
  for (int i = 0; i < 3; i++) {
    _buzz(BUZZ_LONG_MS, BUZZ_GAP_MS);
    _buzz(BUZZ_LONG_MS, BUZZ_PAUSE_MS);
  }
}

// Pattern 3 — Critical / HIGH: SOS  (···−−−···)
void buzzerCritical() {
  Serial.println("[BUZZ] Pattern 3: CRITICAL SOS");
  for (int rep = 0; rep < 2; rep++) {
    // 3 short
    for (int i = 0; i < 3; i++) _buzz(BUZZ_SHORT_MS, BUZZ_GAP_MS);
    _yieldHttp(BUZZ_PAUSE_MS);
    // 3 long
    for (int i = 0; i < 3; i++) _buzz(BUZZ_LONG_MS, BUZZ_GAP_MS);
    _yieldHttp(BUZZ_PAUSE_MS);
    // 3 short
    for (int i = 0; i < 3; i++) _buzz(BUZZ_SHORT_MS, BUZZ_GAP_MS);
    _yieldHttp(BUZZ_PAUSE_MS * 3);
  }
}

void buzzerOFF() {
  manualBuzz = false;
  digitalWrite(BUZZER_PIN, LOW);
  Serial.println("[BUZZ] OFF");
}

// ─────────────────────────────────────────────────────────────
//  SIM800L HELPERS
// ─────────────────────────────────────────────────────────────
// Read complete responses; a fixed delay is not an acknowledgement.
// Prompt and submit modes must not mistake an unrelated OK for SMS success.
bool readModem(String &response, unsigned long waitMs, bool wantPrompt, bool wantSubmit) {
  response = "";
  String line;
  bool submitted = false;
  unsigned long t0 = millis();
  while (millis() - t0 < waitMs) {
    serviceHardware();
    while (sim800l.available()) {
      char c = (char)sim800l.read();
      if (wantPrompt && c == '>') return true;
      if (c == '\r') continue;
      if (c != '\n') {
        if (line.length() < 256) line += c;
        continue;
      }
      line.trim();
      if (line.length() == 0) continue;
      Serial.print("[SIM<] "); Serial.println(line);
      if (response.length() < 1536) response += line + "\n";
      if (line == "ERROR" || line.startsWith("+CME ERROR:") || line.startsWith("+CMS ERROR:")) {
        simLastError = line;
        return false;
      }
      if (line.startsWith("+CMGS:")) submitted = true;
      if (line == "OK" && !wantPrompt && (!wantSubmit || submitted)) return true;
      line = "";
    }
    delay(2);
  }
  simLastError = wantSubmit ? "SMS submission timeout; delivery unknown" : "Modem response timeout";
  return false;
}

bool sendAT(const char* cmd, String &response, unsigned long waitMs = 5000) {
  while (sim800l.available()) Serial.write(sim800l.read());
  Serial.print("[SIM>] "); Serial.println(cmd);
  sim800l.print(cmd);
  sim800l.print('\r');
  if (readModem(response, waitMs, false, false)) return true;
  Serial.print("[SIM] Failed "); Serial.print(cmd);
  Serial.print(": "); Serial.println(simLastError);
  return false;
}

bool prepareSMS() {
  sim800lReady = false;
  String r;
  if (!sendAT("AT", r, 1500)) return false;
  if (!sendAT("ATE0", r)) return false;
  if (!sendAT("AT+CMEE=2", r)) return false;
  if (!sendAT("AT+CPIN?", r)) return false;
  if (r.indexOf("+CPIN: READY") < 0) {
    simLastError = "SIM not ready: " + r;
    return false;
  }
  if (!sendAT("AT+CREG?", r)) return false;
  int start = r.indexOf("+CREG:");
  int mode = -1, registration = -1;
  if (start < 0 || sscanf(r.c_str() + start, "+CREG: %d,%d", &mode, &registration) != 2 ||
      (registration != 1 && registration != 5)) {
    simLastError = "Not registered on GSM network: " + r;
    return false;
  }
  if (!sendAT("AT+CMGF=1", r)) return false;
  if (!sendAT("AT+CMGF?", r)) return false;
  String compact = r;
  compact.replace(" ", "");
  if (compact.indexOf("+CMGF:1") < 0) {
    simLastError = "SMS text mode was not enabled";
    return false;
  }
  if (!sendAT("AT+CSCS=\"GSM\"", r)) return false;
  // Do not repeatedly overwrite CSMP. The module's default is 17,167,0,0.
  if (!sendAT("AT+CSCA?", r)) return false;
  start = r.indexOf("+CSCA:");
  int quote = r.indexOf('"', start < 0 ? 0 : start);
  int endQuote = r.indexOf('"', quote + 1);
  if (start < 0 || quote < 0 || endQuote <= quote + 1) {
    simLastError = "SIM has no SMS service center; check SMSC with the SIM carrier";
    return false;
  }
  // v2.2: push incoming SMS straight to the serial stream as +CMT lines
  // so serviceIncomingSMS() below can detect and display them.
  if (!sendAT("AT+CNMI=2,2,0,0,0", r)) return false;
  simLastError = "";
  sim800lReady = true;
  return true;
}

void diagnoseSIM() {
  String r;
  Serial.println("[SIM DIAG] Modem identity, SIM, signal, registration, SMS settings");
  const char* commands[] = {"AT", "ATE0", "AT+CMEE=2", "ATI", "AT+CPIN?",
                            "AT+CSQ", "AT+CREG?", "AT+COPS?", "AT+CMGF?",
                            "AT+CSCS?", "AT+CSMP?", "AT+CSCA?", "AT+CNMI?"};
  for (const char* cmd : commands) sendAT(cmd, r);
  sim800lReady = false; // Revalidate before the next queued SMS.
  simRetryAt = millis();
  Serial.println("[SIM DIAG] Complete");
}

void initSIM800L() {
  Serial.println("[SIM] Initializing SIM800L...");
  _yieldHttp(3000);
  if (prepareSMS()) Serial.println("[SIM] SMS ready (SIM + GSM registration + text mode checked)");
  else { Serial.print("[SIM] Not ready: "); Serial.println(simLastError); }
  simRetryAt = sim800lReady ? millis() : millis() + 15000;
}

void sendSMS(String message) {
  if (message.length() > 160) message = message.substring(0, 160);
  String recipient = String(FARMER_PHONE);
  if (!recipient.startsWith("+")) recipient = "+" + recipient;
  String cmd = "AT+CMGS=\"" + recipient + "\"";
  String r;
  smsLastResult = "sending";
  Serial.print("[SIM>] "); Serial.println(cmd);
  sim800l.print(cmd); sim800l.print('\r');
  if (!readModem(r, 10000, true, false)) {
    String error = simLastError;
    sim800l.write(27); // Cancel text entry if a late prompt arrives.
    _yieldHttp(200);
    simLastError = error;
    smsLastResult = "failed before message submission";
  } else {
    sim800l.print(message);
    sim800l.write(26);
    // SIMCom specifies up to 60 seconds for CMGS. Do not retry an
    // ambiguous submission: it can deliver duplicate messages.
    if (readModem(r, 60000, false, true)) {
      simLastError = "";
      smsLastResult = "submitted to network";
      Serial.println("[SMS] SUBMITTED OK (+CMGS confirmed)");
      return;
    }
    smsLastResult = "submission failed or unconfirmed";
  }
  sim800lReady = false;
  simRetryAt = millis() + 15000;
  Serial.print("[SMS] "); Serial.print(smsLastResult);
  Serial.print(": "); Serial.println(simLastError);
}

// ─────────────────────────────────────────────────────────────
//  INCOMING SMS DISPLAY (v2.2 addition)
//  Reads whatever bytes have arrived from the SIM800L. Every byte
//  is still echoed raw to Serial exactly as v2.1 did (so nothing
//  about the existing debug output is removed) — this just also
//  watches the stream for a "+CMT:" unsolicited notification and,
//  once the following line (the message body) arrives, prints a
//  clean formatted block to the Serial Monitor.
// ─────────────────────────────────────────────────────────────
void serviceIncomingSMS() {
  while (sim800l.available()) {
    char c = (char)sim800l.read();
    Serial.write(c);              // preserve original raw pass-through

    if (c == '\r') continue;
    simIncomingBuffer += c;

    if (c == '\n') {
      String line = simIncomingBuffer;
      line.trim();
      simIncomingBuffer = "";

      if (line.length() == 0) continue;

      if (line.startsWith("+CMT:")) {
        // Example: +CMT: "+639171234567","","26/09/26,14:30:00+00"
        int firstQuote  = line.indexOf('"');
        int secondQuote = line.indexOf('"', firstQuote + 1);
        pendingIncomingSender = line.substring(firstQuote + 1, secondQuote);
        expectingSmsBody = true;   // next non-empty line is the SMS text
      } else if (expectingSmsBody) {
        expectingSmsBody = false;
        Serial.println("\n===========================================");
        Serial.println("         INCOMING SMS RECEIVED             ");
        Serial.println("===========================================");
        Serial.print("Sender Number  : ");
        Serial.println(pendingIncomingSender);
        Serial.print("Message Content: ");
        Serial.println(line);
        Serial.println("===========================================\n");
      }
    }
  }
}

String jsonString(String value) {
  value.replace("\\", "\\\\");
  value.replace("\"", "\\\"");
  value.replace("\r", "\\r");
  value.replace("\n", "\\n");
  value.replace("\t", "\\t");
  return "\"" + value + "\"";
}

// ─────────────────────────────────────────────────────────────
//  COMMAND PROCESSOR  (shared by HTTP and Serial)
// ─────────────────────────────────────────────────────────────
String processCommand(String cmd) {
  cmd.trim();
  if (cmd.length() == 0) return "ERR:empty";

  Serial.print("[CMD] "); Serial.println(cmd);

  if (cmd == "BUZZ_SPRAY") {
    pendingBuzz = 4;   // 2.5s single tone with the pump
    return "OK";

  } else if (cmd == "BUZZ_ALERT" || cmd == "BUZZ_PEST") {
    pendingBuzz = 1;
    return "OK";

  } else if (cmd == "BUZZ_FUNGUS") {
    pendingBuzz = 2;
    return "OK";

  } else if (cmd == "BUZZ_CRITICAL") {
    pendingBuzz = 3;
    return "OK";

  } else if (cmd == "BUZZ_OFF") {
    pendingBuzz = 0;
    buzzerOFF();
    return "OK";

  } else if (cmd == "PUMP_ON") {
    relayON();
    pumpActive = true;
    pumpOffAt = millis() + 2500;  // auto-off after 2.5s (single cycle)
    Serial.println("[PUMP] ON (single cycle 2.5s)");
    return "OK";

  } else if (cmd == "PUMP_HOLD") {
    relayON();
    pumpActive = true;
    pumpOffAt = millis() + 4000;  // 4s safety watchdog (refreshed by heartbeat)
    pendingBuzz = 0;
    manualBuzz = true;
    digitalWrite(BUZZER_PIN, HIGH);
    Serial.println("[PUMP] HOLD (manual hold active)");
    return "OK";

  } else if (cmd == "PUMP_HEARTBEAT") {
    if (pumpActive) {
      pumpOffAt = millis() + 4000;  // extend safety watchdog
      return "OK:alive";
    }
    return "ERR:pump_idle";

  } else if (cmd == "PUMP_OFF") {
    relayOFF();
    pumpActive = false;
    pumpOffAt = 0;
    if (manualBuzz) buzzerOFF();
    Serial.println("[PUMP] OFF");
    return "OK";

  } else if (cmd.startsWith("SMS:")) {
    String txt = cmd.substring(4);
    return queueSMS(txt) ? "OK:queued" : "ERR:sms_queue_full_or_empty";

  } else if (cmd == "SIM_DIAG") {
    simDiagnosticsPending = true;
    return "OK:diagnostics_queued";

  } else if (cmd == "STATUS") {
    String ip  = WiFi.localIP().toString();
    String res = "{\"pump\":" + String(pumpActive ? "true" : "false")
               + ",\"sim\":" + String(sim800lReady ? "true" : "false")
               + ",\"sim_error\":" + jsonString(simLastError)
               + ",\"sms_result\":" + jsonString(smsLastResult)
               + ",\"sms_queued\":" + String(smsCount)
               + ",\"ip\":\"" + ip + "\""
               + ",\"rssi\":" + String(WiFi.RSSI())
               + "}";
    return res;

  } else {
    Serial.print("[CMD] Unknown: "); Serial.println(cmd);
    return "ERR:unknown_cmd";
  }
}

// ─────────────────────────────────────────────────────────────
//  HTTP SERVER HANDLERS
// ─────────────────────────────────────────────────────────────
void handleCmd() {
  if (!server.hasArg("plain")) {
    server.send(400, "text/plain", "ERR:no_body");
    return;
  }
  String body   = server.arg("plain");
  String result = processCommand(body);
  server.send(result.startsWith("ERR:") ? 400 : 200, "text/plain", result);
}

void handleStatus() {
  String result = processCommand("STATUS");
  server.send(200, "application/json", result);
}

void handleNotFound() {
  server.send(404, "text/plain", "ERR:not_found");
}

// ─────────────────────────────────────────────────────────────
//  WIFI CONNECT
// ─────────────────────────────────────────────────────────────
void connectWiFi() {
  Serial.print("[WiFi] Connecting to ");
  Serial.print(WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);

  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 30) {
    delay(500);
    Serial.print(".");
    attempts++;
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println();
    Serial.print("[WiFi] Connected! IP: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println();
    Serial.println("[WiFi] FAILED — will retry in loop()");
  }
}

// ─────────────────────────────────────────────────────────────
//  SETUP
// ─────────────────────────────────────────────────────────────
void setup() {
  Serial.begin(USB_BAUD);
  Serial.setTimeout(20);
  sim800l.begin(SIM_BAUD, SERIAL_8N1, SIM_RX_PIN, SIM_TX_PIN);

  pinMode(RELAY_PIN,  OUTPUT);
  pinMode(BUZZER_PIN, OUTPUT);

  // Safety OFF at boot
  relayOFF();
  buzzerOFF();

  Serial.println("==========================================");
  Serial.println(" FarmCam ESP32 Bridge v2.2 (Manual pump + SMS fix + SMS receive) ");
  Serial.println(" Pump=GPIO27  Buzzer=GPIO26              ");
  Serial.println(" SIM800L: TX2=GPIO17  RX2=GPIO16         ");
  Serial.println("==========================================");

  // 1. Connect WiFi
  connectWiFi();

  // 2. Start HTTP server
  server.on("/cmd",    HTTP_POST, handleCmd);
  server.on("/status", HTTP_GET,  handleStatus);
  server.onNotFound(handleNotFound);
  server.begin();
  Serial.print("[HTTP] Server listening on port ");
  Serial.println(ESP32_HTTP_PORT);

  // 3. Init SIM800L
  initSIM800L();

  // Queue boot SMS so setup() is not blocked if the network is weak
  queueSMS(String("FarmCam Online! ESP32 IP: ") + WiFi.localIP().toString());

  // 5. Boot buzzer confirm — short double-beep
  _buzz(100, 80); _buzz(100, 0);
  Serial.println("[READY] Waiting for commands from RPi5 via WiFi...");
}

// ─────────────────────────────────────────────────────────────
//  LOOP
// ─────────────────────────────────────────────────────────────
void loop() {
  serviceHardware();

  if (pendingBuzz > 0) {
    int kind = pendingBuzz;
    pendingBuzz = 0;
    if (kind == 1) buzzerPest();
    else if (kind == 2) buzzerFungus();
    else if (kind == 3) buzzerCritical();
    else if (kind == 4) { Serial.println("[BUZZ] 2.5s spray tone"); _buzz(2500, 0); }
  }

  if (simDiagnosticsPending && !pumpActive) {
    simDiagnosticsPending = false;
    diagnoseSIM();
  }

  // Keep messages queued while the SIM boots/registers. Only one modem
  // transaction runs at a time; HTTP and the pump watchdog keep running.
  // Sending while held is safe because every modem wait services HTTP and
  // the hardware watchdog. Do not defer the manual notification to release.
  if (smsCount > 0 && (long)(millis() - simRetryAt) >= 0) {
    if (!prepareSMS()) {
      Serial.print("[SMS] Waiting: "); Serial.println(simLastError);
      simRetryAt = millis() + 15000;
    } else {
      String text = smsQueue[smsHead];
      smsHead = (smsHead + 1) % SMS_QUEUE_SIZE;
      smsCount--;
      sendSMS(text);
    }
  }

  if (Serial.available()) {
    String cmd = Serial.readStringUntil('\n');
    String res = processCommand(cmd);
    Serial.println(res);
  }

  if (sim800l.available()) {
    // Do not block the HTTP server waiting for unsolicited modem text.
    // v2.2: this now also detects and formats incoming SMS (+CMT).
    serviceIncomingSMS();
  }

  static unsigned long lastWifiRetry = 0;
  if (WiFi.status() != WL_CONNECTED && millis() - lastWifiRetry >= 15000) {
    lastWifiRetry = millis();
    Serial.println("[WiFi] Disconnected — reconnecting...");
    WiFi.reconnect();
  }
}
