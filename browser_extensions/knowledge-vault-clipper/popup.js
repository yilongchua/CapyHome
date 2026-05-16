const apiBaseInput = document.getElementById("apiBase");
const topicInput = document.getElementById("topic");
const notesInput = document.getElementById("notes");
const clipButton = document.getElementById("clipButton");
const statusNode = document.getElementById("status");

async function loadSettings() {
  const saved = await chrome.storage.local.get(["apiBase"]);
  if (saved.apiBase) {
    apiBaseInput.value = saved.apiBase;
  }
}

function setStatus(message, isError = false) {
  statusNode.textContent = message;
  statusNode.style.color = isError ? "#b91c1c" : "#57534e";
}

function extractPagePayload() {
  const selection = window.getSelection ? String(window.getSelection()).trim() : "";
  const title = document.title || location.href;
  const article = document.querySelector("article, main");
  const bodyText = (selection || article?.innerText || document.body.innerText || "").trim();
  const lines = [`# ${title}`, "", `Source URL: ${location.href}`, ""];
  if (selection) {
    lines.push("## Selected Text", "", selection, "");
  }
  if (bodyText) {
    lines.push("## Page Content", "", bodyText);
  }
  return {
    url: location.href,
    title,
    markdown: lines.join("\n").trim(),
  };
}

clipButton.addEventListener("click", async () => {
  try {
    clipButton.disabled = true;
    setStatus("Clipping page...");
    const apiBase = apiBaseInput.value.trim().replace(/\/$/, "");
    await chrome.storage.local.set({ apiBase });

    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id) {
      throw new Error("No active tab found.");
    }

    const [result] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: extractPagePayload,
    });
    const payload = result?.result;
    if (!payload?.url || !payload?.markdown) {
      throw new Error("Could not extract page content.");
    }

    const notes = notesInput.value.trim();
    const markdown = notes ? `## Operator Notes\n\n${notes}\n\n${payload.markdown}` : payload.markdown;
    const response = await fetch(`${apiBase}/api/vault/clip`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url: payload.url,
        title: payload.title,
        markdown,
        topic: topicInput.value.trim(),
      }),
    });
    if (!response.ok) {
      const details = await response.text();
      throw new Error(details || `HTTP ${response.status}`);
    }
    setStatus("Page queued for vault ingestion.");
  } catch (error) {
    setStatus(error instanceof Error ? error.message : "Clip failed.", true);
  } finally {
    clipButton.disabled = false;
  }
});

void loadSettings();
