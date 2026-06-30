// KiloVault HLX+ -> PC bridge for ESP32 (NimBLE-Arduino).
//
// The ESP32 acts as a BLE central: it scans for HLX+ batteries, connects to
// each, subscribes to the 0xFFE4 status notifications, reassembles the 121-byte
// frames and forwards every raw frame to the PC over USB serial using a tiny
// line protocol that the Python `serial` transport understands:
//
//     S <mac> <name>\n      battery connected
//     F <mac> <hex>\n       one complete raw 121-byte frame, hex-encoded
//     D <mac>\n             battery disconnected
//     # <text>\n            log line (ignored by the PC)
//
// The ESP32 never decodes the data — the single Python decoder stays
// authoritative. Place the ESP32 next to the battery bank and run a USB cable
// (or use the optional WiFi TCP server) to the PC.
//
// Build with PlatformIO (see platformio.ini). Library: h2zero/NimBLE-Arduino.

#include <Arduino.h>
#include <NimBLEDevice.h>
#include <map>
#include <vector>

static const char *KV_SERVICE = "FFE0";
static const char *KV_NOTIFY = "FFE4";
static const uint8_t KV_START = 0xB0;
static const size_t KV_FRAME_LEN = 121;
static const size_t MAX_PEERS = 3;   // see CONFIG_BT_NIMBLE_MAX_CONNECTIONS

struct Peer {
  std::string mac;
  std::string name;
  std::vector<uint8_t> buf;
};

static std::map<std::string, Peer> g_peers;          // mac -> peer
static std::vector<NimBLEAdvertisedDevice> g_toConnect;

static void emitLine(const String &s) { Serial.println(s); }

static bool looksLikeHlx(const NimBLEAdvertisedDevice *dev) {
  if (dev->isAdvertisingService(NimBLEUUID(KV_SERVICE))) return true;
  std::string n = dev->getName();
  if (n.empty()) return false;
  // Names look like "12V150Ah-102"; match the "<num>V<num>Ah" shape loosely.
  return (n.find("Ah") != std::string::npos && n.find('V') != std::string::npos) ||
         (n.find("KiloVault") != std::string::npos);
}

// --- notifications -------------------------------------------------------
static void onNotify(NimBLERemoteCharacteristic *chr, uint8_t *data, size_t len,
                     bool isNotify) {
  std::string mac = chr->getRemoteService()->getClient()->getPeerAddress().toString();
  auto it = g_peers.find(mac);
  if (it == g_peers.end()) return;
  Peer &p = it->second;

  // Reassemble: a chunk starting with 0xB0 begins a new frame.
  if (len > 0 && data[0] == KV_START) p.buf.clear();
  p.buf.insert(p.buf.end(), data, data + len);

  while (p.buf.size() >= KV_FRAME_LEN) {
    static const char *HEX = "0123456789ABCDEF";
    String line = "F ";
    line += mac.c_str();
    line += ' ';
    line.reserve(line.length() + KV_FRAME_LEN * 2 + 1);
    for (size_t i = 0; i < KV_FRAME_LEN; i++) {
      uint8_t b = p.buf[i];
      line += HEX[b >> 4];
      line += HEX[b & 0x0F];
    }
    emitLine(line);
    p.buf.erase(p.buf.begin(), p.buf.begin() + KV_FRAME_LEN);
    // Resync to the next start marker if there is trailing junk.
    auto m = std::find(p.buf.begin(), p.buf.end(), KV_START);
    if (m == p.buf.end()) p.buf.clear();
    else p.buf.erase(p.buf.begin(), m);
  }
  if (p.buf.size() > 4 * KV_FRAME_LEN) p.buf.clear();
}

// --- client (dis)connect callbacks --------------------------------------
class ClientCB : public NimBLEClientCallbacks {
  void onDisconnect(NimBLEClient *c, int reason) override {
    std::string mac = c->getPeerAddress().toString();
    emitLine(String("D ") + mac.c_str());
    g_peers.erase(mac);
  }
};
static ClientCB g_clientCb;

static bool connectPeer(const NimBLEAdvertisedDevice &dev) {
  std::string mac = dev.getAddress().toString();
  if (g_peers.count(mac)) return true;
  if (g_peers.size() >= MAX_PEERS) return false;

  NimBLEClient *client = NimBLEDevice::createClient();
  client->setClientCallbacks(&g_clientCb, false);
  client->setConnectTimeout(10 * 1000);
  if (!client->connect(&dev)) {
    NimBLEDevice::deleteClient(client);
    return false;
  }
  NimBLERemoteService *svc = client->getService(KV_SERVICE);
  if (!svc) { client->disconnect(); return false; }
  NimBLERemoteCharacteristic *chr = svc->getCharacteristic(KV_NOTIFY);
  if (!chr || !chr->canNotify()) { client->disconnect(); return false; }

  Peer p;
  p.mac = mac;
  p.name = dev.getName();
  g_peers[mac] = p;

  String s = "S ";
  s += mac.c_str();
  s += ' ';
  s += (p.name.empty() ? mac.c_str() : p.name.c_str());
  emitLine(s);

  chr->subscribe(true, onNotify);
  return true;
}

// --- scan ---------------------------------------------------------------
class ScanCB : public NimBLEScanCallbacks {
  void onResult(const NimBLEAdvertisedDevice *dev) override {
    if (looksLikeHlx(dev) && !g_peers.count(dev->getAddress().toString())) {
      g_toConnect.push_back(*dev);
    }
  }
};
static ScanCB g_scanCb;

void setup() {
  Serial.begin(115200);
  delay(200);
  emitLine("# KiloVault ESP32 bridge starting");

  NimBLEDevice::init("KV-Bridge");
  NimBLEDevice::setPower(ESP_PWR_LVL_P9);

  NimBLEScan *scan = NimBLEDevice::getScan();
  scan->setScanCallbacks(&g_scanCb, false);
  scan->setActiveScan(true);
  scan->setInterval(100);
  scan->setWindow(80);
}

void loop() {
  // Keep scanning so new / re-powered batteries are picked up.
  if (g_peers.size() < MAX_PEERS) {
    NimBLEScan *scan = NimBLEDevice::getScan();
    if (!scan->isScanning()) scan->start(4 * 1000, false, false);
  }
  // Connect anything the scan queued (done outside the scan callback).
  if (!g_toConnect.empty()) {
    std::vector<NimBLEAdvertisedDevice> pending;
    pending.swap(g_toConnect);
    for (auto &dev : pending) connectPeer(dev);
  }
  delay(500);
}
