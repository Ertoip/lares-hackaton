FINCANTIERI DMM  •  Maritime Security Hackathon  •  Rome 2026 

**Challenge DMM** 

**MARITIME SECURITY HACKATHON** 

## **Single-Operator Command & Control platform** 

## **for Multi-Domain Unmanned Operations** 

_Area: Command & Control for Manned-Unmanned Teaming & Minimal-Crew Mission Workspace_ 

## **Problem Statement** 

Build a single-operator command interface and information management system that allows intuitive tasking, monitoring and re-planning of a heterogeneous unmanned team operating across air, surface and subsurface domains, even when individual assets experience degraded performance or intermittent communications. 

At the same time, the system must function as an intelligent bridge between multiple data sources that dynamically prioritizes, filters and presents multi-source data to a single watch officer, adapting to context, threat level and operator cognitive state. 

## **What you're actually building** 

You are building a Decision Support System (DSS) for a single human operator. The operator has to direct a small fleet of unmanned vehicles — drones, robotic boats, underwater robots — across a complex environment, and gets flooded with telemetry, sensor data and alerts. Your software has to: 

- **Filter and rank** incoming events so the operator see the right thing first. 

- **Estimate and propagate uncertainty** honestly — when the system doesn't know something, the operator must see that. 

- **Suggest re-tasking and re-planning** when vehicles fail, comms drop, or threats appear — with explanations. 

- **Keep the human in the loop** — recommend, don't decide. The operator picks; your software argues the case. Every recommendation must be explained and correlated by its assumptions and reason in operator readable language. 

There is no real classified data and there is no pre-event dataset. You will assemble a credible data picture from public APIs and self-generated synthetic feeds during the 48 hours, with your assigned mentor as a domain expert in the loop. 

## **Glossary — Naval domain in plain English** 

Quick reference: every term in this list appears later in the document. If you have not worked on naval applications, skim this page first — then come back as needed. The brief uses full names alongside acronyms the first time each one appears, so you can also read straight through. 

Page 1 of 12  •  Challenge DMM — Single-Operator C2 for Multi-Domain Unmanned Operations 

FINCANTIERI DMM  •  Maritime Security Hackathon  •  Rome 2026 

|||
|---|---|
|**Term**|**What it means in this challenge**|
|||
|||
|**AIS**|Automatic Identification System: a radio transponder ships use to broadcast their<br>identity, position and course. Required by international rules on most commercial<br>vessels — but easy to switch off or spoof.|
|||
|**ASW / MCM**|Anti-Submarine Warfare / Mine Counter-Measures. Detect submarines / sea mines.|
|||
|**AUV / UUV**|Autonomous (or Uncrewed) Underwater Vehicle. A robotic submarine, either pre-<br>programmed or remotely controlled.|
|||
|||
|**CIC**|Combat Information Centre — the room on a warship where operators monitor sensors<br>and run tactical operations. The "ops room".|
|||
|||
|**DSS**|Decision Support System: software that helps a human make better decisions, faster, by<br>filtering, ranking and explaining options. The thing you're building.|
|||
|||
|**ESM / ELINT**|Electronic Support Measures / Electronic Intelligence. Passive listening for enemy radars<br>and emissions.|
|||
|||
|**EW / GPS denial /**<br>**spoofing**|Electronic Warfare: jamming or fooling electronics. GPS denial = signal blocked. GPS<br>spoofing = fake signals make receivers report wrong positions.|
|||
|||
|**INS**|Inertial Navigation System: estimates position from gyros and accelerometers when GPS<br>is unavailable. Drifts over time.|
|||
|||
|**ISR**|Intelligence, Surveillance, Reconnaissance — gathering information about an area or<br>target.|
|||
|||
|**IPMS**|Integrated Platform Management System: onboard system controlling the ship's<br>machinery — the "OS" for ship hardware.|
|||
|||
|**METL**|Mission Essential Task List: the prioritised, deadline-tagged list of things the mission<br>must accomplish.|
|||
|||
|**RMP**|Recognized Maritime Picture: the unified tactical map of all known objects in the area,<br>fused from multiple sensors.|
|||
|||
|**ROE**|Rules of Engagement: the rules that say what the operator and ship are allowed to do.|
|||
|||
|**UAV / VTOL UAV**|Uncrewed Aerial Vehicle (a drone). VTOL = Vertical Takeoff and Landing — like a<br>quadcopter or tiltrotor.|
|||
|||
|**USV**|Uncrewed Surface Vehicle. A robotic boat operating on the water surface.|
|||
|||
|**Acoustic link**|Underwater communication via sound waves. Slow (a few kbps), high-latency (10–60 s),<br>unreliable in bad water conditions.|
|||
|||
|**Bingo**|Fuel/battery threshold below which a vehicle MUST start returning home, or it won't<br>make it back.|
|||
|||
|**Dark vessel**|Ship operating with AIS off or spoofed. A red flag in maritime surveillance.|
|||
|||
|**Gray-zone**<br>**operations**|Activities below the threshold of open war: surveillance, harassment, sabotage,<br>deniable incidents.|
|||
|||
|**Mothership**|The ship that carries, launches, recovers and controls the smaller unmanned vehicles.|
|||



Page 2 of 12  •  Challenge DMM — Single-Operator C2 for Multi-Domain Unmanned Operations 

FINCANTIERI DMM  •  Maritime Security Hackathon  •  Rome 2026 

|||
|---|---|
|**Term**|**What it means in this challenge**|
|||
|||
|**Operator / Watch**<br>**officer**|The single human in front of the console. The user of your software.|
|||
|||
|**Sea state**|0–9 scale of sea roughness. 0 = mirror calm, 4–5 = working limit for small uncrewed<br>boats, 9 = phenomenal storm.|
|||
|||
|**Side-scan sonar**|Sonar that scans the seafloor sideways from a moving platform. Used for mines, cables,<br>wreck mapping.|
|||
|||
|**Submarine cable**|Real-world critical infrastructure: ~600 cables on the seabed carry ~99% of<br>intercontinental internet traffic.|
|||
|||
|**Thermocline**|Layer in the sea where temperature changes sharply with depth. It bends or blocks<br>sound, breaking acoustic communications.|
|||



## **1.  Where this software runs (operational context)** 

Modern warships are designed to operate with much smaller crews than a generation ago — what would have needed 200 people now needs 80, and what needed 80 now needs 30. Fewer humans means each one handles more decisions, more sensor feeds, more responsibility. This challenge is about one specific role on those ships: the operator who manages the fleet of unmanned vehicles. 

The class of ships we have in mind is the new generation that Fincantieri designs and builds for European navies — patrol vessels, corvettes, future logistics and mine counter-measures motherships. You don't need to know the ship details. What matters is that these ships are designed to: 

- **Carry and operate a fleet of unmanned vehicles** (drones, robotic boats, underwater robots) — the ship is their base, launching them, recovering them, controlling them, processing what they observe. 

- **Operate in contested coastal regions** — Central Mediterranean, Black Sea, Baltic, Strait of Hormuz, Gulf of Guinea. There's no open war, but there's constant activity: surveillance, interference, accidents, deniable incidents ("gray-zone" operations). 

- **Protect critical underwater infrastructure** — the cables and pipelines on the seabed that carry internet traffic, electricity, gas. Recent Baltic incidents (2023–2025) have made this a daily concern. 

## **What the environment is like** 

- **GPS unreliable.** Adversaries jam GPS signals (denial) or broadcast fake ones (spoofing). Vehicles must keep operating without it. 

- **Radio is contested.** Jamming and interference on UHF/VHF and satellite links is normal, not exceptional. 

- **Underwater communication is bad.** Radio doesn't propagate underwater. UUVs talk via acoustic links — slow (a few kbps), high-latency (10–60 s), and they drop out completely when the water is too noisy or has the wrong temperature profile (a thermocline). 

- **Many overlapping concerns.** Civilian shipping with disabled transponders ("dark vessels"), small fast attack craft, possible adversary underwater robots — all in the same picture as the friendly vehicles you're trying to control. 

Page 3 of 12  •  Challenge DMM — Single-Operator C2 for Multi-Domain Unmanned Operations 

FINCANTIERI DMM  •  Maritime Security Hackathon  •  Rome 2026 

In the ship's operations room (the Combat Information Centre, CIC), one operator is dedicated full-time to the unmanned fleet, while other operators handle navigation, the ship's own defenses, and communications with the chain of command. Today's military software was designed for an era when ships had a couple of drones at most. It doesn't scale to a heterogeneous fleet of six or eight vehicles in three different domains. That's the gap your DSS fills. 

## **2.  What the operator actually struggles with** 

Five real-world tensions the operator lives every shift. Your DSS will be judged on whether it addresses these — not on whether it generically helps. If the demo doesn't visibly tackle at least three of these, the jury will notice. 

||||
|---|---|---|
|**#**|**Operational tension**|**What it actually looks like**|
||||
|**P1**|Information overload & alert<br>fatigue|Without smart filtering, the operator's screen becomes a firehose:<br>hundreds of alerts in 30 minutes, of which 95% are noise. The operator<br>saturates, real warnings get lost in the queue. Your software must<br>reduce information volume, not increase it.|
||||
|**P2**|Decisions on partial and stale<br>data|An underwater robot can be out of contact for 25–40 minutes during a<br>seafloor scan. When it surfaces, the operator must interpret a state<br>that is part historical and part probabilistic. Today's tools just show a<br>"last known position" pin — they don't say how old it is, how confident<br>it is, or which actions are still valid.|
||||
|**P3**|Reaction time doesn't scale|Three robotic boats in three different sectors all detect suspicious<br>contacts at the same time. One human can't redirect them<br>sequentially without losing the others. Without automated<br>prioritisation, the operator either freezes on one or fires off shallow<br>decisions on all three.|
||||
|**P4**|Different domains have<br>different physics|Drones (radio link, ms latency, reliable), surface boats (satellite or relay<br>link, seconds latency), underwater robots (acoustic link, tens of<br>seconds, asynchronous): a command sent to an underwater robot is<br>not the same as one to a drone. The interface must make this<br>difference visible.|
||||
|**P5**|Knowing when to give up<br>gracefully|Mission resilience is not about completing every task — it's about<br>knowing when to abandon one task to save the rest. (Example: pull<br>back a UUV near its battery limit even if 15% of cable is uninspected,<br>because losing the UUV would be worse.) The operator needs help<br>quantifying that trade-off.|



## **3.  Decisions the DSS has to support** 

"Mission resilience" is the high-level goal, but it doesn't tell you what to model. We've broken it down into six concrete decision classes. Your DSS doesn't have to address all six — but it must address at least three, and for each one it does address, you must point at the model and say what objective function it optimises. We are not asking for generic AI — we are asking for an identifiable decision engine with declared objectives. 

Page 4 of 12  •  Challenge DMM — Single-Operator C2 for Multi-Domain Unmanned Operations 

FINCANTIERI DMM  •  Maritime Security Hackathon  •  Rome 2026 

|||||
|---|---|---|---|
|**ID**|**Decision class**|**Objective function (what your model**<br>**optimises)**|**Expected output**|
|||||
|**D1**|Re-tasking when<br>something breaks|Maximise the number of mission tasks<br>still covered, given degraded or<br>unavailable vehicles|Ranked list of {vehicle →<br>task} reassignments with<br>coverage scores|
|||||
|**D2**|Re-planning the whole<br>mission|Minimise loss of mission value (weighted<br>sum of task priorities) under time,<br>energy, risk constraints|Revised mission plan: which<br>tasks survive, which are<br>dropped, with explicit<br>trade-off|
|||||
|**D3**|Triaging the alert queue|Rank events by mission relevance ×<br>criticality × time-to-act|Top-N queue presented<br>one at a time, in<br>manageable order|
|||||
|**D4**|Knowing what the system<br>doesn't know|Track and propagate uncertainty over<br>each vehicle's state and over the mission|State estimate with<br>confidence interval /<br>probable-position cone /<br>freshness indicator|
|||||
|||||
|**D5**|Showing the operator the<br>trade-offs|Surface Pareto-optimal options across<br>time, energy, coverage, risk|Interactive Pareto front the<br>operator can explore<br>before committing|
|||||



## **4.  The three predefined degradation scenarios** 

Three pre-scripted scenarios. Your DSS must demonstrate handling **at least one** end-to-end (must-have); handling all three is a strong differentiator at jury demo (should-have). Each scenario is a finite-state machine with a defined trigger, time window, mission impact, and the decision classes (from §3) it requires the DSS to address. The descriptions in the table below give you trigger times and event sequences sufficient to script your own injection timeline in code; ask your mentor to refine specific parameters as needed. 

|||||
|---|---|---|---|
|**Scenario**|**Trigger and dynamics**|**Mission impact if**<br>**unmanaged**|**Decision**<br>**classes**|
|||||
||Underwater robot inspecting a seabed cable loses<br>its acoustic link 18 minutes into the run (water<br>turbidity rises and ambient noise spikes). Last<br>known position and intended track are known.<br>Forecast: ~25 min of blackout before next surfacing<br>window.|3 km of cable not<br>inspected; the<br>robot itself may be<br>unrecoverable.|**D4 + D1**|
|**A — Underwater**<br>**contact lost**||||
|||||
||USV-1, while running ISR, spots a fast inflatable<br>boat at minute 22 — no AIS, behaviour suggests<br>hostile intent. Two minutes later, USV-2 reports a<br>sensor failure (debris impact, reduced capability<br>set).|Loss of awareness<br>over two sectors at<br>once; potential<br>exposure of the<br>mothership.|**D3 + D1 +**<br>**D2**|
|**B — Threat**<br>**appears +**<br>**vehicle damaged**||||
|||||



Page 5 of 12  •  Challenge DMM — Single-Operator C2 for Multi-Domain Unmanned Operations 

FINCANTIERI DMM  •  Maritime Security Hackathon  •  Rome 2026 

|||||
|---|---|---|---|
|**Scenario**|**Trigger and dynamics**|**Mission impact if**<br>**unmanaged**|**Decision**<br>**classes**|
|||||
||Local GPS jamming starts at minute 30, affecting 2<br>drones and 1 USV in the same sector. They fall back<br>to inertial navigation; position drift accumulates at<br>~50 m/min.|Relative positions<br>degrade — risk of<br>vehicles colliding<br>with each other;<br>loss of targeting<br>accuracy.|**D4 + D1**|
|**C — GPS**<br>**jammed,**<br>**vehicles drift**||||
|||||



These three cover the main ways things go wrong in the real world: communication links break (Scenario A), threats appear while vehicles are already degrading (Scenario B), electronic warfare disrupts navigation (Scenario C). At demo time the jury can ask you to run any of the three live. That's the moment your DSS has to actually work, not just look like it works. 

## **5.  Mission-critical main parameters your DSS model must consume** 

Independently of where the data comes from (public APIs, self-generated synthetic, mentor-mediated), the DSS model is considered robust only if it integrates the following main parameters across three families. If your model uses none of these, it's not a DSS — it's a dashboard. It is possible to consider other further parameters if considered useful for your DSS model. 

## **Per-vehicle state vector** 

- Position (x, y, z) with uncertainty σ; velocity vector; heading. 

- Fuel or battery percentage; sensor health; payload state. 

- Link quality: timestamp of last contact, expected next contact window, link type, residual bandwidth. 

- Capability vector: what this vehicle can still do given its current state. (Example: USV-2, after a debris impact, can still do visual ISR but no longer passive sonar.) 

## **Mission state** 

- Ordered task list (METL) with priorities and deadlines. 

- Active rules of engagement. 

- Geofenced areas (operating zones, no-go zones). 

- Bingo (return-home) thresholds for each vehicle. 

## **Tactical environment (the maritime picture)** 

- Environmental Conditions. 

- AIS contacts (commercial ships broadcasting their identity). 

- Radar tracks (objects detected by sensors, with or without identity). 

- Passive electronic intelligence events (ESM/ELINT — detected radio or radar emissions from other ships). 

- Acoustic detections (relevant when underwater robots are involved). 

- Classification confidence per contact. 

- Behaviour flags: cooperative, suspicious, hostile. 

Page 6 of 12  •  Challenge DMM — Single-Operator C2 for Multi-Domain Unmanned Operations 

FINCANTIERI DMM  •  Maritime Security Hackathon  •  Rome 2026 

## **6. Data strategy: how to get what you need during the 48 hours** 

**No pre-event dataset. By design.** 

Real classified telemetry, real Command and Control Communication, real intelligence — none of it can be released for IP, ITAR and operational reasons. More importantly: assembling a credible data picture from public, synthetic and human sources is exactly the skill the operational world needs from this challenge. Your data picture comes from three tiers, used together: public APIs (real, live), **synthetic feeds you generate yourselves** , and your assigned mentor as domain expert in the loop. 

## **6.1 Tier 1 — Public APIs (real, live, free)** 

You can pull real maritime data live during the event. The table below lists curated sources we recommend. All registrations are free and self-service: the "Auth" column shows what each requires. Sign-up flows take 1–10 minutes per service — allocate the first hour of the hackathon to provisioning credentials before any coding (§6.4 has a practical onboarding sequence). 

||||||
|---|---|---|---|---|
|**Source**|**What you get**|**Auth**|**Format / access**|**Use it for**|
||||||
|**AISStream.io**|Real-time global AIS traffic —<br>every commercial ship<br>broadcasting position, course,<br>speed, identity.|Free key<br>(1 min<br>signup)|WebSocket, JSON|Populating the<br>tactical map with<br>real ship traffic.|
||||||
|**Copernicus**<br>**Marine Service**|Oceanography: waves,<br>currents, sea state, salinity,<br>sound velocity profile, sea<br>surface temperature.|Free,<br>registered|REST + Zarr /<br>NetCDF|Conditioning<br>vehicle physics on<br>real sea state and<br>acoustic<br>propagation.|
||||||
|**Open-Meteo**<br>**Marine**|Wave height, period, direction<br>at any lat-lon. Marine weather<br>forecast.|None|REST, JSON|Quick environment<br>baseline without<br>registration friction.|
||||||
|**TeleGeography**<br>**Submarine Cable**<br>**Map**|Real routes of the ~600<br>submarine cables carrying<br>~99% of intercontinental<br>internet traffic.|None<br>(public<br>web)|Web map / scrape|Anchoring cable-<br>protection<br>scenarios in real<br>infrastructure.|
||||||
|**OpenSeaMap /**<br>**OpenStreetMap**|Nautical charts, ports,<br>lighthouses, navigation aids.|None|Tile server /<br>Overpass API|Map base layer<br>with maritime<br>context.|
||||||
|**Sentinel Hub**<br>**(Copernicus)**|Optical and radar satellite<br>imagery (Sentinel-1 SAR,<br>Sentinel-2 multispectral).|Free tier|REST, OGC WMS|Adding satellite-<br>based detection of<br>dark vessels / vessel<br>wakes.|
||||||
|**OpenAIP**|Airspace structure, restricted<br>zones, aviation hazards.|Free key|REST, JSON|UAV mission<br>planning over<br>realistic airspace.|



Page 7 of 12  •  Challenge DMM — Single-Operator C2 for Multi-Domain Unmanned Operations 

FINCANTIERI DMM  •  Maritime Security Hackathon  •  Rome 2026 

_You are not limited to this list. Useful AIS fallbacks if AISStream flakes during demo: Datalastic, VesselFinder, MarineTraffic API. Other sources fair game: NOAA NDBC (buoys), EMODnet (European marine data), Global Fishing Watch (vessel behaviour patterns), ECMWF Open Data (weather). The list above is the curated subset we have validated to work reliably for hackathon demos._ 

## **6.2  Tier 2 — Synthetic data you generate yourselves** 

Some data simply does not exist publicly: vehicle telemetry, link quality time series, internal sensor states, threat events. You have to model and generate these yourselves. This is not a workaround — it is the modelling skill the jury is testing. A good DSS demo includes a credible, parametric synthetic data generator. A bad one consumes random noise and pretends. 

- **Vehicle telemetry.** Define your own data schema for the vehicle state vector — see §5 for the required fields — and build a mock generator emitting at realistic rates: UAV update rate 5–10 Hz, USV update rate 0.5–2 Hz, UUV asynchronous when submerged. Ask your mentor for finer-grained ranges (battery decay, sensor degradation, link drop probabilities). 

- **Link quality time series.** Model acoustic links with stochastic drop probability conditional on sea state and depth. Sound velocity profiles can be sourced live from Copernicus Marine Service for a chosen lat-lon. 

- **Threat events.** Scripted using the three pre-defined degradation scenarios (§4). The descriptions in §4 give you the trigger times and event sequences needed to write your own injection timeline in code (Python list of timestamped events, JSON file, whatever you prefer — the format is your choice). 

- **Sensor health states.** Simple state machines with capability vectors that degrade on event injection. 

- **Operator workload proxy.** If you target the adaptive HMI should-have, model workload as a function of alert rate, decision latency, and number of active vehicles. 

## **7.  Objectives** 

Design an onboard asset capable of showing and enabling: 

## **1.  Unified Operational Situational Awareness** 

- A unified map display showing the position and status of all vehicles, including estimated localization for platforms operating with intermittent or degraded communications (probable-position cone, freshness indicator). 

- Intuitive task assignment via drag-and-drop or equivalent direct-manipulation mechanisms — allocation of operational areas, waypoints, or specific targets to individual vehicles. 

- Capability to handle at least one of the three predefined degradation scenarios (§4) end-to-end, with automated system-generated re-planning proposals to ensure mission continuity. 

- Identifiable decision engine addressing at least three of the five decision classes (D1–D5, §3), with declared objective functions documented in the README. 

## **2.  Unified Info Dashboard for Event Prioritization and Triage** 

- Unified dashboard integrating at least three distinct must-have data sources — one per axis: vehicles, environment/contacts, comms/threats. 

- Dynamic prioritization of information based on operational relevance and urgency — visible to the operator, with the ranking criterion explainable on demand. 

Page 8 of 12  •  Challenge DMM — Single-Operator C2 for Multi-Domain Unmanned Operations 

FINCANTIERI DMM  •  Maritime Security Hackathon  •  Rome 2026 

- Automated triage capability handling three or more simultaneous events, ranked by criticality and presented to the operator in a sequential, manageable manner. 

- Explanation layer: every recommendation accompanied by its assumptions and reasoning in operator-readable language. 

## **8.  Success criteria** 

## **Must-have** 

- Unified map showing every vehicle's position and status, including degraded-comms estimation (probable-position cone, freshness). 

- Direct task assignment (drag-and-drop or equivalent). 

- At least three distinct data sources integrated, one per axis (vehicles / environment / commsthreats). 

- Identifiable decision engine addressing at least three of D1–D5, with declared objective functions. 

- End-to-end demo of the DSS handling at least one of the three predefined degradation scenarios. 

- Triage capability: three or more simultaneous events ranked by criticality, presented one at a time. 

## **Should-have** 

- DSS handles all three degradation scenarios. 

- Explanation layer with operator-readable rationale per recommendation. 

- Adaptive interface: density, font, salience respond to operator workload or threat level. 

- Pareto-front visualization of trade-offs (D5) for at least one decision class. 

## **9. The demo moment we want to see** 

Live run of Scenario B (Threat Pop-up + Vehicle Damage). At minute 22 the DSS surfaces the threat and shows the operator three ranked options, each with explicit trade-offs (cover the threat with a drone, accept an ISR gap on USV-2's sector, abort a secondary task). At minute 24, when USV-2 starts degrading, the DSS automatically updates the recommendation and revises the trade-off front. The operator clicks one option; the DSS shows the new mission plan: what was kept, what was sacrificed, and the resulting confidence level on each surviving task. All in under 90 seconds. The judges should lean forward when they realize: this is one human, orchestrating six robotic vehicles across three domains, and they can follow every decision the system suggested. Every recommendation must be explained and correlated by its assumptions and reason. 

## **10. Constraints and out of scope** 

## **Constraints** 

- Software runs on standard hardware (laptop, single screen of maximum 26 in). No specialized military equipment. 

- Synthetic and public data only. Real classified or industrial-IP feeds are prohibited. 

- Every recommendation the DSS produces must be explainable in plain language. Black-box outputs without justification will not score. 

- Resilience by design: the DSS must keep working when at least one data source drops at runtime. 

## **Out of scope** 

- Designing the unmanned vehicles themselves. You operate above the vehicle abstraction layer. 

Page 9 of 12  •  Challenge DMM — Single-Operator C2 for Multi-Domain Unmanned Operations 

FINCANTIERI DMM  •  Maritime Security Hackathon  •  Rome 2026 

- Real-time integration with operational Combat Management Systems. 

- Weapons employment or targeting logic. The DSS supports mission resilience, not engagement decisions. 

- Voice or natural-language interfaces. Interesting, but unscoped within 48 hours. 

## **11.  Mentor and how to start** 

Each team is supported by mentors combining naval operational background with one software-side domain (human factors / UX, multi-agent systems, or operations research). Mentors clarify operational context and validate that what you're building makes sense. They will not prescribe specific algorithms or write code for you. Use them aggressively in the first 12 hours to make sure your scoping is right. 

## **Common pitfalls to avoid** 

- Building a beautiful dashboard with no decision engine underneath. "A map is not a DSS." 

- Treating all three vehicle domains identically. Drones, surface boats, underwater robots have completely different communication physics — the operator must see that. 

- Hiding uncertainty. Operators need to see when the system is unsure. False precision kills trust. 

- Optimising for one degradation scenario only. The jury will test you on the others. 

- Skipping the explanation layer. Recommendations without rationale will not score on operational realism. 

- Waiting for data. There is no dataset. Start the synthetic generators in hour 1 and iterate. 

Page 10 of 12  •  Challenge DMM — Single-Operator C2 for Multi-Domain Unmanned Operations 

FINCANTIERI DMM  •  Maritime Security Hackathon  •  Rome 2026 

**Challenge x and y** 

**DMM HACKATHON** 

_The merged challenge above can also be tackled as two distinct sub-challenges, originally conceived as Challenge X (the multi-vehicle command interface) and Challenge Y (the operator console and triage layer). Teams may target one stream or both — the merged version remains the integrated form the jury rewards highest. Each sub-challenge inherits the operational context (§1), painpoints (§2), data strategy (§6), and constraints (§10) from the merged document._ 

## **C H A L LE NGE  ST A TE ME N T  X** 

## **A single operator orchestrating drones, robotic boats** 

## **and underwater vehicles via a drone gateway** 

_Area: Command & Control for Manned-Unmanned Teaming_ 

## **Problem** 

Build a single-operator command interface that enables intuitive tasking, monitoring and re-planning of a heterogeneous unmanned team (air + surface + subsurface) when individual vehicles degrade or lose connectivity. The interface must make the different communication physics of each domain (radio, satellite, acoustic) explicit to the operator, and provide automated re-planning when assets fail. 

## **Objective** 

Design an onboard decision asset able to show and enable: 

- Operational situational awareness through a unified map showing position and status of all vehicles, including estimated localisation for platforms with intermittent or degraded communications (probable-position cone, freshness indicator). 

- Intuitive task assignment via drag-and-drop, allocating operational areas, waypoints or targets to individual vehicles. 

- Capability to handle at least one of the three predefined degradation scenarios (Scenario A — underwater contact lost; Scenario C — GPS jammed and vehicles drift) with automated, explainable re-planning proposals to ensure mission continuity. 

- Identifiable decision engine addressing at least D1 (re-tasking) and D4 (uncertainty propagation) from the decision classes catalogue. 

## **Data strategy notes for X** 

- Vehicle telemetry: synthetic. Define your own schema using the §5 required fields, build a mock generator emitting at realistic rates. 

- Tactical map background: live AIS via AISStream + nautical chart base layer (OpenSeaMap). 

- Acoustic link quality: stochastic model conditioned on Copernicus Marine sound velocity profile + sea state. 

Page 11 of 12  •  Challenge DMM — Single-Operator C2 for Multi-Domain Unmanned Operations 

FINCANTIERI DMM  •  Maritime Security Hackathon  •  Rome 2026 

- Mentor on-demand: realistic ranges for vehicle dynamics, link drop rates, recovery procedures. 

## **C H A L LE NGE  ST A TE ME N T  Y** 

## **Human-Machine Interface for a low-manned** 

## **operational console** 

_Area: Minimal-Crew Mission Workspace_ 

## **Problem** 

Build an intelligent bridge information management system that dynamically prioritizes, filters and presents multi-source data to a single watch officer, adapting to context, threat level and operator cognitive state. The system must reduce information volume, not increase it, and must explain why it shows what it shows. 

## **Objective** 

Design an onboard asset able to show and enable: 

- Unified dashboard integrating at least three distinct must-have data sources, with dynamic prioritization based on operational relevance and urgency. 

- Automated triage capability handling three or more simultaneous events, ranked by criticality and presented to the operator in a sequential, manageable manner. 

- Explanation layer: every prioritisation decision is accompanied by its rationale, accessible on demand. 

- Identifiable decision engine addressing at least D3 (information triage) and one of D2 (re-planning) or D6 (trade-off visualisation). 

## **Data strategy notes for Y** 

- Tactical contacts: live AIS feed via AISStream — real ship traffic populates the picture. 

- Ship platform health (IPMS): synthetic — generate plausible alert streams for propulsion, power, fluids. 

- Threat warnings (ESM/ELINT events): synthetic — script timed event injections according to Scenario B. 

- Operator workload proxy (optional, for adaptive HMI): self-defined function of alert rate and decision latency. 

- Mentor on-demand: realistic alert frequencies, typical operator triage time per event, IPMS event taxonomy. 

_— End of challenge brief —_ 

_During the event: your assigned mentor is your first contact_ 

Page 12 of 12  •  Challenge DMM — Single-Operator C2 for Multi-Domain Unmanned Operations 

