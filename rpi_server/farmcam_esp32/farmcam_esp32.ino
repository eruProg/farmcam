/*
 * ============================================================
 *  FarmCam ESP32 — WiFi Hardware Bridge  v3.0
 *  Hardware: ESP32 DevKit V1 + SIM800L EVB
 *            + 1x Relay Module + Active Buzzer
 * ============================================================
 *
 *  ARCHITECTURE (v3 — non-blocking SMS, ACK protocol, WDT)
 *  ─────────────────────────────────────────────────────────
 *  The ESP32 joins the same WiFi hotspot (FarmCam / farmcam1)
 *  that the RPi5 is connected to.  RPi5 sends commands via HTTP
 *  POST to this ESP32's local IP.  No USB cable needed between
 *  RPi5 and ESP32 in production — both are on the same LAN.
 *
 *  v3.0 CHANGES (over v2.3):
 *  - Non-blocking SMS state machine (SMS never blocks loop()).
 *    The pump safety watchdog continues to run during modem waits.
 *  - JSON-line ACK protocol:
 *      Pi → ESP32:  SMS_JSON:{"id":"<uuid>","to":"+63...","msg":"<text>"}
 *      ESP32 → Pi:  OK:queued:<id>  (immediate)
 *      ESP32 → Pi:  SMSACK:{"id":"<id>","status":"sent"|"failed","err":"<reason>"}
 *  - Millisecond timestamps on every Serial.print() log line.
 *  - Hardware WDT: resets ESP32 if loop() freezes for >30 s.
 *  - Phone numbers moved to #define — set them here before flashing.
 *    Never put real numbers in a file committed to git.
 *
 * ============================================================
 *  !! CONFIGURE BEFORE FLASHING !!
 * ============================================================
 *  Copy device_config.example.h to device_config.h and configure it.
 *  device_config.h is ignored by Git; keep your settings there.
 * ============================================================
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
 *  SIM800L EVB — separate 5V/2A PSU (≥1000µF cap near VCC),
 *                shared GND with ESP32.
 *
 *  PUMP / RELAY WIRING:
 *  COM → 12V PSU (+)  |  NO → Pump (+)  |  Pump (−) → PSU (−)
 *
 * ============================================================
 *  HTTP COMMANDS  (POST /cmd  body: text/plain)
 * ============================================================
 *  BUZZ_ALERT          → Pattern 1 – Pest alert  (fast double beep)
 *  BUZZ_FUNGUS         → Pattern 2 – Fungus alert (slow single pulse)
 *  BUZZ_CRITICAL       → Pattern 3 – Critical / HIGH (SOS tone)
 *  BUZZ_OFF            → Silence buzzer immediately
 *  PUMP_ON             → Activate relay (single 2.5 s cycle)
 *  PUMP_HOLD           → Activate relay (manual; refresh with PUMP_HEARTBEAT)
 *  PUMP_HEARTBEAT      → Extend manual hold 4 s watchdog
 *  PUMP_OFF            → Deactivate relay immediately
 *  SMS:<text>          → Legacy: queue plain-text SMS (no ACK)
 *  SMS_JSON:<json>     → v3: queue JSON-tagged SMS with ACK protocol
 *  SIM_DIAG            → Print modem diagnostics to Serial
 *  STATUS              → GET /status  returns JSON status
 *
 *  RESPONSE:  "OK" on success, "ERR:<msg>" on failure
 * ============================================================
 */

#include <WiFi.h>
#include <WebServer.h>
#include <esp_task_wdt.h>   // Hardware watchdog

// Local credentials are ignored by Git. Copy device_config.example.h first.
#if __has_include("device_config.h")
#include "device_config.h"
#else
#include "device_config.example.h"
#endif
const char* WIFI_SSID = FARMCAM_WIFI_SSID;
const char* WIFI_PASS = FARMCAM_WIFI_PASS;

// ─── Network ─────────────────────────────────────────────────
#define ESP32_HTTP_PORT   80

// ─── Pin Definitions ─────────────────────────────────────────
#define RELAY_PIN      27
#define BUZZER_PIN     26
#define SIM_RX_PIN     16
#define SIM_TX_PIN     17

// ─── Hardware Config ─────────────────────────────────────────
#define RELAY_ACTIVE_LOW  true
#define USB_BAUD          115200
#define SIM_BAUD          9600
#define WDT_TIMEOUT_S     30    // Hardware WDT resets if loop() freezes

// ─── Buzzer Pattern Timing (ms) ──────────────────────────────
#define BUZZ_SHORT_MS  150
#define BUZZ_LONG_MS   500
#define BUZZ_GAP_MS    120
#define BUZZ_PAUSE_MS  300

// ─── SMS Queue ────────────────────────────────────────────────
#define SMS_QUEUE_SIZE 6
struct SmsItem {
  String id;    // UUID for ACK (empty = legacy no-ACK)
  String to;
  String msg;
};
SmsItem smsQueue[SMS_QUEUE_SIZE];
int smsHead = 0, smsTail = 0, smsCount = 0;

// ─── Non-blocking SMS state machine ──────────────────────────
// The modem is never polled in a blocking loop from loop().
// Instead we store a state and advance it on each loop() call.
enum SmsState {
  SMS_IDLE,
  SMS_PREP,
  SMS_CMGS_CMD,
  SMS_WAIT_PROMPT,
  SMS_SEND_BODY,
  SMS_WAIT_CMGS,
  SMS_DONE,
  SMS_FAIL
};
SmsState   smsSendState   = SMS_IDLE;
String     smsCurrent_id  = "";
String     smsCurrent_to  = "";
String     smsCurrent_msg = "";
String     smsModemBuf    = "";
unsigned long smsStateStarted = 0;
#define SMS_STEP_TIMEOUT_MS  12000   // each step must complete within this

// ─── Globals ─────────────────────────────────────────────────
HardwareSerial sim800l(2);
WebServer      server(ESP32_HTTP_PORT);

bool pumpActive    = false;
bool sim800lReady  = false;
String simLastError  = "Not initialized";
String smsLastResult = "none";
unsigned long buzzerOffAt = 0;
String smsBootId;
struct SmsReceipt { String id; String status; String err; };
SmsReceipt smsReceipts[32];
int receiptCursor = 0;

int receiptIndex(const String &id) {
  if (!id.length()) return -1;
  for (int i=0;i<32;i++) if (smsReceipts[i].id==id) return i;
  return -1;
}
void recordReceipt(const String &id, const String &status, const String &err) {
  if (!id.length()) return;
  int index=receiptIndex(id);
  if(index<0) { index=receiptCursor; receiptCursor=(receiptCursor+1)%32; }
  smsReceipts[index]={id,status,err};
}

unsigned long simRetryAt = 0;
bool simDiagnosticsPending = false;
bool manualBuzz = false;
unsigned long pumpOffAt = 0;
int  pendingBuzz   = 0;

// ─── Incoming SMS parsing ─────────────────────────────────────
String simIncomingBuffer   = "";
bool   expectingSmsBody    = false;
String pendingIncomingSender = "";

// ─── Logging helper ───────────────────────────────────────────
// Prefix every log line with milliseconds since boot.
#define LOG(tag, msg)  do { \
  Serial.print("["); Serial.print(millis()); Serial.print("] "); \
  Serial.print("[" tag "] "); Serial.println(msg); } while(0)
#define LOGF(tag, ...)  do { \
  Serial.print("["); Serial.print(millis()); Serial.print("] "); \
  Serial.print("[" tag "] "); Serial.printf(__VA_ARGS__); Serial.println(); } while(0)

// ─── Relay helpers ────────────────────────────────────────────
void relayON()  { digitalWrite(RELAY_PIN, RELAY_ACTIVE_LOW ? LOW  : HIGH); }
void relayOFF() { digitalWrite(RELAY_PIN, RELAY_ACTIVE_LOW ? HIGH : LOW);  }

// ─── Pump safety watchdog ────────────────────────────────────
void servicePumpWatchdog() {
  if (pumpActive && (long)(millis() - pumpOffAt) >= 0) {
    relayOFF(); pumpActive = false; pumpOffAt = 0;
    if (manualBuzz) { digitalWrite(BUZZER_PIN, LOW); manualBuzz = false; }
    LOG("PUMP", "auto-OFF (safety timeout)");
  }
}

void serviceHardware() {
  servicePumpWatchdog();
  if (buzzerOffAt && (long)(millis()-buzzerOffAt)>=0) { buzzerOffAt=0; digitalWrite(BUZZER_PIN,LOW); }
  server.handleClient();
  servicePumpWatchdog();
  esp_task_wdt_reset();   // pet the hardware WDT
}

// ─── Small yield helper (services HTTP and WDT during short waits) ───
void _yieldHttp(unsigned long ms) {
  unsigned long t0 = millis();
  while (millis() - t0 < ms) { serviceHardware(); delay(5); }
}

// ─── Buzzer patterns ─────────────────────────────────────────
void _buzz(int onMs, int offMs) {
  digitalWrite(BUZZER_PIN, HIGH); _yieldHttp(onMs);
  digitalWrite(BUZZER_PIN, manualBuzz ? HIGH : LOW);
  if (offMs > 0) _yieldHttp(offMs);
}
void buzzerPest()     { LOG("BUZZ","Pattern 1: Pest");
  for(int r=0;r<2;r++){for(int i=0;i<3;i++)_buzz(BUZZ_SHORT_MS,BUZZ_GAP_MS);_yieldHttp(BUZZ_PAUSE_MS);}}
void buzzerFungus()   { LOG("BUZZ","Pattern 2: Fungus");
  for(int i=0;i<3;i++){_buzz(BUZZ_LONG_MS,BUZZ_GAP_MS);_buzz(BUZZ_LONG_MS,BUZZ_PAUSE_MS);}}
void buzzerCritical() { LOG("BUZZ","Pattern 3: CRITICAL SOS");
  for(int r=0;r<2;r++){
    for(int i=0;i<3;i++)_buzz(BUZZ_SHORT_MS,BUZZ_GAP_MS); _yieldHttp(BUZZ_PAUSE_MS);
    for(int i=0;i<3;i++)_buzz(BUZZ_LONG_MS, BUZZ_GAP_MS); _yieldHttp(BUZZ_PAUSE_MS);
    for(int i=0;i<3;i++)_buzz(BUZZ_SHORT_MS,BUZZ_GAP_MS); _yieldHttp(BUZZ_PAUSE_MS*3);}}
void buzzerOFF()      { buzzerOffAt=0; manualBuzz=false; digitalWrite(BUZZER_PIN,LOW); LOG("BUZZ","OFF"); }

// ─── SIM800L helpers ─────────────────────────────────────────
bool readModem(String &response, unsigned long waitMs,
               bool wantPrompt, bool wantSubmit) {
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
      if (c != '\n') { if (line.length() < 256) line += c; continue; }
      line.trim();
      if (line.length() == 0) continue;
      LOGF("SIM<", "%s", line.c_str());
      if (response.length() < 1536) response += line + "\n";
      if (line == "ERROR" || line.startsWith("+CME ERROR:")
          || line.startsWith("+CMS ERROR:")) {
        simLastError = line; return false;
      }
      if (line.startsWith("+CMGS:")) submitted = true;
      if (line == "OK" && !wantPrompt && (!wantSubmit || submitted)) return true;
      line = "";
    }
    delay(2);
  }
  simLastError = wantSubmit ? "SMS submission timeout" : "Modem response timeout";
  return false;
}

bool sendAT(const char* cmd, String &response, unsigned long waitMs = 5000) {
  while (sim800l.available()) sim800l.read();
  LOGF("SIM>", "%s", cmd);
  sim800l.print(cmd); sim800l.print('\r');
  if (readModem(response, waitMs, false, false)) return true;
  LOGF("SIM", "Failed %s: %s", cmd, simLastError.c_str());
  return false;
}

bool prepareSMS() {
  sim800lReady = false;
  String r;
  if (!sendAT("AT",r,1500)) return false;
  if (!sendAT("ATE0",r))    return false;
  if (!sendAT("AT+CMEE=2",r)) return false;
  if (!sendAT("AT+CPIN?",r)) return false;
  if (r.indexOf("+CPIN: READY") < 0) { simLastError="SIM not ready: "+r; return false; }
  if (!sendAT("AT+CREG?",r)) return false;
  int mode=-1, reg=-1;
  int start=r.indexOf("+CREG:");
  if (start<0 || sscanf(r.c_str()+start,"+CREG: %d,%d",&mode,&reg)!=2
      || (reg!=1 && reg!=5)) {
    simLastError="Not registered: "+r; return false;
  }
  if (!sendAT("AT+CMGF=1",r)) return false;
  if (!sendAT("AT+CMGF?",r)) return false;
  String compact=r; compact.replace(" ","");
  if (compact.indexOf("+CMGF:1")<0) { simLastError="SMS text mode failed"; return false; }
  if (!sendAT("AT+CSCS=\"GSM\"",r)) return false;
  if (!sendAT("AT+CSCA?",r)) return false;
  int sq=r.indexOf("+CSCA:");
  int q1=r.indexOf('"',sq<0?0:sq);
  int q2=r.indexOf('"',q1+1);
  if (sq<0||q1<0||q2<=q1+1) { simLastError="No SMSC address"; return false; }
  if (!sendAT("AT+CNMI=2,2,0,0,0",r)) return false;
  simLastError=""; sim800lReady=true;
  return true;
}

void diagnoseSIM() {
  String r,cpin,csq,creg;
  LOG("SIM DIAG","Running full modem diagnostics...");
  const char* cmds[]={"AT","ATE0","AT+CMEE=2","ATI","AT+CPIN?",
                      "AT+CSQ","AT+CREG?","AT+COPS?","AT+CMGF?",
                      "AT+CSCS?","AT+CSMP?","AT+CSCA?","AT+CNMI?"};
  for(const char* c:cmds){
    sendAT(c,r);
    if(String(c)=="AT+CPIN?") cpin=r;
    if(String(c)=="AT+CSQ")   csq=r;
    if(String(c)=="AT+CREG?") creg=r;
  }
  int csqVal=-1,cregMode=-1,cregState=-1;
  int ci=csq.indexOf("+CSQ:");
  if(ci>=0) sscanf(csq.c_str()+ci,"+CSQ: %d",&csqVal);
  int cr=creg.indexOf("+CREG:");
  if(cr>=0) sscanf(creg.c_str()+cr,"+CREG: %d,%d",&cregMode,&cregState);
  Serial.println("-----------------------------------------");
  LOG("SIM DIAG","Plain English summary:");
  LOGF("SIM DIAG","  SIM    : %s", humanSimState(cpin).c_str());
  LOGF("SIM DIAG","  Signal : %s", humanSignal(csqVal).c_str());
  LOGF("SIM DIAG","  Network: %s", humanRegistration(cregState).c_str());
  LOGF("SIM DIAG","  Result : %s",
       (cregState==1||cregState==5)?"Ready to send SMS":"Cannot send SMS yet");
  Serial.println("-----------------------------------------");
  sim800lReady=false; simRetryAt=millis();
  LOG("SIM DIAG","Complete");
}

void initSIM800L() {
  LOG("SIM","Initializing SIM800L...");
  _yieldHttp(3000);
  if (prepareSMS()) LOG("SIM","SMS ready");
  else LOGF("SIM","Not ready: %s", simLastError.c_str());
  simRetryAt = sim800lReady ? millis() : millis()+15000;
}

// ─── Direct (blocking) SMS — used only for boot notification ────
void sendSMS_blocking(String to, String message) {
  if (message.length()>160) message=message.substring(0,160);
  if (!to.startsWith("+")) to="+"+to;
  String cmd="AT+CMGS=\""+to+"\"";
  String r;
  LOGF("SIM>","%s",cmd.c_str());
  sim800l.print(cmd); sim800l.print('\r');
  if (!readModem(r,10000,true,false)) {
    sim800l.write(27); _yieldHttp(200);
    smsLastResult="failed before prompt"; return;
  }
  sim800l.print(message); sim800l.write(26);
  if (readModem(r,60000,false,true)) {
    smsLastResult="submitted to network";
    LOG("SMS","SUBMITTED OK (+CMGS confirmed)");
  } else {
    smsLastResult="submission failed"; sim800lReady=false;
    simRetryAt=millis()+15000;
  }
}

// ─── Non-blocking SMS queue helpers ──────────────────────────
bool queueSMS_ack(String id, String to, String msg) {
  if (receiptIndex(id)>=0) return true;
  if (smsCount >= SMS_QUEUE_SIZE) return false;
  smsQueue[smsTail] = {id, to, msg};
  smsTail = (smsTail+1) % SMS_QUEUE_SIZE;
  smsCount++;
  recordReceipt(id,"queued","");
  LOGF("SMS QUEUE","Enqueued id=%s to=%s", id.c_str(), to.c_str());
  return true;
}

bool queueSMS(String msg) {
  return queueSMS_ack("", String("+") + String(FARMER_PHONE), msg);
}

// ─── Non-blocking SMS sender — call from loop() ────────────────
// Reads one character at a time from the modem to avoid blocking.
void serviceSmsSend() {
  if (smsSendState == SMS_IDLE) {
    if (smsCount == 0) return;
    if ((long)(millis()-simRetryAt) < 0) return;
    if (!prepareSMS()) {
      LOGF("SMS","Waiting: %s", simLastError.c_str());
      simRetryAt = millis()+15000; return;
    }
    SmsItem &item = smsQueue[smsHead];
    smsCurrent_id  = item.id;
    recordReceipt(item.id,"sending","");
    smsCurrent_to  = item.to;
    smsCurrent_msg = item.msg;
    smsHead = (smsHead+1) % SMS_QUEUE_SIZE;
    smsCount--;
    smsModemBuf = "";
    smsSendState = SMS_CMGS_CMD;
    smsStateStarted = millis();
    if (smsCurrent_msg.length()>160) smsCurrent_msg=smsCurrent_msg.substring(0,160);
    if (!smsCurrent_to.startsWith("+")) smsCurrent_to="+"+smsCurrent_to;
    String cmd="AT+CMGS=\""+smsCurrent_to+"\"";
    LOGF("SIM>","%s",cmd.c_str());
    sim800l.print(cmd); sim800l.print('\r');
    return;
  }

  // Timeout guard
  if (millis()-smsStateStarted > SMS_STEP_TIMEOUT_MS) {
    LOGF("SMS","Step timeout in state %d",smsSendState);
    _smsFailAndAck("step_timeout");
    return;
  }

  // Read available bytes into buffer
  while (sim800l.available()) {
    char c=(char)sim800l.read();
    if (c=='>') {
      if (smsSendState==SMS_CMGS_CMD || smsSendState==SMS_WAIT_PROMPT) {
        LOGF("SIM<","[prompt >]");
        sim800l.print(smsCurrent_msg); sim800l.write(26);
        smsSendState=SMS_WAIT_CMGS; smsStateStarted=millis();
      }
      smsModemBuf=""; return;
    }
    if (c=='\r') continue;
    if (c=='\n') {
      smsModemBuf.trim();
      if (smsModemBuf.length()>0) {
        LOGF("SIM<","%s",smsModemBuf.c_str());
        if (smsModemBuf.startsWith("+CMGS:") && smsSendState==SMS_WAIT_CMGS) {
          smsSendState=SMS_DONE;
        } else if (smsModemBuf=="OK" && smsSendState==SMS_DONE) {
          smsLastResult="submitted to network";
          _smsSuccessAndAck();
        } else if (smsModemBuf=="ERROR" || smsModemBuf.startsWith("+CME ERROR:")
                   || smsModemBuf.startsWith("+CMS ERROR:")) {
          simLastError=smsModemBuf;
          _smsFailAndAck(smsModemBuf);
        }
      }
      smsModemBuf="";
    } else {
      if (smsModemBuf.length()<256) smsModemBuf+=c;
    }
  }
}

void _smsSuccessAndAck() {
  recordReceipt(smsCurrent_id,"sent","");
  LOG("SMS","SUBMITTED OK (+CMGS confirmed)");
  if (smsCurrent_id.length()>0) {
    String ack="SMSACK:{\"id\":\""+smsCurrent_id+"\",\"status\":\"sent\",\"err\":\"\"}";
    Serial.println(ack);
    // If RPi5 polls /status it will see smsLastResult; direct ACK line on Serial
  }
  smsSendState=SMS_IDLE; sim800lReady=false; simRetryAt=millis();
}

void _smsFailAndAck(String err) {
  recordReceipt(smsCurrent_id, smsSendState==SMS_WAIT_CMGS || smsSendState==SMS_DONE ? "unknown" : "failed",err);
  sim800l.write(27);
  smsLastResult="failed: "+err;
  if (smsCurrent_id.length()>0) {
    String ack="SMSACK:{\"id\":\""+smsCurrent_id+"\",\"status\":\"failed\",\"err\":\""
               +err+"\"}";
    Serial.println(ack);
  }
  smsSendState=SMS_IDLE; sim800lReady=false; simRetryAt=millis()+15000;
  LOGF("SMS","Failed: %s", err.c_str());
}

// ─── Incoming SMS display ─────────────────────────────────────
void serviceIncomingSMS() {
  while (sim800l.available()) {
    char c=(char)sim800l.read();
    Serial.write(c);
    if (c=='\r') continue;
    simIncomingBuffer+=c;
    if (c=='\n') {
      String line=simIncomingBuffer; line.trim(); simIncomingBuffer="";
      if (line.length()==0) continue;
      if (line.startsWith("+CMT:")) {
        int q1=line.indexOf('"'); int q2=line.indexOf('"',q1+1);
        pendingIncomingSender=line.substring(q1+1,q2);
        expectingSmsBody=true;
      } else if (expectingSmsBody) {
        expectingSmsBody=false;
        Serial.println("\n===========================================");
        Serial.println("         INCOMING SMS RECEIVED             ");
        Serial.println("===========================================");
        LOGF("SMS IN","Sender: %s", pendingIncomingSender.c_str());
        LOGF("SMS IN","Body  : %s", line.c_str());
        Serial.println("===========================================\n");
      }
    }
  }
}

// ─── JSON string escaping ─────────────────────────────────────
String jsonString(String v) {
  v.replace("\\","\\\\"); v.replace("\"","\\\"");
  v.replace("\r","\\r");  v.replace("\n","\\n");  v.replace("\t","\\t");
  return "\""+v+"\"";
}

// ─── Human-readable helpers ───────────────────────────────────
String humanSignal(int csq) {
  if (csq==99||csq<0) return "No signal";
  int dbm=-113+(csq*2);
  String bars=(csq>=20)?"Excellent(4/4)":(csq>=15)?"Good(3/4)":(csq>=10)?"Fair(2/4)":(csq>=5)?"Weak(1/4)":"Very weak";
  return bars+", ~"+String(dbm)+" dBm";
}
String humanRegistration(int creg) {
  switch(creg){
    case 0: return "Not registered, not searching";
    case 1: return "Registered (home)";
    case 2: return "Searching...";
    case 3: return "Registration denied";
    case 5: return "Registered (roaming)";
    default: return "Unknown("+String(creg)+")";
  }
}
String humanSimState(const String &r) {
  if(r.indexOf("+CPIN: READY")>=0) return "SIM ready";
  if(r.indexOf("SIM PIN")>=0)      return "SIM PIN locked";
  if(r.indexOf("SIM PUK")>=0)      return "SIM PUK locked";
  if(r.length()==0)                return "No response (check power)";
  return "Not ready ("+r+")";
}
String humanExplain(const String &err) {
  if(err.length()==0)                             return "No error";
  if(err.startsWith("Not registered"))            return "No cell signal yet";
  if(err.startsWith("SIM not ready"))             return "SIM card problem";
  if(err.indexOf("SMSC")>=0)                      return "Missing SMS center number";
  if(err.indexOf("timeout")>=0)                  return "Modem timeout — check wiring";
  return err;
}

// ─── Command processor ───────────────────────────────────────
String processCommand(String cmd) {
  cmd.trim();
  if (cmd.length()==0) return "ERR:empty";
  LOGF("CMD","%s",cmd.c_str());

  if (cmd.startsWith("BUZZ_PULSE:")) {
    int duration=cmd.substring(11).toInt();
    if(duration<1 || duration>500) return "ERR:duration";
    pendingBuzz=0; manualBuzz=false;
    buzzerOffAt=millis()+duration; digitalWrite(BUZZER_PIN,HIGH); return "OK";
  }
  if (cmd=="BUZZ_SPRAY")   { pendingBuzz=4; return "OK"; }
  if (cmd=="BUZZ_ALERT" || cmd=="BUZZ_PEST") { pendingBuzz=1; return "OK"; }
  if (cmd=="BUZZ_FUNGUS")  { pendingBuzz=2; return "OK"; }
  if (cmd=="BUZZ_CRITICAL"){ pendingBuzz=3; return "OK"; }
  if (cmd=="BUZZ_OFF")     { pendingBuzz=0; buzzerOFF(); return "OK"; }

  if (cmd=="PUMP_ON") {
    relayON(); pumpActive=true; pumpOffAt=millis()+2500;
    LOG("PUMP","ON (single 2.5 s)"); return "OK";
  }
  if (cmd=="PUMP_HOLD") {
    relayON(); pumpActive=true; pumpOffAt=millis()+4000;
    pendingBuzz=0; buzzerOFF();
    LOG("PUMP","HOLD"); return "OK";
  }
  if (cmd=="PUMP_HEARTBEAT") {
    if (pumpActive) { pumpOffAt=millis()+4000; return "OK:alive"; }
    return "ERR:pump_idle";
  }
  if (cmd=="PUMP_OFF") {
    relayOFF(); pumpActive=false; pumpOffAt=0;
    if(manualBuzz) buzzerOFF();
    LOG("PUMP","OFF"); return "OK";
  }

  // Legacy plain SMS (no ACK)
  if (cmd.startsWith("SMS:")) {
    String txt=cmd.substring(4);
    return queueSMS(txt) ? "OK:queued" : "ERR:sms_queue_full";
  }

  // v3: JSON-tagged SMS with ACK
  if (cmd.startsWith("SMS_JSON:")) {
    String json=cmd.substring(9);
    // Parse {"id":"...","to":"...","msg":"..."}
    String id="",to="",msg="";
    auto extractField=[&](const String &key)->String {
      int k=json.indexOf("\""+key+"\"");
      if(k<0) return "";
      int colon=json.indexOf(':',k+key.length()+2);
      int start=json.indexOf('"',colon+1);
      if(colon<0 || start<0) return "";
      String value="";
      bool escape=false;
      for(int i=start+1;i<(int)json.length();i++) {
        char c=json[i];
        if(escape) {
          if(c=='"'||c=='\\'||c=='/') value+=c;
          else if(c=='n'||c=='r'||c=='t') value+=' ';
          else return "";
          escape=false;
        } else if(c=='\\') escape=true;
        else if(c=='"') return value;
        else value+=c;
      }
      return "";
    };
    id=extractField("id"); to=extractField("to"); msg=extractField("msg");
    if(id.length()==0||to.length()==0||msg.length()==0)
      return "ERR:sms_json_parse_failed";
    if(id.length()>64 || to.length()>16 || msg.length()>160) return "ERR:sms_too_long";
    if(receiptIndex(id)>=0) return "OK:known:"+id;
    if(!queueSMS_ack(id,to,msg)) return "ERR:queue_full";
    return "OK:queued:"+id;
  }

  if (cmd=="SIM_DIAG") { simDiagnosticsPending=true; return "OK:queued"; }

  if (cmd=="STATUS") {
    String ip=WiFi.localIP().toString();
    return "{\"pump\":"+String(pumpActive?"true":"false")
          +",\"sim\":"+String(sim800lReady?"true":"false")
          +",\"sim_error\":"+jsonString(simLastError)
          +",\"sim_error_readable\":"+jsonString(humanExplain(simLastError))
          +",\"sms_result\":"+jsonString(smsLastResult)
          +",\"sms_result_readable\":"+jsonString(
            smsLastResult=="submitted to network"?"Message sent":
            smsLastResult.startsWith("sending")?"Sending...":
            smsLastResult=="none"?"No message yet":
            humanExplain(simLastError))
          +",\"sms_queued\":"+String(smsCount)
          +",\"sms_state\":"+String((int)smsSendState)
          +",\"ip\":\""+ip+"\""
          +",\"rssi\":"+String(WiFi.RSSI())
          +"}";
  }

  LOGF("CMD","Unknown: %s", cmd.c_str());
  return "ERR:unknown_cmd";
}

// ─── HTTP handlers ────────────────────────────────────────────
void handleCmd() {
  if (!server.hasArg("plain")) { server.send(400,"text/plain","ERR:no_body"); return; }
  String body=server.arg("plain");
  String res=processCommand(body);
  server.send(res=="ERR:queue_full"?503:(res.startsWith("ERR:")?400:200),"text/plain",res);
}
void handleSmsStatus() {
  String id=server.arg("id");
  int index=receiptIndex(id);
  String status=index<0?"unknown":smsReceipts[index].status;
  String err=index<0?"Message ID not retained":smsReceipts[index].err;
  server.send(200,"application/json",String("{\"id\":")+jsonString(id)
    +",\"boot_id\":"+jsonString(smsBootId)+",\"status\":"+jsonString(status)
    +",\"err\":"+jsonString(err)+"}");
}
void handleStatus() {
  server.send(200,"application/json",processCommand("STATUS"));
}
void handleNotFound() { server.send(404,"text/plain","ERR:not_found"); }

// ─── WiFi ────────────────────────────────────────────────────
void connectWiFi() {
  LOGF("WiFi","Connecting to %s ...", WIFI_SSID);
  WiFi.mode(WIFI_STA); WiFi.begin(WIFI_SSID,WIFI_PASS);
  for(int i=0; WiFi.status()!=WL_CONNECTED && i<30; i++) { delay(500); Serial.print("."); }
  if(WiFi.status()==WL_CONNECTED) {
    Serial.println();
    LOGF("WiFi","Connected! IP: %s", WiFi.localIP().toString().c_str());
  } else {
    Serial.println();
    LOG("WiFi","FAILED — will retry in loop()");
  }
}

// ─── Setup ───────────────────────────────────────────────────
void setup() {
  smsBootId=String((uint32_t)esp_random(),HEX)+String((uint32_t)esp_random(),HEX);
  Serial.begin(USB_BAUD);
  Serial.setTimeout(20);
  sim800l.begin(SIM_BAUD, SERIAL_8N1, SIM_RX_PIN, SIM_TX_PIN);

  pinMode(RELAY_PIN, OUTPUT); pinMode(BUZZER_PIN, OUTPUT);
  relayOFF(); buzzerOFF();

  // Hardware WDT — resets ESP32 if loop() freezes for WDT_TIMEOUT_S seconds
  esp_task_wdt_config_t watchdogConfig = {};
  watchdogConfig.timeout_ms = WDT_TIMEOUT_S * 1000;
  watchdogConfig.idle_core_mask = 0;
  watchdogConfig.trigger_panic = true;
  if (esp_task_wdt_init(&watchdogConfig) == ESP_ERR_INVALID_STATE) {
    esp_task_wdt_reconfigure(&watchdogConfig);
  }
  esp_task_wdt_add(NULL);

  Serial.println();
  Serial.println("==========================================");
  Serial.println(" FarmCam ESP32 Bridge v3.0               ");
  Serial.println(" Non-blocking SMS | ACK protocol | WDT   ");
  Serial.println(" Pump=GPIO27  Buzzer=GPIO26               ");
  Serial.println(" SIM800L: TX2=GPIO17  RX2=GPIO16          ");
  Serial.println("==========================================");

  connectWiFi();

  server.on("/cmd",    HTTP_POST, handleCmd);
  server.on("/status", HTTP_GET,  handleStatus);
  server.on("/sms/status", HTTP_GET, handleSmsStatus);
  server.onNotFound(handleNotFound);
  server.begin();
  LOGF("HTTP","Server on port %d", ESP32_HTTP_PORT);

  initSIM800L();

  // Queue boot SMS
  queueSMS(String("FarmCam Online v3! ESP32 IP: ")+WiFi.localIP().toString());

  // Boot beep
  _buzz(100,80); _buzz(100,0);
  LOG("READY","Waiting for commands from RPi5...");
}

// ─── Loop ────────────────────────────────────────────────────
void loop() {
  serviceHardware();   // HTTP + pump WDT + WDT reset

  // Buzzer patterns
  if (pendingBuzz > 0) {
    int kind=pendingBuzz; pendingBuzz=0;
    if(kind==1) buzzerPest();
    else if(kind==2) buzzerFungus();
    else if(kind==3) buzzerCritical();
    else if(kind==4) { LOG("BUZZ","2.5 s spray tone"); _buzz(2500,0); }
  }

  if (simDiagnosticsPending && !pumpActive && smsSendState == SMS_IDLE) {
    simDiagnosticsPending=false; diagnoseSIM();
  }

  // Non-blocking SMS sender
  serviceSmsSend();

  // Serial console commands
  if (Serial.available()) {
    String cmd=Serial.readStringUntil('\n');
    Serial.println(processCommand(cmd));
  }

  // Incoming SMS display
  if (smsSendState == SMS_IDLE && sim800l.available()) {
    serviceIncomingSMS();
  }

  // WiFi reconnect
  static unsigned long lastWifiRetry=0;
  if(WiFi.status()!=WL_CONNECTED && millis()-lastWifiRetry>=15000) {
    lastWifiRetry=millis(); LOG("WiFi","Disconnected — reconnecting..."); WiFi.reconnect();
  }
}
