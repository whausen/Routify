// Routify – Start: Eingabe, Spracheingabe, Direktlinks
// (klassisches Skript; gemeinsame Namen liegen global, Reihenfolge siehe index.html)

// ---------- Eingabe
const LEERZUSTAND = $("result").innerHTML;  // Startansicht der Planung (für „Neue Planung“)
// Eingabefeld wächst beim Tippen mit (bis ca. 8 Zeilen)
const autoHoehe = () => { const t = $("input"); t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight + 2, 200) + "px"; };
$("input").addEventListener("input", autoHoehe);
// „Planen“ bzw. Enter: läuft gerade eine Sprachaufnahme, wird sie erst beendet und fertig erkannt
const absenden = async () => { if (sprache.laeuft) await aufnahmeStoppen(); send($("input").value); };
$("form").onsubmit = (e) => { e.preventDefault(); absenden(); };
$("input").onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); absenden(); } };

$("neu").onclick = () => {
  kontext = null; data = null; current = null; selection = {}; setBahn(false); renderExpert();
  $("input").placeholder = "Wohin soll es gehen?";
  Object.values(layers).forEach(l => l.clearLayers());
  chart.data.datasets = []; chart.update(); $("profLbl").textContent = "Höhenprofil"; $("profil").classList.add("leer");
  $("result").innerHTML = LEERZUSTAND; kartenVarianten(-1);
  addMsg("Okay, neue Planung. Wohin soll es gehen?");
  map.setView([51.2, 10.4], 6);
};

// Direktlink für Demo/Tests: http://localhost:8000/?q=3 Tage von ...
const urlParams = new URLSearchParams(location.search);
if (urlParams.get("experte") === "1" || urlParams.get("einstellungen") === "1") { expertAn = true; renderExpert(); }  // ?einstellungen=1
if (["wegtyp", "untergrund"].includes(urlParams.get("farbe"))) setColorMode(urlParams.get("farbe"));  // ?farbe=untergrund
// Mehrere q-Parameter werden nacheinander gesendet (Demo von Änderungen): ?q=Mainz nach Koblenz&q=lieber 3 Tage
const linkOrt = (wert) => (wert || "").trim().slice(0, 160);
const linkStart = linkOrt(urlParams.get("start"));
const linkZiel = linkOrt(urlParams.get("ziel"));
if (linkStart && linkZiel) {
  const profil = urlParams.get("profil");
  const kategorien = urlParams.getAll("poi");
  send(`${linkStart} → ${linkZiel}`, {
    start: linkStart,
    ziel: linkZiel,
    profil: ["trekking", "safety", "fastbike", "gravel", "mtb", "shortest"].includes(profil) ? profil : "trekking",
    zwischenziele: urlParams.getAll("via").slice(0, 5).map(linkOrt).filter(Boolean),
    vorlieben: [...new Set(urlParams.getAll("vorliebe"))].filter(v => Object.hasOwn(VORLIEBE_LABEL, v)),
    poi_wuensche: ["fahrradladen", "verleih"].filter(k => kategorien.includes(k)).map(kategorie => ({kategorie})),
    bahnhoefe_zeigen: kategorien.includes("bahnhof"),
    nur_variante: 0,
  });
} else {
  (async () => { for (const q of urlParams.getAll("q")) await send(q); })();
}

// ---------- Spracheingabe: lokal mit Whisper (POST /api/sprache) – das Audio verlässt den Rechner nicht
// Während der Aufnahme wird alle ~2,5 s der bisherige Mitschnitt erkannt (Text erscheint fast live),
// beim Stoppen bzw. „Planen“ ein letztes Mal vollständig.
const sprache = { rec: null, stream: null, teile: [], vorher: "", laeuft: false, busy: false, timer: null, typ: "" };
const micLabel = (t) => { const s = $("mic").querySelector("span"); if (s) s.textContent = t; };

async function spracheErkennen() {
  if (!sprache.teile.length) return null;
  const blob = new Blob(sprache.teile, { type: sprache.typ || "audio/webm" });
  const r = await fetch("/api/sprache", { method: "POST", headers: { "Content-Type": blob.type }, body: blob });
  const j = await r.json().catch(() => ({}));
  if (!r.ok || j.fehler) throw new Error(j.fehler || "Spracherkennung fehlgeschlagen");
  return j.text || "";
}
function spracheText(text) {
  if (text == null) return;
  $("input").value = [sprache.vorher, text].filter(Boolean).join(" ");
  autoHoehe();
}
async function zwischenstand() {
  if (!sprache.laeuft || sprache.busy) return;
  sprache.busy = true;
  try { spracheText(await spracheErkennen()); } catch (e) { /* Zwischenstände dürfen scheitern */ }
  sprache.busy = false;
}

async function aufnahmeStarten() {
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    addMsg("Dieser Browser kann kein Audio aufnehmen. Bitte die Anfrage eintippen.", "err"); return;
  }
  try {
    sprache.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
  } catch (e) {
    addMsg("Kein Zugriff aufs Mikrofon – bitte im Browser erlauben (Schloss-Symbol in der Adresszeile).", "err"); return;
  }
  sprache.typ = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"].find(t => MediaRecorder.isTypeSupported(t)) || "";
  sprache.rec = new MediaRecorder(sprache.stream, sprache.typ ? { mimeType: sprache.typ } : undefined);
  sprache.typ = sprache.rec.mimeType || sprache.typ;
  sprache.teile = []; sprache.vorher = $("input").value.trim(); sprache.laeuft = true;
  sprache.rec.ondataavailable = (e) => { if (e.data.size) sprache.teile.push(e.data); };
  sprache.rec.start(500);
  sprache.timer = setInterval(zwischenstand, 2500);
  $("mic").classList.add("rec"); micLabel("Stopp"); $("mic").title = "Aufnahme läuft – klicken zum Beenden (oder gleich „Planen“)";
  $("input").focus();
}

// Beendet die Aufnahme und setzt den vollständig erkannten Text ein. Gibt erst zurück, wenn der Text da ist.
async function aufnahmeStoppen() {
  if (!sprache.laeuft) return;
  sprache.laeuft = false; clearInterval(sprache.timer);
  await new Promise(ok => { sprache.rec.onstop = ok; sprache.rec.stop(); });
  sprache.stream.getTracks().forEach(t => t.stop());
  $("mic").classList.remove("rec"); micLabel("erkenne …"); $("mic").disabled = true;
  while (sprache.busy) await new Promise(r => setTimeout(r, 100));  // laufenden Zwischenstand abwarten
  try { spracheText(await spracheErkennen()); }
  catch (e) { addMsg(esc(e.message), "err"); }
  finally { $("mic").disabled = false; micLabel("Sprechen"); $("mic").title = "Spracheingabe (lokal, Whisper)"; }
}

$("mic").title = "Spracheingabe (lokal, Whisper)";
$("mic").onclick = () => (sprache.laeuft ? aufnahmeStoppen() : aufnahmeStarten());
// Mikrofon gibt es im Browser nur über HTTPS oder localhost – über die Netzadresse (QR-Code) also ausblenden
if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) $("mic").hidden = true;

// ---------- Spalten „Chat“ und „Aktuelle Planung“ einklappen (mehr Platz für die Karte)
function spalteUmschalten(spalte) {
  const app = document.querySelector(".app");
  app.classList.toggle("zu-" + spalte);
  // Karte und Höhenprofil an die neue Breite anpassen
  const anpassen = () => { map.invalidateSize(); if (typeof chart !== "undefined" && chart) chart.resize(); };
  requestAnimationFrame(anpassen); setTimeout(anpassen, 250);  // zweimal: nach dem Umbruch und nach dem Nachladen der Kacheln
}
document.querySelectorAll(".zuklappen, .rail").forEach(b => b.addEventListener("click", () => spalteUmschalten(b.dataset.spalte)));
