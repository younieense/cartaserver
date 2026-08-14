export function $(sel, root = document) {
  return root.querySelector(sel);
}

export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2).toLowerCase(), v);
    else if (v !== false && v != null) node.setAttribute(k, v === true ? "" : v);
  }
  for (const child of [].concat(children)) {
    if (child == null || child === false) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function toast(message) {
  const box = document.getElementById("toast");
  box.textContent = message;
  box.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => {
    box.hidden = true;
  }, 2800);
}

export function moneyNormalize(raw) {
  return String(raw || "").trim().replace(/\s/g, "").replace(",", ".");
}

export function formatRub(value) {
  if (value == null || value === "") return "";
  const n = Number(value);
  if (Number.isNaN(n)) return String(value);
  return `${n.toFixed(2)} руб.`;
}

export function toolbar({ title, back, connected }) {
  return el("div", { class: "toolbar" }, [
    el("div", { class: `status-dot${connected ? " on" : ""}`, title: connected ? "Подключено" : "Нет связи" }),
    back ? el("button", { class: "btn-back", type: "button", text: "←", onClick: back }) : null,
    el("h1", { class: "toolbar-title", text: title }),
  ]);
}

export function showModal({ title, body, buttons }) {
  const modal = document.getElementById("modal");
  $("#modalTitle").textContent = title || "";
  const bodyEl = $("#modalBody");
  bodyEl.innerHTML = "";
  if (typeof body === "string") bodyEl.textContent = body;
  else if (body) bodyEl.append(body);
  const actions = $("#modalActions");
  actions.innerHTML = "";
  for (const b of buttons || []) {
    actions.append(
      el("button", {
        class: `btn${b.primary ? " primary-fill" : ""}`,
        type: "button",
        text: b.label,
        onClick: async () => {
          modal.hidden = true;
          if (b.onClick) await b.onClick();
        },
      })
    );
  }
  modal.hidden = false;
}

export function closeModal() {
  document.getElementById("modal").hidden = true;
}

export function downloadBase64(filename, b64, mime) {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  const blob = new Blob([bytes], {
    type: mime || "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  });
  const url = URL.createObjectURL(blob);
  if (navigator.share && navigator.canShare?.({ files: [new File([blob], filename, { type: blob.type })] })) {
    navigator
      .share({
        files: [new File([blob], filename, { type: blob.type })],
        title: filename,
      })
      .catch(() => triggerDownload(url, filename));
  } else {
    triggerDownload(url, filename);
  }
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

function triggerDownload(url, filename) {
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
}

export function dateIso(d = new Date()) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

export function dateDisplay(iso) {
  const [y, m, d] = iso.split("-");
  return `${d}.${m}.${y}`;
}

export function monthAgoIso() {
  const d = new Date();
  d.setMonth(d.getMonth() - 1);
  return dateIso(d);
}
