"""Generate interactive base64-embedded Download Hub HTML widget."""

import base64
from pathlib import Path

brain = Path(r"C:\Users\yashp\.gemini\antigravity\brain\07e1fcaf-a3ed-4218-ac14-2f9de829afab")
root = Path(r"c:\Desktop\SYMBI\SEM 7\flexi o\CA3 project\lifeplan-ai")

zip_b64 = base64.b64encode((root / "LifePlan_AI_Submission_Package.zip").read_bytes()).decode("ascii")
synopsis_b64 = base64.b64encode((root / "docs" / "LifePlan_AI_Project_Synopsis.docx").read_bytes()).decode("ascii")
design_b64 = base64.b64encode((root / "docs" / "LifePlan_AI_Design_Document.docx").read_bytes()).decode("ascii")
report_b64 = base64.b64encode((root / "docs" / "LifePlan_AI_Project_Report.docx").read_bytes()).decode("ascii")
pptx_b64 = base64.b64encode((root / "docs" / "LifePlan_AI_Presentation.pptx").read_bytes()).decode("ascii")
script_b64 = base64.b64encode((root / "docs" / "LifePlan_AI_Demo_Video_Script.docx").read_bytes()).decode("ascii")

html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <script src="https://www.gstatic.com/antigravity/web/dev/tailwindcss.min.js"></script>
</head>
<body class="bg-transparent text-[var(--foreground)] antialiased p-2">
  <div class="bg-[var(--card)] text-[var(--foreground)] border border-[var(--border)] rounded-xl p-4 shadow-sm max-w-xl mx-auto flex flex-col justify-between" style="min-height: 420px; max-height: 470px;">
    
    <!-- Header -->
    <div class="border-b border-[var(--border)] pb-2 mb-2 flex items-center justify-between">
      <div>
        <h2 class="text-base font-bold text-[var(--foreground)] flex items-center gap-2">
          <span>📦</span> LifePlan AI — One-Click Download Hub
        </h2>
        <p class="text-xs text-[var(--muted-foreground)] mt-0.5">Symbiosis Institute of Technology • CA3 Capstone Project</p>
      </div>
      <span class="px-2 py-0.5 text-xs font-semibold rounded bg-emerald-500/10 text-emerald-600 border border-emerald-500/20">Ready to Download</span>
    </div>

    <!-- Team Metadata Card -->
    <div class="bg-[var(--background)] border border-[var(--border)] rounded-lg p-2.5 mb-2 text-xs">
      <div class="font-semibold text-[var(--foreground)] mb-1">Project Team:</div>
      <div class="grid grid-cols-1 sm:grid-cols-3 gap-1 text-[var(--muted-foreground)]">
        <div>• <strong class="text-[var(--foreground)]">Yash Patel</strong> (2307012235)</div>
        <div>• <strong class="text-[var(--foreground)]">Arsh Ansari</strong> (23070122047)</div>
        <div>• <strong class="text-[var(--foreground)]">Aliraza Shaikh</strong> (23070122023)</div>
      </div>
    </div>

    <!-- Master Download Button -->
    <div class="my-1">
      <a id="btn-master-zip" href="data:application/zip;base64,{zip_b64}" download="LifePlan_AI_Submission_Package.zip" class="w-full flex items-center justify-center gap-2 bg-[var(--primary)] text-[var(--primary-foreground)] py-2.5 px-4 rounded-lg font-bold text-xs shadow-md hover:opacity-90 active:scale-95 transition text-center cursor-pointer">
        <span class="text-sm">📥</span>
        <span>DOWNLOAD ALL (MASTER ZIP PACKAGE • 1.68 MB)</span>
      </a>
      <p class="text-xs text-[var(--muted-foreground)] text-center mt-1">Direct browser download containing all 5 Word & PowerPoint documents, markdown sources, and diagrams.</p>
    </div>

    <!-- Individual File Download Buttons Grid -->
    <div class="grid grid-cols-2 gap-2 my-1">
      <a href="data:application/vnd.openxmlformats-officedocument.wordprocessingml.document;base64,{report_b64}" download="LifePlan_AI_Project_Report.docx" class="flex items-center gap-2 p-2 rounded-lg bg-[var(--background)] border border-[var(--border)] hover:border-[var(--primary)] text-xs text-[var(--foreground)] font-medium transition active:scale-95 cursor-pointer">
        <span class="text-base">📘</span>
        <div class="truncate">
          <div class="font-semibold truncate">Project Report</div>
          <div class="text-xs text-[var(--muted-foreground)]">.docx (518 KB)</div>
        </div>
      </a>

      <a href="data:application/vnd.openxmlformats-officedocument.presentationml.presentation;base64,{pptx_b64}" download="LifePlan_AI_Presentation.pptx" class="flex items-center gap-2 p-2 rounded-lg bg-[var(--background)] border border-[var(--border)] hover:border-[var(--primary)] text-xs text-[var(--foreground)] font-medium transition active:scale-95 cursor-pointer">
        <span class="text-base">📽️</span>
        <div class="truncate">
          <div class="font-semibold truncate">20-Slide Deck</div>
          <div class="text-xs text-[var(--muted-foreground)]">.pptx (88 KB)</div>
        </div>
      </a>

      <a href="data:application/vnd.openxmlformats-officedocument.wordprocessingml.document;base64,{synopsis_b64}" download="LifePlan_AI_Project_Synopsis.docx" class="flex items-center gap-2 p-2 rounded-lg bg-[var(--background)] border border-[var(--border)] hover:border-[var(--primary)] text-xs text-[var(--foreground)] font-medium transition active:scale-95 cursor-pointer">
        <span class="text-base">📄</span>
        <div class="truncate">
          <div class="font-semibold truncate">Project Synopsis</div>
          <div class="text-xs text-[var(--muted-foreground)]">.docx (41 KB)</div>
        </div>
      </a>

      <a href="data:application/vnd.openxmlformats-officedocument.wordprocessingml.document;base64,{design_b64}" download="LifePlan_AI_Design_Document.docx" class="flex items-center gap-2 p-2 rounded-lg bg-[var(--background)] border border-[var(--border)] hover:border-[var(--primary)] text-xs text-[var(--foreground)] font-medium transition active:scale-95 cursor-pointer">
        <span class="text-base">📐</span>
        <div class="truncate">
          <div class="font-semibold truncate">Design Document</div>
          <div class="text-xs text-[var(--muted-foreground)]">.docx (520 KB)</div>
        </div>
      </a>
    </div>

    <!-- Local Path Notice -->
    <div class="border-t border-[var(--border)] pt-2 flex items-center justify-between text-xs text-[var(--muted-foreground)]">
      <div class="truncate">
        💾 Pre-copied to: <code class="text-[var(--foreground)]">C:/Users/yashp/Downloads</code> &amp; <code class="text-[var(--foreground)]">C:/Desktop</code>
      </div>
      <a href="data:application/vnd.openxmlformats-officedocument.wordprocessingml.document;base64,{script_b64}" download="LifePlan_AI_Demo_Video_Script.docx" class="font-medium text-[var(--primary)] hover:underline shrink-0 ml-2">
        🎬 Video Script (.docx)
      </a>
    </div>

  </div>
</body>
</html>"""

out_path = brain / "lifeplan_download_hub.html"
out_path.write_text(html_content, encoding="utf-8")
print(f"Wrote HTML to: {out_path} ({out_path.stat().st_size / 1024:.1f} KB)")
