# Limited Resolution Test — PM10010 Microwave Oven Service Manual

**Date:** 2026-05-24 19:25:46 UTC  
**Manual ID:** `pm10010_service_manual`  
**Mode:** text-only · no vision · 5 Claude calls  

## Verdict: ✅ PASS

| # | Query | Conf | Steps | fault_id |
|---|-------|------|-------|----------|
| 1 | The lamp is not on and the turntable is not rotating. | 🔴 low | 10 | 16 |
| 2 | The fuse is broken and transformer may be short circuited. | 🔴 low | 10 | 17 |
| 3 | How should I safely check the door interlock? | 🔴 low | 10 | 18 |
| 4 | There may be microwave leakage near the door seal. | 🟡 medium | 9 | 19 |
| 5 | How do I discharge the capacitor safely before checking tran | 🔴 low | 10 | 20 |

---
### Query 1: _The lamp is not on and the turntable is not rotating._

- **Confidence:** 🔴 **LOW**
- **Context chunks retrieved:** 5
- **Steps generated:** 10
- **fault_id (SQLite):** 16
- **Manual sections:** 5.4

**Steps (first 5):**

1. SAFETY: Isolate the unit Pull out the power plug from the wall socket. Confirm the unit has no power before proceeding. Do not skip this step. ⚠ SAFETY
2. Check the power connection Inspect the power plug and wall socket for visible damage or poor connection. Re-seat the plug firmly. Restore power briefly and test. (Ref: Section 7, Page 22 — Cause: "Power plug and socket not in good connection")
3. Check the door and door hook Open and close the oven door firmly. Inspect the door hook for visible damage or breakage. A broken hook will prevent the interlock switch from closing, stopping all functions. (Ref: Section 7, Page 22 — Cause: "Door hook broken" / "Interlock switch hasn't closed")
4. SAFETY: Pull power plug again before internal inspection Pull out the power plug. Discharge the high-voltage capacitor before touching any internal components. Use an insulated screwdriver to short the capacitor terminals. (Ref: Section 6.1, Page 21 — "Make sure the power cord is pulled out and the high-voltage capacitor is discharged") ⚠ SAFETY
5. Remove the cabinet Using a "+" (Phillips) screwdriver, loosen the four screws at the back of the oven. Push the cabinet back 25mm and lift it off. (Ref: Section 4.1, Page 9)

---
### Query 2: _The fuse is broken and transformer may be short circuited._

- **Confidence:** 🔴 **LOW**
- **Context chunks retrieved:** 5
- **Steps generated:** 10
- **fault_id (SQLite):** 17
- **Manual sections:** 5.2.5, 5.4

**Steps (first 5):**

1. SAFETY: Isolate the Equipment - Press STOP on the oven - Pull out the power plug from the wall socket - Place a "DO NOT OPERATE" tag on the unit - Confirm power is fully disconnected before proceeding ⚠ SAFETY
2. Remove the Cabinet - Using a suitable screwdriver, remove the cabinet cover and set it aside safely
3. SAFETY: Discharge the Capacitor - Before touching any internal components, discharge the high voltage capacitor fully - ESCALATE: Contact senior technician if you are not trained in capacitor discharge procedure — this step is mandatory and cannot be skipped ⚠ SAFETY 📞 ESCALATE
4. Replace the 8A Fuse - Locate the 8A fuse (ref: Section 7, Fault 1) - Visually confirm it is blown - Replace with a new 8A fuse of the same rating - Do not power on yet — the root cause must be confirmed first
5. Test the Transformer Primary Winding - Using an avometer set to resistance mode, measure the primary winding of the transformer (ref: Section 5.2.5, Fig. 5-10) - Expected reading: approximately 2.2 ohms - If reading is outside this value → transformer is faulty, proceed to Step 6 - If reading is correct → proceed to Step 6 to check secondary winding

---
### Query 3: _How should I safely check the door interlock?_

- **Confidence:** 🔴 **LOW**
- **Context chunks retrieved:** 5
- **Steps generated:** 10
- **fault_id (SQLite):** 18
- **Manual sections:** SAFETY PRECAUTIONS, 4.11

**Steps (first 5):**

1. SAFETY: Remove Power Pull out the power plug from the wall socket completely. Do not proceed with the plug connected. (Manual Section: Safety Precautions, Page 2) ⚠ SAFETY
2. SAFETY: Discharge the Capacitor Using a screwdriver, discharge between one end of the capacitor and the baseboard. This eliminates stored electrical energy that can cause shock even with the plug removed. (Manual Section: 4.11, Page 15) ⚠ SAFETY
3. Remove the Cabinet Take off the outer cabinet to access internal components. (Manual Section: 4.11, Page 15) --- PHASE 2 — INTERLOCK INSPECTION
4. Inspect the Door Physically Check the following items visually before touching any switch: - Door closes fully and securely - No visible damage to hinges or latches - No arcing marks or wear on door seals or sealing surfaces - No signs of the unit being dropped or abused (Manual Section: Safety Precautions, Page 2 — items 1 through 5) ⚠ SAFETY
5. Check Door Hook Condition Inspect the door hook for breakage or wear. A broken hook must be replaced before proceeding. (Manual Section: Fault Table, Page 23)

---
### Query 4: _There may be microwave leakage near the door seal._

- **Confidence:** 🟡 **MEDIUM**
- **Context chunks retrieved:** 5
- **Steps generated:** 9
- **fault_id (SQLite):** 19
- **Manual sections:** 5.3, 2.4.3, 5.2.4

**Steps (first 5):**

1. SAFETY: Stop and Isolate Pull out the power plug from the wall socket before touching any part of the oven. Confirm the oven is fully de-energised before proceeding. ⚠ SAFETY
2. Visual Inspection of Door and Seal Inspect the door for the following conditions (ref. Section 5.3, p.19): - Door is deformed or does not close tightly - Hinges are loose or damaged - Door pressing cover or embed piece is damaged or missing - Visible cracks in the door shielding net/window - Dirt or debris between the door and the oven frame - Obvious damage or unevenness on the oven body If any of these are found, do not power on the oven. Clean any debris now using a dry cloth. For physical damage, ESCALATE: Contact senior technician for this step. 📞 ESCALATE
3. Prepare for Leakage Measurement Place approximately 275 ml of water in a graduated container at the centre of the glass tray (ref. Section 5.3, p.19). Close the door. Set the timer to 3 minutes, power to High.
4. Plug In and Start the Oven Plug the oven back in and start it to run in normal operating mode. Do not touch the oven body during measurement.
5. Measure Microwave Leakage Using a microwave leakage meter, scan the probe around: - All door crack edges (left and right sides) - The window hole positions - Air vents on all four sides of the oven Move the probe at a speed not exceeding 25 mm per second. Keep the probe direction aligned with the outward direction of microwave leakage (ref. Section 5.2.4, p.17). > ✅ Acceptable limit: Maximum 1 milliwatt/cm² at any point. Values above this are abnormal and must be corrected. Target: below 0.75 milliwatt/cm² (ref. Section 5.3, p.19). --- PHASE 2 — Adjustment and Correction

---
### Query 5: _How do I discharge the capacitor safely before checking transformer resistance?_

- **Confidence:** 🔴 **LOW**
- **Context chunks retrieved:** 5
- **Steps generated:** 10
- **fault_id (SQLite):** 20
- **Manual sections:** 5.2.5, SAFETY PRECAUTIONS, 5.4

**Steps (first 5):**

1. SAFETY / LOTO Stop the line immediately. Pull out the power plug from the wall socket. Do not proceed with the power plug connected at any point during this procedure. Confirm the plug is physically removed and in your possession. ⚠ SAFETY
2. SAFETY CHECK (Before Opening Cabinet) Before removing the cabinet, check all of the following (Safety Precautions, Page 2): - Door closes properly - No damage to door hinges or latches - No visible arcing, wear, or damage to door seals - No evidence the unit has been dropped or abused If any of these checks fail — ESCALATE: Contact senior technician before continuing. ⚠ SAFETY 📞 ESCALATE
3. REMOVE THE CABINET Using the appropriate screwdriver, remove the cabinet panels to access the internal components. --- PHASE 2 — CAPACITOR DISCHARGE & TRANSFORMER CHECK
4. IDENTIFY THE CAPACITOR Locate the high voltage capacitor. It is positioned near the diode and transformer inside the cabinet. (Section 4.7, Page 13 / Fig. 4-16)
5. DISCHARGE THE CAPACITOR ⚠️ CRITICAL SAFETY STEP — Lethal voltage may be stored in the capacitor even with power removed. The manual requires the capacitor to be discharged before measurement. (Section 5.2.5, Page 18) > ESCALATE: The manual references discharging the capacitor but does not provide a specific discharge tool or step-by-step discharge method in the provided sections. Contact your senior technician to confirm your site-approved discharge tool and method (typically an insulated resistor discharge tool rated for high-voltage capacitors) before proceeding. Do not short the capacitor terminals with an uninsulated tool or wire. ⚠ SAFETY 📞 ESCALATE
