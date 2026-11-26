// Routify – Karte: Leaflet, Hintergrundkarten, Kartenleiste, Höhenprofil
// (klassisches Skript; gemeinsame Namen liegen global, Reihenfolge siehe index.html)

// ---------- Karte
const map = L.map("map", { zoomControl: true }).setView([51.2, 10.4], 6);
const OSM_ATTR = '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>-Mitwirkende';
const DATA_ATTR = ' · Daten: <a href="https://www.bkg.bund.de" target="_blank">BKG</a> (POI-Open dl-de/by-2-0), OpenStreetMap (ODbL), DWD';
const BKG_ATTR = '© <a href="https://gdz.bkg.bund.de">BKG</a> TopPlusOpen';
const topplus = (style) => `https://sgx.geodatenzentrum.de/wmts_topplus_open/tile/1.0.0/${style}/default/WEBMERCATOR/{z}/{y}/{x}.png`;

// ---------- Hintergrundkarten (Auswahl über Vorschaubilder) und Ebenen (runde Buttons) – siehe Kartenleiste unten
const vorschau = (url) => url.replace("{s}", "a").replace("{z}", "7").replace("{x}", "67").replace("{y}", "42");  // Ausschnitt Mitteldeutschland
const CYCLOSM = "https://{s}.tile-cyclosm.openstreetmap.fr/cyclosm/{z}/{x}/{y}.png";
const OSM_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const BASIS = [
  // Vorschaubilder liegen lokal (static/vorschau/) – die Kartendienste werden erst bei Auswahl aufgerufen
  { name: "Fahrradkarte", info: "CyclOSM", vorschau: "/static/vorschau/cyclosm.png",
    layer: L.tileLayer(CYCLOSM, { maxZoom: 20, subdomains: "abc", attribution: `${OSM_ATTR} · Stil <a href="https://www.cyclosm.org">CyclOSM</a>${DATA_ATTR}` }) },
  { name: "Amtlich", info: "TopPlusOpen (BKG)", vorschau: "/static/vorschau/topplus.png",
    layer: L.tileLayer(topplus("web"), { maxZoom: 18, attribution: BKG_ATTR + DATA_ATTR }) },
  { name: "Amtlich grau", info: "TopPlusOpen (BKG)", vorschau: "/static/vorschau/topplus_grau.png",
    layer: L.tileLayer(topplus("web_grau"), { maxZoom: 18, attribution: BKG_ATTR + DATA_ATTR }) },
  { name: "OSM", info: "OpenStreetMap", vorschau: "/static/vorschau/osm.png",
    layer: L.tileLayer(OSM_URL, { maxZoom: 19, attribution: OSM_ATTR + DATA_ATTR }) },
];
let basisAktiv = null;
// Standard ist die Fahrradkarte; die anderen Karten sind wählbar, werden aber nicht gemerkt.
const STANDARDKARTE = "Fahrradkarte";
const ERSATZKARTE = "Amtlich";
const KACHEL_TIMEOUT_MS = 5000;
const fahrradLayer = BASIS[0].layer;
let kachelTimeout;

function stoppeKachelpruefung() {
  clearTimeout(kachelTimeout);
}

fahrradLayer.on("loading", () => {
  stoppeKachelpruefung();
  kachelTimeout = setTimeout(() => {
    if (basisAktiv?.layer === fahrradLayer) setBasis(ERSATZKARTE);
  }, KACHEL_TIMEOUT_MS);
});
fahrradLayer.on("tileload remove", stoppeKachelpruefung);

function setBasis(name) {
  const b = BASIS.find(x => x.name === name) || BASIS[0];
  if (basisAktiv && basisAktiv !== b) map.removeLayer(basisAktiv.layer);
  if (!map.hasLayer(b.layer)) b.layer.addTo(map);
  b.layer.bringToBack && b.layer.bringToBack();
  basisAktiv = b;
  renderKartenwahl();
}

// Optionale Karten mit Schlüssel (.env)
fetch("/api/config").then(r => r.json()).then(cfg => {
  if (!cfg.thunderforest_key) return;
  const url = `https://{s}.tile.thunderforest.com/cycle/{z}/{x}/{y}.png?apikey=${cfg.thunderforest_key}`;
  BASIS.push({ name: "OpenCycleMap", info: "Thunderforest", vorschau: vorschau(url),
    layer: L.tileLayer(url, { maxZoom: 22, subdomains: "abc", attribution: `Karte © <a href="https://www.thunderforest.com">Thunderforest</a>, ${OSM_ATTR}${DATA_ATTR}` }) });
  renderKartenwahl();
}).catch(() => {});

const layers = {
  routes: L.layerGroup().addTo(map),
  stages: L.layerGroup().addTo(map),
  unterkunft: L.layerGroup().addTo(map),
  bahnhof: L.layerGroup(),  // Notausstiege: nur auf Wunsch (Anfrage oder Klick)
  rad: L.layerGroup(),
  wunsch: L.layerGroup().addTo(map),
  radfernwege: L.tileLayer("https://tile.waymarkedtrails.org/cycling/{z}/{x}/{y}.png", {
    maxZoom: 18, opacity: .8, attribution: '<a href="https://cycling.waymarkedtrails.org">Waymarked Trails</a> (CC-BY-SA)' }),
  drouten: L.tileLayer.wms("https://www.radroutenplaner-deutschland.de/geoserver/rrpd/wms", {
    layers: "droute", format: "image/png", transparent: true, opacity: .7, attribution: "Radnetz Deutschland © BALM" }),
};
let hoverMarker = null;

// ---------- Kartenleiste: runde Ebenen-Buttons + Kartenauswahl mit Vorschau
const svg = (d) => `<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
const EBENEN = [
  { key: "unterkunft", label: "Unterkünfte",
    ic: svg('<path d="M3 18V7"/><path d="M3 14h18v4"/><path d="M21 14v-2a3 3 0 0 0-3-3h-7v5"/><circle cx="7" cy="11" r="1.6"/>') },
  { key: "wunsch", label: "Stopps & Auswahl",
    ic: svg('<path d="M12 21s-6-5.6-6-11a6 6 0 0 1 12 0c0 5.4-6 11-6 11z"/><circle cx="12" cy="10" r="2.2"/>') },
  { key: "bahnhof", label: "Notausstiege (Bahnhöfe ≤ 5 km)",
    ic: svg('<rect x="6" y="3" width="12" height="13" rx="3"/><path d="M6 10h12"/><circle cx="9.5" cy="13" r=".6"/><circle cx="14.5" cy="13" r=".6"/><path d="M8 20l2-4M16 20l-2-4"/>') },
  { key: "rad", label: "Fahrradläden & Verleih",
    ic: svg('<circle cx="6" cy="16" r="3.5"/><circle cx="18" cy="16" r="3.5"/><path d="M6 16l4-7h5l3 7M10 9l2 7h-6M14 6h2"/>') },
  { key: "radfernwege", label: "Radfernwege",
    ic: svg('<path d="M4 20c3-4 5-6 8-6s4-3 4-6 2-4 4-4"/><path d="M4 20h0M20 4h0"/>') },
  { key: "drouten", label: "D-Routen (Radnetz Deutschland)",
    ic: '<span class="mb-d">D</span>' },
];
const leiste = L.control({ position: "topright" });
leiste.onAdd = () => {
  const d = L.DomUtil.create("div", "mapbar");
  d.innerHTML = `
    <button class="mb-btn karte" id="kartenBtn" title="Hintergrundkarte wählen"><span class="mb-thumb" id="kartenThumb"></span><span class="mb-tip">Karte</span></button>
    <div class="mb-sep"></div>
    ${EBENEN.map(e => `<button class="mb-btn" data-l="${e.key}"><span class="mb-ic">${e.ic}</span><span class="mb-tip">${e.label}</span></button>`).join("")}
    <div class="mb-pop" id="kartenPop" hidden></div>`;
  d.querySelectorAll("[data-l]").forEach(b => b.onclick = () => {
    const k = b.dataset.l;
    if (k === "bahnhof") { setBahn(!bahnZeigen); return; }
    map.hasLayer(layers[k]) ? map.removeLayer(layers[k]) : layers[k].addTo(map);
  });
  d.querySelector("#kartenBtn").onclick = (e) => { e.stopPropagation(); const p = $("kartenPop"); p.hidden = !p.hidden; };
  L.DomEvent.disableClickPropagation(d);
  L.DomEvent.disableScrollPropagation(d);
  return d;
};
leiste.addTo(map);
// Leiste schwebt über der Karte (liegt in <main>, nicht im Leaflet-Container)
const leisteEl = document.querySelector(".mapbar");
leisteEl.classList.add("schwebend");
document.querySelector("main").appendChild(leisteEl);
map.on("click", () => { const p = $("kartenPop"); if (p) p.hidden = true; });

function syncLeiste() {
  document.querySelectorAll(".mapbar [data-l]").forEach(b => b.classList.toggle("on", map.hasLayer(layers[b.dataset.l])));
}
map.on("layeradd layerremove", syncLeiste);

function renderKartenwahl() {
  const pop = $("kartenPop");
  if (!pop) return;
  pop.innerHTML = `<div class="mb-poptitle">Hintergrundkarte</div><div class="mb-grid">${BASIS.map(b => `
    <button class="mb-card ${basisAktiv === b ? "on" : ""} ${b.fehlt ? "aus" : ""}" data-b="${esc(b.name)}" ${b.fehlt ? `disabled title="Nicht verfügbar: ${esc(b.info)}"` : ""}>
      ${b.vorschau ? `<img src="${b.vorschau}" alt="">` : `<span class="mb-noimg">nicht verfügbar</span>`}
      <b>${esc(b.name)}</b><small>${esc(b.info)}</small></button>`).join("")}</div>`;
  pop.querySelectorAll("[data-b]").forEach(c => c.onclick = () => { setBasis(c.dataset.b); pop.hidden = true; });
  const th = $("kartenThumb");
  if (th && basisAktiv) th.style.backgroundImage = basisAktiv.vorschau ? `url("${basisAktiv.vorschau}")` : "linear-gradient(135deg,#e8ecef,#cfd8dc)";
}
BASIS.forEach(b => { if (b.vorschau) new Image().src = b.vorschau; });
setBasis(STANDARDKARTE);
syncLeiste();

// ---------- Höhenprofil
const chart = new Chart($("elev"), {
  type: "line",
  data: { datasets: [{ data: [], borderColor: COLORS[0], backgroundColor: "rgba(31,122,77,.12)", fill: true, pointRadius: 0, borderWidth: 1.5, tension: .2 }] },
  options: {
    maintainAspectRatio: false, animation: false, parsing: false,
    interaction: { mode: "nearest", axis: "x", intersect: false },
    plugins: { legend: { display: false }, tooltip: {
      mode: "x", intersect: false,
      callbacks: { title: (i) => `km ${i[0].parsed.x.toFixed(1)}`, label: (i) => `${i.dataset.label}: ${Math.round(i.parsed.y)} m ü. NN` } } },
    scales: {
      x: { type: "linear", title: { display: false }, ticks: { callback: v => v + " km", maxTicksLimit: 12 }, grid: { display: false } },
      y: { ticks: { callback: v => v + " m", maxTicksLimit: 5 } },
    },
    onClick: (e, els) => {
      if (!data) return;
      const hit = chart.getElementsAtEventForMode(e, "nearest", { intersect: false }, false)[0];
      if (hit) { const j = chart.data.datasets[hit.datasetIndex].altIdx; if (data.alternativen[j] !== current) showAlt(j); }
    },
    onHover: (e) => {
      if (!current) return;
      const km = chart.scales.x.getValueForPixel(e.x);
      if (km < 0 || km > current.laenge_km) return;
      const pt = pointAtKm(km);
      if (!pt) return;
      if (!hoverMarker) hoverMarker = L.circleMarker(pt, { radius: 7, color: "#fff", weight: 2, fillColor: "#e67e22", fillOpacity: 1 }).addTo(map);
      else hoverMarker.setLatLng(pt);
    },
  },
  plugins: [{ id: "stageLines", afterDraw(c) {
    if (!current) return;
    const { ctx, chartArea: a, scales: { x } } = c;
    ctx.save(); ctx.strokeStyle = "rgba(0,0,0,.35)"; ctx.setLineDash([4, 4]); ctx.fillStyle = "#555"; ctx.font = "11px system-ui";
    current.etappen.forEach(s => {
      const px = x.getPixelForValue(s.bis_km);
      if (!s.letzte) { ctx.beginPath(); ctx.moveTo(px, a.top); ctx.lineTo(px, a.bottom); ctx.stroke(); }
      ctx.fillText(`Tag ${s.nr}`, x.getPixelForValue((s.von_km + s.bis_km) / 2) - 16, a.top + 12);
    });
    ctx.restore();
  } }],
});
$("elev").parentElement.addEventListener("mouseleave", () => { if (hoverMarker) { map.removeLayer(hoverMarker); hoverMarker = null; } });

function pointAtKm(km) {
  // Geometrie ist gleichmäßig ausgedünnt; Position über Anteil an Gesamtlänge annähern
  const g = current.geometrie, f = km / current.laenge_km;
  return g[Math.min(g.length - 1, Math.round(f * (g.length - 1)))];
}
