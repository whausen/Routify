// Routify – Konto: Anmeldung, gespeicherte Touren, Voreinstellungen, Verlauf
// (klassisches Skript; gemeinsame Namen liegen global, Reihenfolge siehe index.html)

// ---------- Konto: Anmeldung, gespeicherte Touren, Voreinstellungen, Verlauf
let ich = null, kontoTab = "touren";
const api = (url, methode = "GET", daten) => fetch(url, { method: methode, headers: { "Content-Type": "application/json" },
  body: daten ? JSON.stringify(daten) : undefined }).then(async r => ({ ok: r.ok, ...(await r.json().catch(() => ({}))) }));

function kontoKnopf() {
  const b = $("kontoBtn");
  b.classList.toggle("an", !!ich);
  b.innerHTML = ICON_NUTZER;  // nur Symbol – der Name stünde sonst über dem Logo
  b.title = ich ? `${ich.name}: Meine Touren, Voreinstellungen, Verlauf` : "Anmelden";
}

function loginZeigen(hinweis) {
  $("lgFehler").textContent = hinweis || "";
  $("loginModal").hidden = false;
  kontoCaptchaLaden().catch(() => {
    $("lgFehler").textContent = "Die Sicherheitsprüfung konnte nicht geladen werden.";
  });
  setTimeout(() => $("lgName").focus(), 50);
}
let kontoCaptchaInitialisierung, kontoCaptchaWidget;
function kontoCaptchaLaden() {
  kontoCaptchaInitialisierung ??= (async () => {
    const cfg = await api("/api/config");
    if (!cfg.ok) throw new Error("Kontoeinstellungen konnten nicht geladen werden");
    if (!cfg.turnstile_site_key) return;
    if (!window.turnstile) {
      await new Promise((resolve, reject) => {
        const script = document.createElement("script");
        script.src = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
        script.onload = resolve;
        script.onerror = () => { script.remove(); reject(new Error("CAPTCHA-Skript nicht verfügbar")); };
        document.head.append(script);
      });
    }
    kontoCaptchaWidget = window.turnstile.render("#lgCaptcha", {
      sitekey: cfg.turnstile_site_key,
      action: "konto",
      size: "flexible",
    });
  })().catch(error => {
    kontoCaptchaInitialisierung = undefined;
    throw error;
  });
  return kontoCaptchaInitialisierung;
}

let kontoAnfrageLaeuft = false;
async function loginAusfuehren(aktion) {
  if (kontoAnfrageLaeuft) return;
  kontoAnfrageLaeuft = true;
  let r;
  let captchaGesendet = false;
  try {
    await kontoCaptchaLaden();
    const captcha = kontoCaptchaWidget === undefined ? "" : window.turnstile.getResponse(kontoCaptchaWidget);
    if (kontoCaptchaWidget !== undefined && !captcha) {
      $("lgFehler").textContent = "Bitte schließe zuerst die Sicherheitsprüfung ab.";
      return;
    }
    captchaGesendet = !!captcha;
    r = await api(`/api/konto/${aktion}`, "POST", {
      name: $("lgName").value, passwort: $("lgPw").value, captcha_token: captcha,
    });
  } catch {
    $("lgFehler").textContent = "Die Kontoanfrage ist fehlgeschlagen. Bitte versuche es erneut.";
    return;
  } finally {
    kontoAnfrageLaeuft = false;
    if (captchaGesendet) window.turnstile.reset(kontoCaptchaWidget);
  }
  if (!r.ok) { $("lgFehler").textContent = r.fehler || "Das hat nicht geklappt."; return; }
  ich = { name: r.name, voreinst: r.voreinst || {} };
  $("loginModal").hidden = true; $("lgPw").value = "";
  kontoKnopf();
  addMsg(`${aktion === "registrieren" ? "Konto angelegt" : "Angemeldet"} als <b>${esc(ich.name)}</b>. Touren kannst du jetzt mit „☆ Speichern“ sichern.`);
}
$("loginForm").onsubmit = (e) => { e.preventDefault(); loginAusfuehren("anmelden"); };
$("lgReg").onclick = () => { if ($("loginForm").reportValidity()) loginAusfuehren("registrieren"); };
$("lgZu").onclick = () => { $("loginModal").hidden = true; };
$("kontoBtn").onclick = () => {
  if (!ich) { loginZeigen(); return; }
  const k = $("konto"); k.hidden = !k.hidden;
  if (!k.hidden) { renderKonto(); $("planscroll").scrollTop = 0; }
};

async function renderKonto() {
  const k = $("konto");
  if (!ich || k.hidden) return;
  const tab = (id, label) => `<button class="${kontoTab === id ? "on" : ""}" onclick="kontoTab='${id}'; renderKonto()">${label}</button>`;
  let inhalt = "";
  if (kontoTab === "touren") {
    const r = await api("/api/touren");
    const liste = r.touren || [];
    inhalt = liste.length ? liste.map(t => `<div class="ktour">
        <b>${esc(t.name)}</b>
        <span class="src">${[t.kurz.km ? `${t.kurz.km} km` : "", t.kurz.tage ? `${t.kurz.tage} Tag${t.kurz.tage > 1 ? "e" : ""}` : "",
          t.kurz.hm != null ? `${t.kurz.hm} Hm` : "", new Date(t.geaendert * 1000).toLocaleDateString("de-DE")].filter(Boolean).join(" · ")}</span>
        ${t.notiz ? `<div class="src">📝 ${esc(t.notiz)}</div>` : ""}
        <div class="kact">
          <button class="link" onclick="tourOeffnen(${t.id})"><b>Öffnen</b></button>
          <button class="link" onclick="tourUmbenennen(${t.id})">Umbenennen</button>
          <button class="link" onclick="tourNotiz(${t.id})">Notiz</button>
          <button class="link" onclick="tourLoeschen(${t.id})" style="color:#b3261e">Löschen</button>
        </div></div>`).join("") : `<div class="src">Noch keine Touren gespeichert. Plane eine Tour und klicke auf „☆ Speichern“.</div>`;
    kontoTouren = liste;
  } else if (kontoTab === "voreinst") {
    const v = ich.voreinst || {};
    const uk = v.unterkunft_typ || [];
    inhalt = `<div class="src" style="margin-bottom:8px">Gelten bei neuen Anfragen nur, wenn du dazu nichts sagst.</div>
      <div class="fgrid">
        <label>Zielgruppe<select id="v_gruppe"><option value="">– keine –</option>${opt(ZIELGRUPPEN, v.zielgruppe || "")}</select></label>
        <label>Fahrrad<select id="v_rad"><option value="">– keine –</option>${opt(RAEDER, v.fahrrad || "")}</select></label>
        <label>km pro Tag<input type="number" id="v_km" min="10" max="300" step="5" value="${v.km_pro_tag ?? ""}" placeholder="–"></label>
        <label class="full">Unterkunft<span class="checks">${Object.entries({ hotel: "Hotel", guest_house: "Pension", hostel: "Hostel", camp_site: "Camping" })
          .map(([k2, l]) => `<label><input type="checkbox" name="v_uk" value="${k2}" ${uk.includes(k2) ? "checked" : ""}>${l}</label>`).join("")}</span></label>
      </div>
      <button class="go" style="margin-top:10px;width:100%" onclick="voreinstSpeichern()">Speichern</button>`;
  } else {
    const r = await api("/api/verlauf");
    inhalt = (r.verlauf || []).length ? r.verlauf.map(v => `<div class="kverlauf" onclick="send(${esc(JSON.stringify(v.text))})" title="Nochmal planen">↻ ${esc(v.text)}</div>`).join("")
      : `<div class="src">Noch keine Anfragen.</div>`;
  }
  k.innerHTML = `<div class="khead"><b class="kname">${ICON_NUTZER} ${esc(ich.name)}</b>
      <span><button class="link" onclick="abmelden()">Abmelden</button> · <button class="link" onclick="$('konto').hidden=true">Schließen</button></span></div>
    <div class="tabs">${tab("touren", "Meine Touren")}${tab("voreinst", "Voreinstellungen")}${tab("verlauf", "Verlauf")}</div>${inhalt}`;
}
let kontoTouren = [];

async function tourSpeichern() {
  if (!ich) { loginZeigen("Zum Speichern bitte anmelden oder kurz registrieren."); return; }
  const vorschlag = `${data.orte[0].label.split(",")[0]} → ${data.orte.at(-1).label.split(",")[0]}${current.etappen.length > 1 ? `, ${current.etappen.length} Tage` : ""}`;
  const name = prompt("Name der Tour:", vorschlag);
  if (name === null) return;
  const r = await api("/api/touren", "POST", { name: name || vorschlag, params: { ...data.params, wetter_tag: data.startdatum },
    variante: aktuelleVariante(), kurz: { km: Math.round(current.laenge_km), tage: current.etappen.length, hm: current.anstieg_m } });
  if (r.ok) { addMsg(`☆ Tour „${esc(name || vorschlag)}“ gespeichert – zu finden über das Konto-Symbol oben rechts → Meine Touren.`); renderKonto(); }
  else addMsg(esc(r.fehler || "Speichern fehlgeschlagen."), "err");
}
function tourOeffnen(id) {
  const t = kontoTouren.find(x => x.id === id);
  if (!t) return;
  $("konto").hidden = true;
  // mit aktuellen Daten neu planen (Wetter, Unterkünfte), gespeicherte Variante zuerst
  const p = { ...t.params, nur_variante: t.variante, bestaetigt: true };
  const heute = new Date().toISOString().slice(0, 10);
  if (p.startdatum && p.startdatum < heute) { delete p.startdatum; delete p.wetter_tag; }  // Datum liegt in der Vergangenheit
  send(`📂 ${t.name}`, p);
}
async function tourUmbenennen(id) {
  const t = kontoTouren.find(x => x.id === id); const n = prompt("Neuer Name:", t ? t.name : "");
  if (n) { await api(`/api/touren/${id}`, "PATCH", { name: n }); renderKonto(); }
}
async function tourNotiz(id) {
  const t = kontoTouren.find(x => x.id === id); const n = prompt("Notiz zur Tour:", t ? t.notiz : "");
  if (n !== null) { await api(`/api/touren/${id}`, "PATCH", { notiz: n }); renderKonto(); }
}
async function tourLoeschen(id) {
  const t = kontoTouren.find(x => x.id === id);
  if (confirm(`Tour „${t ? t.name : ""}“ löschen?`)) { await api(`/api/touren/${id}`, "DELETE"); renderKonto(); }
}
async function voreinstSpeichern() {
  const r = await api("/api/konto/voreinstellungen", "PUT", { zielgruppe: $("v_gruppe").value || null, fahrrad: $("v_rad").value || null,
    km_pro_tag: $("v_km").value ? Number($("v_km").value) : null,
    unterkunft_typ: [...document.querySelectorAll('input[name="v_uk"]:checked')].map(i => i.value) });
  if (r.ok) { ich.voreinst = r.voreinst; addMsg("⚙️ Voreinstellungen gespeichert – gelten ab der nächsten neuen Anfrage."); renderKonto(); }
}
async function abmelden() {
  await api("/api/konto/abmelden", "POST");
  ich = null; $("konto").hidden = true; kontoKnopf();
  addMsg("Abgemeldet.");
}
api("/api/konto").then(r => { if (r.angemeldet) ich = { name: r.name, voreinst: r.voreinst || {} }; kontoKnopf(); });
