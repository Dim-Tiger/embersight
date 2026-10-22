# EmberSight vs the 2026 Congressional App Challenge (CA-26)

Prepared 2026-10-22 by Claude (AI) for Shaurya, William and Shuntao. Assessed state: `main` at `4b7d23c`, plus what the team told us about the unpushed 2.0. Everything below is advice; you decide, and you write every submitted word yourselves.

**Bottom line.** The app looks and runs like a serious tool, and wildfire is the most local issue a Ventura County judge could hear about. Today the entry would score about **17/30** on the sample rubric, mostly because there is no video yet, the 2.0 code is unpushed, and almost all code on `main` was written by AI or by people outside the team. With the steps below, **24 to 26/30** is realistic by Sunday.

## Sources (cited in brackets below)

- **[R]** 2026 CAC Rules PDF, by section and page. [link](https://www.congressionalappchallenge.us/wp-content/uploads/2026/05/2026-CAC-Rules.pdf)
- **[CA26]** Rep. Brownley's App Challenge page (questions, character limits, video list, past winners). [link](https://juliabrownley.house.gov/services/the-house-student-app-challenge/)
- **[Reg]** National student registration page. [link](https://www.congressionalappchallenge.us/students/student-registration/)
- **[Rub]** 2022 sample rubric: six rows scored 1 to 5, 30 points total. [link](https://www.congressionalappchallenge.us/wp-content/uploads/2022/09/CAC-Judging-Rubric.pdf)
- **[OH13]** OH-13 packet, p.2: the judging questions written out in plain words. [link](https://sykes.house.gov/imo/media/doc/congressional-app-challenge-packet-1.pdf)
- **[B23] [B24] [B25]** Brownley winner releases: [2023](https://juliabrownley.house.gov/brownley-announces-winner-of-the-2023-congressional-app-challenge/), [2024](https://juliabrownley.house.gov/brownley-announces-winners-of-the-2024-congressional-app-challenge/), [2025](https://juliabrownley.house.gov/brownley-announces-winner-of-the-2025-congressional-app-challenge/).

**Corrections to what we thought we knew:**

1. The third CA-26 question asks "What improvements would [you] make if you were going to create a version 2.0?" [CA26]. It asks about the *future*. It does not ask what is new. Show the new 2.0 code in the technical-difficulty answer and in the video. The rule for that is "only the new coding should be highlighted" [R §1 p1].
2. The deadlines disagree. The rules say **12:00 pm EDT Mon Oct 26** (9:00 am PT) [R §5 p3]. The registration page says 9:00 pm ET [Reg]. Plan for the earlier time.
3. Wildfire and evacuation apps have done well here before. The 2024 CA-26 winner was *Sentinel*, which detects wildfires from cameras [B24]. In 2025, *EvacuationHub-DisasterScope* placed 2nd [B25]. The judges have seen this topic, so the difference has to be obvious within seconds.
4. The 2025 panel was Terry Theobald (Ventura County CIO), Joaquin Licea (VCOE CTO) and Dr. Michael Soltys (CSUCI CS) [B25].

## 1. Rubric scorecard

The official criteria are idea, implementation (UX and design), and coding skill [R §7.2 p4]. The sample rubric splits these into six rows [Rub].

| Criterion | What judges look for (source) | Our evidence on `main` today | Score now | Highest-leverage fix | Owner |
|---|---|---|---|---|---|
| **Ideology** (is the need real?) | "Issue or need is extremely relevant" [Rub]. Winners "clearly articulate the issue" [B23] | Wildfire in Ventura County. The README speaks to CAL FIRE incident teams. The local story (2024 Mountain Fire) exists only in the unpushed 2.0 | **4**: very relevant, but the story is generic today | Open with one local, cited fact about the Mountain Fire and one sentence on who suffers when a neighbourhood has a single way out | Team |
| **Impact** | "Strong explanation of its impact" [Rub]. "Compelling value" [B25]. "Applicable for societal use" [OH13 p2] | Human-in-the-loop design (it proposes; a human approves) is real and visible. No user feedback, no measured result | **3**: "impact is broad" [Rub] | Name one decision the app makes faster, for one named role (for example, which neighbourhood to warn first). If an emergency manager ever reacted to it, quote them by name with permission. Never invent a quote | Team |
| **Structure** (the video) | "Video is innovative and engaging" [Rub]. "Most critical component" [R §4 p3]. Pitch "like a Shark Tank presentation" [B25] | No video exists yet | **1**: nothing to score | Record to the beat sheet in section 4 (2:40 to 2:50) | Team |
| **Function** | "Functional with complex features" [Rub] | Briefing ran locally with no API key: 6 of 9 agents done at 90 s, IAP draft waiting for approval. Visible flaws: Terrain says "FBFM40 class is -9999 unknown", IAP shows "confidence 0%", Routing says "no OSM data could be fetched" (screens in PR) | **3**: "works, yet encounters errors" | Record the demo with a working API key on a fire where all 9 agents finish. Have Claude fix the `-9999` and `0%` displays on `main` | Claude (fixes), team (take) |
| **Code** (can you explain it?) | "Explanation of code indicates immense understanding" [Rub]. "In-depth detail on how their algorithm operated" [B23]. Judges may ask for source code [R §7.3 p4] | Most commits carry AI markers (see compliance). The student-written max-flow/min-cut 2.0 is not on GitHub | **2**: little student explanation exists yet | Push 2.0. In the video, one student explains the max-flow/min-cut code on screen for 30 to 40 s in their own words | Team |
| **UI** | "Highly innovative in design and interface" [Rub]. "the winner cannot just present the best-looking application" [B25] | Polished dark map with wind streams, spread cone, infra layers, agent activity, approval queue | **4**: strong; the legend covers the map, and jargon (FBFM40, chains/hr) loses non-experts | Collapse the legend in the demo take. Narrate in plain words. Show the 2.0 "ways out" layer as the hero visual | Team (take), Claude (small UI fixes if asked) |
| **Total** | | | **17/30** | **Sunday target: 25/30** (4, 4, 4, 4, 4, 5) | |

## 2. Compliance checklist

| Rule (source) | Status | Action |
|---|---|---|
| Each member enrolled in middle or high school on Oct 26 [R §1 p1] | Assumed yes | Confirm |
| At least half the team lives **or** attends school in CA-26 [R §1 p1] | **Unverified.** Calabasas ZIP 91302 is split between CA-26 and CA-32 | Look up each home and the school with the 9-digit ZIP on house.gov. Registration requires it anyway [R §2 p1] |
| Team of up to 4; one app per person per year [R §1 p1] | 3 members, so one seat is open | If Dim-Tiger is an eligible student who wrote v1 code, adding them as member 4 is the cleanest fix for authorship. That is a human decision |
| App created after Oct 30, 2025 [R §1 p1] | OK: first commit 2026-05-23 | None |
| Registration: personal (non-school) email, home and school address with ZIP+4, parent name and email [R §2 p1]; eligibility quiz, then an email lets you continue [R §4 p2] | Unknown | Do this **today**. The confirmation email can lag |
| Originality: "all coding... done by the student or student team"; "no other party should have any rights or interest" [R §3 p2] | **Risk.** Commits on `main` (merges included, `git shortlog -sne`): Dim-Tiger (non-team) 44, Claude 26, Shaurya (two identities) 30, MilesG09 (non-team, landing page) 5, William 3. One commit by 苏嘉恒 is on another branch; we don't know who that is | Keep the landing page out of the entry. Say plainly what came from whom. Confirm that the `endeavr.ai` commit email gives no company a claim on the app |
| AI use fully disclosed; AI must not be the entirety; show "significant individual contributions" [R §3 p2] | **Risk.** Across all branches (merges excluded), 26 of the 27 commits under Shaurya's or William's names mention Claude in the message | Add an AI and contributor disclosure to the README, the technical answer and the video. Make the student-written 2.0 the centerpiece |
| 2.0: highlight only the new code [R §1 p1] | The new code is unpushed | Push it before recording the video |
| Libraries documented [R §3 p2] | Partial (README lists stacks) | Add a short "Built with" list: Next.js, React, MapLibre, deck.gl, FastAPI, LangGraph, OSMnx, networkx, Claude API, data sources |
| Video 1 to 3 min, public on YouTube or Vimeo, with names, app name, one-sentence purpose, audience, tools and languages, working app [R §4 p3; CA26] | Not started | Follow the beat sheet. Check it plays in a logged-out window |
| Written answers: 350 / 1,500 / 750 characters [CA26]. The national list has 6 questions [R §4 p3] | Not started | Use the talking points. Check which questions the portal actually shows |
| Source code on request, or disqualification [R §7.3 p4] | Repo is public, no LICENSE, owned by Dim-Tiger | Make sure a team member can share it on request. The README currently fails `uv sync` (readme path) and assumes an API key; the backend fix is in progress |
| No IP violations [R §3 p2] | Map tiles and data are credited in the legend | Use only your own screen footage. News clips need permission |
| Submission locked after deadline [R §4 p2]; each member does the exit survey [R §6 p3] | n/a | Submit Sunday night. Re-read everything before you press submit |

## 3. Top 10 gaps, ranked by impact per hour

| # | Gap | Fix | Hours | Owner | By Sunday? |
|---|---|---|---|---|---|
| 1 | 2.0 code is only on one laptop | Push it to a branch, then merge after review | 0.25 | Shaurya | Yes |
| 2 | Registration and eligibility unconfirmed (ZIP+4, CA-26 split) | Register the team profile; everyone finishes their profile and quiz | 0.5 | All | Yes, start today |
| 3 | AI and outside-contributor use undisclosed | Write a disclosure: what Claude wrote, what Dim-Tiger and MilesG09 wrote, what you wrote. Claude can draft the factual commit table; you write the words | 1 | Team + Claude | Yes |
| 4 | No video (Structure = 1) | Record to the beat sheet. One take for the demo, one for talking | 5 | Team | Yes |
| 5 | Students cannot yet show they understand the code | Each student rehearses explaining max-flow/min-cut and one v1 module (for example spread simulation) for 60 s with no notes | 2 | Each student | Yes |
| 6 | Written answers framed wrong (Q3 is future) | Draft from the talking points; trim to the limits | 2 | Team | Yes |
| 7 | Demo shows `-9999`, `confidence 0%`, routing fallback | Claude fixes the displays on `main`. Record with an API key on a fire that completes | 2 | Claude, then team | Yes |
| 8 | Local relevance is implied but never stated | Pull official Mountain Fire figures from CAL FIRE or Ventura County. Cite them on screen | 1 | Team | Yes |
| 9 | Looks like Sentinel or EvacuationHub at first glance | One line: built for the incident commander, and counts the independent ways out of each neighbourhood | 0.5 | Team | Yes |
| 10 | A judge could not run it from the README | Add a judge quickstart: one command, no-key mode explained, the disclosure, and a link to the video | 1 | Claude | Yes |

Left alone on purpose: rebuilding v1 to raise the student share (no time), polishing the landing page (outside the entry), new agents.

## 4. Talking points (you write the final words)

**Short description: what is it for and why? (350 characters max) [CA26].** Rough budget per point:

- Who it helps (about 70 characters): incident commanders and county emergency managers in the first hours of a wildfire
- The problem (about 110): weather, fuel, roads and people live in different systems; evacuation calls happen under time pressure
- What it does (about 100): one map; an AI team drafts a plan; a human approves every action
- What 2.0 adds (about 70): counts how many separate roads lead out of each neighbourhood, so fragile ones get warned first

**Technical difficulty and how you solved it (1,500 characters max) [CA26].** Keep this to the 2.0 code you wrote yourselves [R §1 p1].

- The question: how many independent ways out does a neighbourhood have? That is the number of edge-disjoint paths, which max-flow with capacity 1 per road computes (Menger's theorem)
- The bottleneck: the min cut names the roads whose loss traps people. Explain how you show that on the map
- What actually broke for you: [your real bug, for example one-way streets, the size of the OSM graph, or where to put the "safe" end of each path]
- How you knew it worked: [your check, for example a hand count on a small neighbourhood, or the Mountain Fire replay]
- One sentence on AI use: which parts an AI assistant helped with, and which parts you wrote and tested by hand

**Version 2.0 improvements (750 characters max) [CA26].** Pick two or three you can explain:

- Live road closures from a public feed, so the ways-out count changes as roads close
- Road capacity (lanes, speed) instead of counting every road as 1
- Testing with a real county emergency manager, and changing what they find confusing
- A phone view for field staff, or Spanish-language alerts

**If the portal shows the six national questions [R §4 p3]:** Inspiration: the Mountain Fire was close to home (only if true for you). Learning: graph algorithms, and why a human must approve AI output.

**Video beat sheet (target 2:40 to 2:50, under 3:00) [R §4 p3; CA26; Rub].**

| Time | Beat | Rubric row | Required item |
|---|---|---|---|
| 0:00 to 0:12 | Title card: EmberSight, your three names, Viewpoint School. One cited local fact about the Mountain Fire | Ideology | Names, app name |
| 0:12 to 0:25 | Say the purpose in one sentence, then who it is for | Ideology, Impact | Purpose, audience |
| 0:25 to 1:05 | Live demo: pick a fire, agents brief, map layers, a human approves the draft | Function, UI | Shows functionality |
| 1:05 to 1:50 | 2.0 on the Mountain Fire replay: one neighbourhood with a single way out, plus the min-cut road highlighted | Function, Impact | Shows functionality |
| 1:50 to 2:25 | A student on camera or voice-over, with code on screen: how max-flow counts the ways out, in your own words | Code | |
| 2:25 to 2:38 | Tools and languages (TypeScript, Python, the libraries), plus one line on AI use | Code | Tools and languages |
| 2:38 to 2:50 | Shark Tank close: the decision it speeds up, and what you would build next | Impact, Structure | |

Video tips: record at 1080p, collapse the legend, use large cursor movements, add captions, and avoid music with lyrics. Watch it once logged out to confirm it is public.

## 5. Never say

- "First", "only", "no one has done this" (Sentinel and EvacuationHub prove the space exists, and nobody can verify these claims)
- "Used by", "partnered with" or "approved by" CAL FIRE, the county or any agency, unless you have it in writing
- "Saves lives", or "would have saved X homes in the Mountain Fire" (a counterfactual nobody can check)
- "Dispatches", "orders" or "evacuates". It proposes, and a human approves
- "Real-time" or "live" for data that is cached or delayed. "Predicts exactly" or any accuracy number you have not measured
- Demo numbers as facts. For example, "6,648 people at risk" came from a no-key fallback run
- "We built all of it ourselves", or "our AI model". You used Claude through an API. You trained nothing
- Anything about the landing page, as if it were your work
- Any quote, statistic or date you cannot link to a source
