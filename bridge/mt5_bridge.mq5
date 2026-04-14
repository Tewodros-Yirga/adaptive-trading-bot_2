//+------------------------------------------------------------------+
//| MT5 File Bridge EA — Adaptive Trading Bot                        |
//| ─────────────────────────────────────────────────────────────── |
//| SETUP:                                                           |
//|  1. Copy to: MT5_DATA_FOLDER/MQL5/Experts/adaptive_bot/         |
//|  2. Compile in MetaEditor (F7)                                   |
//|  3. Tools → Options → Expert Advisors → Allow automated trading  |
//|  4. Attach to any chart (recommended: XAUUSD M1)                |
//|  5. Enable "Allow DLL imports" if prompted                       |
//|                                                                  |
//| HOW IT WORKS:                                                    |
//|  Node.js writes  → MQL5/Files/adaptive_bot/cmd.json             |
//|  EA reads cmd, executes trade, writes result                     |
//|  Node.js reads   ← MQL5/Files/adaptive_bot/resp.json            |
//|                                                                  |
//| Set MT4_FILES_DIR in .env to your terminal's MQL5/Files path:   |
//|  MT4_FILES_DIR=C:\Users\You\AppData\Roaming\MetaQuotes\         |
//|               Terminal\<HASH>\MQL5\Files                         |
//+------------------------------------------------------------------+
#property copyright "Adaptive Trading Bot"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>
#include <Trade\AccountInfo.mqh>

// ── Inputs ───────────────────────────────────────────────────────────────────
input string BridgeSecret   = "bridge_secret_token";  // Must match MT_BRIDGE_SECRET in .env
input string DefaultSymbol  = "XAUUSDm";
input ulong  MagicNumber    = 20240101;
input uint   DeviationPts   = 10;

// ── Globals ──────────────────────────────────────────────────────────────────
#define CMD_FILE   "adaptive_bot\\cmd.json"
#define RESP_FILE  "adaptive_bot\\resp.json"
#define LOG_FILE   "adaptive_bot\\bridge.log"

CTrade          trade;
CPositionInfo   posInfo;
CAccountInfo    acctInfo;

//+------------------------------------------------------------------+
int OnInit() {
   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(DeviationPts);
   trade.SetTypeFilling(ORDER_FILLING_IOC);

   _log("MT5 Bridge started. Watching: " + CMD_FILE);
   Print("[Bridge] MT5 Bridge EA started on ", Symbol(), " ", EnumToString(Period()));
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason) {
   _log("Bridge stopped. Reason=" + IntegerToString(reason));
}

//+------------------------------------------------------------------+
void OnTick() {
   if (!FileIsExist(CMD_FILE, FILE_COMMON)) return;

   // Read command
   int fh = FileOpen(CMD_FILE, FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if (fh == INVALID_HANDLE) return;

   string raw = "";
   while (!FileIsEnding(fh)) raw += FileReadString(fh);
   FileClose(fh);
   FileDelete(CMD_FILE, FILE_COMMON);

   _log("CMD: " + raw);
   string resp = _dispatch(raw);
   _log("RESP: " + resp);

   // Write response
   int rf = FileOpen(RESP_FILE, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if (rf != INVALID_HANDLE) {
      FileWriteString(rf, resp);
      FileClose(rf);
   }
}

//+------------------------------------------------------------------+
string _dispatch(string raw) {
   string action = _jsonGet(raw, "action");

   if (action == "order")     return _handleOrder(raw);
   if (action == "close")     return _handleClose(raw);
   if (action == "account")   return _handleAccount();
   if (action == "positions") return _handlePositions();

   return "{\"error\":\"Unknown action: " + action + "\"}";
}

//+------------------------------------------------------------------+
//| Place a market order                                              |
string _handleOrder(string raw) {
   string sym    = _jsonGet(raw, "symbol");
   string typ    = _jsonGet(raw, "type");
   double vol    = StringToDouble(_jsonGet(raw, "volume"));
   double sl     = StringToDouble(_jsonGet(raw, "stopLoss"));
   double tp     = StringToDouble(_jsonGet(raw, "takeProfit"));
   string comment = _jsonGet(raw, "comment");

   if (sym  == "") sym = DefaultSymbol;
   if (vol  <= 0)  vol = 0.01;
   if (comment == "") comment = "adaptive-bot";

   ENUM_ORDER_TYPE orderType = (typ == "BUY") ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   double price = (orderType == ORDER_TYPE_BUY)
                  ? SymbolInfoDouble(sym, SYMBOL_ASK)
                  : SymbolInfoDouble(sym, SYMBOL_BID);

   bool ok = trade.PositionOpen(sym, orderType, vol, price, sl, tp, comment);

   if (!ok) {
      uint retCode = trade.ResultRetcode();
      return "{\"error\":\"PositionOpen failed\",\"code\":" + IntegerToString(retCode)
           + ",\"msg\":\"" + trade.ResultRetcodeDescription() + "\"}";
   }

   ulong ticket   = trade.ResultOrder();
   double opened  = trade.ResultPrice();

   return "{\"ticket\":"     + IntegerToString((int)ticket)
        + ",\"symbol\":\""   + sym + "\""
        + ",\"type\":\""     + typ + "\""
        + ",\"volume\":"     + DoubleToString(vol, 2)
        + ",\"openPrice\":"  + DoubleToString(opened > 0 ? opened : price, 5)
        + ",\"sl\":"         + DoubleToString(sl, 5)
        + ",\"tp\":"         + DoubleToString(tp, 5)
        + "}";
}

//+------------------------------------------------------------------+
//| Close a position by ticket                                        |
string _handleClose(string raw) {
   ulong  ticket = (ulong)StringToInteger(_jsonGet(raw, "ticket"));
   double vol    = StringToDouble(_jsonGet(raw, "volume"));

   if (!posInfo.SelectByTicket(ticket)) {
      return "{\"error\":\"Position ticket not found\",\"ticket\":" + IntegerToString((int)ticket) + "}";
   }

   bool ok;
   if (vol > 0 && vol < posInfo.Volume()) {
      ok = trade.PositionClosePartial(ticket, vol);
   } else {
      ok = trade.PositionClose(ticket);
   }

   if (!ok) {
      return "{\"error\":\"PositionClose failed\",\"code\":" + IntegerToString(trade.ResultRetcode())
           + ",\"msg\":\"" + trade.ResultRetcodeDescription() + "\"}";
   }
   return "{\"closed\":true,\"ticket\":" + IntegerToString((int)ticket) + "}";
}

//+------------------------------------------------------------------+
//| Account information                                               |
string _handleAccount() {
   return "{\"balance\":"    + DoubleToString(acctInfo.Balance(),    2)
        + ",\"equity\":"     + DoubleToString(acctInfo.Equity(),     2)
        + ",\"margin\":"     + DoubleToString(acctInfo.Margin(),     2)
        + ",\"freeMargin\":" + DoubleToString(acctInfo.FreeMargin(), 2)
        + ",\"leverage\":"   + IntegerToString((int)acctInfo.Leverage())
        + ",\"currency\":\"" + acctInfo.Currency() + "\""
        + ",\"server\":\""   + acctInfo.Server()   + "\""
        + "}";
}

//+------------------------------------------------------------------+
//| Open positions list                                               |
string _handlePositions() {
   string arr  = "[";
   bool   first = true;

   for (int i = 0; i < PositionsTotal(); i++) {
      ulong t = PositionGetTicket(i);
      if (t == 0) continue;
      if (!posInfo.SelectByTicket(t)) continue;

      if (!first) arr += ",";
      arr += "{"
           + "\"ticket\":"    + IntegerToString((int)t)
           + ",\"symbol\":\"" + posInfo.Symbol() + "\""
           + ",\"type\":\""   + (posInfo.PositionType() == POSITION_TYPE_BUY ? "BUY" : "SELL") + "\""
           + ",\"volume\":"   + DoubleToString(posInfo.Volume(),    2)
           + ",\"openPrice\":" + DoubleToString(posInfo.PriceOpen(), 5)
           + ",\"sl\":"       + DoubleToString(posInfo.StopLoss(),  5)
           + ",\"tp\":"       + DoubleToString(posInfo.TakeProfit(), 5)
           + ",\"profit\":"   + DoubleToString(posInfo.Profit(),    2)
           + ",\"swap\":"     + DoubleToString(posInfo.Swap(),      2)
           + "}";
      first = false;
   }
   return "{\"positions\":" + arr + "]}";
}

//+------------------------------------------------------------------+
//| Minimal JSON field extractor (no external libraries needed)      |
string _jsonGet(string json, string key) {
   string search = "\"" + key + "\":";
   int pos = StringFind(json, search);
   if (pos < 0) return "";
   pos += StringLen(search);

   while (pos < StringLen(json) && StringGetCharacter(json, pos) == ' ') pos++;

   bool isStr = (StringGetCharacter(json, pos) == '"');
   if (isStr) {
      pos++;
      string val = "";
      while (pos < StringLen(json)) {
         ushort ch = StringGetCharacter(json, pos++);
         if (ch == '"') break;
         val += ShortToString(ch);
      }
      return val;
   }

   string val = "";
   while (pos < StringLen(json)) {
      ushort ch = StringGetCharacter(json, pos);
      if (ch == ',' || ch == '}' || ch == ']' || ch == ' ' || ch == '\n') break;
      val += ShortToString(ch);
      pos++;
   }
   return val;
}

//+------------------------------------------------------------------+
void _log(string msg) {
   int fh = FileOpen(LOG_FILE, FILE_READ | FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if (fh == INVALID_HANDLE)
      fh = FileOpen(LOG_FILE, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if (fh != INVALID_HANDLE) {
      FileSeek(fh, 0, SEEK_END);
      FileWriteString(fh, TimeToString(TimeCurrent(), TIME_DATE|TIME_SECONDS) + "  " + msg + "\n");
      FileClose(fh);
   }
}
