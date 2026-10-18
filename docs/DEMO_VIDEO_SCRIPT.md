# LifePlan AI — Demo Video Script & Showcase Prompts

> **Target Duration:** 3 to 4 minutes  
> **Key Goal:** Showcase why LifePlan AI outperforms generic LLMs by demonstrating grounded 5-agent LangGraph coordination, natural-language schedule precedence, zero-phantom commitments, deterministic budget validation, and real-time dynamic re-planning.

---

## 🌟 The 3 Ideal Showcase Prompts

### Prompt 1: The Flagship 3-Day Intercity Vacation (Primary Demo)
> **Best for:** Showing multi-day planning, intercity travel, 3-star hotel verification, return deadline enforcement, and the itemized cost ledger.

```text
Plan a 3-day trip from Pune to Mumbai from Friday 9:00 AM to Sunday evening. We need at least a 3-star hotel, want to explore Marine Drive, heritage sites, and great seafood. Budget is ₹22,000 for 2 people. I must be back in Pune by 8:00 PM on Sunday.
```

**Key Features Demonstrated:**
- **Natural-Language Precedence:** Auto-resolves dates, duration, and deadlines without manual calendar math.
- **Intercity Routing:** Automatically adds outbound Pune-to-Mumbai and return Mumbai-to-Pune travel legs (avoiding duplicate return hops on Day 1 or Day 2).
- **Strict Return Deadline:** Departure from Mumbai is calculated backwards from the 8:00 PM Sunday deadline based on realistic highway travel times.
- **3-Star Hotel Verification:** Accommodates guests only in verified 3+ star properties.
- **Itemized Cost Ledger:** Transparent breakdown of Accommodation, Intercity Transit, Local Travel, Dining, Activities, 5% Contingency, and Remaining Budget.

---

### Prompt 2: The Constrained Local Evening (Commitment Handling)
> **Best for:** Showing the "My Existing Commitments" engine, zero phantom events, and strict budget ceilings.

*Before submitting, add one manual commitment in Step 3:*
- **Title:** `Team Project Meeting`
- **Time:** `5:00 PM – 6:30 PM`
- **Choice:** *"Plan around these commitments"*

```text
Plan an evening outing in Shivajinagar, Pune: an acoustic cafe or comedy show followed by authentic Maharashtrian dinner. Total budget ₹1,500 for myself. Avoid noisy clubs.
```

**Key Features Demonstrated:**
- **Zero Phantom Events:** Clean slate with no unwanted recurring meetings or unconfirmed blocks.
- **Availability Window:** First activity begins strictly after 6:30 PM plus travel buffer.
- **Strict Budget Enforcement:** Does not propose upscale restaurants exceeding ₹1,500.
- **Category Filter:** Exclusion filter respects "avoid noisy clubs".

---

### Prompt 3: Dynamic Real-Time Re-planning ("It's Raining!")
> **Best for:** Demonstrating runtime adaptability and LangGraph's critique-and-repair loop.

*Action:* On the finished itinerary, open the **⚡ Quick Re-plan** drawer and select:
> **"It's raining — shift outdoor stops to indoor alternatives"**

**Key Features Demonstrated:**
- Detects outdoor promenade/park stops and swaps them with indoor museums, galleries, or covered cafes.
- Preserves the overall chronological timeline and budget envelope without restarting from scratch.

---

## 🎬 Scene-by-Scene Video Recording Script

```mermaid
flowchart LR
    S1["Scene 1 (0:00–0:40)\nHook & The Problem"] --> S2["Scene 2 (0:40–1:15)\nGuided UI & Commitments"]
    S2 --> S3["Scene 3 (1:15–2:30)\nLive Run & Itinerary"]
    S3 --> S4["Scene 4 (2:30–3:15)\nCost Ledger & Re-planning"]
    S4 --> S5["Scene 5 (3:15–3:45)\nExport & Architecture"]
```

---

### Scene 1: The Problem & Introduction (0:00 – 0:40)
- **Visual:** Open browser to `http://localhost:8000`. Show the clean homepage header with the LifePlan AI logo and the 5-Agent Architecture diagram or banner.
- **Speaker:**
  > *"Have you ever asked ChatGPT to plan a weekend trip, only to get a list of venues that are permanently closed, impossible travel times like crossing Mumbai in ten minutes, or a budget that leaves out hotel taxes and intercity transit?  
  > Generic LLMs hallucinate logistics because they cannot calculate physical routes or verify live constraints.  
  > This is **LifePlan AI** — an autonomous multi-agent planner built with LangGraph, deterministic constraint validation, and real-world map and weather APIs."*

---

### Scene 2: Guided Setup & Commitment Engine (0:40 – 1:15)
- **Visual:**
  1. Click **"Get Started"** or focus on Step 1.
  2. Paste **Prompt 1** into the text area.
  3. Briefly toggle the **"📍 Current GPS"** button to show location resolution, then advance to **Step 3: Commitments**.
  4. Show the **"My Existing Commitments"** card. Point out that it starts completely empty — zero phantom events.
  5. Advance to **Step 4: Review**. Point out the **"Verified Extracted Requirements"** card showing that the prompt's natural language automatically set the dates, hotel stars, and return deadline.
- **Speaker:**
  > *"Instead of an overwhelming form, LifePlan AI uses a clear 4-step wizard.  
  > Here in Step 1, I simply describe what I want in plain English: a 3-day trip from Pune to Mumbai, at least a 3-star hotel, budget of ₹22,000, and a strict return deadline to Pune by 8:00 PM on Sunday.  
  > Notice Step 3: our new manual commitments system starts with zero phantom calendar events. You have complete control to plan around your real commitments or ignore them for vacation mode.  
  > And in Step 4, our natural-language engine has already extracted and verified our exact constraints before any API quota is spent."*

---

### Scene 3: Live Generation & The Verified Itinerary (1:15 – 2:30)
- **Visual:**
  1. Click **"✨ Create My Itinerary"**.
  2. Show the real-time activity stream: Coordinator delegating to Profile, Discovery, Logistics, and Planner.
  3. When the results screen appears, scroll smoothly through:
     - The **Day 1, Day 2, and Day 3 timeline** (highlighting the outbound Pune-to-Mumbai cab leg and hotel check-in).
     - The **Interactive Leaflet Route Map** with numbered venue markers.
     - Note the strict chronological order of stops within each day.
- **Speaker:**
  > *"Under the hood, five specialized agents coordinate over a shared LangGraph blackboard:  
  > The **Coordinator** orchestrates the run; the **Profile Agent** evaluates availability; **Discovery** retrieves real places from OpenStreetMap and Geoapify; **Logistics** computes road-network matrices via OSRM; and the **Planner** solves the schedule.  
  > Notice the result:  
  > Day 1 begins with a realistic 3.5-hour highway drive from Pune to Mumbai.  
  > Intermediate stops are geographically clustered in Mumbai — eliminating the bug where trips routed back to Pune every night!  
  > Every scheduled stop has verified opening hours and real coordinates displayed directly on the interactive Leaflet map."*

---

### Scene 4: Itemized Cost Ledger & Dynamic Re-planning (2:30 – 3:15)
- **Visual:**
  1. Scroll to the **"Trip Cost Ledger"** card. Point out each item:
     - Accommodation (verified 3-star hotel)
     - Intercity Travel
     - Local Travel
     - Dining
     - Activities
     - **5% Contingency Buffer**
     - Remaining Budget with the green **"Within Budget"** badge.
  2. Scroll up to Day 3 afternoon: point out the return journey departs in time to reach Pune *before* 8:00 PM.
  3. Open the **"⚡ Quick Re-plan"** drawer and click **"It's raining"**. Show the instant adaptation.
- **Speaker:**
  > *"Notice our transparent Cost Ledger. Unlike basic chatbots that only guess meal prices, LifePlan AI deterministically itemizes accommodation, intercity highway tolls, local transit, food, activities, and an automatic 5% contingency buffer. Here we are safely within our ₹22,000 budget with ₹2,300 to spare.  
  > Furthermore, our return deadline is strictly guaranteed: the system scheduled our return journey from Mumbai to depart by 4:15 PM so we pull into Pune before our 8:00 PM cutoff.  
  > And if weather strikes? A single click on 'It's raining' dynamically shifts outdoor activities to covered cultural stops without breaking the rest of the schedule."*

---

### Scene 5: Calendar Export, Architecture & Conclusion (3:15 – 3:45)
- **Visual:**
  1. Click **"📅 Add to Calendar (.ics)"** to show the downloaded file.
  2. Briefly show the **"How Your Plan Was Created"** panel with agent stats and token efficiency.
  3. Return to the title view.
- **Speaker:**
  > *"When you're ready, one click exports the entire verified schedule to Apple Calendar, Google Calendar, or Outlook via `.ics`, complete with venue addresses and travel buffers.  
  > With 93 passing automated tests covering all constraint validations, LifePlan AI bridges the gap between generative AI creativity and real-world mathematical feasibility.  
  > Thank you!"*

---

## 💡 Quick Tips for a Flawless Demo Recording

1. **Use Dev Mock Mode if offline or saving API quota:**
   - Toggle the **🧪 Mock Mode** switch in the top navigation bar. It runs the full 5-stage agent flow with realistic simulated data in under 2 seconds.
2. **Resolution:** Record at **1080p (1920x1080)** at 100% or 110% browser zoom for crisp text readability.
3. **Audio:** Use a clean USB microphone or headset; keep sentences brisk and energetic.
4. **Local Server Command:**
   ```powershell
   .venv\Scripts\python -m uvicorn lifeplan.api:app --host 127.0.0.1 --port 8000 --reload
   ```
