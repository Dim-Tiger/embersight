Pass 2 hooks (next):

Replace agents/*.run stub bodies with real LLM + tool orchestration using langchain.chat_models.init_chat_model (Sonnet 4.5 for master_ic + spread, Haiku 4.5 elsewhere).
Implement tools/herbie_wx.py, tools/synoptic_raws.py, tools/landfire.py, tools/pyretechnics_spread.py, tools/buildings.py, tools/infra.py, tools/routing.py.
Wire deck.gl MapboxOverlay onto the MapLibre map for perimeters, spread cones, evac zones, FIRMS hotspots, and structures-at-risk heatmap.
Materialize Tremor BarList / AreaChart / Metric tiles in the Resources / Weather / Threats tabs from state.outputs.