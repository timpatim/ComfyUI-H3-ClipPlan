// H3 Clip Plan - pick or drop a file on the plan and voice nodes.
// Plans (.json) are uploaded to ComfyUI/input/plans/, voice samples to ComfyUI/input/voices/,
// then selected in the node's file list.
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const PLAN = {
    widget: "plan_file",
    subfolder: "plans",
    accept: ".json,application/json",
    pattern: /\.json$/i,
    label: "choose plan file (.json)",
    what: "a .json plan file",
};
const VOICE = {
    widget: "voice_file",
    subfolder: "voices",
    accept: ".wav,.mp3,.flac,.ogg,.m4a,.aac,.opus,audio/*",
    pattern: /\.(wav|mp3|flac|ogg|m4a|aac|opus)$/i,
    label: "choose audio file",
    what: "an audio file (wav, mp3, flac, ogg, m4a, aac, opus)",
};
const NODES = {
    H3PlanClip: PLAN,
    H3PlanAssemble: PLAN,
    H3PlanTimeline: PLAN,
    H3PlanTakes: PLAN,
    H3PlanVoice: VOICE,
};

async function upload(file, kind) {
    const body = new FormData();
    body.append("image", file, file.name);      // ComfyUI's upload route stores any file under this field name
    body.append("subfolder", kind.subfolder);
    body.append("type", "input");
    body.append("overwrite", "true");
    const response = await api.fetchApi("/upload/image", { method: "POST", body });
    if (response.status !== 200) {
        throw new Error(`upload failed (${response.status} ${response.statusText})`);
    }
    const saved = await response.json();
    return saved.subfolder ? `${saved.subfolder}/${saved.name}` : saved.name;
}

function select(node, widget, value) {
    const values = widget.options?.values;
    if (Array.isArray(values) && !values.includes(value)) values.push(value);
    widget.value = value;
    widget.callback?.(value);
    node.setDirtyCanvas?.(true, true);
}

async function handleFile(node, widget, file, kind) {
    if (!file || !kind.pattern.test(file.name || "")) {
        app.extensionManager?.toast?.add?.({ severity: "warn", summary: "H3 Clip Plan", detail: `Choose ${kind.what}.`, life: 4000 });
        return false;
    }
    try {
        select(node, widget, await upload(file, kind));
        return true;
    } catch (error) {
        console.error("H3 Clip Plan:", error);
        alert(`H3 Clip Plan: could not upload the file.\n${error.message}\n\nYou can also copy it into ComfyUI/input/${kind.subfolder}/ and press R to refresh the list.`);
        return false;
    }
}

app.registerExtension({
    name: "h3clipplan.FileUpload",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        const kind = NODES[nodeData.name];
        if (!kind) return;

        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = onNodeCreated?.apply(this, arguments);
            const node = this;
            const widget = node.widgets?.find((w) => w.name === kind.widget);
            if (!widget) return result;

            const picker = document.createElement("input");
            picker.type = "file";
            picker.accept = kind.accept;
            picker.style.display = "none";
            picker.addEventListener("change", async () => {
                if (picker.files?.length) await handleFile(node, widget, picker.files[0], kind);
                picker.value = "";
            });
            document.body.appendChild(picker);

            const button = node.addWidget("button", kind.label, null, () => picker.click());
            button.serialize = false;
            button.options = { ...(button.options ?? {}), serialize: false };

            node.onDragOver = function (event) {
                const items = event?.dataTransfer?.items;
                return !!items && [...items].some((item) => item.kind === "file");
            };
            node.onDragDrop = function (event) {
                const file = [...(event?.dataTransfer?.files ?? [])].find((f) => kind.pattern.test(f.name || ""));
                if (!file) return false;
                handleFile(node, widget, file, kind);
                return true;
            };

            const onRemoved = node.onRemoved;
            node.onRemoved = function () {
                picker.remove();
                return onRemoved?.apply(this, arguments);
            };
            return result;
        };
    },
});
