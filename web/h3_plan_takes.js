// H3 Clip Plan - takes browser: list the takes of every clip, play their preview, choose one.
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const STYLE = `
.h3t { display: flex; flex-direction: column; gap: 6px; height: 100%; font: 12px system-ui, sans-serif; color: #ddd; box-sizing: border-box; padding: 2px; }
.h3t * { box-sizing: border-box; }
.h3t button { background: #2b2f38; color: #ddd; border: 1px solid #454b58; border-radius: 4px; padding: 2px 8px; cursor: pointer; font: inherit; }
.h3t button:hover:not(:disabled) { border-color: #6aa7ff; }
.h3t button:disabled { opacity: .45; cursor: default; }
.h3t .bar { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.h3t .status { color: #9aa1ad; flex: 1; min-width: 120px; }
.h3t .player { background: #000; border-radius: 4px; display: flex; flex-direction: column; }
.h3t video { width: 100%; max-height: 300px; background: #000; display: block; border-radius: 4px 4px 0 0; }
.h3t .cap { display: flex; gap: 8px; align-items: center; padding: 4px 6px; background: #1b1e24; border-radius: 0 0 4px 4px; }
.h3t .cap .txt { flex: 1; color: #bbb; }
.h3t .list { flex: 1; overflow: auto; border: 1px solid #333844; border-radius: 4px; padding: 4px; min-height: 80px; }
.h3t .row { display: grid; grid-template-columns: 170px 1fr; gap: 6px; padding: 4px 2px; border-bottom: 1px solid #2a2e37; align-items: start; }
.h3t .row .lbl { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.h3t .row .lbl b { color: #fff; }
.h3t .row .why { color: #f0b44c; font-size: 11px; white-space: normal; }
.h3t .chips { display: flex; flex-wrap: wrap; gap: 4px; }
.h3t .chip { border-radius: 10px; padding: 1px 8px; border: 1px solid #454b58; background: #23272f; cursor: pointer; white-space: nowrap; }
.h3t .chip.chosen { background: #2c4f86; border-color: #6aa7ff; color: #fff; }
.h3t .chip.other { opacity: .55; border-style: dashed; }
.h3t .chip.stale { border-color: #ff6b6b; color: #ff9b9b; }
.h3t .chip.playing { outline: 2px solid #fff; }
.h3t .none { color: #777; font-style: italic; }
`;

function addStyle() {
    if (document.getElementById("h3t-style")) return;
    const style = document.createElement("style");
    style.id = "h3t-style";
    style.textContent = STYLE;
    document.head.appendChild(style);
}

function el(tag, attrs = {}, ...kids) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
        if (v === undefined || v === null || v === false) continue;
        if (k === "class") node.className = v;
        else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
        else node.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) if (kid !== null && kid !== undefined && kid !== false) node.append(kid instanceof Node ? kid : String(kid));
    return node;
}

function when(seconds) {
    if (!seconds) return "";
    const d = new Date(seconds * 1000);
    const pad = (n) => String(n).padStart(2, "0");
    return `${pad(d.getDate())}/${pad(d.getMonth() + 1)} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function viewUrl(preview) {
    const q = new URLSearchParams({ filename: preview.filename, subfolder: preview.subfolder || "", type: preview.type || "output" });
    return api.apiURL(`/view?${q.toString()}`);
}

class TakesBrowser {
    constructor(node) {
        this.node = node;
        this.data = null;
        this.playing = null;   // {clip, take}
        this.root = el("div", { class: "h3t" });
        this.status = el("span", { class: "status" }, "");
        this.btnRefresh = el("button", { title: "reload the takes of the project", onclick: () => this.refresh() }, "↻ refresh");
        this.btnPrune = el("button", { title: "delete every take that is not chosen, with its preview", onclick: () => this.prune() }, "delete unchosen takes");
        this.video = el("video", { controls: true, preload: "metadata" });
        this.capText = el("span", { class: "txt" }, "Click a take to play it.");
        this.btnUse = el("button", { disabled: true, onclick: () => this.usePlaying() }, "use this take");
        this.list = el("div", { class: "list" });
        this.root.append(
            el("div", { class: "bar" }, this.btnRefresh, this.btnPrune, this.status),
            el("div", { class: "player" }, this.video, el("div", { class: "cap" }, this.capText, this.btnUse)),
            this.list,
        );
    }

    planFile() {
        return this.node.widgets?.find((w) => w.name === "plan_file")?.value || "";
    }

    async call(path, options) {
        const response = await api.fetchApi(path, options);
        const data = await response.json().catch(() => ({ error: `HTTP ${response.status}` }));
        if (!response.ok || data.error) throw new Error(data.error || `HTTP ${response.status}`);
        return data;
    }

    async refresh() {
        const plan = this.planFile();
        if (!plan || plan.startsWith("(")) { this.status.textContent = "choose a plan"; this.list.replaceChildren(); return; }
        try {
            this.status.textContent = "loading…";
            this.show(await this.call(`/h3clipplan/takes?plan=${encodeURIComponent(plan)}`));
        } catch (error) {
            this.status.textContent = `error: ${error.message}`;
        }
    }

    async usePlaying() {
        if (!this.playing) return;
        try {
            this.show(await this.call("/h3clipplan/select", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ plan: this.planFile(), clip: this.playing.clip, take: this.playing.take }),
            }));
        } catch (error) {
            this.status.textContent = `error: ${error.message}`;
        }
    }

    async prune() {
        if (!confirm("Delete every unchosen take and its preview?\nChosen takes and final videos are kept.")) return;
        try {
            const data = await this.call("/h3clipplan/prune", {
                method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ plan: this.planFile() }),
            });
            this.show(data);
            if (data.pruned) this.status.textContent += ` · ${data.pruned.takes} take(s) deleted, ${data.pruned.megabytes} MB freed`;
        } catch (error) {
            this.status.textContent = `error: ${error.message}`;
        }
    }

    play(clip, take) {
        this.playing = { clip: clip.index, take: take.id };
        if (take.preview) {
            this.video.src = viewUrl(take.preview);
            this.video.play().catch(() => {});
        } else {
            this.video.removeAttribute("src");
            this.video.load();
        }
        const states = { ok: "usable", other: "built on another take of the previous clip", stale: "stale" };
        this.capText.textContent = `clip ${clip.index} ${clip.name} · take ${take.id} · ${states[take.state] || take.state}` +
            (take.preview ? "" : " · no preview") + (take.id === clip.chosen ? " · chosen" : "");
        this.btnUse.disabled = take.id === clip.chosen || take.state === "stale";
        this.render();
    }

    usePlayingLabel() { return this.btnUse; }

    show(data) {
        this.data = data;
        this.status.textContent = `${data.project} · can assemble: clips 1 to ${data.usable} of ${data.count}`;
        if (this.playing) {
            const clip = data.clips.find((c) => c.index === this.playing.clip);
            const take = clip?.takes.find((t) => t.id === this.playing.take);
            if (clip && take) this.btnUse.disabled = take.id === clip.chosen || take.state === "stale";
            else { this.playing = null; this.btnUse.disabled = true; }
        }
        this.render();
    }

    render() {
        if (!this.data) return;
        const rows = this.data.clips.map((clip) => {
            const chips = clip.takes.length
                ? clip.takes.map((take) => el("span", {
                    class: `chip ${take.id === clip.chosen ? "chosen" : take.state}${this.playing && this.playing.clip === clip.index && this.playing.take === take.id ? " playing" : ""}`,
                    title: `${take.id}\nseed ${take.seed}\n${when(take.saved_at)}${take.reason ? "\n" + take.reason : ""}`,
                    onclick: () => this.play(clip, take),
                }, (take.id === clip.chosen ? "✓ " : "") + `s${String(take.seed ?? "?").slice(-6)} · ${when(take.saved_at)}`))
                : [el("span", { class: "none" }, "no take")];
            return el("div", { class: "row" },
                el("div", { class: "lbl", title: clip.name },
                    el("b", {}, `${clip.index} `), (clip.continue ? "↪ " : clip.index > 1 ? "✂ " : "") + clip.name,
                    clip.why ? el("div", { class: "why" }, clip.why) : null),
                el("div", { class: "chips" }, chips));
        });
        this.list.replaceChildren(...rows);
    }
}

app.registerExtension({
    name: "h3clipplan.TakesBrowser",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "H3PlanTakes") return;
        addStyle();
        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = onNodeCreated?.apply(this, arguments);
            const node = this;
            const browser = new TakesBrowser(node);
            node.h3Takes = browser;
            const widget = node.addDOMWidget("takes", "h3_takes", browser.root, { serialize: false, hideOnZoom: false, getMinHeight: () => 420 });
            widget.serialize = false;
            const planWidget = node.widgets?.find((w) => w.name === "plan_file");
            if (planWidget) {
                const callback = planWidget.callback;
                planWidget.callback = function () {
                    const r = callback?.apply(this, arguments);
                    setTimeout(() => browser.refresh(), 50);
                    return r;
                };
            }
            const size = node.computeSize?.() || [560, 520];
            node.setSize([Math.max(size[0], 620), Math.max(size[1], 640)]);
            setTimeout(() => browser.refresh(), 300);
            return result;
        };
    },
    setup() {
        const refreshAll = () => {
            for (const node of app.graph?._nodes || []) node.h3Takes?.refresh();
        };
        api.addEventListener("executed", () => setTimeout(refreshAll, 300));
        api.addEventListener("execution_success", () => setTimeout(refreshAll, 300));
    },
});
