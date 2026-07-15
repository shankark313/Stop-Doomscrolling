// AI Briefing — front-end logic (vanilla JS, no frameworks).

const PREDEFINED_TOPICS = [
  "New AI models",
  "AI funding & acquisitions",
  "Product Hunt AI launches",
  "AI tools & breakthroughs",
];

// Local working state, synced to the server on save.
let state = {
  selected_topics: [],
  custom_topics: [],
  channels: [],
  subreddits: [],
  rss_feeds: [],
  duration: "1hr",
  delivery_time: "08:00",
  telegram_chat_id: "",
};

// ---- DOM helpers --------------------------------------------------------- //
const $ = (id) => document.getElementById(id);

function showToast(message, kind = "") {
  const toast = $("toast");
  toast.textContent = message;
  toast.className = "toast show" + (kind ? " " + kind : "");
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => {
    toast.className = "toast";
  }, 3000);
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

// ---- Rendering ----------------------------------------------------------- //
function renderTopics() {
  const list = $("topics-list");
  list.innerHTML = "";

  const selected = new Set(state.selected_topics);
  const allTopics = [...PREDEFINED_TOPICS];
  state.custom_topics.forEach((t) => {
    if (!allTopics.includes(t)) allTopics.push(t);
  });

  allTopics.forEach((topic) => {
    const isCustom = !PREDEFINED_TOPICS.includes(topic);
    const isOn = selected.has(topic);

    const wrap = document.createElement("div");
    wrap.className = "pill-wrap" + (isCustom ? " custom" : "");

    const pill = document.createElement("button");
    pill.type = "button";
    pill.className = "pill" + (isOn ? " on" : "");
    pill.setAttribute("aria-pressed", String(isOn));
    pill.textContent = topic;
    pill.addEventListener("click", () => {
      toggleTopic(topic, !state.selected_topics.includes(topic));
      renderTopics();
    });
    wrap.appendChild(pill);

    if (isCustom) {
      const remove = document.createElement("button");
      remove.className = "pill-x";
      remove.type = "button";
      remove.title = "Remove topic";
      remove.setAttribute("aria-label", `Remove topic ${topic}`);
      remove.innerHTML = "&times;";
      remove.addEventListener("click", () => removeCustomTopic(topic));
      wrap.appendChild(remove);
    }

    list.appendChild(wrap);
  });
}

function renderChannels() {
  const list = $("channels-list");
  list.innerHTML = "";

  if (state.channels.length === 0) {
    const empty = document.createElement("li");
    empty.className = "empty-hint";
    empty.textContent = "No channels yet — add one below.";
    list.appendChild(empty);
    return;
  }

  state.channels.forEach((ch, index) => {
    const item = document.createElement("li");
    item.className = "tag-item";

    const meta = document.createElement("div");
    meta.className = "tag-meta";
    meta.innerHTML =
      `<span class="tag-name">${escapeHtml(ch.name || ch.handle)}</span>` +
      (ch.handle ? `<span class="tag-detail">${escapeHtml(ch.handle)}</span>` : "");

    const remove = document.createElement("button");
    remove.className = "tag-remove";
    remove.type = "button";
    remove.title = "Remove channel";
    remove.setAttribute("aria-label", `Remove channel ${ch.name || ch.handle}`);
    remove.innerHTML = "&times;";
    remove.addEventListener("click", () => {
      state.channels.splice(index, 1);
      renderChannels();
    });

    item.appendChild(meta);
    item.appendChild(remove);
    list.appendChild(item);
  });
}

function renderSettings() {
  renderDuration();
  $("delivery-time").value = state.delivery_time;
  $("chat-id").value = state.telegram_chat_id;
}

function renderDuration() {
  document.querySelectorAll("#duration-segment .segment-btn").forEach((btn) => {
    const on = btn.dataset.value === state.duration;
    btn.classList.toggle("on", on);
    btn.setAttribute("aria-pressed", String(on));
  });
}

// ---- Mutations ----------------------------------------------------------- //
function toggleTopic(topic, checked) {
  const set = new Set(state.selected_topics);
  if (checked) set.add(topic);
  else set.delete(topic);
  state.selected_topics = [...set];
}

function addCustomTopic() {
  const input = $("custom-topic-input");
  const value = input.value.trim();
  if (!value) return;

  const exists =
    PREDEFINED_TOPICS.includes(value) || state.custom_topics.includes(value);
  if (!exists) {
    state.custom_topics.push(value);
  }
  if (!state.selected_topics.includes(value)) {
    state.selected_topics.push(value);
  }
  input.value = "";
  renderTopics();
}

function removeCustomTopic(topic) {
  state.custom_topics = state.custom_topics.filter((t) => t !== topic);
  state.selected_topics = state.selected_topics.filter((t) => t !== topic);
  renderTopics();
}

function addChannel() {
  const nameInput = $("channel-name-input");
  const handleInput = $("channel-handle-input");
  const name = nameInput.value.trim();
  const handle = handleInput.value.trim();

  if (!name && !handle) {
    showToast("Enter a channel name or handle.", "error");
    return;
  }
  state.channels.push({ name: name || handle, handle });
  nameInput.value = "";
  handleInput.value = "";
  renderChannels();
}

function renderSubreddits() {
  const list = $("subreddits-list");
  list.innerHTML = "";

  if (state.subreddits.length === 0) {
    const empty = document.createElement("li");
    empty.className = "empty-hint";
    empty.textContent = "No communities yet — add one below.";
    list.appendChild(empty);
    return;
  }

  state.subreddits.forEach((sub, index) => {
    const item = document.createElement("li");
    item.className = "tag-item";

    const meta = document.createElement("div");
    meta.className = "tag-meta";
    meta.innerHTML = `<span class="tag-name">r/${escapeHtml(sub)}</span>`;

    const remove = document.createElement("button");
    remove.className = "tag-remove";
    remove.type = "button";
    remove.title = "Remove community";
    remove.setAttribute("aria-label", `Remove community r/${sub}`);
    remove.innerHTML = "&times;";
    remove.addEventListener("click", () => {
      state.subreddits.splice(index, 1);
      renderSubreddits();
    });

    item.appendChild(meta);
    item.appendChild(remove);
    list.appendChild(item);
  });
}

function addSubreddit() {
  const input = $("subreddit-input");
  // Accept "r/foo", "/r/foo", or "foo" — store the bare name.
  const value = input.value.trim().replace(/^\/?r\//i, "").replace(/^\//, "").trim();
  if (!value) {
    showToast("Enter a subreddit name.", "error");
    return;
  }
  if (!state.subreddits.includes(value)) {
    state.subreddits.push(value);
  }
  input.value = "";
  renderSubreddits();
}

function renderFeeds() {
  const list = $("feeds-list");
  list.innerHTML = "";

  if (state.rss_feeds.length === 0) {
    const empty = document.createElement("li");
    empty.className = "empty-hint";
    empty.textContent = "No blogs yet — add one below.";
    list.appendChild(empty);
    return;
  }

  state.rss_feeds.forEach((feed, index) => {
    const item = document.createElement("li");
    item.className = "tag-item";

    const meta = document.createElement("div");
    meta.className = "tag-meta";
    meta.innerHTML =
      `<span class="tag-name">${escapeHtml(feed.name || feed.url)}</span>` +
      (feed.url ? `<span class="tag-detail">${escapeHtml(feed.url)}</span>` : "");

    const remove = document.createElement("button");
    remove.className = "tag-remove";
    remove.type = "button";
    remove.title = "Remove blog";
    remove.setAttribute("aria-label", `Remove blog ${feed.name || feed.url}`);
    remove.innerHTML = "&times;";
    remove.addEventListener("click", () => {
      state.rss_feeds.splice(index, 1);
      renderFeeds();
    });

    item.appendChild(meta);
    item.appendChild(remove);
    list.appendChild(item);
  });
}

function addFeed() {
  const nameInput = $("feed-name-input");
  const urlInput = $("feed-url-input");
  const name = nameInput.value.trim();
  const url = urlInput.value.trim();

  if (!url) {
    showToast("Enter an RSS feed URL.", "error");
    return;
  }
  state.rss_feeds.push({ name: name || url, url });
  nameInput.value = "";
  urlInput.value = "";
  renderFeeds();
}

// ---- Server sync --------------------------------------------------------- //
async function loadConfig() {
  try {
    const res = await fetch("/api/config");
    const cfg = await res.json();
    state = {
      selected_topics: cfg.selected_topics || [],
      custom_topics: cfg.custom_topics || [],
      channels: cfg.channels || [],
      subreddits: cfg.subreddits || [],
      rss_feeds: cfg.rss_feeds || [],
      duration: cfg.duration || "1hr",
      delivery_time: cfg.delivery_time || "08:00",
      telegram_chat_id: cfg.telegram_chat_id || "",
    };
  } catch (err) {
    showToast("Could not load settings.", "error");
  }
  renderTopics();
  renderChannels();
  renderSubreddits();
  renderFeeds();
  renderSettings();
}

function collectState() {
  return {
    selected_topics: state.selected_topics,
    custom_topics: state.custom_topics,
    channels: state.channels,
    subreddits: state.subreddits,
    rss_feeds: state.rss_feeds,
    duration: state.duration,
    delivery_time: $("delivery-time").value || "08:00",
    telegram_chat_id: $("chat-id").value.trim(),
  };
}

async function saveConfig() {
  const btn = $("save-btn");
  btn.disabled = true;
  try {
    const res = await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(collectState()),
    });
    const data = await res.json();
    if (data.ok) {
      state.duration = data.config.duration;
      state.delivery_time = data.config.delivery_time;
      state.telegram_chat_id = data.config.telegram_chat_id;
      showToast("Settings saved ✓", "success");
    } else {
      showToast(data.message || "Save failed.", "error");
    }
  } catch (err) {
    showToast("Save failed.", "error");
  } finally {
    btn.disabled = false;
  }
}

async function runNow() {
  const btn = $("run-now-btn");
  btn.disabled = true;
  showToast("Building your briefing…");
  try {
    // Save current settings first so the test run uses them.
    await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(collectState()),
    });
    const res = await fetch("/api/run-now", { method: "POST" });
    const data = await res.json();
    showToast(data.message, data.ok ? "success" : "error");
  } catch (err) {
    showToast("Could not start the briefing.", "error");
  } finally {
    setTimeout(() => {
      btn.disabled = false;
    }, 1500);
  }
}

// ---- Wiring -------------------------------------------------------------- //
function init() {
  $("add-topic-btn").addEventListener("click", addCustomTopic);
  $("custom-topic-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      addCustomTopic();
    }
  });

  $("add-channel-btn").addEventListener("click", addChannel);
  $("channel-handle-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      addChannel();
    }
  });

  $("add-subreddit-btn").addEventListener("click", addSubreddit);
  $("subreddit-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      addSubreddit();
    }
  });

  $("add-feed-btn").addEventListener("click", addFeed);
  $("feed-url-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      addFeed();
    }
  });

  document.querySelectorAll("#duration-segment .segment-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.duration = btn.dataset.value;
      renderDuration();
    });
  });

  $("save-btn").addEventListener("click", saveConfig);
  $("run-now-btn").addEventListener("click", runNow);

  loadConfig();
}

document.addEventListener("DOMContentLoaded", init);
