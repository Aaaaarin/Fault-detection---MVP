# CNC Manual Self-Test Report

**Generated:** 2026-05-24 19:25:25 UTC  
**Manual:** `service_manual_PM10010.pdf`  
**Manual ID:** `pm10010_service_manual`  
**Chunks ingested:** 28  

## Final Verdict: ✅ PASS

## 1. Environment — ✅ PASS

- logging stdlib confirmed: C:\Python314\Lib\logging\__init__.py
- All local modules imported successfully
- .env found at C:\Users\HP\Anaconda projects\Fault detection - MVP\fault-copilot\.env
- ANTHROPIC_API_KEY is set
- Manual: C:\Users\HP\Anaconda projects\Fault detection - MVP\fault-copilot\manuals\service_manual_PM10010.pdf  (1.4 MB)

## 2. Ingestion — ⊘ SKIPPED (already ingested)

- Already ingested (28 chunks).  Use --force to re-ingest.

## 3. Retrieval Tests — ✅ PASS

| Query | Chunks | Top Section | Page | Status |
|-------|--------|-------------|------|--------|
| lamp not on turntable not rotating food not h | 5 | 7 COMMON BREAKDOWN OF MICROWAVE OVEN AND MEANS OF | 22 | ✅ |
| fuse broken transformer short circuit | 5 | 7 COMMON BREAKDOWN OF MICROWAVE OVEN AND MEANS OF | 22 | ✅ |
| safety precautions before servicing interlock | 5 | SAFETY PRECAUTIONS | 2 | ✅ |
| door interlock switch latch pilot switch | 5 | 4.11 | 15 | ✅ |
| magnetron antenna wave guide assembly | 5 | 2. | 12 | ✅ |
| fan motor assembly shaft glue | 5 | 1. | 13 | ✅ |
| microwave leakage door seal measurement | 5 | “THETRANSFERING LINE ONE–FOURTHWAVE | 8 | ✅ |
| capacitor discharge diode polarity transforme | 5 | 5.2.5 | 18 | ✅ |

## 4. Resolution Tests — ✅ PASS

## 5. Logging Test — ✅ PASS

- Logged fault_id=15
- Resolution logged as complete
- get_recent_faults() confirmed the entry

## Recommendations

- None — all tests passed. 🎉
