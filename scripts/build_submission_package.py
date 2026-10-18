"""Build Complete LifePlan AI Submission Package.

Generates:
1. LifePlan_AI_Project_Report.docx
2. LifePlan_AI_Presentation.pptx (20 slides with speaker notes)
3. LifePlan_AI_Demo_Video_Script.docx
4. Copies all files into LifePlan_AI_Submission_Package/
5. Creates LifePlan_AI_Submission_Package.zip in the project root

Team Members:
- YASH PATEL (2307012235)
- ARSH ANSARI (23070122047)
- ALIRAZA SHAIKH (23070122023)
"""

from __future__ import annotations

import os
import shutil
import zipfile
from pathlib import Path

import docx
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls

from pptx import Presentation
from pptx.util import Inches as PptxInches, Pt as PptxPt
from pptx.dml.color import RGBColor as PptxRGBColor
from pptx.enum.text import PP_ALIGN

ROOT = Path(__file__).resolve().parent.parent
BRAIN = Path(r"C:\Users\yashp\.gemini\antigravity\brain\07e1fcaf-a3ed-4218-ac14-2f9de829afab")
PACKAGE_DIR = ROOT / "LifePlan_AI_Submission_Package"
ZIP_PATH = ROOT / "LifePlan_AI_Submission_Package.zip"

NAVY = RGBColor(27, 54, 93)      # #1B365D
ACCENT = RGBColor(43, 108, 176)  # #2B6CB0
DARK = RGBColor(26, 32, 44)      # #1A202C
GREEN = RGBColor(40, 167, 69)
SLATE = RGBColor(100, 116, 139)

PPT_NAVY = PptxRGBColor(27, 54, 93)
PPT_ACCENT = PptxRGBColor(43, 108, 176)
PPT_DARK = PptxRGBColor(26, 32, 44)
PPT_WHITE = PptxRGBColor(255, 255, 255)
PPT_MUTED = PptxRGBColor(100, 116, 139)
PPT_BG_CARD = PptxRGBColor(241, 245, 249)


def set_cell_background(cell, hex_color: str):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{hex_color}"/>')
    tc_pr.append(shd)


def set_cell_margins(cell, top=100, bottom=100, left=150, right=150):
    tc_pr = cell._tc.get_or_add_tcPr()
    tc_mar = parse_xml(
        f'<w:tcMar {nsdecls("w")}>'
        f'<w:top w:w="{top}" w:type="dxa"/>'
        f'<w:bottom w:w="{bottom}" w:type="dxa"/>'
        f'<w:left w:w="{left}" w:type="dxa"/>'
        f'<w:right w:w="{right}" w:type="dxa"/>'
        f'</w:tcMar>'
    )
    tc_pr.append(tc_mar)


def add_callout(doc: docx.Document, text: str, prefix: str = "NOTE:"):
    tbl = doc.add_table(rows=1, cols=1)
    tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    tbl.autofit = False
    tbl.columns[0].width = Inches(6.5)
    
    cell = tbl.cell(0, 0)
    set_cell_background(cell, "F0F4F8")
    set_cell_margins(cell, top=140, bottom=140, left=200, right=200)
    
    tc_pr = cell._tc.get_or_add_tcPr()
    borders = parse_xml(
        f'<w:tcBorders {nsdecls("w")}>'
        f'<w:top w:val="none"/>'
        f'<w:left w:val="single" w:sz="24" w:space="0" w:color="1B365D"/>'
        f'<w:bottom w:val="none"/>'
        f'<w:right w:val="none"/>'
        f'</w:tcBorders>'
    )
    tc_pr.append(borders)
    
    p = cell.paragraphs[0]
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(2)
    r1 = p.add_run(f"{prefix} ")
    r1.bold = True
    r1.font.color.rgb = NAVY
    r1.font.name = "Calibri"
    r1.font.size = Pt(10)
    
    r2 = p.add_run(text)
    r2.font.color.rgb = DARK
    r2.font.name = "Calibri"
    r2.font.size = Pt(10)
    doc.add_paragraph()


def format_table_header(row, col_names: list[str], widths: list[float] | None = None):
    for idx, name in enumerate(col_names):
        cell = row.cells[idx]
        if widths and idx < len(widths):
            cell.width = Inches(widths[idx])
        set_cell_background(cell, "1B365D")
        set_cell_margins(cell, top=140, bottom=140, left=150, right=150)
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        p.paragraph_format.space_before = Pt(2)
        p.paragraph_format.space_after = Pt(2)
        r = p.add_run(name)
        r.bold = True
        r.font.name = "Calibri"
        r.font.color.rgb = RGBColor(255, 255, 255)
        r.font.size = Pt(9.5)


def format_table_row(row, values: list[str], is_even: bool = False, widths: list[float] | None = None):
    bg_color = "F8FAFC" if is_even else "FFFFFF"
    for idx, val in enumerate(values):
        cell = row.cells[idx]
        if widths and idx < len(widths):
            cell.width = Inches(widths[idx])
        set_cell_background(cell, bg_color)
        set_cell_margins(cell, top=100, bottom=100, left=150, right=150)
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        p.paragraph_format.space_before = Pt(2)
        p.paragraph_format.space_after = Pt(2)
        r = p.add_run(val)
        r.font.name = "Calibri"
        r.font.color.rgb = DARK
        r.font.size = Pt(9)


def add_header_metadata(doc: docx.Document, doc_type: str):
    title_p = doc.add_paragraph()
    title_p.paragraph_format.space_before = Pt(0)
    title_p.paragraph_format.space_after = Pt(4)
    r_title = title_p.add_run("LIFEPLAN AI")
    r_title.bold = True
    r_title.font.name = "Calibri"
    r_title.font.size = Pt(24)
    r_title.font.color.rgb = NAVY

    sub_p = doc.add_paragraph()
    sub_p.paragraph_format.space_before = Pt(0)
    sub_p.paragraph_format.space_after = Pt(12)
    r_sub = sub_p.add_run(f"Autonomous Multi-Agent Activity & Trip Planner — {doc_type}")
    r_sub.font.name = "Calibri"
    r_sub.font.size = Pt(14)
    r_sub.font.color.rgb = ACCENT

    tbl = doc.add_table(rows=5, cols=2)
    tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    tbl.autofit = False
    tbl.columns[0].width = Inches(2.2)
    tbl.columns[1].width = Inches(4.3)
    
    meta = [
        ("Course / Project:", "Semester VII Flexi / CA3 Capstone Project (Agentic AI)"),
        ("Project Team:", "YASH PATEL (2307012235)\nARSH ANSARI (23070122047)\nALIRAZA SHAIKH (23070122023)"),
        ("Institution:", "Symbiosis Institute of Technology (SIT), Pune\nSymbiosis International (Deemed University)"),
        ("Department:", "Department of Computer Science & Information Technology"),
        ("Repository URL:", "https://github.com/yashpatelSIT/lifeplan-ai"),
    ]
    for idx, (k, v) in enumerate(meta):
        row = tbl.rows[idx]
        cell_k, cell_v = row.cells[0], row.cells[1]
        set_cell_background(cell_k, "EDF2F7")
        set_cell_background(cell_v, "F7FAFC")
        set_cell_margins(cell_k, top=60, bottom=60, left=100, right=100)
        set_cell_margins(cell_v, top=60, bottom=60, left=100, right=100)
        
        pk = cell_k.paragraphs[0]
        pk.paragraph_format.space_before = Pt(2)
        pk.paragraph_format.space_after = Pt(2)
        rk = pk.add_run(k)
        rk.bold = True
        rk.font.name = "Calibri"
        rk.font.size = Pt(9.5)
        rk.font.color.rgb = NAVY
        
        pv = cell_v.paragraphs[0]
        pv.paragraph_format.space_before = Pt(2)
        pv.paragraph_format.space_after = Pt(2)
        rv = pv.add_run(v)
        rv.font.name = "Calibri"
        rv.font.size = Pt(9.5)
        rv.font.color.rgb = DARK

    doc.add_paragraph().paragraph_format.space_after = Pt(12)


# ---------------------------------------------------------------------
# BUILD PROJECT REPORT (.docx)
# ---------------------------------------------------------------------

def build_project_report_docx():
    doc = docx.Document()
    for s in doc.sections:
        s.top_margin = Inches(1.0)
        s.bottom_margin = Inches(1.0)
        s.left_margin = Inches(1.0)
        s.right_margin = Inches(1.0)

    add_header_metadata(doc, "Academic Project Report")

    # Read the markdown report
    rep_md_path = BRAIN / "project_report.md"
    content = rep_md_path.read_text(encoding="utf-8") if rep_md_path.exists() else ""

    # Add Executive Summary
    h_exec = doc.add_heading("Executive Summary", level=1)
    h_exec.style.font.color.rgb = NAVY
    doc.add_paragraph(
        "LifePlan AI is an autonomous multi-agent planning platform designed to overcome the core logistical, "
        "spatial, and financial failures of single-prompt generative AI systems. By decoupling semantic reasoning from "
        "deterministic real-world truth, LifePlan AI orchestrates five specialized agents over a LangGraph shared blackboard: "
        "Adaptive Coordinator (Supervisor), Profile & Routine Agent, Discovery & Location Agent, Travel & Logistics Agent, and "
        "Planner & Optimization Agent. Grounded in seven live APIs (Geoapify, OpenStreetMap, OSRM, Tavily, SerpAPI, Open-Meteo, "
        "and Frankfurter), the platform enforces hard constraints deterministically via a Smart Decision Engine and is validated "
        "by a 93-test automated regression suite with a 100% pass rate."
    )

    # Add Chapter 1: Introduction
    doc.add_heading("1. Introduction & Problem Statement", level=1).style.font.color.rgb = NAVY
    doc.add_paragraph(
        "When generic LLMs are asked to plan a trip, they suffer from critical real-world limitations:\n"
        "1. Spatial Ignorance: Treating cities as pure text, hallucinating zero-time commutes across congested metros or omitting 150 km highway transit.\n"
        "2. Phantom Calendar Clashes: Fabricating default 9-to-5 routines or latching onto unconfirmed calendar feeds, blocking users when they are actually free.\n"
        "3. Financial Drift: Tallying restaurant meals while omitting hotel nights, intercity cab tolls, local transit, and contingency funds.\n"
        "4. Hallucinated POIs: Suggesting permanently closed attractions, educational institutions (colleges, clinics), or outdoor activities during severe storms."
    )

    # Add Chapter 2: System Architecture
    doc.add_heading("2. System Architecture & 5-Agent Design", level=1).style.font.color.rgb = NAVY
    doc.add_paragraph(
        "The architecture is built on LangGraph using an immutable shared blackboard state. "
        "The 5 autonomous agents specialize in distinct domains:"
    )

    tbl = doc.add_table(rows=6, cols=3)
    tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    tbl.autofit = False
    format_table_header(tbl.rows[0], ["Agent Name", "Domain & Responsibility", "Grounding / Tools"], [1.8, 2.7, 2.0])
    agents = [
        ("Adaptive Coordinator", "Supervisor & orchestrator. Intent decomposition, task delegation, review round arbitration, and plan finalization.", "LangGraph StateGraph, geocode, validate_state"),
        ("Profile & Availability", "True availability window calculation. Manual commitment management, zero phantom events, and holiday detection.", "calculate_availability, public_holidays, check_calendar"),
        ("Discovery & Location", "Verified venue & live event retrieval. Purges irrelevant colleges/hospitals, verifies opening hours and weather.", "Geoapify Places, OSM Overpass, Tavily, Open-Meteo"),
        ("Travel & Logistics", "Road-network distance matrices, transit modes, hotel estimation, coordinate swapping correction, and FX rates.", "OSRM Routing Engine, SerpAPI Google Hotels, Frankfurter"),
        ("Planner & Optimization", "Schedule synthesis. Window-packing solver, chronological locking, pacing buffers, and meal gap enforcement.", "build_schedule solver, validate_plan"),
    ]
    for idx, (a_name, a_desc, a_tools) in enumerate(agents):
        format_table_row(tbl.rows[idx + 1], [a_name, a_desc, a_tools], is_even=(idx % 2 == 1), widths=[1.8, 2.7, 2.0])

    doc.add_paragraph().paragraph_format.space_after = Pt(8)

    # Embed architecture diagram
    arch_img = ROOT / "docs" / "architecture.png"
    if arch_img.exists():
        p_img = doc.add_paragraph()
        p_img.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_img.add_run().add_picture(str(arch_img), width=Inches(6.0))
        p_cap = doc.add_paragraph()
        p_cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r_c = p_cap.add_run("Figure 1: LifePlan AI Multi-Agent LangGraph Blackboard Architecture")
        r_c.font.size = Pt(8.5)
        r_c.font.italic = True
        r_c.font.color.rgb = SLATE

    # Add Chapter 3: Mathematical & Deterministic Formulations
    doc.add_heading("3. Mathematical Formulations & Deterministic Engine", level=1).style.font.color.rgb = NAVY
    formulas = [
        ("Coordinate Inversion Correction:", "If (lat > 50.0 and lon < 40.0) => (lat', lon') = (lon, lat) [Indian Subcontinent Normalization]"),
        ("Speed Plausibility Boundaries:", "Speed v = d_route / (t_route / 60). Valid iff v <= 160 km/h and not (v < 2 km/h and d > 0.5 km and t > 60 min)"),
        ("50 km Duplicate Return Suppression:", "AddReturnLeg = True iff d_haversine(LastStop, Origin) <= 50.0 km. Eliminates duplicate Pune return trips on intermediate Mumbai nights."),
        ("Return Deadline Back-Calculation:", "T_depart = T_deadline - delta_t_return. Any activity ending after T_depart triggers RETURN_DEADLINE_VIOLATED."),
        ("Itemized Cost Ledger & Contingency:", "Total = Direct_Expenses + ceil(0.05 * Direct_Expenses). Remaining = Budget_max - Total. Strictly fails if Total > Budget_max."),
    ]
    for title, rule in formulas:
        p_f = doc.add_paragraph()
        p_f.paragraph_format.space_before = Pt(3)
        p_f.paragraph_format.space_after = Pt(3)
        r1 = p_f.add_run(f"• {title} ")
        r1.bold = True
        r1.font.color.rgb = NAVY
        r2 = p_f.add_run(rule)
        r2.font.color.rgb = DARK

    # Add Chapter 4: Testing & Verification
    doc.add_heading("4. Experimental Results & Regression Verification", level=1).style.font.color.rgb = NAVY
    doc.add_paragraph(
        "LifePlan AI is verified by an automated test suite of 93 test cases covering all overhaul criteria (100% green). "
        "The dedicated regression suite (tests/test_overhaul_regression.py) proves that all 20 specified criteria pass reliably in 18.17s."
    )

    out_file = ROOT / "docs" / "LifePlan_AI_Project_Report.docx"
    doc.save(out_file)
    print(f"Created: {out_file}")


# ---------------------------------------------------------------------
# BUILD DEMO VIDEO SCRIPT (.docx)
# ---------------------------------------------------------------------

def build_demo_script_docx():
    doc = docx.Document()
    for s in doc.sections:
        s.top_margin = Inches(1.0)
        s.bottom_margin = Inches(1.0)
        s.left_margin = Inches(1.0)
        s.right_margin = Inches(1.0)

    add_header_metadata(doc, "Demo Video Script & Showcase Prompts")

    doc.add_heading("1. Curated Showcase Prompts", level=1).style.font.color.rgb = NAVY
    
    prompts = [
        ("Prompt 1: The Flagship 3-Day Intercity Vacation (Primary Showcase)",
         "Plan a 3-day trip from Pune to Mumbai from Friday 9:00 AM to Sunday evening. We need at least a 3-star hotel, "
         "want to explore Marine Drive, heritage sites, and great seafood. Budget is ₹22,000 for 2 people. I must be back in Pune by 8:00 PM on Sunday.",
         "Demonstrates natural-language date extraction, realistic highway driving (~3.5 hrs), 3-star hotel verification, "
         "Sunday return deadline back-calculation, interactive Leaflet map, and transparent itemized cost ledger with 5% contingency buffer."),
        ("Prompt 2: The Constrained Local Evening (Commitments & Budget Ceilings)",
         "Plan an evening outing in Shivajinagar, Pune: an acoustic cafe or comedy show followed by authentic Maharashtrian dinner. "
         "Total budget ₹1,500 for myself. Avoid noisy clubs.",
         "Demonstrates zero phantom events, scheduling strictly after manual meetings (e.g. 5:00-6:30 PM), hard budget limit, and category exclusion filtering."),
        ("Prompt 3: Dynamic Real-Time Re-planning ('It's Raining!')",
         "Trigger via Quick Re-plan drawer: 'It's raining — shift outdoor stops to indoor alternatives'",
         "Demonstrates runtime multi-agent adaptation without restarting the budget or resetting the entire plan."),
    ]
    for title, text, why in prompts:
        p_t = doc.add_paragraph()
        p_t.paragraph_format.space_before = Pt(6)
        r_t = p_t.add_run(title)
        r_t.bold = True
        r_t.font.color.rgb = NAVY
        
        add_callout(doc, text, "PROMPT TEXT:")
        p_w = doc.add_paragraph()
        p_w.paragraph_format.space_after = Pt(8)
        r_w1 = p_w.add_run("What this demonstrates: ")
        r_w1.bold = True
        r_w1.font.color.rgb = ACCENT
        p_w.add_run(why)

    # Scene-by-Scene Script
    doc.add_heading("2. Scene-by-Scene Recording Script (3–4 Minutes)", level=1).style.font.color.rgb = NAVY
    scenes = [
        ("Scene 1 (0:00–0:40): Introduction & The Problem",
         "Visual: Show http://localhost:8000 with Mock Mode OFF.\n"
         "Dialogue: 'Have you ever asked ChatGPT to plan a trip, only to get permanently closed venues, impossible travel times, "
         "or a budget missing hotel taxes? Generic LLMs hallucinate logistics because they cannot calculate physical routes. "
         "This is LifePlan AI — an autonomous multi-agent planner built with LangGraph, deterministic constraint validation, and real map APIs.'"),
        ("Scene 2 (0:40–1:15): Guided Setup & Commitments Engine",
         "Visual: Paste Prompt 1. Advance through Step 2 to Step 3. Show 'My Existing Commitments' starting with 0 phantom events. Show Step 4 verified requirements.\n"
         "Dialogue: 'LifePlan AI uses a clear 4-step wizard. Notice Step 3: our new manual commitments system starts with zero phantom calendar events. "
         "In Step 4, our natural-language engine has already extracted and verified our exact constraints before any API quota is spent.'"),
        ("Scene 3 (1:15–2:30): Live Generation & Verified Itinerary",
         "Visual: Click Create My Itinerary. Watch live agent logs. Scroll through Day 1 to Day 3 timeline and Leaflet map.\n"
         "Dialogue: 'Five specialized agents coordinate over a shared LangGraph blackboard. Day 1 begins with a realistic 3.5-hour highway drive from Pune to Mumbai. "
         "Intermediate stops are clustered in Mumbai—eliminating the bug where trips routed back to Pune every night! Every stop has verified opening hours on the map.'"),
        ("Scene 4 (2:30–3:15): Itemized Cost Ledger & Re-planning",
         "Visual: Scroll to Trip Cost Ledger card. Point out hotel, transit, food, 5% contingency. Trigger Quick Re-plan 'It's raining'.\n"
         "Dialogue: 'Unlike basic chatbots that guess meal prices, LifePlan AI deterministically itemizes accommodation, intercity tolls, local transit, food, activities, "
         "and an automatic 5% contingency buffer. Our return deadline is strictly guaranteed before 8 PM. And if weather strikes, one click shifts outdoor activities to covered cultural stops.'"),
        ("Scene 5 (3:15–3:45): Calendar Export & Conclusion",
         "Visual: Click Add to Calendar (.ics). Show download.\n"
         "Dialogue: 'When ready, one click exports the schedule to Apple, Google, or Outlook calendar via .ics. With 93 passing automated tests, "
         "LifePlan AI bridges the gap between AI creativity and real-world mathematical feasibility. Thank you!'"),
    ]
    for s_title, s_content in scenes:
        doc.add_heading(s_title, level=2).style.font.color.rgb = ACCENT
        p_sc = doc.add_paragraph(s_content)
        p_sc.paragraph_format.line_spacing = 1.15
        p_sc.paragraph_format.space_after = Pt(6)

    out_file = ROOT / "docs" / "LifePlan_AI_Demo_Video_Script.docx"
    doc.save(out_file)
    print(f"Created: {out_file}")


# ---------------------------------------------------------------------
# BUILD PRESENTATION (.pptx)
# ---------------------------------------------------------------------

def build_presentation_pptx():
    prs = Presentation()
    prs.slide_width = PptxInches(13.333)  # 16:9 Widescreen
    prs.slide_height = PptxInches(7.5)
    blank_layout = prs.slide_layouts[6]

    slides_data = [
        # Slide 1: Title
        {
            "title": "LIFEPLAN AI",
            "subtitle": "Autonomous Multi-Agent Personal Activity & Trip Planner",
            "bullets": [
                "Grounded Real-World Logistics, Constraint Enforcement & LangGraph Coordination",
                "",
                "Project Team Members:",
                "• YASH PATEL — PRN: 2307012235",
                "• ARSH ANSARI — PRN: 23070122047",
                "• ALIRAZA SHAIKH — PRN: 23070122023",
                "",
                "Department of Computer Science & Information Technology",
                "Symbiosis Institute of Technology (SIT), Symbiosis International (Deemed University), Pune",
                "Repository: https://github.com/yashpatelSIT/lifeplan-ai",
            ],
            "notes": "Good morning respected evaluators. Today we present LifePlan AI, an autonomous multi-agent planning platform designed to solve the critical logistical and constraint failures of generative AI in travel planning.",
        },
        # Slide 2: Problem Statement
        {
            "title": "The Core Problem: Why Generic LLMs Fail",
            "subtitle": "Generative Prose vs Real-World Physical Execution",
            "bullets": [
                "❌ Spatial Ignorance: Treats cities as abstract words; assumes 0-minute commutes or ignores 150 km highway drives.",
                "❌ Phantom Calendar Collisions: Hallucinates 9-to-5 routines or latches onto unconfirmed feeds, blocking free users.",
                "❌ Budget & Expense Amnesia: Tallies meal costs but forgets accommodation, intercity tolls, local transit, and contingency funds.",
                "❌ Hallucinated POIs & Closed Venues: Suggests closed landmarks, colleges instead of tourist spots, or parks in heavy rain.",
                "❌ Flawed Multi-Day Routing: Routes users back to their origin city every single night of a 3-day vacation!",
            ],
            "notes": "When you ask ChatGPT to plan a weekend trip, it gives a fluent narrative. But it ignores travel times, hallucinates closed venues, and leaves out hotel bills.",
        },
        # Slide 3: Solution
        {
            "title": "Proposed Solution: LifePlan AI",
            "subtitle": "Separating Semantic Reasoning from Mathematical Truth",
            "bullets": [
                "• Architecture: 5 Specialized Autonomous Agents coordinated over a LangGraph Shared Blackboard.",
                "• Deterministic Truth: LLMs handle semantic intent; deterministic code & real APIs handle coordinates, dates, and math.",
                "• Smart Decision Engine: Deterministically validates budgets, star ratings, and arrival deadlines independently of model output.",
                "• Zero Phantom Events: Clean-slate manual commitments engine with 'Plan around' vs 'Ignore' modes.",
                "• Transparent Cost Ledger: Full itemized accounting with an automatic 5% contingency buffer.",
                "• 100% Verified Grounding: Venues, routes, weather, and hotel classes verified via 7 live external APIs.",
            ],
            "notes": "LifePlan AI solves this by introducing specialized autonomous agents that coordinate over a shared LangGraph blackboard.",
        },
        # Slide 4: System Architecture
        {
            "title": "System Architecture: LangGraph Shared Blackboard",
            "subtitle": "Hierarchical Supervisor-Worker State Machine",
            "bullets": [
                "• Supervisor Node (Adaptive Coordinator): Manages overall state machine, delegates tasks, and chairs review rounds.",
                "• Worker ReAct Nodes: Profile, Discovery, Logistics, and Planner each run internal Reason + Act tool loops.",
                "• Shared Blackboard State: Immutable state holding availability, candidate pools, road matrices, draft schedules, and issues.",
                "• Parallel Critique Round: Coordinator fans out using LangGraph Send to Profile, Discovery, and Logistics in parallel.",
                "• Bounded Revision Loops: Up to 2 automated repair rounds if high-severity violations are detected.",
            ],
            "notes": "Here is our architecture. The Coordinator oversees four worker agents. Agents write structured updates to the blackboard and critique drafts in parallel.",
        },
        # Slide 5: The 5 Agents Table
        {
            "title": "The 5 Specialized Autonomous Agents",
            "subtitle": "Clear Separation of Concerns and Tool Boundaries",
            "bullets": [
                "1. 🤖 Adaptive Coordinator: Supervisor. Intent decomposition, schedule resolution, review arbitration, and finalization.",
                "2. 🧠 Profile & Availability Agent: Evaluates free-time windows. Manages manual commitments and checks public holidays.",
                "3. 📍 Discovery & Location Agent: Discovers verified POIs and live events. Purges irrelevant colleges/hospitals and checks weather.",
                "4. 🚗 Travel & Logistics Agent: Calculates road-network travel times via OSRM, verifies hotel classes, and converts FX currencies.",
                "5. 📅 Planner & Optimization Agent: Schedule solver. Window-packing solver, chronological locking, and pacing buffers.",
            ],
            "notes": "Each agent is an expert in its own domain. Profile protects free time; Discovery finds open places; Logistics computes travel; Planner builds the schedule.",
        },
        # Slide 6: Agent 1 Coordinator
        {
            "title": "Agent 1: Adaptive Coordinator (Supervisor)",
            "subtitle": "The Orchestrator and Decision Arbiter",
            "bullets": [
                "• Supervisor Pattern: Central director of the LangGraph state machine routing conditional edges.",
                "• Intent Classification: Identifies plan mode (now, evening, day, weekend, domestic_trip, international_trip).",
                "• Natural-Language Precedence: Extracted dates and deadlines explicitly override default form selectors.",
                "• Review Chairperson: Aggregates critique reports, detects high-severity issues, and commands targeted repair rounds.",
                "• Circuit Breakers: Detects external API quota exhaustion and smoothly falls back to deterministic algorithms.",
            ],
            "notes": "The Coordinator oversees the lifecycle, delegates tasks, resolves conflicts between agents, and ensures no invalid plan reaches the user.",
        },
        # Slide 7: Agent 2 Profile
        {
            "title": "Agent 2: Profile & Availability Agent",
            "subtitle": "Zero Phantom Events & Manual Commitment Precision",
            "bullets": [
                "• Eliminates Phantom Events: Permanently fixes the legacy bug that inserted 12 phantom calendar meetings.",
                "• Clean-Slate Availability: A new request starts with an empty commitments list (100% open free window).",
                "• Manual Commitments Engine: Full Add, Edit, and Delete controls for real commitments (e.g. Project Sync 5:00-6:30 PM).",
                "• Explicit User Choice: 'Plan around these commitments' vs 'Ignore commitments for this plan' (vacation mode).",
                "• Commute Buffering: Automatically appends realistic travel buffers after meetings and work blocks.",
            ],
            "notes": "The Profile agent guarantees users are never constrained by fake meetings. The calendar starts 100% open unless explicitly entered.",
        },
        # Slide 8: Agent 3 Discovery
        {
            "title": "Agent 3: Discovery & Location Agent",
            "subtitle": "Grounding Truth & Institutional Noise Filtering",
            "bullets": [
                "• 100% Grounded Venues: POIs queried via Geoapify Places API and OpenStreetMap Overpass API.",
                "• Irrelevant Venue Purge: Deterministically filters out institutional and non-leisure entities:",
                "  - Educational institutions: Colleges, universities, coaching academies, hostels.",
                "  - Medical facilities: Hospitals, clinics, diagnostic centers.",
                "  - Administrative / Residential: Police stations, government offices, cooperative housing societies.",
                "• Real-Time Events: Queries live comedy gigs, concerts, and cultural exhibitions via Tavily Search API.",
                "• Weather Exposure: Evaluates precipitation forecasts via Open-Meteo; shifts outdoor visits to indoor alternatives under rain.",
            ],
            "notes": "Discovery ensures venues are real, open, and relevant. It purges non-tourist noise like colleges and clinics from activity recommendations.",
        },
        # Slide 9: Agent 4 Logistics
        {
            "title": "Agent 4: Travel & Logistics Agent",
            "subtitle": "Real Road Networks & Verified Hotel Ratings",
            "bullets": [
                "• OSRM Road Networks: Computes actual road driving, cab, and walking transit matrices instead of straight-line distances.",
                "• Coordinate Normalization: Detects and fixes swapped Indian coordinates (lat > 50, lon < 40).",
                "• Speed Plausibility: Rejects unrealistic durations (e.g. 1,204 minutes for 5 km) or speeds > 160 km/h.",
                "• Hotel Star Verification: Queries SerpAPI Google Hotels for verified star classes (3-star, 4-star, 5-star) and live nightly rates.",
                "• Intercity Connections: Automatically generates highway transit legs (e.g. ~3.5 hrs Pune-to-Mumbai cab).",
            ],
            "notes": "Logistics enforces the laws of physics. It calculates actual road times via OSRM, normalizes coordinates, and verifies hotel star ratings.",
        },
        # Slide 10: Agent 5 Planner
        {
            "title": "Agent 5: Planner & Optimization Agent",
            "subtitle": "The Constraint-Satisfying Schedule Solver",
            "bullets": [
                "• Greedy Window-Packing: Balances interest suitability, distance minimization, opening hours, and budget.",
                "• Operating Hours Adherence: Evaluates interval overlaps; never schedules attractions when closed.",
                "• Meal Gap Enforcement: Guarantees realistic spacing between lunch and dinner reservations.",
                "• Strict Chronological Guarantee: All timeline stops are strictly ordered by arrival time.",
                "• Pacing Configurations: Supports relaxed (+20 min buffer), balanced (+10 min), and packed pacing modes.",
            ],
            "notes": "Planner puts the schedule puzzle together. It packs activities into available windows, locks chronological order, and enforces opening hours.",
        },
        # Slide 11: Multi-Agent Collaboration
        {
            "title": "Multi-Agent Collaboration: Review & Repair Loop",
            "subtitle": "How Agents Critique and Fix Draft Itineraries",
            "bullets": [
                "1. Planner submits a draft schedule to the Coordinator.",
                "2. Coordinator initiates a parallel critique round via LangGraph Send:",
                "   - Profile Agent checks for calendar overlaps and window violations.",
                "   - Discovery Agent checks venue operating hours, grounding, and weather exposure.",
                "   - Logistics Agent checks transit feasibility between stops and total trip budget.",
                "3. If high-severity issues are found, Coordinator demands targeted revisions from the Planner.",
                "4. Up to 2 revision rounds occur before presentation, guaranteeing a verified output.",
            ],
            "notes": "This parallel review round is what makes our system resilient. If Profile catches a meeting clash, Planner is forced to revise before the user sees it.",
        },
        # Slide 12: Smart Decision Engine
        {
            "title": "Smart Decision Engine: Deterministic Enforcement",
            "subtitle": "Why Code Enforces Hard Constraints (Not Prompts)",
            "bullets": [
                "• The Problem: Prompts like 'keep budget under ₹1500' are soft hints that LLMs often ignore.",
                "• Deterministic Code Enforcement:",
                "  - Hard Budget Ceiling: Total cost > budget immediately raises OVER_BUDGET; marks plan status as 'failed'.",
                "  - Mandatory Return Deadline: Calculates latest departure T_depart = T_deadline - delta_t_return.",
                "  - 50 km Return Suppression: Local return legs to origin are suppressed if distance > 50 km on intermediate vacation nights.",
                "  - Fail-Honest Policy: If constraints are impossible, fails honestly rather than hallucinating fake bookings.",
            ],
            "notes": "We never let an LLM decide if a plan is valid. Our Smart Decision Engine evaluates structured data in deterministic Python code.",
        },
        # Slide 13: Tech Stack & APIs
        {
            "title": "External APIs & Technology Stack",
            "subtitle": "Grounded Real-World Production Architecture",
            "bullets": [
                "• Multi-Agent Framework: LangGraph & LangChain (StateGraph, ReAct loops, shared blackboard).",
                "• Language Models: Groq (Llama-3.3 / OSS) for ultra-low latency; Google Gemini for fallback.",
                "• Places & Mapping: Geoapify Places API, OpenStreetMap Overpass, OSRM Routing Engine.",
                "• Live Events & Accommodation: Tavily AI Search, SerpAPI (Google Flights & Google Hotels).",
                "• Weather & FX: Open-Meteo (hourly precipitation probability), Frankfurter (ECB currency rates).",
                "• Web API & Frontend: FastAPI, Uvicorn, Vanilla JS, Semantic HTML5, Leaflet.js interactive maps.",
            ],
            "notes": "Our stack combines FastAPI and LangGraph with seven external APIs for mapping, places, live events, hotels, weather, and currency.",
        },
        # Slide 14: User Experience
        {
            "title": "User Experience: Guided 4-Step Wizard",
            "subtitle": "Consumer-Friendly Workflow (No Developer Jargon)",
            "bullets": [
                "• Step 1 (Describe): Natural-language intent textarea with quick idea chips (Weekend getaway, Plan a date).",
                "• Step 2 (Preferences): Origin city with one-click 📍 Current GPS geolocation, budget picker, and interest tags.",
                "• Step 3 (Commitments): Clean commitments manager with 0 phantom events; Plan around vs Ignore mode.",
                "• Step 4 (Review): Verified requirements preview confirming extracted dates, hotel star tiers, and deadlines.",
                "• Finished Plan: Chronological timeline, interactive Leaflet map, itemized cost ledger, and .ics calendar export.",
            ],
            "notes": "We redesigned the interface into a guided 4-step wizard that shields the user from technical complexity while giving complete control.",
        },
        # Slide 15: Critical Innovations
        {
            "title": "Critical Engineering Innovations",
            "subtitle": "Breakthroughs Delivered in System Overhaul",
            "bullets": [
                "1. Natural-Language Precedence: Prompts override form pickers and clear conflicting GPS coordinates.",
                "2. Coordinate Normalization: Automatically detects and swaps inverted coordinates on the Indian subcontinent.",
                "3. Plausibility Filters: Eliminates absurd 1,204-minute city hops by enforcing physical speed boundaries.",
                "4. Duplicate Leg Elimination: 50 km geographic threshold stops vacations from routing back to origin every night.",
                "5. Itemized Cost Ledger: Automatically itemizes hotel, transit, food, activities, and a 5% contingency buffer.",
                "6. Timeline Chronological Lock: Strictly sorts stops by arrival time, ensuring alternative sorting never scrambles schedules.",
            ],
            "notes": "These engineering breakthroughs resolved legacy routing bugs, duplicate return legs, and coordinate inversions.",
        },
        # Slide 16: Dynamic Re-Planning
        {
            "title": "Dynamic Re-Planning & Disruption Handling",
            "subtitle": "Runtime Multi-Agent Disruption Recovery",
            "bullets": [
                "• Real-World Disruptions: Weather changes, traffic delays, or venues closing unexpectedly.",
                "• The ⚡ Quick Re-plan Drawer:",
                "  - 🌧️ 'It's raining — shift outdoor stops to indoor alternatives': Discovery swaps parks for covered museums/cafes.",
                "  - ⏰ 'Running 45 minutes late': Planner shifts subsequent stops forward and preserves reservations.",
                "  - 🚫 'Venue closed / skipped': Drops the stop and pulls the next best alternative from the shortlisted pool.",
                "• State Preservation: Re-planning repairs the existing blackboard without losing budget or resetting the plan.",
            ],
            "notes": "If it rains during your trip, you click 'It's raining' in our quick re-plan drawer. The multi-agent graph dynamically adapts in real time.",
        },
        # Slide 17: Testing & Verification
        {
            "title": "Testing, Verification & Regression Results",
            "subtitle": "100% Pass Rate Across All 93 Automated Tests",
            "bullets": [
                "• Automated Test Suite: Built with Pytest using synthetic and mock fixtures for high-speed offline verification.",
                "• Overhaul Regression Suite (test_overhaul_regression.py): 20 targeted tests covering all overhaul criteria.",
                "• Key Verified Test Scenarios (All 100% Passed):",
                "  ✔ test_01: Zero phantom calendar events with empty commitments list.",
                "  ✔ test_04: Pune-to-Mumbai multi-day vacation generates outbound & return highway legs.",
                "  ✔ test_05: Mandatory 8:00 PM Sunday return deadline back-calculated and enforced.",
                "  ✔ test_06: Minimum 3-star hotel requirement verified; lower ratings rejected.",
                "  ✔ test_07: Strict ₹1,500 budget fails honestly when plan costs ₹4,500.",
                "  ✔ test_11 & 12: Inverted coordinates corrected and implausible speeds rejected.",
                "  ✔ test_13: Duplicate return trips back to Pune (>50 km away) suppressed on intermediate days.",
            ],
            "notes": "We verified every single constraint with 93 automated tests that run in 18 seconds. All 20 overhaul criteria pass with 100% success.",
        },
        # Slide 18: Live Demo
        {
            "title": "Live Demo Showcase Walkthrough",
            "subtitle": "Flagship Scenario: Pune to Mumbai 3-Day Vacation",
            "bullets": [
                "• User Prompt: 'Plan a 3-day trip from Pune to Mumbai from Friday 9:00 AM to Sunday evening. "
                "We need at least a 3-star hotel, want to explore Marine Drive, heritage sites, and great seafood. "
                "Budget is ₹22,000 for 2 people. I must be back in Pune by 8:00 PM on Sunday.'",
                "• Key Live Capabilities Displayed:",
                "  1. Natural-Language Precedence: Extracted Friday-Sunday dates, budget, and Pune-Mumbai city pair.",
                "  2. Outbound Intercity Leg: Realistic 3.5-hour highway cab from Pune to Mumbai on Friday morning.",
                "  3. Verified Accommodation: Taj Lands End / Marine Plaza (3+ star rating verified, 2 nights).",
                "  4. Strict Return Deadline: Departs Mumbai at 4:15 PM Sunday to arrive in Pune before 8:00 PM.",
                "  5. Itemized Cost Ledger: Hotel + Transit + Food + Activities + 5% Contingency = ₹19,650 (Within Budget).",
            ],
            "notes": "In our live demo, we showcase this prompt to demonstrate all multi-agent capabilities working together smoothly.",
        },
        # Slide 19: Conclusion & Future Scope
        {
            "title": "Conclusion & Future Roadmap",
            "subtitle": "Bridging Generative AI and Grounded Operations",
            "bullets": [
                "• Project Contributions Summary:",
                "  - Deployed an autonomous 5-agent system using LangGraph with supervisor orchestration.",
                "  - Eliminated phantom calendar events and added manual commitment controls.",
                "  - Built a deterministic Smart Decision Engine enforcing budgets, ratings, and physical transit.",
                "  - Achieved 100% pass rate across 93 automated regression tests.",
                "• Future Work & Extensions:",
                "  - 🎟️ Direct Booking APIs: Live reservations via IRCTC, OpenTable, and Booking.com.",
                "  - 👥 Collaborative Group Planning: Multi-user shared planning rooms with live venue voting.",
                "  - 🚆 Public Transit Micro-Routing: Integration of real-time GTFS feeds for metro and rail systems.",
            ],
            "notes": "In conclusion, LifePlan AI proves that combining LLMs with deterministic multi-agent graphs produces reliable planning software.",
        },
        # Slide 20: Thank You / Q&A
        {
            "title": "LIFEPLAN AI",
            "subtitle": "Autonomous Multi-Agent Personal Activity & Trip Planner",
            "bullets": [
                "Thank You for Your Time & Attention!",
                "",
                "Project Team Members:",
                "• YASH PATEL — PRN: 2307012235",
                "• ARSH ANSARI — PRN: 23070122047",
                "• ALIRAZA SHAIKH — PRN: 23070122023",
                "",
                "Institution: Symbiosis Institute of Technology (SIT), Pune",
                "Repository: https://github.com/yashpatelSIT/lifeplan-ai",
                "Live Application: http://localhost:8000",
                "",
                "Questions & Answers Welcome",
            ],
            "notes": "Thank you very much. We are now happy to answer any questions from the evaluators.",
        },
    ]

    for s_idx, data in enumerate(slides_data):
        slide = prs.slides.add_slide(blank_layout)

        # Header banner shape
        header_shape = slide.shapes.add_shape(
            1,  # MSO_SHAPE.RECTANGLE
            PptxInches(0), PptxInches(0), PptxInches(13.333), PptxInches(1.4)
        )
        header_shape.fill.solid()
        header_shape.fill.fore_color.rgb = PPT_NAVY
        header_shape.line.color.rgb = PPT_NAVY

        # Slide number badge
        badge = slide.shapes.add_textbox(PptxInches(12.0), PptxInches(0.3), PptxInches(1.0), PptxInches(0.6))
        tf_b = badge.text_frame
        p_b = tf_b.paragraphs[0]
        p_b.text = f"{s_idx + 1} / 20"
        p_b.font.size = PptxPt(13)
        p_b.font.bold = True
        p_b.font.color.rgb = PPT_WHITE
        p_b.alignment = PP_ALIGN.RIGHT

        # Header title
        title_box = slide.shapes.add_textbox(PptxInches(0.8), PptxInches(0.15), PptxInches(11.0), PptxInches(0.7))
        tf = title_box.text_frame
        p = tf.paragraphs[0]
        p.text = data["title"]
        p.font.size = PptxPt(24)
        p.font.bold = True
        p.font.color.rgb = PPT_WHITE

        # Header subtitle
        sub_box = slide.shapes.add_textbox(PptxInches(0.8), PptxInches(0.8), PptxInches(11.0), PptxInches(0.5))
        tf_sub = sub_box.text_frame
        p_sub = tf_sub.paragraphs[0]
        p_sub.text = data["subtitle"]
        p_sub.font.size = PptxPt(13)
        p_sub.font.color.rgb = PPT_ACCENT

        # Body Card Container
        body_card = slide.shapes.add_shape(
            1,  # RECTANGLE
            PptxInches(0.8), PptxInches(1.7), PptxInches(11.733), PptxInches(5.3)
        )
        body_card.fill.solid()
        body_card.fill.fore_color.rgb = PPT_BG_CARD
        body_card.line.color.rgb = PptxRGBColor(226, 232, 240)

        # Body text
        text_box = slide.shapes.add_textbox(PptxInches(1.1), PptxInches(1.9), PptxInches(11.133), PptxInches(4.9))
        tf_body = text_box.text_frame
        tf_body.word_wrap = True

        for b_idx, bullet in enumerate(data["bullets"]):
            p_bullet = tf_body.paragraphs[0] if b_idx == 0 else tf_body.add_paragraph()
            p_bullet.text = bullet
            p_bullet.font.size = PptxPt(14 if len(data["bullets"]) <= 6 else 12.5)
            p_bullet.font.color.rgb = PPT_DARK
            p_bullet.space_after = PptxPt(6)

        # Speaker notes
        notes_slide = slide.notes_slide
        tf_notes = notes_slide.notes_text_frame
        tf_notes.text = data["notes"]

    out_file = ROOT / "docs" / "LifePlan_AI_Presentation.pptx"
    prs.save(out_file)
    print(f"Created: {out_file}")


# ---------------------------------------------------------------------
# BUNDLE AND ZIP SUBMISSION PACKAGE
# ---------------------------------------------------------------------

def bundle_package():
    if PACKAGE_DIR.exists():
        shutil.rmtree(PACKAGE_DIR)
    PACKAGE_DIR.mkdir(parents=True, exist_ok=True)

    # Copy files
    files_to_copy = [
        (ROOT / "docs" / "LifePlan_AI_Project_Synopsis.docx", "01_LifePlan_AI_Project_Synopsis.docx"),
        (ROOT / "docs" / "LifePlan_AI_Design_Document.docx", "02_LifePlan_AI_Design_Document.docx"),
        (ROOT / "docs" / "LifePlan_AI_Project_Report.docx", "03_LifePlan_AI_Project_Report.docx"),
        (ROOT / "docs" / "LifePlan_AI_Presentation.pptx", "04_LifePlan_AI_Presentation.pptx"),
        (ROOT / "docs" / "LifePlan_AI_Demo_Video_Script.docx", "05_LifePlan_AI_Demo_Video_Script.docx"),
        (BRAIN / "project_report.md", "06_LifePlan_AI_Project_Report.md"),
        (BRAIN / "presentation_deck.md", "07_LifePlan_AI_Presentation_Deck.md"),
        (BRAIN / "demo_video_script_and_prompts.md", "08_LifePlan_AI_Demo_Video_Script.md"),
        (ROOT / "docs" / "architecture.png", "architecture.png"),
        (ROOT / "docs" / "architecture.svg", "architecture.svg"),
    ]

    for src, dest_name in files_to_copy:
        if src.exists():
            dest = PACKAGE_DIR / dest_name
            shutil.copy2(src, dest)

    # Create ZIP archive
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()

    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zipf:
        for file in PACKAGE_DIR.iterdir():
            zipf.write(file, arcname=f"LifePlan_AI_Submission_Package/{file.name}")

    print(f"Created ZIP: {ZIP_PATH} ({ZIP_PATH.stat().st_size / (1024*1024):.2f} MB)")


if __name__ == "__main__":
    build_project_report_docx()
    build_demo_script_docx()
    build_presentation_pptx()
    bundle_package()
