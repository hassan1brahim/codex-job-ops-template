const form = document.getElementById("profile-form");
const saved = document.getElementById("saved");

function getPath(object, path) {
  return path.split(".").reduce((value, key) => value?.[key], object);
}

function setPath(object, path, value) {
  const parts = path.split(".");
  let cursor = object;
  for (const part of parts.slice(0, -1)) cursor = cursor[part] ||= {};
  cursor[parts.at(-1)] = value.trim();
}

async function load() {
  const profile = await getWorkdayProfile();
  for (const element of form.elements) {
    if (!element.name) continue;
    element.value = getPath(profile, element.name) ?? "";
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const profile = {};
  for (const element of form.elements) {
    if (!element.name) continue;
    setPath(profile, element.name, element.value);
  }
  await chrome.storage.local.set({ workdayProfile: profile });
  saved.textContent = "Saved.";
  setTimeout(() => (saved.textContent = ""), 2000);
});

document.getElementById("reset").addEventListener("click", async () => {
  await chrome.storage.local.remove("workdayProfile");
  await load();
  saved.textContent = "Defaults restored.";
});

load();
