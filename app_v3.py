"""
Agnes — AI Supply Chain Intelligence  (app_v3.py)
Run:  streamlit run app_v3.py

Unified conversational UI with:
  - Text + voice input
  - Barcode scanning (camera / upload / manual)
  - Intent detection → ingredient analysis / barcode scan / bottleneck analysis
  - Dynamic card-based results with suppliers, substitutes, demand aggregation
"""
from __future__ import annotations
import json, re, sys, csv, os
from pathlib import Path
from datetime import datetime

import streamlit as st
import streamlit.components.v1 as components

_ROOT = Path(__file__).parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.procurement.cpg_db import CpgDatabase
from src.agnes.actions import analyze_ingredient, analyze_barcode, analyze_bottleneck
from src.procurement.barcode_lookup import (
    lookup_barcode, extract_barcode_from_image,
)

# ─── page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Agnes · AI Supply Chain Assistant",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ─── global CSS ───────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
html, body, [class*="css"] { font-family: 'Inter', sans-serif; }

/* hide streamlit chrome + sidebar */
#MainMenu, footer, header { visibility: hidden; }
[data-testid="stSidebar"], [data-testid="collapsedControl"] { display: none; }
.block-container { padding-top: 1.2rem; padding-bottom: 2rem; max-width: 1100px; }

/* ── hero ── */
.agnes-hero {
    background: linear-gradient(135deg, #0f172a 0%, #1e3a5f 50%, #0ea5e9 100%);
    border-radius: 20px;
    padding: 2.2rem 2.5rem 1.8rem;
    margin-bottom: 1.2rem;
    box-shadow: 0 8px 32px rgba(0,0,0,0.25);
    position: relative;
    overflow: hidden;
}
.agnes-hero::before {
    content: '';
    position: absolute; top: -50%; right: -20%;
    width: 400px; height: 400px;
    background: radial-gradient(circle, rgba(14,165,233,0.15) 0%, transparent 70%);
    border-radius: 50%;
}
.agnes-hero h1 {
    color: #f1f5f9; font-size: 2rem; font-weight: 800;
    margin-bottom: 0.2rem; position: relative;
}
.agnes-hero p {
    color: #94a3b8; font-size: 0.92rem; margin-bottom: 0;
    position: relative;
}

/* ── cards ── */
.card {
    background: #ffffff; border: 1px solid #e2e8f0;
    border-radius: 14px; padding: 1.2rem 1.5rem;
    margin-bottom: 0.8rem; box-shadow: 0 2px 8px rgba(0,0,0,0.04);
    transition: box-shadow 0.2s;
}
.card:hover { box-shadow: 0 4px 16px rgba(0,0,0,0.08); }

/* ── risk cards ── */
.risk-critical { border-left: 4px solid #dc2626; }
.risk-high     { border-left: 4px solid #ea580c; }
.risk-medium   { border-left: 4px solid #d97706; }
.risk-low      { border-left: 4px solid #16a34a; }

/* ── metric tile ── */
.metric-tile {
    background: #f8fafc; border: 1px solid #e2e8f0;
    border-radius: 12px; padding: 0.9rem; text-align: center;
}
.metric-tile .val { font-size: 1.6rem; font-weight: 700; color: #1e3a5f; line-height: 1; }
.metric-tile .lbl { font-size: 0.72rem; color: #64748b; margin-top: 4px; font-weight: 500; text-transform: uppercase; letter-spacing: 0.5px; }

/* ── verdict badges ── */
.badge {
    display: inline-block; padding: 3px 10px; border-radius: 20px;
    font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px;
}
.badge-excellent { background: #dcfce7; color: #166534; }
.badge-good      { background: #dbeafe; color: #1e40af; }
.badge-possible  { background: #fef9c3; color: #854d0e; }
.badge-limited   { background: #ffedd5; color: #9a3412; }
.badge-poor      { background: #fee2e2; color: #991b1b; }

/* ── score bar ── */
.score-bar-wrap { background: #e2e8f0; border-radius: 99px; height: 6px; flex: 1; }
.score-bar-fill { height: 6px; border-radius: 99px; background: linear-gradient(90deg, #3b82f6, #06b6d4); }

/* ── supplier row ── */
.sup-row {
    display: flex; align-items: center; gap: 12px;
    padding: 12px 16px; border-radius: 10px; margin-bottom: 6px;
    border: 1px solid #e2e8f0; background: #fff; transition: all 0.15s;
}
.sup-row:hover { box-shadow: 0 2px 12px rgba(0,0,0,0.08); border-color: #3b82f6; }

/* ── ingredient chip ── */
.ing-chip {
    display: inline-block; padding: 6px 14px; margin: 4px;
    background: #f1f5f9; border: 1px solid #cbd5e1;
    border-radius: 20px; cursor: pointer; font-size: 0.82rem;
    color: #334155; font-weight: 500; white-space: nowrap;
    transition: all 0.15s;
}
.ing-chip:hover { background: #dbeafe; border-color: #3b82f6; color: #1e40af; }
.ing-chip.active { background: #1e3a5f; color: white; border-color: #1e3a5f; }

/* ── section divider ── */
.sec-div { border: none; border-top: 1px solid #e2e8f0; margin: 1.5rem 0; }

/* ── input mode tabs ── */
.input-mode-btn {
    padding: 8px 18px; border-radius: 10px; border: 1px solid #e2e8f0;
    background: #f8fafc; font-size: 0.85rem; font-weight: 600;
    color: #64748b; cursor: pointer; transition: all 0.15s;
}
.input-mode-btn:hover { background: #dbeafe; border-color: #3b82f6; color: #1e40af; }
.input-mode-btn.active { background: #1e3a5f; color: white; border-color: #1e3a5f; }

/* ── Streamlit search bar overrides ── */
div[data-testid="stButton"] > button[kind="primary"] {
    border-radius: 44px !important;
    font-weight: 700 !important;
    letter-spacing: 0.3px !important;
    padding: 0.55rem 1.2rem !important;
}

/* ── section header ── */
.section-header {
    display: flex; align-items: center; gap: 10px;
    margin-bottom: 1rem; padding-bottom: 0.5rem;
    border-bottom: 2px solid #e2e8f0;
}
.section-icon {
    width: 32px; height: 32px; border-radius: 8px;
    display: flex; align-items: center; justify-content: center;
    font-size: 16px; flex-shrink: 0;
}
.section-title { font-size: 1.1rem; font-weight: 700; color: #0f172a; }
</style>
""", unsafe_allow_html=True)


# ─── helpers ──────────────────────────────────────────────────────────────────

def _metric(val, label: str) -> str:
    return (f'<div class="metric-tile">'
            f'<div class="val">{val}</div>'
            f'<div class="lbl">{label}</div></div>')

def _badge(verdict: str) -> str:
    return f'<span class="badge badge-{verdict}">{verdict}</span>'

def _section(icon: str, bg: str, title: str) -> str:
    return (f'<div class="section-header">'
            f'<div class="section-icon" style="background:{bg};">{icon}</div>'
            f'<div class="section-title">{title}</div></div>')


# ─── render functions ─────────────────────────────────────────────────────────

def _render_suppliers(suppliers: list[dict]):
    """Render a ranked supplier list with score bars."""
    for i, s in enumerate(suppliers):
        name = s.get("Name", s.get("supplier_name", "Unknown"))
        comp = s.get("composite_score", 0)
        verdict = s.get("verdict", "")
        rank = s.get("rank", i + 1)
        bar_pct = min(int(comp), 100)

        st.markdown(f"""
        <div class="sup-row">
          <div style="font-size:1rem;font-weight:700;color:#94a3b8;width:28px;text-align:center;">
            #{rank}</div>
          <div style="flex:1;min-width:0;">
            <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
              <span style="font-weight:600;color:#0f172a;font-size:0.9rem;">{name}</span>
              {_badge(verdict) if verdict else ''}
            </div>
            <div style="display:flex;align-items:center;gap:8px;margin-top:5px;">
              <div class="score-bar-wrap">
                <div class="score-bar-fill" style="width:{bar_pct}%;"></div>
              </div>
              <span style="font-size:1.1rem;font-weight:700;color:#1e3a5f;">{comp:.0f}</span>
            </div>
          </div>
        </div>
        """, unsafe_allow_html=True)


def _render_substitutes(substitutes: list[dict]):
    """Render substitute ingredient cards."""
    for sub in substitutes:
        score = sub.get("similarity_score", 0)
        if isinstance(score, str):
            try:
                score = float(score)
            except ValueError:
                score = 0
        score_pct = int(score * 100)
        ev = sub.get("evidence", {})
        is_web = ev.get("source") == "web_search"

        cat_tag = ""
        if is_web:
            cat_tag = "WEB RESULT"
        elif ev.get("category_match"):
            cat_tag = ev.get("functional_category", "")

        sup_names = ", ".join(sub.get("suppliers", [])[:4]) or "No supplier data"
        companies = ", ".join(sub.get("used_by_companies", [])[:4]) or "---"

        # Safely format percentages
        name_sim = ev.get("name_similarity", 0)
        bom_overlap = ev.get("bom_cooccurrence", 0)
        try:
            name_sim_pct = f"{float(name_sim):.0%}"
        except (ValueError, TypeError):
            name_sim_pct = str(name_sim)
        try:
            bom_pct = f"{float(bom_overlap):.0%}"
        except (ValueError, TypeError):
            bom_pct = str(bom_overlap)
        bom_count = sub.get("bom_count", 0)

        # Render name + score header
        st.markdown(f"**{sub['ingredient']}** — {score_pct}% match"
                    + (f" &nbsp; `{cat_tag}`" if cat_tag else ""))

        # Score bar
        st.markdown(f"""
        <div style="background:#e2e8f0;border-radius:6px;height:8px;margin-bottom:8px;overflow:hidden;">
          <div style="width:{score_pct}%;height:100%;background:linear-gradient(90deg,#3b82f6,#06b6d4);border-radius:6px;"></div>
        </div>
        """, unsafe_allow_html=True)

        # Details
        st.caption(f"Name similarity: {name_sim_pct} · BOM overlap: {bom_pct} · BOMs: {bom_count}")
        st.caption(f"**Suppliers:** {sup_names} · **Companies:** {companies}")


# ─── intent detection ─────────────────────────────────────────────────────────

_BOTTLENECK_KEYWORDS = {
    "bottleneck", "shortage", "delay", "risk", "disruption", "stockout",
    "out of stock", "unavailable", "supply issue", "backup", "alternative",
    "contingency", "single source", "crisis",
}

_BARCODE_RE = re.compile(r'^\d{8,14}$')


def detect_intent(text: str) -> str:
    """Classify user input into: barcode, bottleneck, or ingredient."""
    cleaned = text.strip()

    # Pure barcode number
    if _BARCODE_RE.match(cleaned):
        return "barcode"

    lower = cleaned.lower()

    # Bottleneck keywords
    for kw in _BOTTLENECK_KEYWORDS:
        if kw in lower:
            return "bottleneck"

    return "ingredient"


def extract_ingredient_from_bottleneck(text: str) -> str:
    """Extract the ingredient name from a bottleneck query."""
    lower = text.lower().strip()
    # Remove bottleneck keywords to get the ingredient
    for kw in sorted(_BOTTLENECK_KEYWORDS, key=len, reverse=True):
        lower = lower.replace(kw, "")
    # Remove common filler words
    for w in ["in", "for", "with", "the", "of", "a", "an", "my", "our", "is", "are", "has", "have"]:
        lower = re.sub(rf'\b{w}\b', '', lower)
    return lower.strip().strip('.,!?')


# ─── database (cached) ───────────────────────────────────────────────────────

@st.cache_resource
def get_db():
    return CpgDatabase("db.sqlite")

@st.cache_data
def get_all_ingredient_names() -> list[str]:
    """Return sorted list of all ingredient names for autocomplete."""
    db = get_db()
    idx = db._ingredient_index()
    return sorted(set(item["ingredient_name"] for item in idx))


# ── Helper for rendering compliance standard cards (Tab 4) ───────────────────
def _render_standard_cards(standards: list[dict]):
    """Render compliance standard result cards."""
    ev_labels = {
        "third_party": ("Third-Party Certified", "#16a34a", "#dcfce7"),
        "certificate_unverified": ("Certificate (Unverified)", "#ca8a04", "#fef9c3"),
        "self_declared": ("Self-Declared", "#ea580c", "#ffedd5"),
        "expired": ("Expired", "#dc2626", "#fee2e2"),
        "none": ("No Evidence", "#64748b", "#f1f5f9"),
    }

    for std in standards:
        score = std["score"]
        max_pts = std["max_points"]
        ev = std["evidence_level"]
        label, color, bg = ev_labels.get(ev, ("Unknown", "#64748b", "#f1f5f9"))
        bar_pct = int(score / max_pts * 100) if max_pts else 0

        flags_html = ""
        if std.get("red_flags"):
            flags_html = "".join(
                f'<div style="font-size:0.75rem;color:#dc2626;margin-top:4px;">'
                f'&#9888; {f}</div>'
                for f in std["red_flags"]
            )

        # Official verification link (always show the authentic database)
        verify_html = ""
        v_url = std.get("verification_url", "")
        v_db = std.get("verification_db", "")
        if v_url and v_db:
            verify_html = (
                f'<div style="margin-top:6px;">'
                f'<a href="{v_url}" target="_blank" '
                f'style="font-size:0.75rem;color:#0891b2;font-weight:600;'
                f'text-decoration:none;display:inline-flex;align-items:center;gap:4px;">'
                f'&#128279; Verify at {v_db}</a></div>'
            )

        st.markdown(f"""
        <div class="card" style="border-left:4px solid {color};padding:0.8rem 1.2rem;">
          <div style="display:flex;align-items:center;justify-content:space-between;
                      flex-wrap:wrap;gap:8px;">
            <div style="flex:1;min-width:200px;">
              <div style="font-weight:700;color:#0f172a;font-size:0.9rem;">
                {std['standard_name']}</div>
              <div style="font-size:0.78rem;color:#64748b;margin-top:2px;">
                {std.get('details', '')}</div>
            </div>
            <div style="text-align:right;min-width:100px;">
              <div style="font-size:1.2rem;font-weight:800;color:{color};">
                {score}/{max_pts}</div>
              <div style="display:inline-block;padding:2px 8px;border-radius:12px;
                          font-size:0.7rem;font-weight:600;color:{color};background:{bg};">
                {label}</div>
            </div>
          </div>
          <div style="margin-top:6px;background:#e2e8f0;border-radius:6px;height:6px;overflow:hidden;">
            <div style="width:{bar_pct}%;height:100%;background:{color};border-radius:6px;"></div>
          </div>
          {flags_html}
          {verify_html}
        </div>
        """, unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# HERO HEADER
# ══════════════════════════════════════════════════════════════════════════════

st.markdown("""
<div class="agnes-hero">
  <h1>Agnes</h1>
  <p>AI Supply Chain Assistant — Find suppliers, substitutes, scan barcodes, and manage bottlenecks</p>
</div>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# TOP-LEVEL TABS: Supply Intelligence | Internal Procurement | Order Procurement
# ══════════════════════════════════════════════════════════════════════════════

tab_supply, tab_internal, tab_orders, tab_compliance, tab_supdb, tab_ranking = st.tabs([
    "Supply Intelligence",
    "Internal Procurement",
    "Order Procurement",
    "Risk & Compliance",
    "Supplier Database",
    "Final Ranking",
])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1: SUPPLY INTELLIGENCE (existing search + analysis UI)
# ══════════════════════════════════════════════════════════════════════════════
with tab_supply:

    # ══════════════════════════════════════════════════════════════════════════
    # INPUT AREA
    # ══════════════════════════════════════════════════════════════════════════

    # ── Spherecast voice orb (ElevenLabs conversational agent trigger) ──
    # System prompt for the ElevenLabs agent lives in src/agnes/elevenlabs_prompt.py
    # Paste AGNES_SYSTEM_PROMPT into the ElevenLabs dashboard "System Prompt" field,
    # and replace YOUR_AGENT_ID below with the agent ID from ElevenLabs.
    _ELEVENLABS_AGENT_ID = "agent_5601kpg8kpk3f6zt8cqy954111dn"

    SPHERE_HTML = """
    <div id="sphere-wrapper" style="
      display: flex; flex-direction: column; align-items: center;
      justify-content: center; padding: 20px 0 6px; user-select: none;
    ">
      <!-- Sphere -->
      <div id="agnesSphere" onclick="toggleAgnes()" style="
        width: 130px; height: 130px; border-radius: 50%; cursor: pointer;
        background: radial-gradient(circle at 32% 28%,
          #ffffff 0%, #d4fffe 6%, #a2fafa 18%, #7af5f5 32%,
          #55ecec 48%, #33d9d9 62%, #1fc4c4 76%, #17a8a8 90%, #128a8a 100%);
        box-shadow:
          0 0 35px rgba(162,250,250,0.6),
          0 0 70px rgba(162,250,250,0.35),
          0 0 110px rgba(162,250,250,0.15),
          inset 0 -10px 22px rgba(18,138,138,0.45),
          inset 0 5px 14px rgba(255,255,255,0.4);
        position: relative;
        transition: transform 0.3s ease, box-shadow 0.3s ease;
        animation: spherePulse 3s ease-in-out infinite;
      ">
        <div style="position:absolute;top:13%;left:20%;width:34px;height:22px;border-radius:50%;
          background:radial-gradient(ellipse,rgba(255,255,255,0.9) 0%,transparent 70%);
          transform:rotate(-25deg);"></div>
        <div style="position:absolute;top:27%;left:36%;width:18px;height:11px;border-radius:50%;
          background:radial-gradient(ellipse,rgba(255,255,255,0.45) 0%,transparent 70%);
          transform:rotate(-15deg);"></div>
      </div>

      <!-- Status -->
      <div id="sphereStatus" style="
        margin-top:14px; font-size:14px; font-weight:600;
        color:#7af5f5; letter-spacing:0.5px; text-align:center;
        transition: color 0.3s;
      ">Click to talk to Agnes</div>

      <!-- Transcript area -->
      <div id="transcript" style="
        margin-top:10px; font-size:12px; color:#94a3b8; text-align:center;
        max-width:400px; min-height:18px; line-height:1.4;
      "></div>

      <style>
        @keyframes spherePulse {
          0%,100% {
            box-shadow:0 0 35px rgba(162,250,250,0.6),0 0 70px rgba(162,250,250,0.35),
              0 0 110px rgba(162,250,250,0.15),inset 0 -10px 22px rgba(18,138,138,0.45),
              inset 0 5px 14px rgba(255,255,255,0.4);
            transform:scale(1);
          }
          50% {
            box-shadow:0 0 45px rgba(162,250,250,0.75),0 0 90px rgba(162,250,250,0.45),
              0 0 140px rgba(162,250,250,0.2),inset 0 -10px 22px rgba(18,138,138,0.45),
              inset 0 5px 14px rgba(255,255,255,0.4);
            transform:scale(1.04);
          }
        }
        @keyframes sphereListening {
          0%,100% {
            box-shadow:0 0 40px rgba(162,250,250,0.7),0 0 80px rgba(162,250,250,0.45),
              0 0 130px rgba(162,250,250,0.25),inset 0 -10px 22px rgba(18,138,138,0.45),
              inset 0 5px 14px rgba(255,255,255,0.45);
            transform:scale(1.05);
          }
          50% {
            box-shadow:0 0 55px rgba(162,250,250,0.9),0 0 110px rgba(162,250,250,0.55),
              0 0 170px rgba(162,250,250,0.3),inset 0 -10px 22px rgba(18,138,138,0.45),
              inset 0 5px 14px rgba(255,255,255,0.5);
            transform:scale(1.10);
          }
        }
        @keyframes sphereSpeaking {
          0%,100% { transform:scale(1.05); }
          25%     { transform:scale(1.12); }
          50%     { transform:scale(1.06); }
          75%     { transform:scale(1.14); }
        }
        #agnesSphere:hover {
          transform:scale(1.10) !important;
          box-shadow:0 0 50px rgba(162,250,250,0.8),0 0 100px rgba(162,250,250,0.5),
            0 0 150px rgba(162,250,250,0.25),inset 0 -10px 22px rgba(18,138,138,0.45),
            inset 0 5px 14px rgba(255,255,255,0.45) !important;
        }
        #agnesSphere.listening { animation:sphereListening 1.2s ease-in-out infinite !important; }
        #agnesSphere.speaking  { animation:sphereSpeaking 0.6s ease-in-out infinite !important; }
      </style>
    </div>

    <script>
    const AGENT_ID = '""" + _ELEVENLABS_AGENT_ID + """';
    const WS_URL = 'wss://api.elevenlabs.io/v1/convai/conversation?agent_id=' + AGENT_ID;

    let ws = null;
    let micStream = null;
    let playbackCtx = null;   // for playing agent audio
    let captureCtx = null;    // for mic capture (browser picks sample rate)
    let scriptNode = null;
    let sourceNode = null;
    let isActive = false;
    let audioQueue = [];
    let isPlaying = false;

    /* ── helpers ───────────────────────────────────── */
    function setStatus(text, color) {
      document.getElementById('sphereStatus').textContent = text;
      document.getElementById('sphereStatus').style.color = color || '#7af5f5';
    }
    function setTranscript(text) {
      document.getElementById('transcript').textContent = text;
    }
    function setSphereClass(cls) {
      const s = document.getElementById('agnesSphere');
      s.classList.remove('listening','speaking');
      if (cls) s.classList.add(cls);
    }

    /* ── audio playback (handles both PCM and encoded) */
    let currentSource = null;
    async function playAudioChunk(base64) {
      audioQueue.push(base64);
      if (!isPlaying) drainQueue();
    }
    async function drainQueue() {
      if (audioQueue.length === 0) { isPlaying = false; setSphereClass('listening'); return; }
      isPlaying = true;
      setSphereClass('speaking');
      const b64 = audioQueue.shift();
      try {
        const raw = atob(b64);
        const buf = new Uint8Array(raw.length);
        for (let i = 0; i < raw.length; i++) buf[i] = raw.charCodeAt(i);
        if (!playbackCtx) playbackCtx = new (window.AudioContext || window.webkitAudioContext)();

        // Try decoding as encoded audio first (mp3/opus — ElevenLabs default)
        try {
          const decoded = await playbackCtx.decodeAudioData(buf.buffer.slice(0));
          const src = playbackCtx.createBufferSource();
          src.buffer = decoded;
          src.connect(playbackCtx.destination);
          src.onended = () => { if (currentSource === src) currentSource = null; drainQueue(); };
          currentSource = src;
          src.start();
          return;
        } catch(_) {}

        // Fallback: treat as raw PCM 16-bit mono 16kHz
        const samples = new Float32Array(buf.length / 2);
        const view = new DataView(buf.buffer);
        for (let i = 0; i < samples.length; i++) {
          samples[i] = view.getInt16(i * 2, true) / 32768;
        }
        const abuf = playbackCtx.createBuffer(1, samples.length, 16000);
        abuf.getChannelData(0).set(samples);
        const src = playbackCtx.createBufferSource();
        src.buffer = abuf;
        src.connect(playbackCtx.destination);
        src.onended = () => { if (currentSource === src) currentSource = null; drainQueue(); };
        currentSource = src;
        src.start();
      } catch(e) {
        console.error('Audio playback error:', e);
        drainQueue();
      }
    }

    function stopPlayback() {
      if (currentSource) {
        try { currentSource.stop(); } catch(e) {}
        currentSource = null;
      }
      audioQueue = [];
      isPlaying = false;
    }

    /* ── mic capture → 16kHz PCM base64 ───────────── */
    function startMic(stream) {
      micStream = stream;
      // Let browser pick its native sample rate (usually 48000)
      captureCtx = new (window.AudioContext || window.webkitAudioContext)();
      sourceNode = captureCtx.createMediaStreamSource(stream);
      const nativeSR = captureCtx.sampleRate;
      const targetSR = 16000;
      const ratio = nativeSR / targetSR;

      scriptNode = captureCtx.createScriptProcessor(4096, 1, 1);
      scriptNode.onaudioprocess = (e) => {
        if (!ws || ws.readyState !== 1) return;
        const input = e.inputBuffer.getChannelData(0);

        // Resample from native rate to 16kHz
        const outLen = Math.floor(input.length / ratio);
        const pcm16 = new Int16Array(outLen);
        for (let i = 0; i < outLen; i++) {
          // Linear interpolation for cleaner resampling
          const srcIdx = i * ratio;
          const idx0 = Math.floor(srcIdx);
          const idx1 = Math.min(idx0 + 1, input.length - 1);
          const frac = srcIdx - idx0;
          const sample = input[idx0] + frac * (input[idx1] - input[idx0]);
          pcm16[i] = Math.max(-32768, Math.min(32767, Math.round(sample * 32767)));
        }

        // Base64 encode
        const bytes = new Uint8Array(pcm16.buffer);
        let binary = '';
        const chunkSize = 8192;
        for (let i = 0; i < bytes.length; i += chunkSize) {
          binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunkSize));
        }
        ws.send(JSON.stringify({
          type: "user_audio_chunk",
          user_audio_chunk: btoa(binary)
        }));
      };
      sourceNode.connect(scriptNode);
      scriptNode.connect(captureCtx.destination);
    }
    function stopMic() {
      if (scriptNode) { scriptNode.disconnect(); scriptNode = null; }
      if (sourceNode) { sourceNode.disconnect(); sourceNode = null; }
      if (captureCtx) { captureCtx.close().catch(()=>{}); captureCtx = null; }
      if (micStream) { micStream.getTracks().forEach(t => t.stop()); micStream = null; }
    }

    /* ── main toggle ──────────────────────────────── */
    async function toggleAgnes() {
      if (isActive) {
        isActive = false;
        stopMic();
        stopPlayback();
        if (ws) { ws.close(); ws = null; }
        setSphereClass('');
        setStatus('Click to talk to Agnes', '#7af5f5');
        setTranscript('');
        return;
      }

      setStatus('Requesting microphone...', '#a2fafa');
      let stream;
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true, sampleRate: 16000 }
        });
      } catch(e) {
        setStatus('Microphone access denied', '#f87171');
        return;
      }

      setStatus('Connecting to Agnes...', '#a2fafa');
      ws = new WebSocket(WS_URL);

      ws.onopen = () => {
        isActive = true;
        // Correct initialization message for ElevenLabs ConvAI
        ws.send(JSON.stringify({
          type: "conversation_initiation_client_data",
          conversation_config_override: {
            tts: {
              agent_output_audio_format: "pcm_16000"
            }
          }
        }));
        startMic(stream);
        setSphereClass('listening');
        setStatus('Agnes is listening...', '#a2fafa');
      };

      ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        switch(data.type) {
          case 'audio':
          case 'audio_response':
            const aEvent = data.audio_event || data.audio_response_event;
            if (aEvent && aEvent.audio_base_64) {
              playAudioChunk(aEvent.audio_base_64);
            } else if (data.audio) {
              playAudioChunk(data.audio);
            }
            break;
          case 'agent_response':
            const agEvent = data.agent_response_event;
            if (agEvent) {
              const text = agEvent.text || agEvent.agent_response || data.text;
              if (text) setTranscript('Agnes: ' + text);
              setSphereClass('speaking');
            }
            break;
          case 'user_transcript':
            const uEvent = data.user_transcript_event || data.user_transcription_event;
            if (uEvent) {
              const text = uEvent.text || uEvent.user_transcript || data.text;
              if (text) setTranscript('You: ' + text);
              setSphereClass('listening');
            }
            break;
          case 'ping':
            if (data.ping_event) {
              setTimeout(() => {
                if (ws && ws.readyState === 1) {
                  ws.send(JSON.stringify({ type: 'pong', event_id: data.ping_event.event_id }));
                }
              }, data.ping_event.ping_ms);
            }
            break;
          case 'interruption':
            stopPlayback();
            setSphereClass('listening');
            break;
        }
      };

      ws.onerror = (e) => {
        console.error('WS error:', e);
        setStatus('Connection error — click to retry', '#f87171');
        setSphereClass('');
        stopMic();
        isActive = false;
      };

      ws.onclose = () => {
        if (isActive) {
          setStatus('Conversation ended — click to restart', '#94a3b8');
          setSphereClass('');
          stopMic();
          isActive = false;
        }
      };
    }
    </script>
    """

    # ── Hide Streamlit chrome on text inputs ──────────────────────────────────────
    st.markdown("""
    <style>
      div[data-testid="InputInstructions"] { display: none !important; }
    </style>
    """, unsafe_allow_html=True)

    # ── Mode selector (pill-style tabs) ──────────────────────────────────────────
    _MODE_OPTIONS = [
        ("Talk to Agnes", "&#127908;"),
        ("Type Query", "&#128269;"),
        ("Scan Barcode", "&#128247;"),
        ("Upload Photo", "&#128228;"),
        ("Enter Barcode Number", "&#9874;"),
    ]

    if "input_mode" not in st.session_state:
        st.session_state["input_mode"] = "Talk to Agnes"

    _pill_cols = st.columns(len(_MODE_OPTIONS))
    for i, (label, icon) in enumerate(_MODE_OPTIONS):
        with _pill_cols[i]:
            if st.button(
                f"{label}",
                key=f"mode_{label}",
                use_container_width=True,
                type="primary" if st.session_state["input_mode"] == label else "secondary",
            ):
                st.session_state["input_mode"] = label
                st.rerun()

    input_mode = st.session_state["input_mode"]

    query_text = ""
    barcode_value = None
    run_analysis = False

    # ── Autocomplete ingredient list (for JS search component) ───────────────────
    _all_ingredients = get_all_ingredient_names()


    def _get_suggestions(text: str) -> list[str]:
        """Filter ingredients: starts-with first, then contains."""
        if not text:
            return []
        q = text.lower().strip()
        starts = [n for n in _all_ingredients if n.lower().startswith(q)]
        contains = [n for n in _all_ingredients if q in n.lower() and not n.lower().startswith(q)]
        return starts + contains


    def _on_search_change(key: str):
        """on_change callback — just marks that suggestions should be shown."""
        st.session_state[f"{key}_show_suggestions"] = True


    def _render_search(placeholder: str, key: str):
        """Render search bar with Analyze button + autocomplete suggestions.

        Uses on_change callback so suggestions appear on every keystroke.
        Returns (query_text, run_analysis).
        """
        input_key = f"{key}_input"
        picked_key = f"{key}_picked"

        # If a suggestion was clicked last run, fill it in
        if st.session_state.get(picked_key):
            st.session_state[input_key] = st.session_state[picked_key]
            st.session_state[picked_key] = ""

        # If a quick chip was clicked last run, fill it in
        if st.session_state.get(f"{key}_chip_pick"):
            st.session_state[input_key] = st.session_state[f"{key}_chip_pick"]
            st.session_state[f"{key}_chip_pick"] = ""

        # Search input + Analyze button
        col_input, col_btn = st.columns([5, 1])
        with col_input:
            typed = st.text_input(
                "search", placeholder=placeholder,
                label_visibility="collapsed", key=input_key,
                on_change=_on_search_change, args=(key,),
            )
        with col_btn:
            btn = st.button("Analyze", type="primary", use_container_width=True, key=f"{key}_btn")

        # Show suggestions when text is present (either from on_change or from typing+enter)
        if typed and not btn:
            matches = _get_suggestions(typed)
            if matches:
                suggestion_box = st.container(height=160, border=True)
                with suggestion_box:
                    for name in matches[:30]:
                        if st.button(
                            name, key=f"{key}_s_{name}",
                            use_container_width=True,
                            type="tertiary",
                        ):
                            st.session_state[picked_key] = name
                            st.session_state[f"{key}_show_suggestions"] = False
                            st.rerun()

        return typed, btn


    def _render_chips(key: str):
        """Render clickable quick-suggestion chips."""
        chip_cols = st.columns(4)
        chips = ["magnesium stearate", "soy lecithin", "bottleneck in vitamin c", "3017620422003"]
        for i, text in enumerate(chips):
            with chip_cols[i]:
                if st.button(text, key=f"{key}_chip_{i}", use_container_width=True, type="tertiary"):
                    st.session_state[f"{key}_chip_pick"] = text
                    st.rerun()


    # ══════════════════════════════════════════════════════════════════════════════
    # INPUT MODES
    # ══════════════════════════════════════════════════════════════════════════════

    if input_mode == "Talk to Agnes":
        components.html(SPHERE_HTML, height=250)
        query_text, run_analysis = _render_search(
            "Ask Agnes — e.g. 'magnesium stearate', 'bottleneck in soy lecithin'...", "talk"
        )
        if not query_text:
            _render_chips("talk")

    elif input_mode == "Type Query":
        query_text, run_analysis = _render_search(
            "Search ingredients, barcodes, or describe a bottleneck...", "type"
        )
        if not query_text:
            _render_chips("type")

    elif input_mode == "Scan Barcode":
        st.markdown("""
        <div class="card" style="text-align:center;padding:1rem;">
          <p style="color:#64748b;font-size:0.9rem;margin:0;">
            Point your camera at a barcode — it will be detected automatically
          </p>
        </div>
        """, unsafe_allow_html=True)

        cam_photo = st.camera_input("Scan barcode", key="bc_camera", label_visibility="collapsed")
        if cam_photo is not None:
            img_bytes = cam_photo.getvalue()
            with st.spinner("Detecting barcode..."):
                barcode_value = extract_barcode_from_image(img_bytes)
            if barcode_value:
                st.success(f"Barcode detected: **{barcode_value}**")
                run_analysis = True
            else:
                st.warning("Could not detect barcode. Try manual entry.")
                manual = st.text_input("Enter barcode manually", key="bc_cam_fallback")
                if manual:
                    barcode_value = manual.strip()
                    run_analysis = st.button("Look Up", type="primary", key="bc_cam_lookup")

    elif input_mode == "Upload Photo":
        uploaded = st.file_uploader(
            "Upload barcode image",
            type=["png", "jpg", "jpeg", "bmp", "gif", "webp"],
            key="bc_upload",
        )
        if uploaded:
            img_bytes = uploaded.read()
            col_img, col_info = st.columns([1, 3])
            with col_img:
                st.image(img_bytes, width=200)
            with col_info:
                with st.spinner("Decoding barcode..."):
                    barcode_value = extract_barcode_from_image(img_bytes)
                if barcode_value:
                    st.success(f"Barcode detected: **{barcode_value}**")
                    run_analysis = True
                else:
                    st.warning("Could not decode barcode. Enter manually:")
                    manual = st.text_input("Barcode number", key="bc_upload_fallback")
                    if manual:
                        barcode_value = manual.strip()
                        run_analysis = st.button("Look Up", type="primary", key="bc_upload_lookup")

    elif input_mode == "Enter Barcode Number":
        col_bc, col_btn = st.columns([5, 1])
        with col_bc:
            bc_num = st.text_input(
                "Barcode",
                placeholder="e.g. 3017620422003, 049000006346",
                label_visibility="collapsed",
                key="bc_number",
            )
            if bc_num:
                barcode_value = bc_num.strip()
        with col_btn:
            if barcode_value:
                run_analysis = st.button("Look Up", type="primary", use_container_width=True, key="bc_num_lookup")


    # ── Determine what to run ─────────────────────────────────────────────────────

    # If text input contains a barcode, treat it as barcode
    if query_text and not barcode_value:
        if _BARCODE_RE.match(query_text.strip()):
            barcode_value = query_text.strip()
            query_text = ""


    # ══════════════════════════════════════════════════════════════════════════════
    # EXECUTE ANALYSIS
    # ══════════════════════════════════════════════════════════════════════════════

    if run_analysis and (query_text or barcode_value):
        db = get_db()

        st.markdown('<hr class="sec-div"/>', unsafe_allow_html=True)

        # ── BARCODE FLOW ──────────────────────────────────────────────────────
        if barcode_value:
            with st.spinner(f"Scanning barcode {barcode_value}..."):
                result = analyze_barcode(barcode_value, db)

            product = result["product"]

            if product.get("status") == "found":
                # ── Product Details Header ──
                st.markdown(_section("&#128230;", "#dbeafe",
                            f"Product: {product.get('product_name', 'Unknown')}"),
                            unsafe_allow_html=True)

                # Product image + basic info
                if product.get("image_url"):
                    col_img, col_detail = st.columns([1, 3])
                    with col_img:
                        st.image(product["image_url"], width=160)
                    with col_detail:
                        st.markdown(f"**Brand:** {product.get('brand', '---') or '---'}")
                        st.markdown(f"**Source:** {product.get('source', '---')}")
                        if product.get("categories"):
                            st.markdown(f"**Categories:** {product['categories']}")
                        for key, label in [("quantity", "Size"), ("countries", "Countries"), ("nutriscore", "Nutri-Score")]:
                            val = product.get(key)
                            if val:
                                st.markdown(f"**{label}:** {val}")
                else:
                    st.markdown(f"**Brand:** {product.get('brand', '---') or '---'}")

                # ── International Consumer Industry Standards ──
                st.markdown('<hr class="sec-div"/>', unsafe_allow_html=True)
                st.markdown(_section("&#127760;", "#ede9fe",
                            "International Consumer Industry Standards"),
                            unsafe_allow_html=True)

                std1, std2 = st.columns(2)
                gtin_val = product.get("gtin", "---")
                hs_val = product.get("hs_code", "---")
                std1.markdown(f"""
                <div class="card" style="border-left:4px solid #7c3aed;">
                  <div style="font-size:0.75rem;color:#64748b;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px;">
                    GTIN — Global Trade Item Number</div>
                  <div style="font-size:1.3rem;font-weight:800;color:#1e293b;font-family:monospace;">{gtin_val}</div>
                </div>""", unsafe_allow_html=True)
                std2.markdown(f"""
                <div class="card" style="border-left:4px solid #0891b2;">
                  <div style="font-size:0.75rem;color:#64748b;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px;">
                    HS Code — Harmonized System</div>
                  <div style="font-size:1.3rem;font-weight:800;color:#1e293b;font-family:monospace;">{hs_val}</div>
                </div>""", unsafe_allow_html=True)

                # ── All Ingredients & Their Suppliers (one by one) ──
                ingredients = result.get("ingredients", [])
                if ingredients:
                    st.markdown('<hr class="sec-div"/>', unsafe_allow_html=True)
                    st.markdown(_section("&#129514;", "#fef3c7",
                                f"Ingredients Breakdown — {len(ingredients)} ingredients"),
                                unsafe_allow_html=True)

                    for idx, ing in enumerate(ingredients, 1):
                        found_badge = (
                            '<span style="background:#dcfce7;color:#166534;padding:2px 8px;'
                            'border-radius:10px;font-size:11px;font-weight:600;">IN DB</span>'
                            if ing["found_in_db"] else
                            '<span style="background:#fee2e2;color:#991b1b;padding:2px 8px;'
                            'border-radius:10px;font-size:11px;font-weight:600;">NOT IN DB</span>'
                        )

                        # Build supplier names list
                        sup_names = []
                        if ing["top_suppliers"]:
                            sup_names = [s.get("Name", s.get("supplier_name", "?")) for s in ing["top_suppliers"]]

                        # Build substitute names list
                        sub_names = []
                        if ing["top_substitutes"]:
                            sub_names = [s.get("ingredient", "?") for s in ing["top_substitutes"]]

                        st.markdown(f"""
                        <div class="card" style="border-left:4px solid {'#22c55e' if ing['found_in_db'] else '#ef4444'};">
                          <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">
                            <span style="background:#0f172a;color:white;padding:3px 10px;border-radius:50%;
                                   font-size:0.8rem;font-weight:700;">{idx}</span>
                            <span style="font-weight:700;color:#0f172a;font-size:1rem;">{ing["name"]}</span>
                            {found_badge}
                          </div>
                          <div style="display:flex;gap:20px;flex-wrap:wrap;">
                            <div class="metric-tile" style="flex:1;min-width:120px;">
                              <div class="val">{ing["supplier_count"]}</div>
                              <div class="lbl">Suppliers in DB</div>
                            </div>
                            <div class="metric-tile" style="flex:1;min-width:120px;">
                              <div class="val">{len(ing.get("used_by_companies", []))}</div>
                              <div class="lbl">Unique Companies</div>
                            </div>
                            <div class="metric-tile" style="flex:1;min-width:120px;">
                              <div class="val">{ing["substitute_count"]}</div>
                              <div class="lbl">Substitutes</div>
                            </div>
                          </div>
                        </div>
                        """, unsafe_allow_html=True)

                        # Expandable details per ingredient
                        with st.expander(f"View details for {ing['name']}"):
                            if sup_names:
                                st.markdown("**Ranked Suppliers:**")
                                for s in ing["top_suppliers"]:
                                    name = s.get("Name", s.get("supplier_name", "?"))
                                    comp = s.get("composite_score", 0)
                                    verdict = s.get("verdict", "")
                                    st.markdown(
                                        f"- **{name}** — Score: {comp:.0f}/100 {_badge(verdict) if verdict else ''}",
                                        unsafe_allow_html=True,
                                    )
                            else:
                                st.caption("No suppliers found in database.")

                            if sub_names:
                                st.markdown("**Substitutes** *(variant forms excluded — e.g. MgO ≠ MgCl₂ ≠ MgCO₃)*:")
                                for sub in ing["top_substitutes"]:
                                    score = sub.get("similarity_score", 0)
                                    st.markdown(f"- **{sub['ingredient']}** — {score:.0%} match")
                            else:
                                st.caption("No substitutes found. Variant forms (same base ingredient) are excluded.")

                            if ing.get("used_by_companies"):
                                chips = "".join(f'<span class="ing-chip">{c}</span>' for c in ing["used_by_companies"])
                                st.markdown(f"**Used by:** {chips}", unsafe_allow_html=True)

                    # ── Auto-discover suppliers silently ──
                    ing_names = [ing["name"] for ing in ingredients if ing.get("name")]
                    if ing_names:
                        from src.procurement.supplier_discovery import discover_for_ingredients
                        from src.procurement.supplier_db import SupplierDatabase as _DiscDB
                        _disc_db = _DiscDB()
                        _disc_db.clear_discovered()

                        with st.spinner("Discovering suppliers across all 5 tiers..."):
                            _disc_all = discover_for_ingredients(
                                ing_names, max_per_source=3)

                        for _dn, _ds in _disc_all.items():
                            for _s in _ds:
                                try:
                                    _disc_db.add_supplier(_s)
                                except Exception:
                                    pass

            elif product.get("status") == "not_found":
                st.warning(f"Barcode **{barcode_value}** not found in any database.")
                if product.get("sources_checked"):
                    st.caption(f"Sources checked: {', '.join(product['sources_checked'])}")
            else:
                st.error(product.get("message", "Lookup failed"))

        # ── BOTTLENECK FLOW ───────────────────────────────────────────────────
        elif detect_intent(query_text) == "bottleneck":
            ingredient = extract_ingredient_from_bottleneck(query_text)
            if not ingredient:
                ingredient = query_text  # fallback to full text

            with st.spinner(f"Analyzing bottleneck for '{ingredient}'..."):
                result = analyze_bottleneck(ingredient, db)

            risk = result["risk_assessment"]
            risk_class = f"risk-{risk['risk_level']}"
            risk_color = {"critical": "#dc2626", "high": "#ea580c", "medium": "#d97706", "low": "#16a34a"}.get(
                risk["risk_level"], "#64748b"
            )

            # Risk assessment header
            st.markdown(_section("&#9888;&#65039;", "#fef2f2",
                        f"Bottleneck Analysis: {result['ingredient']}"), unsafe_allow_html=True)

            st.markdown(f"""
            <div class="card {risk_class}">
              <div style="display:flex;align-items:center;gap:12px;margin-bottom:10px;">
                <span style="background:{risk_color};color:white;padding:6px 16px;border-radius:20px;
                       font-size:13px;font-weight:700;text-transform:uppercase;">
                  {risk["risk_level"]} RISK</span>
                <span style="font-size:0.9rem;color:#64748b;">
                  {risk["supplier_count"]} supplier(s) · {risk["affected_companies"]} companies · {risk["affected_products"]} products
                </span>
              </div>
              <div style="font-size:0.85rem;color:#475569;">
                {"<br/>".join(f"&#8226; {f}" for f in risk["risk_factors"])}
              </div>
            </div>
            """, unsafe_allow_html=True)

            # Metrics
            m1, m2, m3, m4 = st.columns(4)
            m1.markdown(_metric(risk["supplier_count"], "Suppliers"), unsafe_allow_html=True)
            m2.markdown(_metric(risk["affected_companies"], "Companies"), unsafe_allow_html=True)
            m3.markdown(_metric(risk["affected_products"], "Products"), unsafe_allow_html=True)
            m4.markdown(_metric(len(result["substitutes"]), "Substitutes"), unsafe_allow_html=True)

            # Recommendations
            if result["recommendations"]:
                st.markdown('<hr class="sec-div"/>', unsafe_allow_html=True)
                st.markdown(_section("&#128161;", "#dbeafe", "Recommendations"), unsafe_allow_html=True)
                for rec in result["recommendations"]:
                    st.markdown(f"""
                    <div class="card" style="border-left:3px solid #3b82f6;padding:0.8rem 1.2rem;">
                      <span style="font-size:0.9rem;color:#1e3a5f;">{rec}</span>
                    </div>
                    """, unsafe_allow_html=True)

            # Suppliers
            if result["suppliers"]:
                st.markdown('<hr class="sec-div"/>', unsafe_allow_html=True)
                st.markdown(_section("&#127981;", "#dcfce7",
                            f"Current Suppliers ({len(result['suppliers'])})"), unsafe_allow_html=True)
                _render_suppliers(result["suppliers"])

            # Substitutes
            if result["substitutes"]:
                st.markdown('<hr class="sec-div"/>', unsafe_allow_html=True)
                st.markdown(_section("&#128260;", "#fef3c7",
                            f"Substitutes ({len(result['substitutes'])})"), unsafe_allow_html=True)
                _render_substitutes(result["substitutes"])

            # Impact
            impact = result.get("impact", {})
            if impact.get("companies"):
                st.markdown('<hr class="sec-div"/>', unsafe_allow_html=True)
                st.markdown(_section("&#127758;", "#ede9fe",
                            f"Cross-Company Impact ({len(impact['companies'])} companies)"), unsafe_allow_html=True)
                chips = "".join(f'<span class="ing-chip">{c}</span>' for c in impact["companies"])
                st.markdown(f'<div style="margin:8px 0;">{chips}</div>', unsafe_allow_html=True)

        # ── INGREDIENT FLOW ───────────────────────────────────────────────────
        else:
            with st.spinner(f"Analyzing '{query_text}'..."):
                result = analyze_ingredient(query_text, db)

            # ── Step 1: Ingredient Details ──
            st.markdown(_section("&#129514;", "#dbeafe",
                        f"Ingredient Details: {result['ingredient']}"), unsafe_allow_html=True)

            # Ingredient info card
            st.markdown(f"""
            <div class="card">
              <div style="font-size:1.1rem;font-weight:700;color:#0f172a;margin-bottom:6px;">
                {result['ingredient']}</div>
              <div style="font-size:0.85rem;color:#64748b;">
                {'Found in database with ' + str(len(result["matches"])) + ' match(es)' if result["matches"]
                 else 'Not found in database — showing web results'}</div>
            </div>
            """, unsafe_allow_html=True)

            # ── International Consumer Industry Standards ──
            st.markdown('<hr class="sec-div"/>', unsafe_allow_html=True)
            st.markdown(_section("&#127760;", "#ede9fe",
                        "International Consumer Industry Standards"),
                        unsafe_allow_html=True)

            hs_val = result.get("hs_code", "2106")
            std1, std2 = st.columns(2)
            std1.markdown(f"""
            <div class="card" style="border-left:4px solid #0891b2;">
              <div style="font-size:0.75rem;color:#64748b;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px;">
                HS Code — Harmonized System</div>
              <div style="font-size:1.3rem;font-weight:800;color:#1e293b;font-family:monospace;">{hs_val}</div>
            </div>""", unsafe_allow_html=True)
            std2.markdown(f"""
            <div class="card" style="border-left:4px solid #7c3aed;">
              <div style="font-size:0.75rem;color:#64748b;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px;">
                GTIN — Global Trade Item Number</div>
              <div style="font-size:1.3rem;font-weight:800;color:#1e293b;font-family:monospace;">N/A (Ingredient)</div>
              <div style="font-size:0.7rem;color:#94a3b8;margin-top:2px;">
                GTIN applies to finished products, not raw ingredients</div>
            </div>""", unsafe_allow_html=True)

            # ── Key Metrics: Suppliers · Companies · Substitutes ──
            st.markdown('<hr class="sec-div"/>', unsafe_allow_html=True)
            n_suppliers = len(result["suppliers"])
            n_companies = len(result["demand_aggregation"].get("companies", []))
            n_substitutes = len(result["substitutes"])

            m1, m2, m3 = st.columns(3)
            m1.markdown(f"""
            <div class="card" style="border-left:4px solid #22c55e;text-align:center;">
              <div style="font-size:2rem;font-weight:800;color:#1e293b;">{n_suppliers}</div>
              <div style="font-size:0.8rem;color:#64748b;text-transform:uppercase;letter-spacing:1px;">
                Suppliers in Database</div>
            </div>""", unsafe_allow_html=True)
            m2.markdown(f"""
            <div class="card" style="border-left:4px solid #7c3aed;text-align:center;">
              <div style="font-size:2rem;font-weight:800;color:#1e293b;">{n_companies}</div>
              <div style="font-size:0.8rem;color:#64748b;text-transform:uppercase;letter-spacing:1px;">
                Unique Companies</div>
            </div>""", unsafe_allow_html=True)
            m3.markdown(f"""
            <div class="card" style="border-left:4px solid #0891b2;text-align:center;">
              <div style="font-size:2rem;font-weight:800;color:#1e293b;">{n_substitutes}</div>
              <div style="font-size:0.8rem;color:#64748b;text-transform:uppercase;letter-spacing:1px;">
                Substitutes</div>
              <div style="font-size:0.65rem;color:#94a3b8;margin-top:2px;">
                variant forms excluded (e.g. MgO ≠ MgCl₂ ≠ MgCO₃)</div>
            </div>""", unsafe_allow_html=True)

            # ── Substitutes ──
            if result["substitutes"]:
                st.markdown('<hr class="sec-div"/>', unsafe_allow_html=True)
                st.markdown(_section("&#128260;", "#fef3c7",
                            f"Substitutes ({n_substitutes})"), unsafe_allow_html=True)
                st.caption("Only true substitutes shown — variant forms of the same base ingredient "
                           "(e.g., magnesium oxide vs magnesium stearate) are excluded.")
                _render_substitutes(result["substitutes"])
            elif result["matches"]:
                st.markdown('<hr class="sec-div"/>', unsafe_allow_html=True)
                st.info("No substitutes found. Variant forms were excluded "
                        "(e.g., MgO, MgCl₂, MgCO₃ are all magnesium — not substitutes for each other).")

            # ── Demand Aggregation (Companies using this ingredient) ──
            demand = result.get("demand_aggregation", {})
            companies = demand.get("companies", [])
            if companies:
                st.markdown('<hr class="sec-div"/>', unsafe_allow_html=True)
                st.markdown(_section("&#127758;", "#ede9fe",
                            f"Demand Aggregation — {len(companies)} companies"), unsafe_allow_html=True)

                st.markdown(f"""
                <div class="card">
                  <div style="display:flex;gap:16px;flex-wrap:wrap;font-size:0.85rem;color:#475569;margin-bottom:8px;">
                    <span><b>Total usages:</b> {demand.get("total_usages", 0)}</span>
                    <span><b>Finished goods:</b> {len(demand.get("finished_goods", []))}</span>
                  </div>
                  <div>{"".join(f'<span class="ing-chip">{c}</span>' for c in companies)}</div>
                </div>
                """, unsafe_allow_html=True)

                if demand.get("finished_goods"):
                    with st.expander(f"Finished goods using this ingredient ({len(demand['finished_goods'])})"):
                        for fg in demand["finished_goods"][:20]:
                            st.markdown(f"- `{fg}`")

        # ── Auto-discover suppliers on web (runs silently in background) ─────
        _disc_name = result.get("ingredient", query_text) if result else query_text
        if _disc_name:
            from src.procurement.supplier_discovery import discover_for_ingredients
            from src.procurement.supplier_db import SupplierDatabase as _DiscDB2
            _disc_db2 = _DiscDB2()
            _disc_db2.clear_discovered()

            with st.spinner("Discovering suppliers across all 5 tiers..."):
                _disc_all2 = discover_for_ingredients(
                    [_disc_name], max_per_source=5)

            _disc_total2 = 0
            for _dn2, _ds2 in _disc_all2.items():
                for _s2 in _ds2:
                    try:
                        _disc_db2.add_supplier(_s2)
                        _disc_total2 += 1
                    except Exception:
                        pass

        # ── Export button ──
        if result:
            st.download_button(
                "Download Results (JSON)",
                data=json.dumps(result, indent=2, ensure_ascii=False, default=str),
                file_name="agnes_result.json",
                mime="application/json",
                key="export_json",
            )

    elif not run_analysis:
        # Empty state — minimal, no duplicate chips
        st.markdown("""
        <div style="text-align:center;padding:2rem 1rem;color:#94a3b8;">
          <div style="font-size:1rem;font-weight:600;">
            Search for an ingredient, barcode, or describe a supply chain issue above
          </div>
        </div>
        """, unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2: INTERNAL PROCUREMENT
# ══════════════════════════════════════════════════════════════════════════════

_INTERNAL_DIR = _ROOT / "internal_procurement"
_INTERNAL_DIR.mkdir(parents=True, exist_ok=True)


def _read_internal_records() -> list[dict]:
    """Read all CSV/JSON files from internal_procurement/ folder."""
    records = []
    for f in sorted(_INTERNAL_DIR.iterdir()):
        if f.suffix == ".csv":
            with open(f, "r", encoding="utf-8-sig") as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    row["_source_file"] = f.name
                    records.append(dict(row))
        elif f.suffix == ".json" and f.name != "approved_suppliers.json":
            data = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(data, list):
                for item in data:
                    item["_source_file"] = f.name
                    records.append(item)
    return records


def _read_approved_suppliers() -> list[dict]:
    """Read approved_suppliers.json."""
    path = _INTERNAL_DIR / "approved_suppliers.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else data.get("suppliers", [])
    return []


with tab_internal:
    st.markdown(_section("&#128203;", "#dbeafe", "Internal Procurement"),
                unsafe_allow_html=True)
    st.caption("Material usage reports, inventory ledger, stock cards, and approved suppliers. "
               "Upload or view internal procurement records.")

    # ── Sub-tabs ──
    int_tab_reports, int_tab_suppliers, int_tab_upload = st.tabs([
        "Material Usage & Stock",
        "Approved Suppliers",
        "Upload Records",
    ])

    # ── Material Usage & Stock ──
    with int_tab_reports:
        records = _read_internal_records()
        if records:
            st.markdown(f"""
            <div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:1rem;">
              <div class="metric-tile" style="flex:1;min-width:140px;">
                <div class="val">{len(records)}</div>
                <div class="lbl">Total Records</div>
              </div>
              <div class="metric-tile" style="flex:1;min-width:140px;">
                <div class="val">{len(set(r.get('_source_file','') for r in records))}</div>
                <div class="lbl">Source Files</div>
              </div>
              <div class="metric-tile" style="flex:1;min-width:140px;">
                <div class="val">{len(set(r.get('supplier','') or r.get('vendor','') for r in records if r.get('supplier') or r.get('vendor')))}</div>
                <div class="lbl">Unique Suppliers</div>
              </div>
            </div>
            """, unsafe_allow_html=True)

            # Status breakdown
            statuses = {}
            for r in records:
                s = (r.get("delivery_status") or r.get("status") or "unknown").strip().lower()
                statuses[s] = statuses.get(s, 0) + 1

            if statuses:
                st.markdown(_section("&#128202;", "#dcfce7", "Status Breakdown"),
                            unsafe_allow_html=True)
                cols = st.columns(min(len(statuses), 4))
                for i, (status, count) in enumerate(sorted(statuses.items(), key=lambda x: -x[1])):
                    color = "#16a34a" if "deliver" in status else (
                        "#d97706" if "hold" in status or "pending" in status else "#64748b"
                    )
                    cols[i % len(cols)].markdown(f"""
                    <div class="card" style="border-left:4px solid {color};text-align:center;padding:0.8rem;">
                      <div style="font-size:1.4rem;font-weight:800;color:#1e293b;">{count}</div>
                      <div style="font-size:0.75rem;color:#64748b;text-transform:uppercase;">{status}</div>
                    </div>""", unsafe_allow_html=True)

            # Records table
            st.markdown('<hr class="sec-div"/>', unsafe_allow_html=True)
            st.markdown(_section("&#128196;", "#fef3c7", "All Records"),
                        unsafe_allow_html=True)

            # Filter
            search_filter = st.text_input(
                "Filter records", placeholder="Search by component, supplier, PO number...",
                key="internal_filter", label_visibility="collapsed",
            )

            display_records = records
            if search_filter:
                q = search_filter.lower()
                display_records = [
                    r for r in records
                    if any(q in str(v).lower() for v in r.values())
                ]

            if display_records:
                # Show as styled cards
                for r in display_records[:50]:
                    component = r.get("component_name") or r.get("component") or r.get("material") or "—"
                    supplier = r.get("supplier") or r.get("vendor") or "—"
                    qty = r.get("quantity") or r.get("qty") or "—"
                    price = r.get("unit_price_usd") or r.get("unit_price") or r.get("price") or "—"
                    status = r.get("delivery_status") or r.get("status") or "—"
                    po = r.get("po_number") or r.get("po") or "—"
                    date = r.get("date") or r.get("order_date") or "—"
                    lead = r.get("lead_time_weeks") or r.get("lead_time") or "—"
                    quality = r.get("quality_issue") or ""
                    notes = r.get("notes") or ""

                    status_lower = status.lower()
                    border_color = "#22c55e" if "deliver" in status_lower else (
                        "#d97706" if "hold" in status_lower else "#94a3b8"
                    )

                    quality_badge = ""
                    if quality and quality.lower() not in ("none", "n/a", ""):
                        quality_badge = (f'<span style="background:#fee2e2;color:#991b1b;padding:2px 8px;'
                                         f'border-radius:10px;font-size:11px;font-weight:600;">'
                                         f'QUALITY: {quality}</span>')

                    st.markdown(f"""
                    <div class="card" style="border-left:4px solid {border_color};">
                      <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px;flex-wrap:wrap;">
                        <span style="font-weight:700;color:#0f172a;font-size:0.95rem;">{component}</span>
                        <span style="background:#f1f5f9;padding:2px 8px;border-radius:10px;font-size:11px;
                               font-weight:600;color:#64748b;">PO: {po}</span>
                        {quality_badge}
                      </div>
                      <div style="display:flex;gap:20px;flex-wrap:wrap;font-size:0.82rem;color:#475569;">
                        <span><b>Supplier:</b> {supplier}</span>
                        <span><b>Qty:</b> {qty}</span>
                        <span><b>Unit Price:</b> ${price}</span>
                        <span><b>Status:</b> {status}</span>
                        <span><b>Lead Time:</b> {lead} weeks</span>
                        <span><b>Date:</b> {date}</span>
                      </div>
                      {'<div style="font-size:0.78rem;color:#94a3b8;margin-top:4px;">' + notes + '</div>' if notes else ''}
                    </div>
                    """, unsafe_allow_html=True)
            else:
                st.info("No records match your filter.")

            # Download
            st.download_button(
                "Download All Records (JSON)",
                data=json.dumps(records, indent=2, ensure_ascii=False, default=str),
                file_name="internal_procurement_records.json",
                mime="application/json",
                key="dl_internal",
            )
        else:
            st.info("No internal procurement records found. Upload files in the 'Upload Records' tab.")

    # ── Approved Suppliers ──
    with int_tab_suppliers:
        suppliers = _read_approved_suppliers()
        if suppliers:
            st.markdown(f"""
            <div class="metric-tile" style="max-width:200px;margin-bottom:1rem;">
              <div class="val">{len(suppliers)}</div>
              <div class="lbl">Approved Suppliers</div>
            </div>
            """, unsafe_allow_html=True)

            for sup in suppliers:
                name = sup.get("name") or sup.get("supplier_name", "Unknown")
                region = sup.get("region", "—")
                lead = sup.get("lead_time_weeks", "—")
                rating = sup.get("rating", "—")
                certs = sup.get("certifications", [])
                min_order = sup.get("minimum_order", "—")
                specialties = sup.get("specialties", [])

                cert_badges = " ".join(
                    f'<span style="background:#dbeafe;color:#1e40af;padding:2px 8px;'
                    f'border-radius:10px;font-size:11px;font-weight:600;">{c}</span>'
                    for c in (certs if isinstance(certs, list) else [certs])
                )

                st.markdown(f"""
                <div class="card" style="border-left:4px solid #22c55e;">
                  <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;flex-wrap:wrap;">
                    <span style="font-weight:700;color:#0f172a;font-size:1rem;">{name}</span>
                    <span style="font-size:0.85rem;color:#d97706;">{'★' * int(float(rating)) if rating != '—' else ''} {rating}</span>
                  </div>
                  <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px;">
                    {cert_badges}
                  </div>
                  <div style="display:flex;gap:20px;flex-wrap:wrap;font-size:0.82rem;color:#475569;">
                    <span><b>Region:</b> {region}</span>
                    <span><b>Lead Time:</b> {lead} weeks</span>
                    <span><b>Min Order:</b> {min_order} units</span>
                  </div>
                  {'<div style="font-size:0.78rem;color:#64748b;margin-top:4px;"><b>Specialties:</b> ' + ", ".join(specialties) + "</div>" if specialties else ""}
                </div>
                """, unsafe_allow_html=True)
        else:
            st.info("No approved suppliers file found.")

    # ── Upload Records ──
    with int_tab_upload:
        st.markdown("""
        <div class="card" style="text-align:center;">
          <div style="font-size:1.5rem;margin-bottom:8px;">&#128228;</div>
          <div style="font-weight:600;color:#0f172a;">Upload Internal Procurement Records</div>
          <div style="font-size:0.85rem;color:#64748b;margin-top:4px;">
            Upload CSV, Excel, or JSON files containing material usage reports,
            inventory ledgers, stock cards, or purchase orders.
          </div>
        </div>
        """, unsafe_allow_html=True)

        uploaded_files = st.file_uploader(
            "Upload procurement files",
            type=["csv", "xlsx", "xls", "json"],
            accept_multiple_files=True,
            key="upload_internal",
        )

        if uploaded_files:
            for uf in uploaded_files:
                dest = _INTERNAL_DIR / uf.name
                dest.write_bytes(uf.getvalue())
                st.success(f"Saved **{uf.name}** to internal procurement records.")

            st.info("Switch to the 'Material Usage & Stock' tab to view your uploaded records.")

        # Show existing files
        existing = sorted(_INTERNAL_DIR.iterdir())
        if existing:
            st.markdown('<hr class="sec-div"/>', unsafe_allow_html=True)
            st.markdown("**Existing files in internal procurement:**")
            for f in existing:
                size_kb = f.stat().st_size / 1024
                mod_time = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
                st.markdown(f"- `{f.name}` — {size_kb:.1f} KB — modified {mod_time}")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3: ORDER PROCUREMENT
# ══════════════════════════════════════════════════════════════════════════════

with tab_orders:
    st.markdown(_section("&#128230;", "#fef3c7", "Order Procurement"),
                unsafe_allow_html=True)
    st.caption("Access BOMs from email, purchase orders, and requisitions. "
               "Automate reading materials and updating the system.")

    ord_tab_bom, ord_tab_po, ord_tab_auto = st.tabs([
        "BOM Ingestion",
        "Purchase Orders",
        "Auto-Ingest Setup",
    ])

    # ── BOM Ingestion ──
    with ord_tab_bom:
        st.markdown("""
        <div class="card">
          <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">
            <span style="font-size:1.5rem;">&#128220;</span>
            <span style="font-weight:700;color:#0f172a;font-size:1rem;">Bill of Materials (BOM) Ingestion</span>
          </div>
          <div style="font-size:0.85rem;color:#64748b;">
            Upload BOM files received via email or from internal systems.
            The system will extract all materials and update the database.
            <br/><br/>
            <b>Supported formats:</b> CSV, Excel (.xlsx), JSON, PDF
          </div>
        </div>
        """, unsafe_allow_html=True)

        bom_files = st.file_uploader(
            "Upload BOM files",
            type=["csv", "xlsx", "xls", "json", "pdf"],
            accept_multiple_files=True,
            key="upload_bom",
        )

        if bom_files:
            for bf in bom_files:
                st.markdown(f'<hr class="sec-div"/>', unsafe_allow_html=True)
                st.markdown(_section("&#128196;", "#dcfce7", f"Processing: {bf.name}"),
                            unsafe_allow_html=True)

                if bf.name.endswith(".csv"):
                    content = bf.getvalue().decode("utf-8-sig")
                    reader = csv.DictReader(content.splitlines())
                    rows = list(reader)

                    if rows:
                        # Extract material names from common column names
                        material_cols = [c for c in rows[0].keys()
                                         if any(kw in c.lower() for kw in
                                                ["material", "ingredient", "component", "item",
                                                 "part", "raw material", "description"])]
                        qty_cols = [c for c in rows[0].keys()
                                    if any(kw in c.lower() for kw in
                                           ["qty", "quantity", "amount", "units"])]

                        st.markdown(f"""
                        <div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:1rem;">
                          <div class="metric-tile" style="flex:1;min-width:120px;">
                            <div class="val">{len(rows)}</div>
                            <div class="lbl">Line Items</div>
                          </div>
                          <div class="metric-tile" style="flex:1;min-width:120px;">
                            <div class="val">{len(rows[0].keys())}</div>
                            <div class="lbl">Columns</div>
                          </div>
                        </div>
                        """, unsafe_allow_html=True)

                        # Show detected materials
                        if material_cols:
                            st.markdown(f"**Detected material column:** `{material_cols[0]}`")
                            materials = sorted(set(
                                r[material_cols[0]].strip()
                                for r in rows if r.get(material_cols[0], "").strip()
                            ))
                            st.markdown(f"**{len(materials)} unique materials found:**")
                            for mat in materials[:30]:
                                st.markdown(f"- {mat}")

                            # Analyze each material against the database
                            if st.button(f"Analyze all materials from {bf.name}",
                                         type="primary", key=f"analyze_bom_{bf.name}"):
                                db = get_db()
                                for mat in materials:
                                    with st.expander(f"Analysis: {mat}"):
                                        result = analyze_ingredient(mat, db)
                                        n_sup = len(result["suppliers"])
                                        n_sub = len(result["substitutes"])
                                        st.markdown(f"- **Suppliers:** {n_sup}")
                                        st.markdown(f"- **Substitutes:** {n_sub}")
                                        if result["suppliers"]:
                                            top = result["suppliers"][0]
                                            name = top.get("Name", top.get("supplier_name", "?"))
                                            st.markdown(f"- **Top supplier:** {name} "
                                                        f"(score: {top.get('composite_score', 0):.0f})")
                        else:
                            st.warning("Could not auto-detect material column. Columns found: "
                                       + ", ".join(f"`{c}`" for c in rows[0].keys()))
                            st.dataframe(rows[:10])
                    else:
                        st.warning("CSV file is empty.")

                elif bf.name.endswith((".xlsx", ".xls")):
                    try:
                        import pandas as pd
                        df = pd.read_excel(bf, engine="openpyxl")
                        st.markdown(f"**{len(df)} rows, {len(df.columns)} columns**")
                        st.dataframe(df.head(20))
                    except ImportError:
                        st.error("Install `openpyxl` to read Excel files: `pip install openpyxl`")

                elif bf.name.endswith(".json"):
                    data = json.loads(bf.getvalue().decode("utf-8"))
                    if isinstance(data, list):
                        st.markdown(f"**{len(data)} items found**")
                        st.json(data[:10])
                    else:
                        st.json(data)

                elif bf.name.endswith(".pdf"):
                    st.info("PDF BOM processing — save the file and use the PDF harvester.")
                    dest = _INTERNAL_DIR / bf.name
                    dest.write_bytes(bf.getvalue())
                    st.success(f"Saved **{bf.name}** for processing.")

    # ── Purchase Orders ──
    with ord_tab_po:
        st.markdown("""
        <div class="card">
          <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">
            <span style="font-size:1.5rem;">&#128179;</span>
            <span style="font-weight:700;color:#0f172a;font-size:1rem;">Purchase Order Requisitions</span>
          </div>
          <div style="font-size:0.85rem;color:#64748b;">
            View and manage purchase orders from internal systems and digital sources.
          </div>
        </div>
        """, unsafe_allow_html=True)

        # Load from data/procurement_records.csv and internal_procurement/
        po_records = []

        # Historical procurement records
        hist_path = _ROOT / "data" / "procurement_records.csv"
        if hist_path.exists():
            with open(hist_path, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    row["_source"] = "Historical (data/procurement_records.csv)"
                    po_records.append(dict(row))

        # Internal procurement records
        int_path = _INTERNAL_DIR / "procurement_q1_2026.csv"
        if int_path.exists():
            with open(int_path, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    row["_source"] = "Internal (Q1 2026)"
                    po_records.append(dict(row))

        if po_records:
            # Summary metrics
            total_value = 0
            for r in po_records:
                try:
                    total_value += float(r.get("total_price_usd", 0) or 0)
                except (ValueError, TypeError):
                    pass

            unique_suppliers = set(
                r.get("supplier", "") for r in po_records if r.get("supplier")
            )

            st.markdown(f"""
            <div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:1rem;">
              <div class="metric-tile" style="flex:1;min-width:140px;">
                <div class="val">{len(po_records)}</div>
                <div class="lbl">Purchase Orders</div>
              </div>
              <div class="metric-tile" style="flex:1;min-width:140px;">
                <div class="val">${total_value:,.0f}</div>
                <div class="lbl">Total Value (USD)</div>
              </div>
              <div class="metric-tile" style="flex:1;min-width:140px;">
                <div class="val">{len(unique_suppliers)}</div>
                <div class="lbl">Unique Suppliers</div>
              </div>
            </div>
            """, unsafe_allow_html=True)

            # Filter
            po_filter = st.text_input(
                "Filter POs", placeholder="Search by PO number, component, supplier...",
                key="po_filter", label_visibility="collapsed",
            )

            display_pos = po_records
            if po_filter:
                q = po_filter.lower()
                display_pos = [r for r in po_records if any(q in str(v).lower() for v in r.values())]

            for r in display_pos[:50]:
                po_num = r.get("po_number", "—")
                comp = r.get("component_name") or r.get("component") or "—"
                supplier = r.get("supplier", "—")
                qty = r.get("quantity", "—")
                price = r.get("unit_price_usd") or r.get("unit_price", "—")
                total = r.get("total_price_usd") or r.get("total_price", "—")
                status = r.get("delivery_status") or r.get("status", "—")
                date = r.get("date") or r.get("order_date", "—")
                source = r.get("_source", "")

                status_lower = status.lower() if isinstance(status, str) else ""
                border = "#22c55e" if "deliver" in status_lower else (
                    "#d97706" if "hold" in status_lower or "pending" in status_lower else "#3b82f6"
                )

                st.markdown(f"""
                <div class="card" style="border-left:4px solid {border};">
                  <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px;flex-wrap:wrap;">
                    <span style="font-weight:700;color:#0f172a;">{comp}</span>
                    <span style="background:#f1f5f9;padding:2px 8px;border-radius:10px;font-size:11px;
                           font-weight:600;color:#64748b;">{po_num}</span>
                    <span style="font-size:11px;color:#94a3b8;margin-left:auto;">{source}</span>
                  </div>
                  <div style="display:flex;gap:16px;flex-wrap:wrap;font-size:0.82rem;color:#475569;">
                    <span><b>Supplier:</b> {supplier}</span>
                    <span><b>Qty:</b> {qty}</span>
                    <span><b>Price:</b> ${price}</span>
                    <span><b>Total:</b> ${total}</span>
                    <span><b>Status:</b> {status}</span>
                    <span><b>Date:</b> {date}</span>
                  </div>
                </div>
                """, unsafe_allow_html=True)

            st.download_button(
                "Download All POs (JSON)",
                data=json.dumps(po_records, indent=2, ensure_ascii=False, default=str),
                file_name="purchase_orders.json",
                mime="application/json",
                key="dl_pos",
            )
        else:
            st.info("No purchase order records found.")

    # ── Auto-Ingest Setup ──
    with ord_tab_auto:
        st.markdown("""
        <div class="card" style="border-left:4px solid #7c3aed;">
          <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">
            <span style="font-size:1.5rem;">&#9881;</span>
            <span style="font-weight:700;color:#0f172a;font-size:1rem;">Automated BOM & PO Ingestion</span>
          </div>
          <div style="font-size:0.85rem;color:#475569;line-height:1.6;">
            Configure the system to automatically read and process procurement documents:
            <br/><br/>
            <b>How it works:</b>
            <ol style="margin-top:4px;padding-left:20px;">
              <li>BOMs and POs arrive via email or are saved to the internal procurement folder</li>
              <li>The folder watcher detects new/changed files automatically</li>
              <li>Materials are extracted and matched against the database</li>
              <li>The system updates its records with new materials, suppliers, and quantities</li>
            </ol>
          </div>
        </div>
        """, unsafe_allow_html=True)

        st.markdown('<hr class="sec-div"/>', unsafe_allow_html=True)
        st.markdown(_section("&#128194;", "#dcfce7", "Watch Folder Configuration"),
                    unsafe_allow_html=True)

        watch_path = str(_INTERNAL_DIR.resolve())
        st.markdown(f"""
        <div class="card">
          <div style="font-size:0.82rem;color:#64748b;margin-bottom:4px;">Monitored Folder:</div>
          <div style="font-family:monospace;font-size:0.9rem;color:#0f172a;background:#f1f5f9;
                      padding:8px 14px;border-radius:8px;">{watch_path}</div>
          <div style="font-size:0.78rem;color:#94a3b8;margin-top:6px;">
            Drop CSV, Excel, JSON, or PDF files here. They will be auto-processed.
          </div>
        </div>
        """, unsafe_allow_html=True)

        # Supported formats
        st.markdown("""
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:1rem;">
          <span style="background:#dcfce7;color:#166534;padding:4px 12px;border-radius:12px;
                 font-size:12px;font-weight:600;">CSV</span>
          <span style="background:#dbeafe;color:#1e40af;padding:4px 12px;border-radius:12px;
                 font-size:12px;font-weight:600;">Excel (.xlsx)</span>
          <span style="background:#fef3c7;color:#92400e;padding:4px 12px;border-radius:12px;
                 font-size:12px;font-weight:600;">JSON</span>
          <span style="background:#fee2e2;color:#991b1b;padding:4px 12px;border-radius:12px;
                 font-size:12px;font-weight:600;">PDF</span>
        </div>
        """, unsafe_allow_html=True)

        # ── Gmail Integration (live) ──────────────────────────────────────────
        st.markdown('<hr class="sec-div"/>', unsafe_allow_html=True)
        st.markdown(_section("&#128231;", "#ede9fe", "Gmail Integration"),
                    unsafe_allow_html=True)

        # Check if gmail sync module is available
        try:
            from src.agnes.gmail_sync import GmailInboxStore, GmailSetupError
            _gmail_available = True
        except ImportError:
            _gmail_available = False

        if not _gmail_available:
            st.warning("Gmail sync module not found. Ensure `src/agnes/gmail_sync.py` exists.")
        else:
            # Check if Google client libraries are installed
            try:
                from google.auth.transport.requests import Request as _GReq  # noqa: F401
                _google_libs = True
            except ImportError:
                _google_libs = False

            if not _google_libs:
                st.markdown("""
                <div class="card" style="border-left:4px solid #d97706;">
                  <div style="font-size:0.85rem;color:#475569;line-height:1.6;">
                    <b>Setup required:</b> Install Google API client libraries:
                    <pre style="background:#f1f5f9;padding:8px 12px;border-radius:8px;margin-top:6px;
                                font-size:0.82rem;">pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib</pre>
                    Then place your <code>credentials.json</code> (OAuth client secret) in the project root.
                  </div>
                </div>
                """, unsafe_allow_html=True)
            else:
                # Gmail is ready — show sync controls
                gmail_col1, gmail_col2 = st.columns([3, 1])
                with gmail_col1:
                    gmail_query = st.text_input(
                        "Gmail search filter",
                        placeholder="e.g. 'subject:BOM' or 'from:supplier@example.com has:attachment'",
                        key="gmail_query",
                        label_visibility="collapsed",
                    )
                with gmail_col2:
                    gmail_max = st.number_input("Max emails", min_value=1, max_value=500,
                                                value=50, key="gmail_max")

                gmail_sync_btn = st.button("Sync Gmail Now", type="primary", key="gmail_sync_btn")

                if gmail_sync_btn:
                    try:
                        store = GmailInboxStore()
                        with st.spinner("Syncing Gmail inbox..."):
                            result = store.sync_mailbox(
                                query=gmail_query or None,
                                max_messages=gmail_max,
                            )
                        st.success(f"Synced **{result['fetched_count']}** emails to local database.")
                        store.close()
                    except GmailSetupError as e:
                        st.error(f"Gmail setup error: {e}")
                    except Exception as e:
                        st.error(f"Sync failed: {e}")

                # Show mailbox stats if DB exists
                _gmail_db = _ROOT / "data" / "gmail" / "agnes_gmail.db"
                if _gmail_db.exists():
                    try:
                        store = GmailInboxStore()
                        stats = store.mailbox_stats()
                        store.close()

                        st.markdown(f"""
                        <div style="display:flex;gap:16px;flex-wrap:wrap;margin-top:0.8rem;">
                          <div class="metric-tile" style="flex:1;min-width:120px;">
                            <div class="val">{stats['message_count']}</div>
                            <div class="lbl">Synced Emails</div>
                          </div>
                          <div class="metric-tile" style="flex:1;min-width:120px;">
                            <div class="val">{(stats.get('newest_message') or '—')[:10]}</div>
                            <div class="lbl">Newest</div>
                          </div>
                          <div class="metric-tile" style="flex:1;min-width:120px;">
                            <div class="val">{(stats.get('last_sync_at') or '—')[:10]}</div>
                            <div class="lbl">Last Sync</div>
                          </div>
                        </div>
                        """, unsafe_allow_html=True)

                        # Search synced emails
                        st.markdown('<hr class="sec-div"/>', unsafe_allow_html=True)
                        email_search = st.text_input(
                            "Search synced emails",
                            placeholder="Search by subject, sender, or content...",
                            key="gmail_search",
                            label_visibility="collapsed",
                        )
                        if email_search:
                            store = GmailInboxStore()
                            results = store.search_messages(email_search, limit=20)
                            store.close()

                            if results["results"]:
                                st.markdown(f"**{results['count']} results:**")
                                for msg in results["results"]:
                                    sender_short = (msg["sender"] or "")[:50]
                                    subj = msg["subject"] or "(no subject)"
                                    date_short = (msg["date"] or "")[:10]
                                    snippet = (msg["snippet"] or "")[:120]

                                    st.markdown(f"""
                                    <div class="card" style="border-left:4px solid #7c3aed;padding:0.8rem 1.2rem;">
                                      <div style="display:flex;align-items:center;gap:10px;margin-bottom:4px;flex-wrap:wrap;">
                                        <span style="font-weight:700;color:#0f172a;font-size:0.9rem;">{subj}</span>
                                        <span style="font-size:0.75rem;color:#94a3b8;margin-left:auto;">{date_short}</span>
                                      </div>
                                      <div style="font-size:0.8rem;color:#64748b;">
                                        <b>From:</b> {sender_short}
                                      </div>
                                      <div style="font-size:0.78rem;color:#94a3b8;margin-top:4px;">
                                        {snippet}...
                                      </div>
                                    </div>
                                    """, unsafe_allow_html=True)
                            else:
                                st.info("No matching emails found.")

                    except Exception as e:
                        st.error(f"Error reading Gmail database: {e}")
                else:
                    st.info("No synced emails yet. Click **Sync Gmail Now** to start.")

        # ── Digital Document Sources (live) ───────────────────────────────────
        st.markdown('<hr class="sec-div"/>', unsafe_allow_html=True)
        st.markdown(_section("&#127760;", "#fef3c7", "Digital Document Sources"),
                    unsafe_allow_html=True)

        st.markdown("""
        <div class="card">
          <div style="font-size:0.85rem;color:#475569;line-height:1.6;">
            Search the web for procurement documents, spec sheets, and supplier data.
            Results are downloaded and extracted automatically.
          </div>
        </div>
        """, unsafe_allow_html=True)

        doc_col1, doc_col2 = st.columns([4, 1])
        with doc_col1:
            doc_query = st.text_input(
                "Search for documents",
                placeholder="e.g. 'magnesium stearate spec sheet PDF' or 'soy lecithin supplier datasheet'",
                key="doc_search_query",
                label_visibility="collapsed",
            )
        with doc_col2:
            doc_search_btn = st.button("Search Web", type="primary", key="doc_search_btn")

        if doc_search_btn and doc_query:
            try:
                from data_collection.search_engine import multi_engine_search
                with st.spinner(f"Searching web for '{doc_query}'..."):
                    urls = multi_engine_search(doc_query, max_per_engine=10)

                if urls:
                    st.markdown(f"**{len(urls)} results found:**")
                    for url_info in urls:
                        url = url_info if isinstance(url_info, str) else url_info.get("url", str(url_info))
                        title = url_info.get("title", url) if isinstance(url_info, dict) else url
                        domain = url.split("/")[2] if "/" in url and len(url.split("/")) > 2 else url

                        st.markdown(f"""
                        <div class="card" style="border-left:4px solid #0891b2;padding:0.7rem 1.2rem;">
                          <div style="font-weight:600;color:#0f172a;font-size:0.88rem;margin-bottom:2px;">
                            {title[:80]}</div>
                          <div style="font-size:0.75rem;color:#0891b2;">{domain}</div>
                          <div style="font-size:0.72rem;color:#94a3b8;margin-top:2px;word-break:break-all;">
                            {url[:100]}</div>
                        </div>
                        """, unsafe_allow_html=True)
                else:
                    st.info("No results found. Try a different query.")
            except ImportError:
                st.error("Search engine module not available. Check `data_collection/search_engine.py`.")
            except Exception as e:
                st.error(f"Search failed: {e}")

        # PDF Harvester
        st.markdown('<hr class="sec-div"/>', unsafe_allow_html=True)
        st.markdown(_section("&#128196;", "#dcfce7", "PDF Spec Sheet Harvester"),
                    unsafe_allow_html=True)

        pdf_col1, pdf_col2 = st.columns([4, 1])
        with pdf_col1:
            pdf_query = st.text_input(
                "Search for PDF spec sheets",
                placeholder="e.g. 'magnesium stearate technical data sheet'",
                key="pdf_harvest_query",
                label_visibility="collapsed",
            )
        with pdf_col2:
            pdf_btn = st.button("Find PDFs", type="primary", key="pdf_harvest_btn")

        if pdf_btn and pdf_query:
            try:
                from data_collection.pdf_harvester import find_datasheets
                with st.spinner(f"Searching for PDF spec sheets: '{pdf_query}'..."):
                    results = find_datasheets(pdf_query, max_results=5)

                if results:
                    st.markdown(f"**{len(results)} PDF(s) found:**")
                    for pdf_info in results:
                        url = pdf_info.get("url", "") if isinstance(pdf_info, dict) else str(pdf_info)
                        title = pdf_info.get("title", url) if isinstance(pdf_info, dict) else url
                        st.markdown(f"""
                        <div class="card" style="border-left:4px solid #16a34a;padding:0.7rem 1.2rem;">
                          <div style="font-weight:600;color:#0f172a;font-size:0.88rem;">
                            &#128196; {title[:80]}</div>
                          <div style="font-size:0.72rem;color:#94a3b8;margin-top:2px;word-break:break-all;">
                            {url[:120]}</div>
                        </div>
                        """, unsafe_allow_html=True)
                else:
                    st.info("No PDFs found. Try a broader query.")
            except ImportError:
                st.error("PDF harvester module not available. Check `data_collection/pdf_harvester.py`.")
            except Exception as e:
                st.error(f"PDF search failed: {e}")

        # Existing spec sheets
        _spec_dir = _ROOT / "data" / "spec_sheets"
        if _spec_dir.exists():
            pdfs = sorted(_spec_dir.glob("*.pdf"))
            if pdfs:
                st.markdown('<hr class="sec-div"/>', unsafe_allow_html=True)
                st.markdown(f"**{len(pdfs)} downloaded spec sheets:**")
                for p in pdfs[:20]:
                    size_kb = p.stat().st_size / 1024
                    st.markdown(f"- `{p.name}` — {size_kb:.1f} KB")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4: SUPPLIER RISK & COMPLIANCE
# ══════════════════════════════════════════════════════════════════════════════
with tab_compliance:
    st.markdown(_section("&#128737;", "#fef3c7", "Supplier Risk &amp; Compliance"),
                unsafe_allow_html=True)
    st.markdown("""
    <div class="card">
      <div style="font-size:0.85rem;color:#475569;line-height:1.6;">
        Evaluate suppliers against <b>8 international standards</b> across
        <b>Legal Compliance</b> (30 pts) and <b>Quality Standards</b> (50 pts).
        Total score: <b>80 points</b>. Evidence is gathered via web search and
        scored by verification level.
      </div>
    </div>
    """, unsafe_allow_html=True)

    # ── Mode selector: Web Search vs Manual Entry ──
    comp_mode = st.radio(
        "Evaluation mode",
        ["Web Search (auto-scan)", "Manual Entry"],
        horizontal=True,
        key="comp_mode",
        label_visibility="collapsed",
    )

    if comp_mode == "Web Search (auto-scan)":
        # ── Supplier name input ──
        comp_col1, comp_col2 = st.columns([4, 1])
        with comp_col1:
            comp_supplier = st.text_input(
                "Supplier name",
                placeholder="Enter supplier / company name (e.g. 'Brenntag', 'BASF', 'Cargill')",
                key="comp_supplier_name",
                label_visibility="collapsed",
            )
        with comp_col2:
            comp_btn = st.button("Evaluate", type="primary",
                                 use_container_width=True, key="comp_eval_btn")

        if comp_btn and comp_supplier:
            from src.procurement.compliance import evaluate_supplier, STANDARDS

            progress_bar = st.progress(0, text="Starting compliance scan...")

            def _update_progress(step, total, msg):
                progress_bar.progress(step / total if total else 0, text=msg)

            with st.spinner("Scanning web for compliance evidence..."):
                report = evaluate_supplier(
                    comp_supplier,
                    target_market="both",
                    progress_callback=_update_progress,
                )

            progress_bar.empty()
            st.session_state["comp_report"] = report.to_dict()

        # ── Display report ──
        if "comp_report" in st.session_state:
            rpt = st.session_state["comp_report"]

            # ── Overall score banner ──
            total = rpt["total_score"]
            max_s = rpt["max_score"]
            pct = int(total / max_s * 100) if max_s else 0
            risk = rpt["risk_level"]
            risk_colors = {
                "low": ("#16a34a", "#dcfce7"),
                "medium": ("#ca8a04", "#fef9c3"),
                "high": ("#ea580c", "#ffedd5"),
                "critical": ("#dc2626", "#fee2e2"),
            }
            rc, rbg = risk_colors.get(risk, ("#64748b", "#f1f5f9"))

            st.markdown(f"""
            <div style="background:linear-gradient(135deg,{rbg},white);
                        border:2px solid {rc};border-radius:16px;
                        padding:1.5rem 2rem;margin:1.2rem 0;">
              <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;">
                <div>
                  <div style="font-size:0.78rem;color:#64748b;text-transform:uppercase;
                              letter-spacing:1px;font-weight:600;">Compliance Score</div>
                  <div style="font-size:2.8rem;font-weight:800;color:{rc};line-height:1;">
                    {total}<span style="font-size:1.2rem;color:#94a3b8;">/{max_s}</span>
                  </div>
                </div>
                <div style="text-align:center;">
                  <div style="font-size:0.75rem;color:#64748b;text-transform:uppercase;
                              letter-spacing:1px;font-weight:600;">Risk Level</div>
                  <div style="font-size:1.5rem;font-weight:700;color:{rc};
                              text-transform:uppercase;">{risk}</div>
                </div>
                <div style="text-align:center;">
                  <div style="font-size:0.75rem;color:#64748b;">Legal</div>
                  <div style="font-size:1.3rem;font-weight:700;color:#0f172a;">
                    {rpt['legal_score']}/{rpt['legal_max']}</div>
                </div>
                <div style="text-align:center;">
                  <div style="font-size:0.75rem;color:#64748b;">Quality</div>
                  <div style="font-size:1.3rem;font-weight:700;color:#0f172a;">
                    {rpt['quality_score']}/{rpt['quality_max']}</div>
                </div>
              </div>
              <div style="margin-top:12px;background:#e2e8f0;border-radius:8px;height:10px;overflow:hidden;">
                <div style="width:{pct}%;height:100%;background:{rc};border-radius:8px;
                            transition:width 0.5s;"></div>
              </div>
            </div>
            """, unsafe_allow_html=True)

            # ── Red Flags ──
            if rpt["red_flags"]:
                st.markdown('<hr class="sec-div"/>', unsafe_allow_html=True)
                st.markdown(_section("&#9888;", "#fee2e2", "Red Flags"),
                            unsafe_allow_html=True)
                for flag in rpt["red_flags"]:
                    st.markdown(f"""
                    <div style="background:#fee2e2;border-left:4px solid #dc2626;
                                padding:0.6rem 1rem;border-radius:8px;margin-bottom:6px;
                                font-size:0.84rem;color:#991b1b;">
                      &#9888; {flag}
                    </div>
                    """, unsafe_allow_html=True)

            # ── Detailed Breakdown ──
            st.markdown('<hr class="sec-div"/>', unsafe_allow_html=True)

            # Legal standards
            st.markdown(_section("&#9878;", "#dbeafe",
                                 f"Legal / Mandatory Standards ({rpt['legal_score']}/{rpt['legal_max']})"),
                        unsafe_allow_html=True)
            _render_standard_cards([s for s in rpt["standards"] if s["category"] == "legal"])

            # Quality standards
            st.markdown('<hr class="sec-div"/>', unsafe_allow_html=True)
            st.markdown(_section("&#127942;", "#dcfce7",
                                 f"Quality Standards ({rpt['quality_score']}/{rpt['quality_max']})"),
                        unsafe_allow_html=True)
            _render_standard_cards([s for s in rpt["standards"] if s["category"] == "quality"])

            # ── Verification Guide ──
            st.markdown('<hr class="sec-div"/>', unsafe_allow_html=True)
            with st.expander("Verification Guide — How to manually verify certificates"):
                st.markdown("""
                **Trust but Verify Workflow:**

                1. **Check Expiry Date** — If expired = 0 points / Red Flag
                2. **Verify the Issuer** — Is the lab/auditor reputable?
                   (SGS, TUV, Intertek, BSI, Bureau Veritas = High Trust)
                3. **Cross-Reference** — Enter certificate number in IAF CertSearch
                   or the auditor's own website
                4. **Name Match Check** — Does the certificate name exactly match
                   the selling company? Different name = possible middleman (High Risk)

                **Official Verification Databases:**

                | Standard | Verification Database | Link |
                |----------|----------------------|------|
                | ISO 10377 / 9001 | IAF CertSearch | [iafcertsearch.org](https://www.iafcertsearch.org/) |
                | IEC | IECEE CB Certificate Search | [iecee.org/certificates](https://www.iecee.org/certificates) |
                | CPSC | CPSC Accepted Labs | [cpsc.gov](https://www.cpsc.gov/cgi-bin/labsearch/) |
                | REACH | ECHA Registered Substances | [echa.europa.eu](https://echa.europa.eu/information-on-chemicals) |
                | CE Mark | EU NANDO (Notified Bodies) | [ec.europa.eu/nando](https://ec.europa.eu/growth/tools-databases/nando/) |
                | HACCP | BRCGS Certificated Sites | [brcdirectory.co.uk](https://brcdirectory.co.uk/) |
                | Codex | FAO/WHO Codex Standards | [fao.org/fao-who-codexalimentarius](https://www.fao.org/fao-who-codexalimentarius/) |
                | ASTM | ASTM International | [astm.org](https://www.astm.org/) |

                **All-in-One Certification Search Tools:**

                | Tool | What it does | Link |
                |------|-------------|------|
                | SGS | Multi-cert supplier search | [sgs.com](https://www.sgs.com/) |
                | TUV Rheinland | Certificate search (Certipedia) | [certipedia.com](https://www.certipedia.com/) |
                | Intertek | Supplier assurance | [intertek.com](https://www.intertek.com/) |
                | Ecovadis | Sustainability & compliance ratings | [ecovadis.com](https://ecovadis.com/) |

                **Accredited Certification Bodies (High Trust):**
                SGS, TUV Rheinland, TUV SUD, Bureau Veritas, Intertek,
                BSI, DNV, DEKRA, UL, CSA, NSF, Eurofins
                """)

    else:
        # ── Manual Entry Mode ──
        st.markdown("""
        <div class="card" style="border-left:4px solid #7c3aed;">
          <div style="font-size:0.85rem;color:#475569;line-height:1.6;">
            Manually enter the evidence level for each standard based on
            documents you've received from the supplier.
          </div>
        </div>
        """, unsafe_allow_html=True)

        from src.procurement.compliance import (
            STANDARDS, EVIDENCE_THIRD_PARTY, EVIDENCE_CERTIFICATE,
            EVIDENCE_SELF_DECLARED, EVIDENCE_EXPIRED, EVIDENCE_NONE,
            score_from_manual_input,
        )

        evidence_options = {
            "Third-party certified (SGS, TUV, etc.)": EVIDENCE_THIRD_PARTY,
            "Certificate provided (unverified issuer)": EVIDENCE_CERTIFICATE,
            "Self-declared compliance": EVIDENCE_SELF_DECLARED,
            "Expired certification": EVIDENCE_EXPIRED,
            "No evidence": EVIDENCE_NONE,
        }
        option_labels = list(evidence_options.keys())

        manual_entries = {}

        st.markdown("#### Legal / Mandatory Standards (30 pts)")
        legal_stds = [s for s in STANDARDS if s["category"] == "legal"]
        for std in legal_stds:
            sel = st.selectbox(
                f"{std['full_name']} — {std['description']}",
                options=option_labels,
                index=4,  # default "No evidence"
                key=f"manual_{std['id']}",
            )
            manual_entries[std["id"]] = evidence_options[sel]

        st.markdown("#### Quality Standards (50 pts)")
        qual_stds = [s for s in STANDARDS if s["category"] == "quality"]
        for std in qual_stds:
            sel = st.selectbox(
                f"{std['full_name']} — {std['description']}",
                options=option_labels,
                index=4,
                key=f"manual_{std['id']}",
            )
            manual_entries[std["id"]] = evidence_options[sel]

        if st.button("Calculate Score", type="primary", key="manual_score_btn"):
            report = score_from_manual_input(manual_entries)
            st.session_state["comp_report"] = report.to_dict()
            st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# TAB 5: SUPPLIER DATABASE
# ══════════════════════════════════════════════════════════════════════════════
with tab_supdb:
    from src.procurement.supplier_db import SupplierDatabase

    st.markdown(_section("&#128450;", "#dbeafe", "Supplier Intelligence Hub"),
                unsafe_allow_html=True)
    st.markdown("""
    <div class="card">
      <div style="font-size:0.85rem;color:#475569;line-height:1.6;">
        Central database for all supplier intelligence — internal records,
        Obsidian Web Clipper imports, and web-scraped data. Each supplier is tracked
        across <b>5 source tiers</b> with full triangulation and red-flag monitoring.
      </div>
    </div>
    """, unsafe_allow_html=True)

    sdb = SupplierDatabase()

    # ── Sub-tabs ──
    sdb_tab_overview, sdb_tab_discover, sdb_tab_add, sdb_tab_import, sdb_tab_browse, sdb_tab_scrape = st.tabs([
        "Overview",
        "Discover Suppliers",
        "Add Supplier",
        "Import Data",
        "Browse & Search",
        "Web Verification",
    ])

    # ── 5A: Overview Dashboard ──────────────────────────────────────────────
    with sdb_tab_overview:
        stats = sdb.get_stats()
        total = stats.get("total_suppliers", 0)
        tri_count = stats.get("triangulated_count", 0)
        avg_comp = stats.get("avg_data_completeness", 0)
        by_tier = stats.get("by_tier", {})
        by_country = stats.get("by_country", {})

        # Metric cards
        st.markdown(f"""
        <div style="display:flex;gap:14px;flex-wrap:wrap;margin:1rem 0;">
          <div class="metric-tile" style="flex:1;min-width:130px;">
            <div class="val">{total}</div><div class="lbl">Total Suppliers</div>
          </div>
          <div class="metric-tile" style="flex:1;min-width:130px;">
            <div class="val" style="color:#16a34a;">{by_tier.get('Tier 1 - Primary', 0)}</div>
            <div class="lbl">Tier 1 (Primary)</div>
          </div>
          <div class="metric-tile" style="flex:1;min-width:130px;">
            <div class="val" style="color:#0891b2;">{by_tier.get('Tier 2 - Backup', 0)}</div>
            <div class="lbl">Tier 2 (Backup)</div>
          </div>
          <div class="metric-tile" style="flex:1;min-width:130px;">
            <div class="val" style="color:#ca8a04;">{by_tier.get('Tier 3 - Conditional', 0)}</div>
            <div class="lbl">Tier 3 (Conditional)</div>
          </div>
          <div class="metric-tile" style="flex:1;min-width:130px;">
            <div class="val" style="color:#dc2626;">{by_tier.get('Tier 4 - Reject', 0)}</div>
            <div class="lbl">Tier 4 (Reject)</div>
          </div>
        </div>
        <div style="display:flex;gap:14px;flex-wrap:wrap;margin-bottom:1rem;">
          <div class="metric-tile" style="flex:1;min-width:130px;">
            <div class="val">{tri_count}</div><div class="lbl">Triangulated</div>
          </div>
          <div class="metric-tile" style="flex:1;min-width:130px;">
            <div class="val">{avg_comp:.1f}/10</div><div class="lbl">Avg Data Completeness</div>
          </div>
          <div class="metric-tile" style="flex:1;min-width:130px;">
            <div class="val">{len(by_country)}</div><div class="lbl">Countries</div>
          </div>
        </div>
        """, unsafe_allow_html=True)

        # Red flags summary
        red_flag_suppliers = sdb.get_red_flag_suppliers()
        if red_flag_suppliers:
            st.markdown(_section("&#9888;", "#fee2e2",
                                 f"Red Flag Suppliers ({len(red_flag_suppliers)})"),
                        unsafe_allow_html=True)
            for rfs in red_flag_suppliers[:10]:
                flags = []
                if rfs.get("recall_history"):
                    flags.append("Recall history")
                if rfs.get("eu_safety_gate_flagged"):
                    flags.append("EU Safety Gate")
                if rfs.get("cpsc_recall_flagged"):
                    flags.append("CPSC recall")
                if rfs.get("self_declared_only"):
                    flags.append("Self-declared only")
                if rfs.get("red_flags"):
                    flags.append(str(rfs["red_flags"])[:60])
                st.markdown(f"""
                <div class="card" style="border-left:4px solid #dc2626;padding:0.6rem 1rem;">
                  <div style="font-weight:700;color:#991b1b;font-size:0.88rem;">
                    {rfs.get('supplier_name', 'Unknown')}</div>
                  <div style="font-size:0.78rem;color:#dc2626;margin-top:2px;">
                    {' | '.join(flags)}</div>
                </div>
                """, unsafe_allow_html=True)

        # Untriangulated suppliers
        untri = sdb.get_untriangulated()
        if untri:
            st.markdown('<hr class="sec-div"/>', unsafe_allow_html=True)
            st.markdown(_section("&#9888;", "#fef9c3",
                                 f"Needs Triangulation ({len(untri)})"),
                        unsafe_allow_html=True)
            for u in untri[:10]:
                checks = []
                if not u.get("triangulation_regulatory"):
                    checks.append("Regulatory")
                if not u.get("triangulation_firstparty"):
                    checks.append("First-party")
                if not u.get("triangulation_trade"):
                    checks.append("Trade/Customs")
                st.markdown(f"""
                <div class="card" style="border-left:4px solid #ca8a04;padding:0.5rem 1rem;">
                  <span style="font-weight:600;color:#0f172a;">{u.get('supplier_name', '?')}</span>
                  <span style="font-size:0.78rem;color:#ca8a04;margin-left:8px;">
                    Missing: {', '.join(checks)}</span>
                </div>
                """, unsafe_allow_html=True)

    # ── 5B: Discovered Suppliers (from DB — populated by Tab 1 auto-discovery) ──
    with sdb_tab_discover:
        st.markdown(_section("&#128269;", "#e0e7ff",
                             "360° Supplier Discovery"),
                    unsafe_allow_html=True)

        # Always read discovered suppliers from the DATABASE (not session state)
        _disc_all_db = sdb.get_all_suppliers()
        _disc_external = [s for s in _disc_all_db if s.get("source_name")]

        if _disc_external:
            # Group by product/ingredient
            _disc_by_product = {}
            for s in _disc_external:
                prod = s.get("product", "Unknown")
                if prod not in _disc_by_product:
                    _disc_by_product[prod] = []
                _disc_by_product[prod].append(s)

            st.markdown(f"""
            <div class="card" style="border:2px solid #16a34a;text-align:center;padding:1rem;">
              <div style="font-size:1.5rem;font-weight:800;color:#16a34a;">
                {len(_disc_external)} Suppliers in Database</div>
              <div style="font-size:0.85rem;color:#64748b;">
                Across {len(_disc_by_product)} product(s) — from web discovery &amp; manual entries</div>
            </div>
            """, unsafe_allow_html=True)

            for prod_name, suppliers in sorted(_disc_by_product.items()):
                with st.expander(f"{prod_name} — {len(suppliers)} suppliers", expanded=len(_disc_by_product) == 1):
                    for s in suppliers:
                        name = s.get("supplier_name", "?")
                        country = s.get("country", "?")
                        src = s.get("source_name", "?")
                        stier = s.get("source_tier", "?")
                        price = s.get("price_per_unit")
                        moq = s.get("moq")
                        price_str = f"{s.get('currency','$')}{price}" if price else "—"
                        moq_str = f"{moq:,}" if moq else "—"

                        cbadges = []
                        for cid, clbl in [("cert_iso","ISO"),("cert_haccp","HACCP"),
                                           ("cert_reach","REACH"),("cert_ce_mark","CE"),
                                           ("cert_fssai","FSSAI"),("cert_brc","BRC")]:
                            if s.get(cid):
                                cbadges.append(clbl)
                        cert_str = " ".join(
                            f'<span style="background:#dbeafe;color:#1e40af;padding:1px 5px;'
                            f'border-radius:6px;font-size:0.65rem;font-weight:600;">{c}</span>'
                            for c in cbadges
                        ) if cbadges else ""

                        tier_colors = {1:"#16a34a",2:"#0891b2",3:"#7c3aed",4:"#ca8a04",5:"#64748b"}
                        tc = tier_colors.get(stier, "#64748b")

                        st.markdown(f"""
                        <div class="card" style="border-left:4px solid {tc};padding:0.6rem 1rem;">
                          <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;">
                            <div style="flex:1;min-width:180px;">
                              <div style="font-weight:700;color:#0f172a;font-size:0.88rem;">{name}</div>
                              <div style="font-size:0.75rem;color:#64748b;">{country} · {src} (T{stier})</div>
                              <div style="margin-top:2px;">{cert_str}</div>
                            </div>
                            <div style="display:flex;gap:14px;align-items:center;">
                              <div style="text-align:center;">
                                <div style="font-size:0.68rem;color:#94a3b8;">Price</div>
                                <div style="font-weight:600;font-size:0.82rem;">{price_str}</div>
                              </div>
                              <div style="text-align:center;">
                                <div style="font-size:0.68rem;color:#94a3b8;">MOQ</div>
                                <div style="font-weight:600;font-size:0.82rem;">{moq_str}</div>
                              </div>
                            </div>
                          </div>
                        </div>
                        """, unsafe_allow_html=True)
        else:
            st.info("No suppliers in database yet. Run an ingredient analysis or barcode scan "
                     "in the **Supply Intelligence** tab — supplier discovery runs automatically.")

    # ── 5C: Add Supplier (manual entry) ─────────────────────────────────────
    with sdb_tab_add:
        st.markdown(_section("&#10133;", "#dcfce7", "Add New Supplier"),
                    unsafe_allow_html=True)

        with st.form("add_supplier_form", clear_on_submit=True):
            st.markdown("**Basic Information**")
            asc1, asc2, asc3 = st.columns(3)
            with asc1:
                _as_name = st.text_input("Supplier Name *", key="as_name")
            with asc2:
                _as_product = st.text_input("Product *", key="as_product")
            with asc3:
                _as_country = st.text_input("Country", key="as_country")

            asc4, asc5 = st.columns(2)
            with asc4:
                _as_category = st.text_input("Product Category", key="as_category")
            with asc5:
                _as_website = st.text_input("Website", key="as_website")

            st.markdown("**Pricing & Quantity**")
            apc1, apc2, apc3, apc4 = st.columns(4)
            with apc1:
                _as_price = st.number_input("Price/unit", min_value=0.0,
                                            value=0.0, step=0.01, key="as_price")
            with apc2:
                _as_currency = st.selectbox("Currency",
                    ["USD", "EUR", "INR", "GBP", "CNY", "JPY"], key="as_currency")
            with apc3:
                _as_moq = st.number_input("MOQ", min_value=0, value=0,
                                          step=100, key="as_moq")
            with apc4:
                _as_capacity = st.number_input("Monthly Capacity", min_value=0,
                                               value=0, step=1000, key="as_capacity")

            apc5, apc6 = st.columns(2)
            with apc5:
                _as_lead = st.number_input("Lead Time (days)", min_value=0,
                                           value=0, step=1, key="as_lead")
            with apc6:
                _as_sample = st.checkbox("Sample Available", key="as_sample")

            st.markdown("**Scoring (0-10)**")
            asc6, asc7 = st.columns(2)
            with asc6:
                _as_scale = st.slider("Scalability", 0, 10, 0, key="as_scale")
            with asc7:
                _as_rely = st.slider("Reliability", 0, 10, 0, key="as_rely")

            st.markdown("**Certifications**")
            cert_cols = st.columns(5)
            _as_certs = {}
            cert_names = [
                ("cert_iso", "ISO"), ("cert_haccp", "HACCP"),
                ("cert_reach", "REACH"), ("cert_ce_mark", "CE Mark"),
                ("cert_cpsc", "CPSC"), ("cert_astm", "ASTM"),
                ("cert_brc", "BRC"), ("cert_fssai", "FSSAI"),
                ("cert_bis", "BIS"),
            ]
            for i, (cid, clabel) in enumerate(cert_names):
                with cert_cols[i % 5]:
                    _as_certs[cid] = st.checkbox(clabel, key=f"as_{cid}")

            st.markdown("**Source & Triangulation**")
            src_c1, src_c2, src_c3 = st.columns(3)
            with src_c1:
                _as_stier = st.selectbox("Source Tier",
                    [1, 2, 3, 4, 5],
                    format_func=lambda x: {
                        1: "T1 - Regulatory", 2: "T2 - Brand/First-party",
                        3: "T3 - B2B Marketplace", 4: "T4 - Trade/Customs",
                        5: "T5 - Aggregator",
                    }[x], key="as_stier")
            with src_c2:
                _as_sname = st.text_input("Source Name", key="as_sname",
                    placeholder="e.g. Alibaba, ImportYeti, FDA")
            with src_c3:
                _as_surl = st.text_input("Source URL", key="as_surl")

            tri_cols = st.columns(3)
            with tri_cols[0]:
                _as_tri_reg = st.checkbox("Regulatory verified", key="as_tri_reg")
            with tri_cols[1]:
                _as_tri_fp = st.checkbox("First-party verified", key="as_tri_fp")
            with tri_cols[2]:
                _as_tri_trade = st.checkbox("Trade/customs verified", key="as_tri_trade")

            st.markdown("**Contact**")
            con_c1, con_c2, con_c3 = st.columns(3)
            with con_c1:
                _as_email = st.text_input("Email", key="as_email")
            with con_c2:
                _as_phone = st.text_input("Phone", key="as_phone")
            with con_c3:
                _as_contact_name = st.text_input("Contact Name", key="as_cname")

            _as_notes = st.text_area("Notes / Red Flags", key="as_notes", height=80)

            submitted = st.form_submit_button("Add Supplier", type="primary")

            if submitted and _as_name:
                supplier_data = {
                    "supplier_name": _as_name,
                    "product": _as_product,
                    "product_category": _as_category,
                    "country": _as_country,
                    "price_per_unit": _as_price if _as_price > 0 else None,
                    "currency": _as_currency,
                    "moq": _as_moq if _as_moq > 0 else None,
                    "monthly_capacity": _as_capacity if _as_capacity > 0 else None,
                    "lead_time_days": _as_lead if _as_lead > 0 else None,
                    "sample_available": _as_sample,
                    "scalability_score": _as_scale if _as_scale > 0 else None,
                    "reliability_score": _as_rely if _as_rely > 0 else None,
                    "source_tier": _as_stier,
                    "source_name": _as_sname,
                    "source_url": _as_surl,
                    "triangulation_regulatory": _as_tri_reg,
                    "triangulation_firstparty": _as_tri_fp,
                    "triangulation_trade": _as_tri_trade,
                    "triangulation_complete": _as_tri_reg and _as_tri_fp and _as_tri_trade,
                    "contact_name": _as_contact_name,
                    "contact_email": _as_email,
                    "contact_phone": _as_phone,
                    "website": _as_website,
                    "notes": _as_notes,
                    "date_scraped": datetime.now().strftime("%Y-%m-%d"),
                }
                supplier_data.update(_as_certs)
                sid = sdb.add_supplier(supplier_data)
                st.success(f"Supplier **{_as_name}** added (ID: {sid})")

    # ── 5C: Import Data ─────────────────────────────────────────────────────
    with sdb_tab_import:
        st.markdown(_section("&#128228;", "#e0e7ff", "Import Supplier Data"),
                    unsafe_allow_html=True)

        imp_mode = st.radio("Import format", [
            "CSV (Obsidian Dataview export)",
            "Obsidian Markdown Note",
        ], horizontal=True, key="imp_mode", label_visibility="collapsed")

        if imp_mode == "CSV (Obsidian Dataview export)":
            csv_file = st.file_uploader(
                "Upload CSV file from Obsidian Dataview export",
                type=["csv"], key="imp_csv",
            )
            if csv_file is not None:
                import tempfile
                with tempfile.NamedTemporaryFile(delete=False, suffix=".csv",
                                                 mode="wb") as tmp:
                    tmp.write(csv_file.read())
                    tmp_path = tmp.name
                try:
                    count = sdb.import_from_csv(tmp_path)
                    st.success(f"Imported **{count}** suppliers from CSV")
                except Exception as e:
                    st.error(f"CSV import failed: {e}")
                finally:
                    os.unlink(tmp_path)
        else:
            md_file = st.file_uploader(
                "Upload Obsidian .md supplier note",
                type=["md", "txt"], key="imp_md",
            )
            if md_file is not None:
                md_content = md_file.read().decode("utf-8")
                try:
                    parsed = sdb.import_from_obsidian_md(md_content)
                    if parsed.get("supplier_name"):
                        sid = sdb.add_supplier(parsed)
                        st.success(
                            f"Imported **{parsed['supplier_name']}** (ID: {sid})"
                        )
                        with st.expander("Parsed fields"):
                            st.json({k: v for k, v in parsed.items()
                                     if v is not None and v != "" and v != False})
                    else:
                        st.warning("Could not parse supplier name from file")
                except Exception as e:
                    st.error(f"Markdown import failed: {e}")

        # Export
        st.markdown('<hr class="sec-div"/>', unsafe_allow_html=True)
        st.markdown(_section("&#128229;", "#f0fdf4", "Export Database"),
                    unsafe_allow_html=True)
        if st.button("Export All Suppliers to CSV", key="exp_csv_btn"):
            export_path = str(_ROOT / "data" / "suppliers_export.csv")
            sdb.export_to_csv(export_path)
            with open(export_path, "r", encoding="utf-8") as ef:
                st.download_button(
                    "Download CSV", ef.read(),
                    file_name="agnes_suppliers_export.csv",
                    mime="text/csv", key="dl_csv",
                )

    # ── 5D: Browse & Search ─────────────────────────────────────────────────
    with sdb_tab_browse:
        st.markdown(_section("&#128269;", "#fef3c7", "Browse & Search Suppliers"),
                    unsafe_allow_html=True)

        browse_query = st.text_input(
            "Search suppliers", key="browse_query",
            placeholder="Search by name, product, or country...",
            label_visibility="collapsed",
        )

        if browse_query:
            results = sdb.search_suppliers(browse_query)
        else:
            results = sdb.get_all_suppliers()

        if results:
            st.markdown(f"**{len(results)} supplier(s)**")
            for sup in results:
                name = sup.get("supplier_name", "Unknown")
                product = sup.get("product", "")
                country = sup.get("country", "")
                tier = sup.get("tier_output", "Unscored")
                price = sup.get("price_per_unit")
                moq = sup.get("moq")
                scale = sup.get("scalability_score")
                rely = sup.get("reliability_score")
                tri = sup.get("triangulation_complete")
                completeness = sup.get("data_completeness_score", 0)
                src = sup.get("source_name", "")

                tier_color = {
                    "Tier 1 - Primary": "#16a34a",
                    "Tier 2 - Backup": "#0891b2",
                    "Tier 3 - Conditional": "#ca8a04",
                    "Tier 4 - Reject": "#dc2626",
                }.get(tier, "#64748b")

                # Cert badges
                certs = []
                for cid, clbl in [("cert_iso","ISO"),("cert_haccp","HACCP"),
                                   ("cert_reach","REACH"),("cert_ce_mark","CE"),
                                   ("cert_cpsc","CPSC"),("cert_astm","ASTM"),
                                   ("cert_brc","BRC"),("cert_fssai","FSSAI"),
                                   ("cert_bis","BIS")]:
                    if sup.get(cid):
                        certs.append(clbl)
                cert_html = " ".join(
                    f'<span style="background:#dbeafe;color:#1e40af;padding:1px 6px;'
                    f'border-radius:8px;font-size:0.68rem;font-weight:600;">{c}</span>'
                    for c in certs
                ) if certs else '<span style="font-size:0.72rem;color:#94a3b8;">No certs</span>'

                # Triangulation indicators
                tri_reg = "&#9989;" if sup.get("triangulation_regulatory") else "&#10060;"
                tri_fp = "&#9989;" if sup.get("triangulation_firstparty") else "&#10060;"
                tri_tr = "&#9989;" if sup.get("triangulation_trade") else "&#10060;"

                price_str = f"{sup.get('currency','')}{price}" if price else "—"
                moq_str = f"{moq:,}" if moq else "—"

                st.markdown(f"""
                <div class="card" style="border-left:4px solid {tier_color};padding:0.8rem 1.2rem;">
                  <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;">
                    <div style="flex:1;min-width:220px;">
                      <div style="font-weight:700;color:#0f172a;font-size:0.95rem;">{name}</div>
                      <div style="font-size:0.8rem;color:#64748b;">{product} · {country}</div>
                      <div style="margin-top:4px;">{cert_html}</div>
                    </div>
                    <div style="display:flex;gap:16px;flex-wrap:wrap;align-items:center;">
                      <div style="text-align:center;">
                        <div style="font-size:0.7rem;color:#94a3b8;">Price</div>
                        <div style="font-weight:600;font-size:0.85rem;">{price_str}</div>
                      </div>
                      <div style="text-align:center;">
                        <div style="font-size:0.7rem;color:#94a3b8;">MOQ</div>
                        <div style="font-weight:600;font-size:0.85rem;">{moq_str}</div>
                      </div>
                      <div style="text-align:center;">
                        <div style="font-size:0.7rem;color:#94a3b8;">Scale</div>
                        <div style="font-weight:600;font-size:0.85rem;">{scale or '—'}/10</div>
                      </div>
                      <div style="text-align:center;">
                        <div style="font-size:0.7rem;color:#94a3b8;">Reliability</div>
                        <div style="font-weight:600;font-size:0.85rem;">{rely or '—'}/10</div>
                      </div>
                      <div style="text-align:center;">
                        <div style="font-size:0.7rem;color:#94a3b8;">Data</div>
                        <div style="font-weight:600;font-size:0.85rem;">{completeness}/10</div>
                      </div>
                    </div>
                    <div style="text-align:center;min-width:80px;">
                      <div style="display:inline-block;padding:3px 10px;border-radius:12px;
                                  font-size:0.72rem;font-weight:700;color:white;background:{tier_color};">
                        {tier.split(' - ')[0] if ' - ' in str(tier) else tier}</div>
                    </div>
                  </div>
                  <div style="display:flex;gap:12px;margin-top:6px;font-size:0.72rem;color:#64748b;">
                    <span>Triangulation: Reg {tri_reg} Brand {tri_fp} Trade {tri_tr}</span>
                    <span>· Source: {src or '—'}</span>
                  </div>
                </div>
                """, unsafe_allow_html=True)
        else:
            st.info("No suppliers in database yet. Add suppliers or import data.")

    # ── 5E: Web Verification ────────────────────────────────────────────────
    with sdb_tab_scrape:
        st.markdown(_section("&#128270;", "#e0e7ff", "Web Data Verification"),
                    unsafe_allow_html=True)
        st.markdown("""
        <div class="card">
          <div style="font-size:0.85rem;color:#475569;line-height:1.6;">
            Verify supplier claims by cross-checking against official databases
            and trade intelligence sources. Enter a supplier name to run
            automated checks across <b>Tier 1-4</b> sources.
          </div>
        </div>
        """, unsafe_allow_html=True)

        ver_col1, ver_col2 = st.columns([4, 1])
        with ver_col1:
            ver_supplier = st.text_input(
                "Supplier to verify", key="ver_supplier",
                placeholder="Enter supplier name to verify...",
                label_visibility="collapsed",
            )
        with ver_col2:
            ver_btn = st.button("Verify", type="primary", key="ver_btn",
                                use_container_width=True)

        if ver_btn and ver_supplier:
            from data_collection.search_engine import multi_engine_search

            verification_checks = [
                {
                    "name": "Tier 1 — Regulatory (ISO / Compliance)",
                    "query": f'"{ver_supplier}" ISO certification site:iafcertsearch.org OR site:echa.europa.eu OR site:fda.gov OR site:cpsc.gov',
                    "color": "#16a34a",
                    "tier": 1,
                },
                {
                    "name": "Tier 2 — Brand / First-party (Product Data)",
                    "query": f'"{ver_supplier}" manufacturer product specifications official site',
                    "color": "#0891b2",
                    "tier": 2,
                },
                {
                    "name": "Tier 3 — B2B Marketplace (Pricing & MOQ)",
                    "query": f'"{ver_supplier}" site:alibaba.com OR site:indiamart.com OR site:thomasnet.com OR site:europages.com',
                    "color": "#7c3aed",
                    "tier": 3,
                },
                {
                    "name": "Tier 4 — Trade / Customs (Shipment History)",
                    "query": f'"{ver_supplier}" site:importyeti.com OR shipment export import customs',
                    "color": "#ca8a04",
                    "tier": 4,
                },
            ]

            progress = st.progress(0, text="Starting verification...")
            all_ver_results = []

            for i, check in enumerate(verification_checks):
                progress.progress((i + 1) / len(verification_checks),
                                  text=f"Checking {check['name']}...")
                try:
                    results = multi_engine_search(
                        check["query"], max_per_engine=5,
                        use_ddg=True, use_bing=True, use_google=False,
                    )
                    valid = [r for r in results if "error" not in r]
                    all_ver_results.append({
                        "check": check,
                        "results": valid,
                        "found": len(valid) > 0,
                    })
                except Exception as e:
                    all_ver_results.append({
                        "check": check,
                        "results": [],
                        "found": False,
                        "error": str(e),
                    })
                import time as _time
                _time.sleep(0.5)

            progress.empty()

            # Display results
            found_count = sum(1 for v in all_ver_results if v["found"])
            st.markdown(f"""
            <div class="card" style="border:2px solid {'#16a34a' if found_count >= 3 else '#ca8a04' if found_count >= 2 else '#dc2626'};
                        padding:1rem;text-align:center;">
              <div style="font-size:1.5rem;font-weight:800;color:#0f172a;">
                {found_count}/4 Tiers Verified</div>
              <div style="font-size:0.85rem;color:#64748b;">
                {'Triangulation PASSED' if found_count >= 3 else 'Triangulation INCOMPLETE — needs more sources'}</div>
            </div>
            """, unsafe_allow_html=True)

            for vr in all_ver_results:
                check = vr["check"]
                results = vr["results"]
                found = vr["found"]
                icon = "&#9989;" if found else "&#10060;"

                st.markdown(f"""
                <div class="card" style="border-left:4px solid {check['color']};padding:0.8rem 1.2rem;">
                  <div style="display:flex;align-items:center;gap:8px;">
                    <span style="font-size:1.1rem;">{icon}</span>
                    <div style="font-weight:700;color:#0f172a;font-size:0.9rem;">
                      {check['name']}</div>
                    <span style="margin-left:auto;font-size:0.78rem;color:#64748b;">
                      {len(results)} result(s)</span>
                  </div>
                </div>
                """, unsafe_allow_html=True)

                if results:
                    for r in results[:3]:
                        url = r.get("url", "")
                        title = r.get("title", url)[:80]
                        domain = r.get("domain", "")
                        snippet = r.get("snippet", "")[:120]
                        st.markdown(f"""
                        <div style="margin-left:2rem;padding:0.4rem 0.8rem;
                                    border-bottom:1px solid #e2e8f0;">
                          <div style="font-size:0.82rem;font-weight:600;color:#0f172a;">
                            {title}</div>
                          <div style="font-size:0.72rem;color:#0891b2;">{domain}</div>
                          <div style="font-size:0.72rem;color:#94a3b8;margin-top:2px;">
                            {snippet}</div>
                        </div>
                        """, unsafe_allow_html=True)
                elif vr.get("error"):
                    st.markdown(f"""
                    <div style="margin-left:2rem;font-size:0.78rem;color:#dc2626;">
                      Search error: {vr['error']}</div>
                    """, unsafe_allow_html=True)

    sdb.close()


# ══════════════════════════════════════════════════════════════════════════════
# TAB 6: FINAL SUPPLIER RANKING
# ══════════════════════════════════════════════════════════════════════════════
with tab_ranking:
    from src.procurement.supplier_db import SupplierDatabase as _RankDB
    from src.procurement.supplier_scorer import rank_suppliers, tier_summary

    st.markdown(_section("&#127942;", "#dcfce7", "Final Supplier Ranking"),
                unsafe_allow_html=True)
    st.markdown("""
    <div class="card">
      <div style="font-size:0.85rem;color:#475569;line-height:1.6;">
        All suppliers scored on a <b>100-point scale</b> across Price (20),
        Quantity (15), Scalability (20), Reliability (25), Data Completeness (10),
        and Triangulation Bonus (10). Red-flag penalties applied automatically.
      </div>
    </div>
    """, unsafe_allow_html=True)

    _rdb = _RankDB()
    _all_sup = _rdb.get_all_suppliers()

    if not _all_sup:
        st.info("No suppliers in database. Add suppliers in the **Supplier Database** tab first.")
    else:
        # Score all suppliers
        ranked = rank_suppliers(_all_sup)
        summary = tier_summary(ranked)

        # ── Summary banner ──
        st.markdown(f"""
        <div style="background:linear-gradient(135deg,#f0fdf4,#ecfeff);
                    border:2px solid #16a34a;border-radius:16px;
                    padding:1.5rem 2rem;margin:1rem 0;">
          <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:14px;">
            <div style="text-align:center;">
              <div style="font-size:2.2rem;font-weight:800;color:#0f172a;">{summary['total']}</div>
              <div style="font-size:0.75rem;color:#64748b;">Total Ranked</div>
            </div>
            <div style="text-align:center;">
              <div style="font-size:2rem;font-weight:800;color:#16a34a;">{summary['tier_1_count']}</div>
              <div style="font-size:0.75rem;color:#16a34a;">Tier 1 Primary</div>
            </div>
            <div style="text-align:center;">
              <div style="font-size:2rem;font-weight:800;color:#0891b2;">{summary['tier_2_count']}</div>
              <div style="font-size:0.75rem;color:#0891b2;">Tier 2 Backup</div>
            </div>
            <div style="text-align:center;">
              <div style="font-size:2rem;font-weight:800;color:#ca8a04;">{summary['tier_3_count']}</div>
              <div style="font-size:0.75rem;color:#ca8a04;">Tier 3 Conditional</div>
            </div>
            <div style="text-align:center;">
              <div style="font-size:2rem;font-weight:800;color:#dc2626;">{summary['tier_4_count']}</div>
              <div style="font-size:0.75rem;color:#dc2626;">Tier 4 Reject</div>
            </div>
            <div style="text-align:center;">
              <div style="font-size:1.5rem;font-weight:800;color:#7c3aed;">{summary['avg_score']}</div>
              <div style="font-size:0.75rem;color:#64748b;">Avg Score</div>
            </div>
          </div>
        </div>
        """, unsafe_allow_html=True)

        # ── Filter ──
        rank_filter = st.radio(
            "Filter by tier", ["All", "Tier 1", "Tier 2", "Tier 3", "Tier 4"],
            horizontal=True, key="rank_filter", label_visibility="collapsed",
        )

        if rank_filter != "All":
            tier_map = {
                "Tier 1": "Tier 1 - Primary", "Tier 2": "Tier 2 - Backup",
                "Tier 3": "Tier 3 - Conditional", "Tier 4": "Tier 4 - Reject",
            }
            ranked = [s for s in ranked if s.get("tier_output") == tier_map.get(rank_filter)]

        # ── Ranked list ──
        for sup in ranked:
            rank = sup.get("rank", "?")
            name = sup.get("supplier_name", "Unknown")
            product = sup.get("product", "")
            country = sup.get("country", "")
            score = sup.get("final_score", 0)
            tier = sup.get("tier_output", "?")
            action = sup.get("action", "")
            bd = sup.get("score_breakdown", {})

            tier_color = {
                "Tier 1 - Primary": "#16a34a",
                "Tier 2 - Backup": "#0891b2",
                "Tier 3 - Conditional": "#ca8a04",
                "Tier 4 - Reject": "#dc2626",
            }.get(tier, "#64748b")

            score_pct = int(score)
            tier_short = tier.split(" - ")[0] if " - " in str(tier) else tier

            # Penalties text
            penalties = bd.get("penalties", [])
            pen_text = " | ".join(penalties) if penalties else ""

            # Header card: rank + name + score + tier
            st.markdown(f"""
            <div class="card" style="border-left:5px solid {tier_color};padding:1rem 1.2rem;">
              <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;">
                <div style="display:flex;align-items:center;gap:14px;flex:1;min-width:200px;">
                  <div style="font-size:1.8rem;font-weight:800;color:{tier_color};">#{rank}</div>
                  <div>
                    <div style="font-weight:700;color:#0f172a;font-size:1rem;">{name}</div>
                    <div style="font-size:0.8rem;color:#64748b;">{product} · {country}</div>
                  </div>
                </div>
                <div style="text-align:center;">
                  <div style="font-size:2rem;font-weight:800;color:{tier_color};line-height:1;">{score}</div>
                  <div style="font-size:0.7rem;color:#64748b;">/100</div>
                </div>
                <div style="display:inline-block;padding:4px 14px;border-radius:12px;font-size:0.75rem;font-weight:700;color:white;background:{tier_color};">{tier_short}</div>
              </div>
              <div style="font-size:0.78rem;color:{tier_color};font-weight:600;margin-top:6px;">{action}</div>
              <div style="margin-top:8px;background:#e2e8f0;border-radius:8px;height:8px;overflow:hidden;">
                <div style="width:{score_pct}%;height:100%;background:{tier_color};border-radius:8px;"></div>
              </div>
            </div>
            """, unsafe_allow_html=True)

            # Breakdown bars — rendered separately to avoid HTML nesting issues
            dims = [
                ("Price", bd.get("price", 0), 20, "#0ea5e9"),
                ("Quantity", bd.get("quantity", 0), 15, "#7c3aed"),
                ("Scalability", bd.get("scalability", 0), 20, "#16a34a"),
                ("Reliability", bd.get("reliability", 0), 25, "#f59e0b"),
                ("Data", bd.get("data_completeness", 0), 10, "#64748b"),
                ("Triangulation", bd.get("triangulation", 0), 10, "#ec4899"),
            ]
            bar_rows = []
            for dim_name, dim_val, dim_max, dim_color in dims:
                w = int(dim_val / dim_max * 100) if dim_max else 0
                bar_rows.append(
                    f'<tr><td style="font-size:0.72rem;color:#64748b;padding:2px 8px 2px 0;text-align:right;width:85px;">{dim_name}</td>'
                    f'<td style="padding:2px 0;"><div style="background:#e2e8f0;border-radius:4px;height:8px;overflow:hidden;min-width:120px;">'
                    f'<div style="width:{w}%;height:100%;background:{dim_color};border-radius:4px;"></div></div></td>'
                    f'<td style="font-size:0.72rem;font-weight:600;color:#0f172a;padding:2px 0 2px 8px;width:55px;">{dim_val}/{dim_max}</td></tr>'
                )
            st.markdown(
                '<table style="width:100%;max-width:420px;border-collapse:collapse;margin:-0.5rem 0 0.3rem 0;">'
                + "".join(bar_rows)
                + '</table>'
                + (f'<div style="font-size:0.72rem;color:#dc2626;">&#9888; {pen_text}</div>' if pen_text else ''),
                unsafe_allow_html=True,
            )

    _rdb.close()
